from __future__ import annotations

import json
import os
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


def test_superseded_observations_are_excluded_from_search_but_exported(tmp_path):
    sys.path.insert(0, str(ROOT))
    from store import RecallStore

    store = RecallStore(tmp_path / "recall.sqlite")
    stale_id = store.add_observation(
        content="Hermes Recall repo path is /mnt/e/Projects/AI/old-recall-memory RECALL-SUPERSEDE-PATH.",
        type="fact",
        scope="project",
        status="active",
        project_path="/mnt/e/Projects/AI/hermes-recall-memory",
    )
    current_id = store.add_observation(
        content="Hermes Recall repo path is /mnt/e/Projects/AI/hermes-recall-memory RECALL-SUPERSEDE-PATH.",
        type="fact",
        scope="project",
        status="active",
        project_path="/mnt/e/Projects/AI/hermes-recall-memory",
        supersedes=stale_id,
    )

    results = store.search_observations("RECALL-SUPERSEDE-PATH", limit=10)
    exported_ids = {item["id"] for item in store.export_archive()["observations"]}

    assert [item["id"] for item in results] == [current_id]
    assert results[0]["supersedes"] == stale_id
    assert results[0]["supersedes_content"] == "Hermes Recall repo path is /mnt/e/Projects/AI/old-recall-memory RECALL-SUPERSEDE-PATH."
    assert {stale_id, current_id}.issubset(exported_ids)
    store.close()


def test_current_observations_exclude_expired_rejected_deleted_and_superseded_rows(tmp_path):
    sys.path.insert(0, str(ROOT))
    from store import RecallStore

    store = RecallStore(tmp_path / "recall.sqlite")
    superseded_id = store.add_observation(content="Old branch decision RECALL-CURRENT-OLD", status="active")
    current_id = store.add_observation(
        content="Use branch feat/recall-memory-provider for Hermes in-tree plugin copy RECALL-CURRENT-NEW",
        type="fact",
        scope="project",
        status="active",
        project_path="/mnt/e/Projects/AI/hermes-agent",
        supersedes=superseded_id,
    )
    expired_id = store.add_observation(content="Expired stale note RECALL-CURRENT-EXPIRED", status="active", expires_at=_iso(-1))
    rejected_id = store.add_observation(content="Rejected stale note RECALL-CURRENT-REJECTED", status="rejected")
    deleted_id = store.add_observation(content="Deleted stale note RECALL-CURRENT-DELETED", status="deleted")
    candidate_id = store.add_observation(content="Candidate note RECALL-CURRENT-CANDIDATE", status="candidate")

    current = store.current_observations(limit=20)
    ids = {item["id"] for item in current}

    assert current_id in ids
    assert superseded_id not in ids
    assert expired_id not in ids
    assert rejected_id not in ids
    assert deleted_id not in ids
    assert candidate_id not in ids
    assert current[0]["trust_level"] in {"archive", "builtin-mirror"}
    assert current[0]["supersedes"] == superseded_id
    assert current[0]["supersedes_content"] == "Old branch decision RECALL-CURRENT-OLD"
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


def test_import_redacts_secret_shapes_before_storage_and_search(tmp_path):
    sys.path.insert(0, str(ROOT))
    from store import RecallStore

    raw_secret = "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"
    github_token = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"
    payload = {
        "version": 1,
        "schema_version": "1",
        "episodes": [
            {
                "id": "import-secret-episode",
                "session_id": "import-session",
                "project_path": "/mnt/e/Projects/AI/hermes-recall-memory",
                "user_text": f"Please debug with OPENAI_API_KEY={raw_secret}",
                "assistant_text": f"Do not persist GitHub token {github_token}",
                "created_at": _iso(0),
            }
        ],
        "observations": [
            {
                "id": "import-secret-observation",
                "type": "fact",
                "scope": "project",
                "trust_level": "archive",
                "confidence": 0.5,
                "importance": 0.5,
                "status": "active",
                "content": f"Imported env had OPENAI_API_KEY={raw_secret} and token {github_token} RECALL-IMPORT-SECRET.",
                "redacted_content": f"Imported env had OPENAI_API_KEY={raw_secret} and token {github_token} RECALL-IMPORT-SECRET.",
                "source_session_id": "import-session",
                "project_path": "/mnt/e/Projects/AI/hermes-recall-memory",
                "created_at": _iso(0),
                "expires_at": None,
                "supersedes": None,
            }
        ],
        "audit_events": [
            {
                "seq": 1,
                "event_id": "audit-secret-event",
                "phase": "result",
                "operation": "import_fixture",
                "target": "observation",
                "content_preview": f"Preview leaked OPENAI_API_KEY={raw_secret}",
                "prev_hash": "",
                "event_hash": "fixture-hash",
                "created_at": _iso(0),
                "metadata_json": "{}",
            }
        ],
    }

    store = RecallStore(tmp_path / "recall.sqlite")
    summary = store.import_archive(payload)
    row = store.get_observation("import-secret-observation")
    episode = store.conn.execute("SELECT * FROM episodes WHERE id='import-secret-episode'").fetchone()
    audit = store.conn.execute("SELECT * FROM audit_events WHERE event_id='audit-secret-event'").fetchone()
    results = store.search_observations("RECALL-IMPORT-SECRET", limit=5)

    assert summary["observations_imported"] == 1
    assert summary["episodes_imported"] == 1
    assert summary["audit_events_imported"] == 1
    assert raw_secret not in row["content"]
    assert github_token not in row["content"]
    assert "OPENAI_API_KEY=[REDACTED]" in row["content"]
    assert raw_secret not in episode["user_text"]
    assert github_token not in episode["assistant_text"]
    assert raw_secret not in audit["content_preview"]
    assert raw_secret not in results[0]["content"]
    assert github_token not in results[0]["content"]
    store.close()


def test_provider_exposes_export_import_and_diagnose_tools(tmp_path):
    Provider = _load_provider_class()
    provider = Provider({"db_path": str(tmp_path / "recall.sqlite")})
    provider.initialize("session-1", hermes_home=tmp_path, cwd="/work")
    try:
        names = {schema["name"] for schema in provider.get_tool_schemas()}
        assert "memory_archive_current" in names
        assert "memory_archive_export" in names
        assert "memory_archive_import" in names
        assert "memory_archive_diagnose" in names

        provider.store.add_observation(content="Diagnose/export marker RECALL-DIAG-219", project_path="/work")
        current = json.loads(provider.handle_tool_call("memory_archive_current", {"limit": 10}))
        assert current["results"]
        assert current["trust"] == "lower-trust archive evidence; built-in MEMORY.md/USER.md remain authoritative"
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


def test_install_script_supports_dry_run_check_and_idempotent_install(tmp_path):
    hermes_home = tmp_path / "custom-hermes-home"
    install_script = ROOT / "scripts" / "install.sh"
    env = {**os.environ, "HERMES_HOME": str(hermes_home)}

    dry_run = subprocess.run(
        [str(install_script), "--dry-run"],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert "DRY RUN" in dry_run.stdout
    assert "hermes config set memory.provider recall" in dry_run.stdout
    assert not (hermes_home / "plugins" / "recall").exists()

    check_before = subprocess.run(
        [str(install_script), "--check"],
        text=True,
        capture_output=True,
        env=env,
    )
    assert check_before.returncode != 0
    assert "missing" in check_before.stdout.lower()

    install = subprocess.run(
        [str(install_script)],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert "Installed Hermes Recall memory provider" in install.stdout
    assert "hermes config set plugins.recall.db_path" in install.stdout

    check_after = subprocess.run(
        [str(install_script), "--check"],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert "Install check OK" in check_after.stdout
    for filename in ("__init__.py", "store.py", "schema.py", "audit.py", "redaction.py", "recall_cli.py", "plugin.yaml"):
        assert (hermes_home / "plugins" / "recall" / filename).exists()

    reinstall = subprocess.run(
        [str(install_script)],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert "Installed Hermes Recall memory provider" in reinstall.stdout


def test_standalone_cli_stats_search_verify_diagnose_export(tmp_path):
    sys.path.insert(0, str(ROOT))
    from store import RecallStore

    db_path = tmp_path / "recall.sqlite"
    store = RecallStore(db_path)
    store.add_observation(content="CLI roadmap marker RECALL-CLI-904", type="fact", status="active")
    superseded_id = store.add_observation(content="CLI stale branch marker RECALL-CLI-OLD", type="fact", status="active")
    store.add_observation(content="CLI current branch marker RECALL-CLI-NEW", type="fact", status="active", supersedes=superseded_id)
    store.close()

    base = [sys.executable, str(ROOT / "recall_cli.py"), "--db", str(db_path)]
    stats = subprocess.run(base + ["stats", "--json"], text=True, capture_output=True, check=True)
    search = subprocess.run(base + ["search", "RECALL-CLI-904", "--json"], text=True, capture_output=True, check=True)
    current = subprocess.run(base + ["current", "--json"], text=True, capture_output=True, check=True)
    verify = subprocess.run(base + ["verify", "--json"], text=True, capture_output=True, check=True)
    diagnose = subprocess.run(base + ["diagnose", "--json"], text=True, capture_output=True, check=True)
    export = subprocess.run(base + ["export"], text=True, capture_output=True, check=True)

    current_results = json.loads(current.stdout)["results"]
    current_ids = {item["id"] for item in current_results}
    assert json.loads(stats.stdout)["observations_by_status"]["active"] == 3
    assert json.loads(search.stdout)["results"][0]["content"] == "CLI roadmap marker RECALL-CLI-904"
    assert superseded_id not in current_ids
    assert any(item["content"] == "CLI current branch marker RECALL-CLI-NEW" for item in current_results)
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
