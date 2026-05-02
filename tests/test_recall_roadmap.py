from __future__ import annotations

import json
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _iso(delta_days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=delta_days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_provider_class():
    # Standalone repo tests run outside a Hermes checkout. Stub the tiny Hermes
    # interfaces the provider imports, then load the repo root as the plugin
    # package name Hermes uses.
    agent_module = types.ModuleType("agent")
    memory_provider_module = types.ModuleType("agent.memory_provider")

    class MemoryProvider:  # minimal stub for subclassing
        pass

    memory_provider_module.MemoryProvider = MemoryProvider
    tools_module = types.ModuleType("tools")
    registry_module = types.ModuleType("tools.registry")
    registry_module.tool_error = lambda message: json.dumps({"error": message})

    sys.modules.setdefault("agent", agent_module)
    sys.modules.setdefault("agent.memory_provider", memory_provider_module)
    sys.modules.setdefault("tools", tools_module)
    sys.modules.setdefault("tools.registry", registry_module)

    import importlib.util

    package_names = ["plugins", "plugins.memory"]
    for name in package_names:
        sys.modules.setdefault(name, types.ModuleType(name))

    spec = importlib.util.spec_from_file_location(
        "plugins.memory.recall",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugins.memory.recall"] = module
    spec.loader.exec_module(module)
    return module.RecallMemoryProvider


def test_expired_observations_are_excluded_from_search(tmp_path):
    sys.path.insert(0, str(ROOT))
    from store import RecallStore

    store = RecallStore(tmp_path / "recall.sqlite")
    expired_id = store.add_observation(
        content="The legacy deploy marker is RECALL-EXPIRE-OLD.",
        expires_at=_iso(-1),
    )
    fresh_id = store.add_observation(
        content="The current deploy marker is RECALL-EXPIRE-NEW.",
        expires_at=_iso(30),
    )

    results = store.search_observations("deploy marker RECALL-EXPIRE", limit=10)

    ids = {item["id"] for item in results}
    assert fresh_id in ids
    assert expired_id not in ids
    store.close()


def test_export_import_roundtrip_preserves_searchable_archive(tmp_path):
    sys.path.insert(0, str(ROOT))
    from store import RecallStore

    source = RecallStore(tmp_path / "source.sqlite")
    source.add_episode(session_id="s1", project_path="/work", user_text="hello", assistant_text="world")
    observation_id = source.add_observation(
        content="Recall export marker RECALL-EXPORT-731 is important.",
        type="fact",
        scope="project",
        status="active",
        source_session_id="s1",
        project_path="/work",
    )
    source.append_audit_event("result", "export_test", "observation", observation_id, {"ok": True})
    archive = source.export_archive()
    source.close()

    target = RecallStore(tmp_path / "target.sqlite")
    summary = target.import_archive(archive)
    results = target.search_observations("RECALL-EXPORT-731", limit=5)
    stats = target.archive_stats()

    assert summary["observations_imported"] == 1
    assert summary["episodes_imported"] == 1
    assert summary["audit_events_imported"] == 1
    assert results and results[0]["id"] == observation_id
    assert stats["episode_count"] == 1
    target.close()


def test_provider_exposes_export_import_and_diagnose_tools(tmp_path):
    Provider = _load_provider_class()
    provider = Provider({"db_path": str(tmp_path / "recall.sqlite")})
    provider.initialize("session-1", hermes_home=tmp_path, cwd="/work")
    try:
        names = {schema["name"] for schema in provider.get_tool_schemas()}
        assert "memory_archive_export" in names
        assert "memory_archive_import" in names
        assert "memory_archive_diagnose" in names

        provider.store.add_observation(content="Diagnose/export marker RECALL-DIAG-219", project_path="/work")
        exported = json.loads(provider.handle_tool_call("memory_archive_export", {}))
        assert exported["version"] == 1
        assert exported["observations"]

        diagnose = json.loads(provider.handle_tool_call("memory_archive_diagnose", {}))
        assert diagnose["ok"] is True
        assert diagnose["checks"]["fts5_available"] is True
        assert diagnose["checks"]["db_writable"] is True
        assert diagnose["checks"]["audit_chain_ok"] is True
    finally:
        provider.shutdown()


def test_standalone_cli_stats_search_verify_diagnose_export(tmp_path):
    sys.path.insert(0, str(ROOT))
    from store import RecallStore

    db_path = tmp_path / "recall.sqlite"
    store = RecallStore(db_path)
    store.add_observation(content="CLI roadmap marker RECALL-CLI-904", type="fact", status="active")
    store.close()

    base = [sys.executable, str(ROOT / "recall_cli.py"), "--db", str(db_path)]
    stats = subprocess.run(base + ["stats", "--json"], text=True, capture_output=True, check=True)
    search = subprocess.run(base + ["search", "RECALL-CLI-904", "--json"], text=True, capture_output=True, check=True)
    verify = subprocess.run(base + ["verify", "--json"], text=True, capture_output=True, check=True)
    diagnose = subprocess.run(base + ["diagnose", "--json"], text=True, capture_output=True, check=True)
    export = subprocess.run(base + ["export"], text=True, capture_output=True, check=True)

    assert json.loads(stats.stdout)["observations_by_status"]["active"] == 1
    assert json.loads(search.stdout)["results"][0]["content"] == "CLI roadmap marker RECALL-CLI-904"
    assert json.loads(verify.stdout)["ok"] is True
    assert json.loads(diagnose.stdout)["ok"] is True
    assert json.loads(export.stdout)["version"] == 1


def test_packaging_and_ci_files_exist():
    pyproject = (ROOT / "pyproject.toml").read_text()
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    assert "recall-cli" in pyproject
    assert "recall_cli" in pyproject
    assert workflow.exists()
    assert "pytest" in workflow.read_text()
