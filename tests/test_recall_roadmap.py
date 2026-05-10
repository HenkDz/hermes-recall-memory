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


def test_store_uses_wal_normal_synchronous_for_archive_write_throughput(tmp_path):
    sys.path.insert(0, str(ROOT))
    from store import RecallStore

    store = RecallStore(tmp_path / "recall.sqlite")
    try:
        journal_mode = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
        synchronous = store.conn.execute("PRAGMA synchronous").fetchone()[0]
        indexes = {
            row["name"]
            for row in store.conn.execute("PRAGMA index_list('observations')").fetchall()
        }

        assert journal_mode.lower() == "wal"
        assert synchronous == 1  # NORMAL; avoids fsync-per-row on archive writes.
        assert "idx_observations_status_expires_order" in indexes
        assert "idx_observations_scope_project_status_expires_order" in indexes
        assert "idx_observations_supersedes_status_expires" in indexes
    finally:
        store.close()


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


def test_quality_ranking_prefers_specific_trusted_current_facts_over_noisy_episode_traces(tmp_path):
    sys.path.insert(0, str(ROOT))
    from store import RecallStore

    store = RecallStore(tmp_path / "recall.sqlite")
    noisy_id = store.add_observation(
        content="User asked: help\nAssistant answered: maybe maybe maybe maybe maybe maybe",
        type="episode",
        trust_level="archive",
        confidence=0.25,
        importance=0.2,
        status="active",
    )
    candidate_id = store.add_observation(
        content="Recall Memory: active source `/mnt/e/Projects/AI/hermes-recall-memory`; profile plugin path `/home/nour/.hermes/profiles/recall-test/plugins/recall`; commit d137670 adds duplicate supersession.",
        type="fact",
        scope="profile",
        trust_level="builtin-mirror",
        confidence=0.95,
        importance=0.9,
        status="candidate",
    )

    ranked = store.rank_observations(limit=10)

    assert ranked[0]["id"] == candidate_id
    assert ranked[0]["quality_score"] > ranked[-1]["quality_score"]
    assert "trusted mirror" in ranked[0]["quality_reasons"]
    assert "specific markers" in ranked[0]["quality_reasons"]
    assert ranked[0]["recommended_action"] == "promote"
    assert ranked[-1]["id"] == noisy_id
    assert ranked[-1]["recommended_action"] == "reject"
    store.close()


def test_consolidation_suggestions_group_same_subject_and_choose_highest_quality_canonical(tmp_path):
    sys.path.insert(0, str(ROOT))
    from store import RecallStore

    store = RecallStore(tmp_path / "recall.sqlite")
    stale_id = store.add_observation(
        content="POTI: admin imports offers; users decide opportunities; outdated branch is old-workflow.",
        type="fact",
        scope="profile",
        trust_level="archive",
        confidence=0.55,
        importance=0.55,
        status="active",
    )
    canonical_id = store.add_observation(
        content="POTI: admin imports offers; users decide opportunities; taken or shortlisted offers become readiness workflows.",
        type="fact",
        scope="profile",
        trust_level="builtin-mirror",
        confidence=0.95,
        importance=0.9,
        status="active",
    )
    unrelated_id = store.add_observation(content="Paperclip debugging: use API, not browser.", type="fact", scope="profile")

    suggestions = store.suggest_consolidations(limit=10)

    assert suggestions
    poti = next(item for item in suggestions if item["subject_key"].startswith("label:poti"))
    assert poti["canonical_id"] == canonical_id
    assert stale_id in poti["duplicate_ids"]
    assert unrelated_id not in poti["duplicate_ids"]
    assert poti["recommended_action"] == "supersede_duplicates"
    assert "POTI:" in poti["suggested_content"]
    store.close()


def test_provider_exposes_quality_rank_and_consolidation_tools_and_audits_candidate_marks(tmp_path):
    Provider = _load_provider_class()
    provider = Provider({"db_path": str(tmp_path / "recall.sqlite")})
    provider.initialize("session-1", hermes_home=tmp_path, cwd="/work")
    try:
        names = {schema["name"] for schema in provider.get_tool_schemas()}
        assert "memory_quality_rank" in names
        assert "memory_consolidation_suggest" in names

        candidate_id = provider.store.add_observation(
            content="Recall Memory: quality rank marker RECALL-QUALITY-731 has stable source path `/mnt/e/Projects/AI/hermes-recall-memory`.",
            type="fact",
            scope="profile",
            trust_level="builtin-mirror",
            confidence=0.95,
            importance=0.9,
            status="candidate",
            project_path="/work",
        )
        provider.store.add_observation(
            content="Recall Memory: quality rank marker RECALL-QUALITY-OLD has stale source path `/old`.",
            type="fact",
            scope="profile",
            trust_level="archive",
            confidence=0.5,
            importance=0.5,
            status="active",
            project_path="/work",
        )

        ranked = json.loads(provider.handle_tool_call("memory_quality_rank", {"limit": 5, "include_statuses": ["candidate", "active"]}))
        suggestions = json.loads(provider.handle_tool_call("memory_consolidation_suggest", {"limit": 5}))
        mark = json.loads(provider.handle_tool_call("memory_candidate_mark", {"id": candidate_id, "status": "promoted", "reason": "high quality"}))
        audit = provider.store.audit_events(limit=5)

        assert ranked["results"][0]["id"] == candidate_id
        assert ranked["results"][0]["quality_score"] >= 0.8
        assert suggestions["results"]
        assert mark == {"success": True, "id": candidate_id, "status": "promoted"}
        assert any(event["operation"] == "candidate_mark" and "high quality" in event["metadata_json"] for event in audit)
    finally:
        provider.shutdown()


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


def test_builtin_memory_replace_supersedes_prior_recall_mirror_without_duplicates(tmp_path):
    Provider = _load_provider_class()
    provider = Provider({"db_path": str(tmp_path / "recall.sqlite")})
    provider.initialize("session-1", hermes_home=tmp_path, cwd="/work")
    try:
        original = "Recall Memory: active source `/old/path`; stress fix pending."
        updated = "Recall Memory: active source `/mnt/e/Projects/AI/hermes-recall-memory`; stress hardening pushed as `903e64b`."

        provider.on_memory_write("add", "memory", original, metadata={"session_id": "s1"})
        provider.on_memory_write("replace", "memory", updated, metadata={"session_id": "s1"})
        provider.on_memory_write("replace", "memory", updated, metadata={"session_id": "s1"})

        current = provider.store.current_observations(scope="profile", limit=20)
        recall_rows = [row for row in current if row["content"].startswith("Recall Memory:")]
        exported = provider.store.export_archive()["observations"]
        old_rows = [row for row in exported if row["content"] == original]

        assert [row["content"] for row in recall_rows] == [updated]
        assert len(old_rows) == 1
        assert recall_rows[0]["supersedes"] == old_rows[0]["id"]
    finally:
        provider.shutdown()


def test_prefetch_filters_single_term_noise_but_keeps_unique_marker_hits(tmp_path):
    Provider = _load_provider_class()
    provider = Provider({"db_path": str(tmp_path / "recall.sqlite"), "max_prefetch_results": 5})
    provider.initialize("session-1", hermes_home=tmp_path, cwd="/work")
    try:
        provider.store.add_observation(
            content="Recall Memory: exact marker RECALL_ROBUSTIFY_903e64b keeps WAL synchronous NORMAL indexes.",
            scope="project",
            project_path="/work",
        )
        provider.store.add_observation(
            content="Unrelated note that only says normal once and should not be injected for broad queries.",
            scope="project",
            project_path="/work",
        )

        broad = provider.prefetch("normal")
        specific = provider.prefetch("RECALL_ROBUSTIFY_903e64b")

        assert broad == ""
        assert "RECALL_ROBUSTIFY_903e64b" in specific
        assert "lower-trust" in specific
    finally:
        provider.shutdown()


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


def test_dogfood_archive_fixtures_cover_current_expiry_redaction_and_roundtrip(tmp_path):
    db_path = tmp_path / "dogfood.sqlite"
    marker = "RECALL_DOGFOOD_TEST_123"
    env = {
        **os.environ,
        "RECALL_DOGFOOD_DB": str(db_path),
        "RECALL_DOGFOOD_MARKER": marker,
    }

    result = subprocess.run(
        [str(ROOT / "scripts" / "recall_dogfood.sh"), "--archive-fixtures-only"],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )

    assert f"DOGFOOD_ARCHIVE_FIXTURES_OK {marker}" in result.stdout
    assert f"DOGFOOD_CURRENT_OK {marker}" in result.stdout
    assert f"DOGFOOD_EXPIRED_OK {marker}" in result.stdout
    assert f"DOGFOOD_REDACTION_OK {marker}" in result.stdout
    assert f"DOGFOOD_ROUNDTRIP_OK {marker}" in result.stdout
    assert "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890" not in result.stdout


def test_standalone_cli_stats_search_verify_diagnose_export(tmp_path):
    sys.path.insert(0, str(ROOT))
    from store import RecallStore

    db_path = tmp_path / "recall.sqlite"
    store = RecallStore(db_path)
    store.add_observation(content="CLI roadmap marker RECALL-CLI-904", type="fact", status="active")
    superseded_id = store.add_observation(content="CLI Branch: stale marker RECALL-CLI-OLD", type="fact", status="active")
    store.add_observation(content="CLI Branch: current marker RECALL-CLI-NEW", type="fact", status="active", supersedes=superseded_id)
    store.close()

    base = [sys.executable, str(ROOT / "recall_cli.py"), "--db", str(db_path)]
    stats = subprocess.run(base + ["stats", "--json"], text=True, capture_output=True, check=True)
    search = subprocess.run(base + ["search", "RECALL-CLI-904", "--json"], text=True, capture_output=True, check=True)
    current = subprocess.run(base + ["current", "--json"], text=True, capture_output=True, check=True)
    rank = subprocess.run(base + ["rank", "--json", "--status", "active"], text=True, capture_output=True, check=True)
    consolidate = subprocess.run(base + ["consolidate", "--json"], text=True, capture_output=True, check=True)
    verify = subprocess.run(base + ["verify", "--json"], text=True, capture_output=True, check=True)
    diagnose = subprocess.run(base + ["diagnose", "--json"], text=True, capture_output=True, check=True)
    export = subprocess.run(base + ["export"], text=True, capture_output=True, check=True)

    current_results = json.loads(current.stdout)["results"]
    current_ids = {item["id"] for item in current_results}
    rank_results = json.loads(rank.stdout)["results"]
    consolidate_results = json.loads(consolidate.stdout)["results"]
    assert json.loads(stats.stdout)["observations_by_status"]["active"] == 3
    assert json.loads(search.stdout)["results"][0]["content"] == "CLI roadmap marker RECALL-CLI-904"
    assert superseded_id not in current_ids
    assert any(item["content"] == "CLI Branch: current marker RECALL-CLI-NEW" for item in current_results)
    assert rank_results and "quality_score" in rank_results[0]
    assert consolidate_results and consolidate_results[0]["recommended_action"] == "supersede_duplicates"
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


def test_compatibility_matrix_document_exists_and_covers_operator_drift():
    compatibility = ROOT / "docs" / "COMPATIBILITY.md"
    text = compatibility.read_text(encoding="utf-8")

    required_phrases = [
        "Hermes compatibility matrix",
        "Tested Hermes Agent baseline",
        "Python and SQLite requirements",
        "SQLite FTS5",
        "recall-cli diagnose --json",
        "Hermes plugin API drift",
        "MemoryProvider",
        "get_tool_schemas",
        "handle_tool_call",
        "sync_turn",
        "lower-trust archive evidence",
    ]
    for phrase in required_phrases:
        assert phrase in text
