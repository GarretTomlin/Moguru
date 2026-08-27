// Options: engine URL, enable toggle, band colors (injected as CSS
// variables on every page so both page spans and the PDF reader match).

const $ = (id) => document.getElementById(id);

function hexToRgba(hex, alpha) {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

async function save() {
  const engineUrl = $("engineUrl").value.trim() || "http://localhost:8766";
  const enabled = $("enabled").checked;
  const iplus = $("iplusColor").value;
  const hard = $("hardColor").value;
  await chrome.storage.sync.set({ engineUrl, enabled, iplusColor: iplus, hardColor: hard });
  // persist as CSS variables the content script can inject
  await chrome.storage.sync.set({
    cssVars:
      `:root{--moguru-iplus-bg:${hexToRgba(iplus, 0.28)};--moguru-iplus-underline:${iplus};` +
      `--moguru-hard-bg:${hexToRgba(hard, 0.30)};--moguru-hard-underline:${hard};}`,
  });
  $("status").textContent = "saved";
  setTimeout(() => ($("status").textContent = ""), 1500);
}

async function load() {
  const s = await chrome.storage.sync.get(["engineUrl", "enabled", "iplusColor", "hardColor"]);
  $("engineUrl").value = s.engineUrl || "http://localhost:8766";
  $("enabled").checked = s.enabled !== false;
  $("iplusColor").value = s.iplusColor || "#4ade80";
  $("hardColor").value = s.hardColor || "#fbbf24";
}

$("openReader").onclick = () => chrome.tabs.create({ url: "reader.html" });
["engineUrl", "enabled", "iplusColor", "hardColor"].forEach((id) =>
  $(id).addEventListener("change", save)
);
load();
