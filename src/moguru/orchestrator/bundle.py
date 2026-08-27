"""Bundle manifest loader + host adapters (Install & Orchestration spec).

`moguru-bundle.json` is the single source of truth: the servers any
MCP-capable host can mount, the data artifacts, the model slots. Adapters
translate the manifest into a host's config format — merge, never overwrite —
and are idempotent.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from moguru.config import REPO_ROOT

MANIFEST_PATH = REPO_ROOT / "moguru-bundle.json"

REQUIRED_TOP = ("name", "version", "servers", "data", "models")


class BundleError(Exception):
    pass


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    path = path or MANIFEST_PATH
    if not path.exists():
        raise BundleError(f"manifest not found: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BundleError(f"manifest is not valid JSON: {e}") from e
    missing = [k for k in REQUIRED_TOP if k not in manifest]
    if missing:
        raise BundleError(f"manifest missing top-level key(s): {missing}")
    for s in manifest["servers"]:
        for field in ("id", "command", "args"):
            if field not in s:
                raise BundleError(f"server entry missing {field!r}: {s!r}")
    return manifest


def mcp_servers_block(manifest: dict[str, Any], python_exe: str | None = None,
                      config_path: Path | None = None) -> dict[str, Any]:
    """The mcpServers JSON block every host understands."""
    python = python_exe or sys.executable
    env_config = str(config_path or (REPO_ROOT / "config.yaml"))
    block: dict[str, Any] = {}
    for s in manifest["servers"]:
        args = [a if a != "python" else python for a in s["args"]]
        if s["command"] == "python":
            block[s["id"]] = {
                "command": python,
                "args": s["args"],
                "env": {"MOGURU_CONFIG": env_config},
            }
        else:
            block[s["id"]] = {"command": s["command"], "args": args}
    return block


# ---------------------------------------------------------------------------
# Host adapters
# ---------------------------------------------------------------------------

HOST_PATHS = {
    "claude-desktop": Path.home()
    / "Library/Application Support/Claude/claude_desktop_config.json",
    "cursor": Path.home() / ".cursor/mcp.json",
    "openclaw": Path.home() / ".openclaw/openclaw.json",
}


def _read_host_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = path.with_suffix(path.suffix + ".moguru-backup")
        shutil.copy2(path, backup)
        print(f"  ⚠ {path} was unparseable; backed up to {backup}, starting fresh")
        return {}


def install_into_host(host: str, manifest: dict[str, Any] | None = None,
                      create: bool = False) -> Path:
    """Merge Moguru's servers into a host's mcpServers. Idempotent."""
    manifest = manifest or load_manifest()
    if host not in HOST_PATHS:
        raise BundleError(
            f"unknown host {host!r} — known: {', '.join(HOST_PATHS)}"
        )
    path = HOST_PATHS[host]
    if not path.parent.exists():
        if not create:
            raise BundleError(
                f"{host} config dir does not exist ({path.parent}) — "
                "pass --create to create it"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".moguru-backup"))
    cfg = _read_host_config(path)
    servers = cfg.setdefault("mcpServers", {})
    block = mcp_servers_block(manifest)
    servers.update(block)  # merge: other servers preserved, ours updated
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def uninstall_from_host(host: str) -> Path:
    if host not in HOST_PATHS:
        raise BundleError(f"unknown host {host!r}")
    path = HOST_PATHS[host]
    if not path.exists():
        return path
    cfg = _read_host_config(path)
    servers = cfg.get("mcpServers") or {}
    manifest = load_manifest()
    for s in manifest["servers"]:
        servers.pop(s["id"], None)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
