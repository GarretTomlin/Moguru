// Moguru Reader — background service worker.
// Owns ALL engine fetches (content scripts are CORS-restricted; the worker
// has explicit host_permissions for localhost) plus the right-click menu.

const DEFAULT_ENGINE = "http://localhost:8766";

async function engineUrl() {
  const { engineUrl } = await chrome.storage.sync.get("engineUrl");
  return engineUrl || DEFAULT_ENGINE;
}

async function engineFetch(path, options = {}) {
  const base = await engineUrl();
  const resp = await fetch(base + path, {
    ...options,
    headers: { "Content-Type": "application/json" },
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`engine ${resp.status}: ${text.slice(0, 200)}`);
  }
  return resp.json();
}


// Deliver a panel to the tab's content script AND to extension pages
// (PDF reader) listening on runtime.
async function broadcastPanel(tabId, panel) {
  const msg = { type: "moguru:panel", panel };
  await chrome.tabs.sendMessage(tabId, msg).catch(() => {});
  await chrome.runtime.sendMessage(msg).catch(() => {});
}

// ---------------------------------------------------------------------------
// Right-click menu
// ---------------------------------------------------------------------------

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "moguru-explain",
      title: "Moguru: Explain 「%s」",
      contexts: ["selection", "page"],
    });
    chrome.contextMenus.create({
      id: "moguru-mine",
      title: "Moguru: Send sentence to Anki",
      contexts: ["selection", "page"],
    });
    chrome.contextMenus.create({
      id: "moguru-known",
      title: "Moguru: Mark known",
      contexts: ["selection", "page"],
    });
  });
});

// Shared explain: /lookup + /ask (+ a hard lookup signal for the shadow model)
async function explainEngine(word, sentence, lemma) {
  if (lemma) {
    engineFetch("/signals", {
      method: "POST",
      body: JSON.stringify({
        signals: [{
          type: "lookup", key: lemma, key_kind: "vocab",
          sentence, modality: "reading",
        }],
      }),
    }).catch(() => {});
  }
  const [lookup, ask] = await Promise.allSettled([
    engineFetch("/lookup", { method: "POST", body: JSON.stringify({ text: word }) }),
    engineFetch("/ask", {
      method: "POST",
      body: JSON.stringify({
        question: `この言葉「${word}」の読み方・意味・アクセントを教えてください。`,
        context: sentence,
      }),
    }),
  ]);
  return {
    entries: lookup.status === "fulfilled" ? lookup.value : { error: String(lookup.reason) },
    answer: ask.status === "fulfilled" ? ask.value.answer : `(model unavailable: ${ask.reason})`,
  };
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  // The content script stashes the clicked token (lemma + sentence) on
  // right-click; fall back to the selection if present.
  const stash = (await chrome.storage.session.get("moguruToken")).moguruToken || {};
  const word = stash.word || info.selectionText || "";
  const sentence = stash.sentence || info.selectionText || "";
  const lemma = stash.lemma || word;

  try {
    if (info.menuItemId === "moguru-explain") {
      if (!word.trim()) {
        await broadcastPanel(tab.id, {
          error: "no word — click directly on a colored word (or select text first)",
        });
        return;
      }
      const { entries, answer } = await explainEngine(word, sentence, stash.lemma);
      await broadcastPanel(tab.id, { word, sentence, entries, answer });
    } else if (info.menuItemId === "moguru-mine") {
      // Reader spec §3: pass the clicked word as the authoritative target.
      const result = await engineFetch("/mine", {
        method: "POST",
        body: JSON.stringify({ text: sentence || word, target: stash.lemma || word, add: true }),
      });
      await bumpAndBroadcast();
      await broadcastPanel(tab.id, {
        word,
        sentence,
        mined: result.results || [],
        minedError: (result.results || []).length === 0
          ? "no i+1 candidate in this text (or everything is already known)"
          : null,
      });
    } else if (info.menuItemId === "moguru-known") {
      if (!lemma) throw new Error("no word to mark");
      await engineFetch("/mark_known", {
        method: "POST",
        body: JSON.stringify({ lemma }),
      });
      await bumpAndBroadcast();
    }
  } catch (e) {
    broadcastPanel(tab.id, { word, sentence, error: String(e) });
  }
});

// After kb changes: fetch the new known-set version and tell every tab to repaint.
async function bumpAndBroadcast() {
  try {
    const { version } = await engineFetch("/known_version");
    const tabs = await chrome.tabs.query({});
    for (const tab of tabs) {
      chrome.tabs.sendMessage(tab.id, { type: "moguru:version", version }).catch(() => {});
    }
  } catch (e) {
    /* engine down — next annotate will refresh */
  }
}

// ---------------------------------------------------------------------------
// Message router for content scripts + the PDF reader page
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  console.log("[moguru] ⇠", msg?.type, msg?.word || msg?.lemma || "");
  (async () => {
    try {
      if (msg.type === "moguru:annotate") {
        const data = await engineFetch("/annotate", {
          method: "POST",
          body: JSON.stringify({ text: msg.text }),
        });
        const { version } = await engineFetch("/known_version").catch(() => ({ version: null }));
        sendResponse({ ok: true, data, version });
      } else if (msg.type === "moguru:version") {
        const { version } = await engineFetch("/known_version");
        sendResponse({ ok: true, version });
      } else if (msg.type === "moguru:stashToken") {
        await chrome.storage.session.set({ moguruToken: msg.token });
        sendResponse({ ok: true });
      } else if (msg.type === "moguru:signals") {
        // behavioral signal batch from a surface (shadow schema, §4)
        const data = await engineFetch("/signals", {
          method: "POST",
          body: JSON.stringify({ signals: msg.signals }),
        });
        sendResponse({ ok: true, accepted: data.accepted, total: data.total });
      } else if (msg.type === "moguru:explain") {
        const { entries, answer } = await explainEngine(msg.word, msg.sentence, msg.lemma);
        sendResponse({ ok: true, entries, answer });
      } else if (msg.type === "moguru:ask") {
        const data = await engineFetch("/ask", {
          method: "POST",
          body: JSON.stringify({ question: msg.question, context: msg.context || "" }) });
        sendResponse({ ok: true, answer: data.answer });
      } else if (msg.type === "moguru:lookup") {
        sendResponse({ ok: true, data: await engineFetch("/lookup", {
          method: "POST", body: JSON.stringify({ text: msg.text }) }) });
      } else if (msg.type === "moguru:mine") {
        const data = await engineFetch("/mine", {
          method: "POST", body: JSON.stringify({ text: msg.text, add: !!msg.add }) });
        if (msg.add) await bumpAndBroadcast();
        sendResponse({ ok: true, data });
      } else if (msg.type === "moguru:mark") {
        await engineFetch("/mark_known", {
          method: "POST", body: JSON.stringify({ lemma: msg.lemma }) });
        await bumpAndBroadcast();
        sendResponse({ ok: true });
      } else {
        sendResponse({ ok: false, error: `unknown message ${msg.type}` });
      }
    } catch (e) {
      sendResponse({ ok: false, error: String(e) });
    }
  })();
  return true; // async sendResponse
});

// Toggle command (Alt+M)
chrome.commands.onCommand.addListener(async (command) => {
  if (command !== "toggle-highlight") return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab) chrome.tabs.sendMessage(tab.id, { type: "moguru:toggle" }).catch(() => {});
});
