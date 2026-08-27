"""shadow-mcp core — the comprehension shadow model (spec §3.7 / shadow spec).

A private, behavioral model of what the learner ACTUALLY understands in
flowing native content — as opposed to what they've made flashcards for
(kb-mcp). The gap between the two is the whole product.

Design:
  - Statistical core (always, cheap): Beta(α, β) per (key, key_kind,
    modality). Unambiguous signals add weighted pseudo-evidence.
  - Small model (async, batched): interprets ambiguous signals (pause,
    rewind, skip) in context, localizes the key, classifies friction.
    Never per-event; never leaves the machine; degrades gracefully.

Private by construction: everything lives in data/user/shadow.sqlite,
never synced, never uploaded.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable

from moguru.config import Config, REPO_ROOT

# Spec §8 schema — verbatim (+ `interpreted` flag for the pending queue).
SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY, ts TEXT, type TEXT,
  key TEXT, key_kind TEXT, modality TEXT,
  sentence TEXT, media_ref TEXT, dwell_ms INTEGER, playback_speed REAL, weight REAL,
  interpreted INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_signals_key ON signals(key, modality);
CREATE TABLE IF NOT EXISTS estimates (
  key TEXT, key_kind TEXT, modality TEXT,
  alpha REAL DEFAULT 1, beta REAL DEFAULT 1,
  sample_size INTEGER DEFAULT 0, last_seen TEXT, updated_at TEXT,
  PRIMARY KEY (key, key_kind, modality)
);
CREATE TABLE IF NOT EXISTS grammar_points ( id INTEGER PRIMARY KEY, name TEXT, matcher TEXT, notes TEXT );
CREATE TABLE IF NOT EXISTS calibration_log ( ts TEXT, key TEXT, modality TEXT, predicted REAL, observed INTEGER );
"""

SIGNAL_TYPES = {
    "hover", "pause", "rewind", "replay", "lookup", "mine", "skip", "complete",
}
MODALITIES = {"reading", "listening"}
KEY_KINDS = {"vocab", "grammar"}

# §4 evidence mapping. Hard evidence dominates soft; weights are the
# calibration tuning knobs (config shadow.weights overrides).
DEFAULT_WEIGHTS = {
    "lookup": 2.0,   # hard: you actively sought the meaning
    "hover": 2.0,    # hard
    "mine": 2.5,     # hard: didn't know it -> now learning
    "rewind": 1.0,   # medium (listening)
    "replay": 1.0,   # medium
    "pause": 0.3,    # soft: nearly ignored until corroborated
    "skip": 0.1,     # very soft
    "complete": 0.3, # soft positive backbone
}
NOT_UNDERSTOOD = {"hover", "pause", "rewind", "replay", "lookup", "mine", "skip"}
AMBIGUOUS = {"pause", "skip", "rewind", "replay"}   # small-model queue
OUTCOME_SIGNALS = {"lookup", "mine", "complete"}     # calibration observables

GAP_P_THRESHOLD = 0.6   # paper-known but p_understood below this = a gap


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(config: Config | None = None) -> sqlite3.Connection:
    if config is None:
        config = Config.load(os.environ.get("MOGURU_CONFIG") or REPO_ROOT / "config.yaml")
    config.user_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.user_dir / "shadow.sqlite")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _weights(config: Config) -> dict[str, float]:
    return {**DEFAULT_WEIGHTS, **(config.shadow_weights or {})}


# ---------------------------------------------------------------------------
# §7 record_signal
# ---------------------------------------------------------------------------

def record_signal(signal: dict[str, Any], config: Config | None = None) -> dict[str, Any]:
    """Ingest one behavioral event -> { accepted, keys_touched[] }."""
    config = config or Config.load(
        os.environ.get("MOGURU_CONFIG") or REPO_ROOT / "config.yaml"
    )
    stype = signal.get("type", "")
    modality = signal.get("modality", "")
    sentence = signal.get("sentence", "") or ""
    if stype not in SIGNAL_TYPES:
        return {"accepted": False, "reason": f"bad type {stype!r}"}
    if modality not in MODALITIES:
        return {"accepted": False, "reason": f"bad modality {modality!r}"}
    if not sentence.strip():
        return {"accepted": False, "reason": "sentence required"}

    weight = _weights(config).get(stype, 0.3)
    keys: list[tuple[str, str]] = []  # (key, kind)

    if signal.get("key"):
        kind = signal.get("key_kind") or "vocab"
        if kind not in KEY_KINDS:
            return {"accepted": False, "reason": f"bad key_kind {kind!r}"}
        keys.append((str(signal["key"]), kind))
    elif stype == "complete":
        # clean pass-through: weak understood evidence for every content lemma
        from moguru.mcp.parser_mcp import core as parser_core

        for t in parser_core.tokenize(sentence, config):
            if parser_core.is_content_word(t) and t["lemma"]:
                keys.append((t["lemma"], "vocab"))

    conn = connect(config)
    try:
        ts = signal.get("ts") or _now()
        # log the signal itself (key may be empty for sentence-level events)
        cur = conn.execute(
            """INSERT INTO signals (ts, type, key, key_kind, modality, sentence,
               media_ref, dwell_ms, playback_speed, weight, interpreted)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (ts, stype, signal.get("key"), signal.get("key_kind") or "vocab",
             modality, sentence, signal.get("media_ref"),
             signal.get("dwell_ms"), signal.get("playback_speed"), weight,
             0 if stype in AMBIGUOUS else 1),
        )
        signal_id = int(cur.lastrowid)

        for key, kind in keys:
            # calibration: outcome signals observe what the pre-update
            # prediction said — the honest order (predict, then update)
            if stype in OUTCOME_SIGNALS:
                _log_calibration(conn, key, kind, modality,
                                 observed=0 if stype in {"lookup", "mine"} else 1,
                                 ts=ts)
            _apply_evidence(conn, key, kind, modality, weight,
                            understood=stype not in NOT_UNDERSTOOD,
                            config=config, ts=ts)
        conn.commit()
    finally:
        conn.close()

    # opportunistic small-model flush when the ambiguous queue grows —
    # ASYNC, never blocking the caller (mining/mark must stay instant)
    pending = _pending_count(config)
    if pending >= 8:
        import threading

        threading.Thread(
            target=_safe_interpret, args=(config,), daemon=True
        ).start()

    return {"accepted": True, "keys_touched": [k for k, _ in keys],
            "signal_id": signal_id}


def _safe_interpret(config: Config) -> None:
    try:
        interpret_pending(config=config)
    except Exception:
        pass  # queue persists; stat core unaffected


def _apply_evidence(conn: sqlite3.Connection, key: str, kind: str,
                    modality: str, weight: float, understood: bool,
                    config: Config, ts: str | None = None) -> None:
    ts = ts or _now()
    row = conn.execute(
        "SELECT * FROM estimates WHERE key=? AND key_kind=? AND modality=?",
        (key, kind, modality),
    ).fetchone()
    if row is None:
        alpha, beta = 1.0, 1.0
        sample = 0
        # §9 cold start: mild prior toward understood if a mature SRS card
        # exists — only mild, because distrusting exactly that assumption
        # is the point of this component.
        if kind == "vocab":
            try:
                from moguru.mcp.kb_mcp import core as kb_core

                if kb_core.is_known(key)["known"]:
                    alpha += 1.0
            except Exception:
                pass
    else:
        alpha, beta, sample = row["alpha"], row["beta"], row["sample_size"]
    if understood:
        alpha += weight
    else:
        beta += weight
    conn.execute(
        """INSERT INTO estimates (key, key_kind, modality, alpha, beta,
           sample_size, last_seen, updated_at) VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(key, key_kind, modality) DO UPDATE SET
             alpha=excluded.alpha, beta=excluded.beta,
             sample_size=excluded.sample_size, last_seen=excluded.last_seen,
             updated_at=excluded.updated_at""",
        (key, kind, modality, alpha, beta, sample + 1, ts, ts),
    )


def _log_calibration(conn: sqlite3.Connection, key: str, kind: str,
                      modality: str, observed: int, ts: str) -> None:
    row = conn.execute(
        "SELECT * FROM estimates WHERE key=? AND key_kind=? AND modality=?",
        (key, kind, modality),
    ).fetchone()
    if row is None or row["sample_size"] < 1:
        return  # nothing was predicted yet — no honest (predicted, observed) pair
    predicted = row["alpha"] / (row["alpha"] + row["beta"])  # pre-update, undecayed
    conn.execute(
        "INSERT INTO calibration_log (ts, key, modality, predicted, observed) VALUES (?,?,?,?,?)",
        (ts, key, modality, round(predicted, 4), observed),
    )


def _pending_count(config: Config) -> int:
    conn = connect(config)
    try:
        return conn.execute(
            "SELECT COUNT(*) c FROM signals WHERE interpreted=0"
        ).fetchone()["c"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# §7 comprehension / comprehension_batch
# ---------------------------------------------------------------------------

def _decay_factor(last_seen: str | None, config: Config) -> float:
    """Gentle exponential decay of evidence without exposure (§9): stale
    estimates lose confidence rather than staying falsely certain."""
    if not last_seen:
        return 1.0
    try:
        then = datetime.fromisoformat(last_seen)
    except ValueError:
        return 1.0
    days = (datetime.now(timezone.utc) - then).total_seconds() / 86400
    factor = 2 ** (-days / max(config.shadow_decay_half_life_days, 1))
    return max(0.25, factor)


def _estimate_from_row(row: sqlite3.Row | None, config: Config,
                       key: str, kind: str, modality: str) -> dict[str, Any]:
    if row is None:
        return {
            "key": key, "key_kind": kind, "modality": modality,
            "p_understood": 0.5, "confidence": "low", "sample_size": 0,
            "last_seen": None,
        }
    decay = _decay_factor(row["last_seen"], config)
    eff_alpha = 1 + (row["alpha"] - 1) * decay
    eff_beta = 1 + (row["beta"] - 1) * decay
    p = eff_alpha / (eff_alpha + eff_beta)
    n = eff_alpha + eff_beta - 2  # effective evidence mass
    min_samples = config.shadow_min_samples
    if row["sample_size"] < min_samples or n < min_samples:
        confidence = "low"
    elif n < 5 * min_samples:
        confidence = "medium"
    else:
        confidence = "high"
    return {
        "key": key, "key_kind": kind, "modality": modality,
        "p_understood": round(p, 4),
        "confidence": confidence,
        "sample_size": row["sample_size"],
        "last_seen": row["last_seen"],
        "alpha": round(eff_alpha, 3), "beta": round(eff_beta, 3),
    }


def comprehension(key: str, modality: str, key_kind: str = "vocab",
                  config: Config | None = None) -> dict[str, Any]:
    """Estimate = { p_understood, confidence, sample_size, last_seen }."""
    config = config or Config.load(
        os.environ.get("MOGURU_CONFIG") or REPO_ROOT / "config.yaml"
    )
    if modality not in MODALITIES:
        raise ValueError(f"modality must be reading|listening, got {modality!r}")
    conn = connect(config)
    try:
        row = conn.execute(
            "SELECT * FROM estimates WHERE key=? AND key_kind=? AND modality=?",
            (key, key_kind, modality),
        ).fetchone()
        return _estimate_from_row(row, config, key, key_kind, modality)
    finally:
        conn.close()


def comprehension_batch(keys: list[str], modality: str,
                         key_kind: str = "vocab",
                         config: Config | None = None) -> list[dict[str, Any]]:
    config = config or Config.load(
        os.environ.get("MOGURU_CONFIG") or REPO_ROOT / "config.yaml"
    )
    conn = connect(config)
    try:
        out = []
        for key in keys:
            row = conn.execute(
                "SELECT * FROM estimates WHERE key=? AND key_kind=? AND modality=?",
                (key, key_kind, modality),
            ).fetchone()
            out.append(_estimate_from_row(row, config, key, key_kind, modality))
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# §5 grammar-point lexicon
# ---------------------------------------------------------------------------

def _seq(tokens: list[dict], i: int, *surfaces: str) -> bool:
    """Do tokens[i:i+len] have exactly these surfaces?"""
    for j, s in enumerate(surfaces):
        if i + j >= len(tokens) or tokens[i + j]["surface"] != s:
            return False
    return True


def _cform(tok: dict) -> str:
    return (tok.get("inflection_type") or "") + (tok.get("inflection_cType") or "")


def find_grammar_points(tokens: list[dict]) -> list[dict[str, Any]]:
    """Pattern lexicon over the parser's token/POS stream (§5). Deterministic
    core; the small model covers the long tail."""
    found: list[dict[str, Any]] = []

    def add(name: str, i: int, j: int) -> None:
        found.append({
            "key": name, "key_kind": "grammar",
            "char_start": tokens[i]["char_start"],
            "char_end": tokens[j]["char_end"],
        })

    for i, t in enumerate(tokens):
        s, pos, lemma = t["surface"], t["pos"], t["lemma"]
        if pos == "助動詞":
            if lemma in {"せる", "させる"}:
                if i + 1 < len(tokens) and tokens[i + 1]["lemma"] in {"られる", "れる"}:
                    add("使役受身（させられる）", i, i + 1)
                else:
                    add("使役（せる・させる）", i, i)
            elif lemma in {"られる", "れる"}:
                add("受身・可能（られる・れる）", i, i)
            elif lemma == "ない":
                add("否定（ない）", i, i)
            elif lemma == "た":
                add("過去（た）", i, i)
            elif lemma in {"よう", "う"}:
                add("意志形（よう・う）", i, i)
            elif s == "そう":
                add("様態（そうだ）", i, i)
            elif lemma == "らしい":
                add("推量（らしい）", i, i)
        if pos == "助詞":
            if s == "なら":
                add("条件（なら）", i, i)
            elif s == "ばかり":
                add("〜ばかり", i, i)
            elif s == "ながら":
                add("〜ながら", i, i)
            elif s == "つつ":
                add("〜つつ", i, i)
            elif s == "て" and i > 0 and _cform(tokens[i - 1]):
                # て形接続 (previous token inflected + て)
                nxt = tokens[i + 1]["lemma"] if i + 1 < len(tokens) else ""
                if nxt in {"しまう", "仕舞う"}:
                    add("〜てしまう", i - 1, i + 1)
                elif nxt in {"おく", "置く"}:
                    add("〜ておく", i - 1, i + 1)
                elif nxt in {"いく", "行く", "くる", "来る"}:
                    add("〜ていく・てくる", i - 1, i + 1)
                elif nxt in {"みる", "見る"}:
                    add("〜てみる", i - 1, i + 1)
                else:
                    add("て形接続", i - 1, i)
        if pos in {"動詞", "形容詞"} and "仮定形" in _cform(t):
            add("条件・仮定形（ば）", i, i)
        if pos in {"動詞", "助動詞"} and s.endswith("たら"):
            add("条件（たら）", i, i)
        if s == "はず":
            add("〜はず（だ）", i, i)
        if s == "べき":
            add("〜べき", i, i)
        if s == "かも":
            add("〜かもしれない", i, i)
        if s == "わけ" and _seq(tokens, i, "わけ", "に", "は"):
            add("〜わけにはいかない", i, i + 2)
        if s == "ざる":
            add("〜ざるを得ない", i, i)
        if s == "お" and pos == "接頭辞":
            for j in range(i + 1, min(i + 5, len(tokens) - 1)):
                if _seq(tokens, j, "に", "なる"):
                    add("敬語（お〜になる）", i, j + 1)
                    break
    # dedupe by (key, span)
    seen = set()
    out = []
    for g in found:
        sig = (g["key"], g["char_start"], g["char_end"])
        if sig not in seen:
            seen.add(sig)
            out.append(g)
    return out


# ---------------------------------------------------------------------------
# §6 predict_friction
# ---------------------------------------------------------------------------

def predict_friction(sentence: str, modality: str = "reading",
                     config: Config | None = None,
                     model_client: Any | None = None) -> list[dict[str, Any]]:
    """Simulate the learner's read of an unseen sentence; call what breaks.
    Friction = { span, type, p_break, reason } ranked by p_break."""
    from moguru.mcp.parser_mcp import core as parser_core

    config = config or Config.load(
        os.environ.get("MOGURU_CONFIG") or REPO_ROOT / "config.yaml"
    )
    tokens = parser_core.tokenize(sentence, config)
    content = [t for t in tokens if parser_core.is_content_word(t) and t["lemma"]]
    grammar = find_grammar_points(tokens)

    keys = [(t["lemma"], "vocab", t) for t in content] + [
        (g["key"], "grammar", g) for g in grammar
    ]
    frictions: list[dict[str, Any]] = []

    reading_ps: list[float] = []
    listening_ps: list[float] = []
    for key, kind, span_src in keys:
        est = comprehension(key, modality, kind, config)
        other = comprehension(key, "listening" if modality == "reading" else "reading", kind, config)
        # confidence shrink: uncertain beliefs pull toward 0.5, not extremes
        n = (est.get("alpha", 1) + est.get("beta", 1)) - 2
        cf = min(1.0, n / max(2 * config.shadow_min_samples, 1))
        p = 0.5 + (est["p_understood"] - 0.5) * cf
        p_break = round(1 - p, 4)
        if p_break > 0.45:
            frictions.append({
                "span": [span_src["char_start"], span_src["char_end"]],
                "type": "vocab" if kind == "vocab" else "grammar",
                "p_break": p_break,
                "reason": (
                    f"{key}: p_understood={est['p_understood']} "
                    f"({est['confidence']}, n={est['sample_size']})"
                ),
            })
        if est["sample_size"] > 0:
            (listening_ps if modality == "listening" else reading_ps).append(est["p_understood"])

    # §6.4 listening parse_speed: long/dense sentence + listening lags reading
    if modality == "listening":
        density = len(content) / max(len(tokens), 1)
        if (len(tokens) > 18 or density > 0.62) and reading_ps and listening_ps:
            lag = (sum(reading_ps) / len(reading_ps)) - (sum(listening_ps) / len(listening_ps))
            if lag > 0.15:
                frictions.append({
                    "span": [0, len(sentence)],
                    "type": "parse_speed",
                    "p_break": round(min(0.9, 0.5 + lag), 4),
                    "reason": (
                        f"long/dense sentence ({len(tokens)} tokens), listening "
                        f"lags reading by {lag:.2f}"
                    ),
                })

    # §6.5 small-model holistic pass: interactions per-key stats miss
    if model_client is None:
        model_client = _try_shadow_client(config)
    if model_client is not None:
        try:
            extra = _holistic_pass(model_client, sentence, tokens, config)
            frictions.extend(extra)
        except Exception:
            pass

    frictions.sort(key=lambda f: -f["p_break"])
    return frictions[:8]


def _try_shadow_client(config: Config):
    from moguru.orchestrator import providers as pm

    try:
        provider, headers = pm.resolve_role("shadow", config)
        # shadow inference is background work — never let it hold a request
        return pm.ProviderClient(provider, headers, timeout=90)
    except Exception:
        return None


def _holistic_pass(client: Any, sentence: str, tokens: list[dict],
                   config: Config) -> list[dict[str, Any]]:
    prompt = (
        "次の日本語の文について、個々の単語は既知でも組み合わせで混乱しそうな点を"
        "特定してください。JSON配列のみ返してください。各要素は "
        '{"span": [開始文字位置, 終了文字位置], "type": "vocab"|"grammar"|"parse_speed", '
        '"p_break": 0〜1, "reason": "日本語で短く"} とします。'
        "混乱点がない場合は [] を返します。\n"
        f"文: {sentence}"
    )
    resp = client.chat(
        [{"role": "user", "content": prompt}],
    )
    text = resp["choices"][0]["message"].get("content") or ""
    if "</think>" in text:
        text = text.split("</think>")[-1]
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        items = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    out = []
    for it in items:
        if not isinstance(it, dict) or "span" not in it:
            continue
        out.append({
            "span": [int(it["span"][0]), int(it["span"][1])],
            "type": it.get("type", "grammar"),
            "p_break": min(0.99, max(0.01, float(it.get("p_break", 0.5)))),
            "reason": str(it.get("reason", "model: interaction"))[:120],
        })
    return out


# ---------------------------------------------------------------------------
# §7 gaps / comprehension_map / explain_estimate / calibration
# ---------------------------------------------------------------------------

def gaps(filter_: dict[str, Any] | None = None,
         config: Config | None = None) -> list[dict[str, Any]]:
    """Paper-known (kb) vs shaky-in-practice. The whole point."""
    config = config or Config.load(
        os.environ.get("MOGURU_CONFIG") or REPO_ROOT / "config.yaml"
    )
    filter_ = filter_ or {}
    modality = filter_.get("modality")
    key_kind = filter_.get("key_kind", "vocab")
    from moguru.mcp.kb_mcp import core as kb_core

    conn = connect(config)
    try:
        rows = conn.execute(
            "SELECT * FROM estimates WHERE key_kind=?", (key_kind,)
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        if modality and row["modality"] != modality:
            continue
        if row["sample_size"] < config.shadow_min_samples:
            continue
        est = _estimate_from_row(row, config, row["key"], row["key_kind"], row["modality"])
        known = kb_core.is_known(row["key"])["known"] if key_kind == "vocab" else False
        if known and est["p_understood"] < GAP_P_THRESHOLD:
            out.append({
                "key": row["key"], "key_kind": row["key_kind"],
                "srs_known": True,
                "p_understood": est["p_understood"],
                "modality": row["modality"],
                "delta": round(1 - est["p_understood"], 4),
            })
    out.sort(key=lambda g: -g["delta"])
    return out


def comprehension_map(scope: str | None = None,
                      config: Config | None = None) -> dict[str, Any]:
    """The shareable, versioned artifact — a heatmap of real comprehension."""
    config = config or Config.load(
        os.environ.get("MOGURU_CONFIG") or REPO_ROOT / "config.yaml"
    )
    conn = connect(config)
    try:
        rows = conn.execute("SELECT * FROM estimates").fetchall()
        meta = conn.execute(
            "SELECT COUNT(*) c, MAX(updated_at) m FROM estimates"
        ).fetchone()
    finally:
        conn.close()
    buckets = {m: [0] * 5 for m in MODALITIES}
    means = {m: [0.0, 0] for m in MODALITIES}
    for row in rows:
        if scope and row["key_kind"] != scope:
            continue
        est = _estimate_from_row(row, config, row["key"], row["key_kind"], row["modality"])
        b = min(4, int(est["p_understood"] * 5))
        buckets[row["modality"]][b] += 1
        w = est.get("alpha", 1) + est.get("beta", 1) - 2
        means[row["modality"]][0] += est["p_understood"] * max(w, 0.01)
        means[row["modality"]][1] += max(w, 0.01)
    version = hashlib.sha1(
        f"{meta['c']}:{meta['m']}".encode()
    ).hexdigest()[:12]
    return {
        "version": version,
        "generated_at": _now(),
        "scope": scope or "all",
        "tracked_keys": meta["c"],
        "buckets_0_to_1": {m: buckets[m] for m in buckets},
        "mean_p_understood": {
            m: round(v[0] / v[1], 4) if v[1] else None for m, v in means.items()
        },
        "top_gaps": gaps({"modality": None}, config)[:10],
    }


def explain_estimate(key: str, modality: str, key_kind: str = "vocab",
                     config: Config | None = None) -> dict[str, Any]:
    """Transparency: WHY does it believe this? The actual evidence trail."""
    config = config or Config.load(
        os.environ.get("MOGURU_CONFIG") or REPO_ROOT / "config.yaml"
    )
    est = comprehension(key, modality, key_kind, config)
    conn = connect(config)
    try:
        trail = conn.execute(
            """SELECT ts, type, sentence, weight, interpreted FROM signals
               WHERE key=? AND modality=? ORDER BY id DESC LIMIT 12""",
            (key, modality),
        ).fetchall()
    finally:
        conn.close()
    evidence = [
        {
            "ts": r["ts"], "type": r["type"], "weight": r["weight"],
            "sentence": (r["sentence"] or "")[:80],
            "interpreted": bool(r["interpreted"]),
        }
        for r in trail
    ]
    hard_neg = sum(e["weight"] for e in evidence
                   if e["type"] in {"lookup", "hover", "mine"})
    pos = sum(e["weight"] for e in evidence if e["type"] == "complete")
    reasoning = (
        f"Beta({est.get('alpha', 1):.2f}, {est.get('beta', 1):.2f}) over "
        f"{est['sample_size']} encounters → p_understood={est['p_understood']}. "
        f"Hard not-understood evidence (lookup/hover/mine): {hard_neg:.1f}; "
        f"clean pass-throughs: {pos:.1f}. "
        + ("Below min_samples — reported low-confidence by rule." 
           if est["confidence"] == "low" else "")
    )
    return {"estimate": est, "evidence": evidence, "reasoning": reasoning}


def calibration(config: Config | None = None) -> dict[str, Any]:
    """Is the model any good? Decile curve + Brier over recent predictions."""
    config = config or Config.load(
        os.environ.get("MOGURU_CONFIG") or REPO_ROOT / "config.yaml"
    )
    conn = connect(config)
    try:
        rows = conn.execute(
            "SELECT predicted, observed FROM calibration_log ORDER BY rowid DESC LIMIT ?",
            (config.shadow_calibration_window,),
        ).fetchall()
    finally:
        conn.close()
    n = len(rows)
    if n == 0:
        return {"curve": [], "brier_score": None, "n": 0}
    buckets = [[] for _ in range(10)]
    for r in rows:
        b = min(9, int(r["predicted"] * 10))
        buckets[b].append(r["observed"])
    curve = [
        {
            "bucket": f"{i/10:.1f}-{(i+1)/10:.1f}",
            "n": len(obs),
            "observed_rate": round(sum(obs) / len(obs), 4) if obs else None,
        }
        for i, obs in enumerate(buckets) if obs
    ]
    brier = sum((r["predicted"] - r["observed"]) ** 2 for r in rows) / n
    return {"curve": curve, "brier_score": round(brier, 4), "n": n}


# ---------------------------------------------------------------------------
# §3 small-model interpreter (async, batched)
# ---------------------------------------------------------------------------

def interpret_pending(limit: int = 16, config: Config | None = None,
                      model_client: Any | None = None) -> dict[str, Any]:
    """Disambiguate ambiguous signals (pause/rewind/replay/skip) in context:
    comprehension-relevant? which key? what friction type? Structured output
    is fed back as targeted evidence."""
    config = config or Config.load(
        os.environ.get("MOGURU_CONFIG") or REPO_ROOT / "config.yaml"
    )
    conn = connect(config)
    try:
        pending = conn.execute(
            """SELECT id, type, sentence, modality, dwell_ms FROM signals
               WHERE interpreted=0 ORDER BY id LIMIT ?""",
            (limit,),
        ).fetchall()
        if not pending:
            return {"interpreted": 0, "pending": 0}
        if model_client is None:
            model_client = _try_shadow_client(config)
        if model_client is None:
            return {"interpreted": 0, "pending": len(pending),
                    "reason": "shadow model unreachable — queue persists"}
        payload = [
            {"id": r["id"], "type": r["type"], "sentence": r["sentence"],
             "dwell_ms": r["dwell_ms"]}
            for r in pending
        ]
        prompt = (
            "学習者の日本語視聴・読解中の行動シグナルです。それぞれについて、"
            "理解に問題があったかを判断し、問題があればどの語彙・文法点が原因かを"
            "特定してください。JSON配列のみ返してください。各要素: "
            '{"id": 数, "comprehension_related": true|false, '
            '"key": "文中最も可能性の高い語（原型）または文法点名", '
            '"key_kind": "vocab"|"grammar", "confidence": 0〜1}\n'
            f"シグナル: {json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            resp = model_client.chat([{"role": "user", "content": prompt}])
        except Exception:
            return {"interpreted": 0, "pending": len(pending),
                    "reason": "shadow model unreachable — queue persists"}
        text = resp["choices"][0]["message"].get("content") or ""
        if "</think>" in text:
            text = text.split("</think>")[-1]
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            return {"interpreted": 0, "pending": len(pending),
                    "reason": "model returned unparseable output"}
        try:
            items = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {"interpreted": 0, "pending": len(pending),
                    "reason": "model returned unparseable JSON"}
        weights = _weights(config)
        applied = 0
        by_id = {r["id"]: r for r in pending}
        for it in items:
            if not isinstance(it, dict) or "id" not in it:
                continue
            row = by_id.get(int(it["id"]))
            if row is None:
                continue
            if it.get("comprehension_related"):
                conf = min(1.0, max(0.0, float(it.get("confidence", 0.5))))
                key = str(it.get("key") or "").strip()
                kind = it.get("key_kind", "vocab")
                if key and kind in KEY_KINDS:
                    _apply_evidence(
                        conn, key, kind, row["modality"],
                        weight=weights.get(row["type"], 0.3) * conf,
                        understood=False, config=config,
                    )
                    applied += 1
            conn.execute("UPDATE signals SET interpreted=1 WHERE id=?", (row["id"],))
        # anything the model didn't mention is resolved as uninformative
        for r in pending:
            conn.execute("UPDATE signals SET interpreted=1 WHERE id=?", (r["id"],))
        conn.commit()
        return {"interpreted": len(pending), "evidence_applied": applied}
    finally:
        conn.close()
