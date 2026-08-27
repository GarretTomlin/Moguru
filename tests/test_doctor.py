"""Doctor + bundle manifest (Install & Orchestration spec §1, §5)."""

from __future__ import annotations

import json

import pytest

from moguru.orchestrator import bundle as bm


def test_manifest_loads_and_validates():
    manifest = bm.load_manifest()
    assert manifest["name"] == "moguru"
    ids = [s["id"] for s in manifest["servers"]]
    assert {"parser", "dict", "freq", "kb", "srs", "media", "shadow"} == set(ids)
    # every server has a runnable module path
    for s in manifest["servers"]:
        assert s["command"] == "python"
        assert any("moguru.mcp" in a for a in s["args"])


def test_manifest_bad_json(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("{oops", encoding="utf-8")
    from moguru.orchestrator.bundle import BundleError

    with pytest.raises(BundleError, match="valid JSON"):
        bm.load_manifest(p)


def test_mcp_servers_block():
    manifest = bm.load_manifest()
    block = bm.mcp_servers_block(manifest, python_exe="/usr/bin/python3")
    assert set(block) == {s["id"] for s in manifest["servers"]}
    for entry in block.values():
        assert entry["command"] == "/usr/bin/python3"
        assert "MOGURU_CONFIG" in entry["env"]


def test_host_adapter_merge_not_overwrite(tmp_path, monkeypatch):
    manifest = bm.load_manifest()
    host_file = tmp_path / "mcp.json"
    host_file.write_text(
        json.dumps({"mcpServers": {"other-tool": {"command": "npx"}}}),
        encoding="utf-8",
    )
    monkeypatch.setitem(bm.HOST_PATHS, "cursor", host_file)
    bm.install_into_host("cursor", manifest)
    merged = json.loads(host_file.read_text())
    assert "other-tool" in merged["mcpServers"]  # preserved
    assert "parser" in merged["mcpServers"]  # ours added
    # idempotent re-run
    bm.install_into_host("cursor", manifest)
    merged2 = json.loads(host_file.read_text())
    assert set(merged2["mcpServers"]) == set(merged["mcpServers"])
    # uninstall removes only ours
    bm.uninstall_from_host("cursor")
    final = json.loads(host_file.read_text())
    assert "parser" not in final["mcpServers"] and "other-tool" in final["mcpServers"]


@pytest.mark.integration
def test_doctor_green_on_real_install():
    from moguru.config import Config, REPO_ROOT

    from moguru.orchestrator import doctor

    cfg = Config.load(REPO_ROOT / "config.yaml")
    if not cfg.dict_db.exists():
        pytest.skip("dictionaries not built")
    assert doctor.run_doctor(cfg, skip_servers=True) == 0


def test_doctor_fails_on_missing_data(tmp_path):
    import yaml

    from moguru.config import Config, REPO_ROOT
    from moguru.orchestrator import doctor

    cfg_data = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
    cfg_data["paths"] = {
        "dictionaries": str(tmp_path / "nope-dict"),
        "user": str(tmp_path / "nope-user"),
    }
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump(cfg_data, allow_unicode=True), encoding="utf-8")
    cfg = Config.load(cfg_file)
    cfg.srs_backend = "none"
    report = doctor.Report()
    doctor.check_dictionaries(report, cfg)
    assert report.failures  # missing DBs must fail, not pass
