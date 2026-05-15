from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

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


def test_consolidation_suggestions_hide_low_quality_episode_trace_groups_by_default(tmp_path):
    sys.path.insert(0, str(ROOT))
    from store import RecallStore

    store = RecallStore(tmp_path / "recall.sqlite")
    for idx in range(3):
        store.add_observation(
            content=f"User asked: synthetic dogfood prompt {idx} RECALL-NOISY-{idx}\nAssistant answered: synthetic dogfood answer {idx}",
            type="episode",
            scope="session",
            trust_level="archive",
            confidence=0.35,
            importance=0.25,
            status="active",
        )
    old_fact_id = store.add_observation(
        content="Recall Memory: quality polish old note for consolidation filtering.",
        type="fact",
        scope="profile",
        trust_level="archive",
        confidence=0.55,
        importance=0.55,
        status="active",
    )
    new_fact_id = store.add_observation(
        content="Recall Memory: quality polish new note for consolidation filtering with commit `0a839cf`.",
        type="fact",
        scope="profile",
        trust_level="builtin-mirror",
        confidence=0.95,
        importance=0.9,
        status="active",
    )

    default_suggestions = store.suggest_consolidations(limit=10)
    low_quality_suggestions = store.suggest_consolidations(limit=10, include_low_quality=True)

    default_keys = {item["subject_key"] for item in default_suggestions}
    low_quality_keys = {item["subject_key"] for item in low_quality_suggestions}
    recall = next(item for item in default_suggestions if item["subject_key"] == "label:recall memory")

    assert "label:user asked" not in default_keys
    assert "label:user asked" in low_quality_keys
    assert recall["canonical_id"] == new_fact_id
    assert old_fact_id in recall["duplicate_ids"]
    store.close()




def test_search_terms_ignore_boolean_operators_in_queries(tmp_path):
    sys.path.insert(0, str(ROOT))
    from store import RecallStore

    store = RecallStore(tmp_path / "recall.sqlite")
    try:
        row_id = store.add_observation(
            content="Recall Memory: conflict suggest marker RECALL-BOOL-731 lives in the recall-test profile.",
            type="fact",
            scope="profile",
            trust_level="builtin-mirror",
            confidence=0.95,
            importance=0.9,
        )

        results = store.search_observations("recall memory OR recall-test OR conflict suggest", limit=5)

        assert results
        assert results[0]["id"] == row_id
        assert "or" not in results[0]["matched_query_terms"]
        assert "OR" not in results[0]["why_retrieved"][0]
    finally:
        store.close()


def test_current_observations_hide_low_quality_active_episode_rows_by_default(tmp_path):
    sys.path.insert(0, str(ROOT))
    from store import RecallStore

    store = RecallStore(tmp_path / "recall.sqlite")
    try:
        noisy_id = store.add_observation(
            content="User asked: test cleanup noise\nAssistant answered: maybe maybe maybe maybe",
            type="episode",
            trust_level="archive",
            confidence=0.25,
            importance=0.2,
            status="active",
        )
        fact_id = store.add_observation(
            content="Recall Memory: current view should keep durable fact marker RECALL-CURRENT-QUALITY-731.",
            type="fact",
            scope="profile",
            trust_level="builtin-mirror",
            confidence=0.95,
            importance=0.9,
            status="active",
        )

        default_ids = {item["id"] for item in store.current_observations(limit=10)}
        raw_ids = {item["id"] for item in store.current_observations(limit=10, include_low_quality=True)}
        cleanup_ids = {item["id"] for item in store.cleanup_candidates(limit=10)}

        assert fact_id in default_ids
        assert noisy_id not in default_ids
        assert {fact_id, noisy_id}.issubset(raw_ids)
        assert noisy_id in cleanup_ids
        assert fact_id not in cleanup_ids
    finally:
        store.close()

def test_provider_consolidation_tool_can_include_low_quality_groups_when_requested(tmp_path):
    Provider = _load_provider_class()
    provider = Provider({"db_path": str(tmp_path / "recall.sqlite")})
    provider.initialize("session-1", hermes_home=tmp_path, cwd="/work")
    try:
        for idx in range(2):
            provider.store.add_observation(
                content=f"User asked: noisy tool prompt {idx} RECALL-TOOL-NOISE-{idx}\nAssistant answered: noisy tool answer {idx}",
                type="episode",
                scope="session",
                trust_level="archive",
                confidence=0.35,
                importance=0.25,
                status="active",
                project_path="/work",
            )
        provider.store.add_observation(
            content="Recall Memory: tool default keeps useful facts only.",
            type="fact",
            scope="profile",
            trust_level="archive",
            confidence=0.55,
            importance=0.55,
            status="active",
            project_path="/work",
        )
        provider.store.add_observation(
            content="Recall Memory: tool default keeps useful facts only with marker `RECALL-TOOL-FACT`.",
            type="fact",
            scope="profile",
            trust_level="builtin-mirror",
            confidence=0.95,
            importance=0.9,
            status="active",
            project_path="/work",
        )

        default = json.loads(provider.handle_tool_call("memory_consolidation_suggest", {"limit": 10}))
        noisy = json.loads(provider.handle_tool_call("memory_consolidation_suggest", {"limit": 10, "include_low_quality": True}))

        assert "label:user asked" not in {item["subject_key"] for item in default["results"]}
        assert "label:user asked" in {item["subject_key"] for item in noisy["results"]}
        assert default["filters"]["include_low_quality"] is False
        assert noisy["filters"]["include_low_quality"] is True
    finally:
        provider.shutdown()


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


def test_builtin_memory_replace_quarantines_all_same_subject_active_mirrors(tmp_path):
    Provider = _load_provider_class()
    provider = Provider({"db_path": str(tmp_path / "recall.sqlite")})
    provider.initialize("session-1", hermes_home=tmp_path, cwd="/work")
    try:
        first = "Recall Memory: active source `/old/path`; first duplicate."
        second = "Recall Memory: active source `/middle/path`; accidental add duplicate."
        final = "Recall Memory: active source `/mnt/e/Projects/AI/hermes-recall-memory`; final canonical fact."

        provider.on_memory_write("add", "memory", first, metadata={"session_id": "s1"})
        provider.on_memory_write("add", "memory", second, metadata={"session_id": "s1"})
        provider.on_memory_write("replace", "memory", final, metadata={"session_id": "s1"})

        current = provider.store.current_observations(scope="profile", limit=20)
        recall_rows = [row for row in current if row["content"].startswith("Recall Memory:")]
        exported = [
            row for row in provider.store.export_archive()["observations"] if row["content"].startswith("Recall Memory:")
        ]
        active_exported = [row for row in exported if row["status"] == "active"]
        rejected_contents = {row["content"] for row in exported if row["status"] == "rejected"}

        assert [row["content"] for row in recall_rows] == [final]
        assert [row["content"] for row in active_exported] == [final]
        assert {first, second}.issubset(rejected_contents)
        assert recall_rows[0]["supersedes"]
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



def test_search_results_explain_why_retrieved_without_changing_trust_model(tmp_path):
    sys.path.insert(0, str(ROOT))
    from store import RecallStore

    store = RecallStore(tmp_path / "recall.sqlite")
    match_id = store.add_observation(
        content="Recall Intelligence: exact marker RECALL-WHY-731 lives in `/mnt/e/Projects/AI/hermes-recall-memory`.",
        type="fact",
        scope="project",
        trust_level="builtin-mirror",
        confidence=0.95,
        importance=0.9,
        status="active",
        project_path="/work",
    )

    results = store.search_observations("RECALL-WHY-731 hermes", limit=5, project_path="/work")

    assert results[0]["id"] == match_id
    assert results[0]["matched_query_terms"]
    assert results[0]["recall_score"] > 0
    assert "why_retrieved" in results[0]
    assert any("matched query terms" in reason for reason in results[0]["why_retrieved"])
    assert "trusted built-in memory mirror" in results[0]["why_retrieved"]
    assert "lower-trust archive evidence" in results[0]["trust"]
    store.close()


def test_conflict_suggestions_surface_contradictory_same_subject_facts_without_mutating_rows(tmp_path):
    sys.path.insert(0, str(ROOT))
    from store import RecallStore

    store = RecallStore(tmp_path / "recall.sqlite")
    old_id = store.add_observation(
        content="Paperclip dev: use port 3100 for the local app.",
        type="fact",
        scope="profile",
        trust_level="archive",
        confidence=0.55,
        importance=0.55,
        status="active",
    )
    new_id = store.add_observation(
        content="Paperclip dev: use port 3102 via `paperclip-dev.sh` for the local app.",
        type="fact",
        scope="profile",
        trust_level="builtin-mirror",
        confidence=0.95,
        importance=0.9,
        status="active",
    )

    conflicts = store.suggest_conflicts(limit=10)

    assert conflicts
    conflict = next(item for item in conflicts if item["subject_key"].startswith("label:paperclip dev"))
    assert conflict["recommended_action"] == "review_conflict"
    assert conflict["conflict_signals"]["numeric_values"] == ["3100", "3102"]
    assert conflict["canonical_candidate_id"] == new_id
    assert {old_id, new_id} == {row["id"] for row in conflict["items"]}
    assert store.get_observation(old_id)["status"] == "active"
    assert store.get_observation(new_id)["status"] == "active"
    store.close()


def test_provider_exposes_conflict_suggestions_tool(tmp_path):
    Provider = _load_provider_class()
    provider = Provider({"db_path": str(tmp_path / "recall.sqlite")})
    provider.initialize("session-1", hermes_home=tmp_path, cwd="/work")
    try:
        names = {schema["name"] for schema in provider.get_tool_schemas()}
        assert "memory_conflict_suggest" in names

        provider.store.add_observation(
            content="POTI auth: MCP server listens on 127.0.0.1:9000.",
            type="fact",
            scope="profile",
            status="active",
            project_path="/work",
        )
        provider.store.add_observation(
            content="POTI auth: MCP server listens on 127.0.0.1:9100.",
            type="fact",
            scope="profile",
            trust_level="builtin-mirror",
            confidence=0.95,
            importance=0.9,
            status="active",
            project_path="/work",
        )

        response = json.loads(provider.handle_tool_call("memory_conflict_suggest", {"limit": 5}))

        assert response["results"]
        assert response["trust"] == "conflict suggestions only; no archive rows were mutated"
        assert response["results"][0]["recommended_action"] == "review_conflict"
    finally:
        provider.shutdown()



def test_provider_current_reports_hidden_cleanup_candidates_and_tool_lists_them(tmp_path):
    Provider = _load_provider_class()
    provider = Provider({"db_path": str(tmp_path / "recall.sqlite")})
    provider.initialize("session-1", hermes_home=tmp_path, cwd="/work")
    try:
        noisy_id = provider.store.add_observation(
            content="User asked: active cleanup tool noise\nAssistant answered: repeated repeated repeated repeated",
            type="episode",
            scope="session",
            trust_level="archive",
            confidence=0.25,
            importance=0.2,
            status="active",
            project_path="/work",
        )
        fact_id = provider.store.add_observation(
            content="Recall Memory: provider current keeps useful fact marker RECALL-CURRENT-TOOL-731.",
            type="fact",
            scope="profile",
            trust_level="builtin-mirror",
            confidence=0.95,
            importance=0.9,
            status="active",
            project_path="/work",
        )

        current = json.loads(provider.handle_tool_call("memory_archive_current", {"limit": 10}))
        raw = json.loads(provider.handle_tool_call("memory_archive_current", {"limit": 10, "include_low_quality": True}))
        cleanup = json.loads(provider.handle_tool_call("memory_cleanup_candidates", {"limit": 10}))

        assert {row["id"] for row in current["results"]} == {fact_id}
        assert {row["id"] for row in raw["results"]} >= {fact_id, noisy_id}
        assert current["hidden_cleanup_candidate_count"] == 1
        assert [row["id"] for row in cleanup["results"]] == [noisy_id]
        assert cleanup["trust"] == "cleanup suggestions only; no archive rows were mutated"
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
        assert "memory_cleanup_candidates" in names

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
        assert diagnose["build_info"]["metadata_versions"]["runtime"] == "0.3.9"
        assert diagnose["warnings"] == []
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
    for filename in ("__init__.py", "store.py", "schema.py", "audit.py", "redaction.py", "recall_cli.py", "plugin.yaml", "after-install.md"):
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
    conflicts = subprocess.run(base + ["conflicts", "--json"], text=True, capture_output=True, check=True)
    apply_dry = subprocess.run(base + ["apply-consolidation", "--canonical-id", next(row["canonical_id"] for row in json.loads(consolidate.stdout)["results"]), "--duplicate-id", superseded_id, "--json"], text=True, capture_output=True, check=True)
    apply_confirmed = subprocess.run(base + ["apply-consolidation", "--canonical-id", next(row["canonical_id"] for row in json.loads(consolidate.stdout)["results"]), "--duplicate-id", superseded_id, "--confirm", "--reason", "cli reviewed", "--json"], text=True, capture_output=True, check=True)
    verify = subprocess.run(base + ["verify", "--json"], text=True, capture_output=True, check=True)
    diagnose = subprocess.run(base + ["diagnose", "--json"], text=True, capture_output=True, check=True)
    export = subprocess.run(base + ["export"], text=True, capture_output=True, check=True)

    current_results = json.loads(current.stdout)["results"]
    current_ids = {item["id"] for item in current_results}
    rank_results = json.loads(rank.stdout)["results"]
    consolidate_results = json.loads(consolidate.stdout)["results"]
    conflict_payload = json.loads(conflicts.stdout)
    assert json.loads(stats.stdout)["observations_by_status"]["active"] == 3
    assert json.loads(search.stdout)["results"][0]["content"] == "CLI roadmap marker RECALL-CLI-904"
    assert superseded_id not in current_ids
    assert any(item["content"] == "CLI Branch: current marker RECALL-CLI-NEW" for item in current_results)
    assert rank_results and "quality_score" in rank_results[0]
    assert consolidate_results and consolidate_results[0]["recommended_action"] == "supersede_duplicates"
    assert conflict_payload["trust"] == "conflict suggestions only; no archive rows were mutated"
    assert json.loads(apply_dry.stdout)["requires_confirm"] is True
    assert json.loads(apply_confirmed.stdout)["duplicates_rejected"] == 1
    assert json.loads(verify.stdout)["ok"] is True
    assert json.loads(diagnose.stdout)["ok"] is True
    assert json.loads(export.stdout)["version"] == 1


def test_provider_promote_requires_confirmation_and_writes_builtin_memory(tmp_path, monkeypatch):
    Provider = _load_provider_class()
    provider = Provider({"db_path": str(tmp_path / "recall.sqlite")})
    provider.initialize("session-1", hermes_home=tmp_path, cwd="/work")
    try:
        candidate_id = provider.store.add_observation(
            content="Recall Memory: safe promotion marker RECALL-PROMOTE-731 belongs in durable memory.",
            type="fact",
            scope="profile",
            trust_level="builtin-mirror",
            confidence=0.95,
            importance=0.9,
            status="candidate",
            project_path="/work",
        )

        dry = json.loads(provider.handle_tool_call("memory_promote_candidate", {"id": candidate_id, "target": "memory"}))
        assert dry["success"] is False
        assert dry["requires_confirm"] is True
        assert "RECALL-PROMOTE-731" in dry["content"]
        assert not (tmp_path / "memories" / "MEMORY.md").exists()

        promoted = json.loads(provider.handle_tool_call(
            "memory_promote_candidate",
            {"id": candidate_id, "target": "memory", "confirm": True, "reason": "operator reviewed"},
        ))
        memory_file = tmp_path / "memories" / "MEMORY.md"
        audit = provider.store.audit_events(limit=5)

        assert promoted["success"] is True
        assert promoted["id"] == candidate_id
        assert promoted["status"] == "promoted"
        assert promoted["target"] == "memory"
        assert "RECALL-PROMOTE-731" in memory_file.read_text(encoding="utf-8")
        assert provider.store.get_observation(candidate_id)["status"] == "promoted"
        assert any(event["operation"] == "promote_to_builtin_memory" and "operator reviewed" in event["metadata_json"] for event in audit)
    finally:
        provider.shutdown()


def test_provider_promote_blocks_low_quality_archive_trace_by_default(tmp_path):
    Provider = _load_provider_class()
    provider = Provider({"db_path": str(tmp_path / "recall.sqlite")})
    provider.initialize("session-1", hermes_home=tmp_path, cwd="/work")
    try:
        noisy_id = provider.store.add_observation(
            content="User asked: say hi\nAssistant answered: hi",
            type="episode",
            scope="session",
            trust_level="archive",
            confidence=0.35,
            importance=0.25,
            status="candidate",
        )

        result = json.loads(provider.handle_tool_call(
            "memory_promote_candidate",
            {"id": noisy_id, "target": "memory", "confirm": True},
        ))

        assert result["success"] is False
        assert "quality" in result["error"].lower()
        assert not (tmp_path / "memories" / "MEMORY.md").exists()
        assert provider.store.get_observation(noisy_id)["status"] == "candidate"
    finally:
        provider.shutdown()




def test_provider_promote_blocks_rejected_rows_unless_explicitly_overridden(tmp_path):
    Provider = _load_provider_class()
    provider = Provider({"db_path": str(tmp_path / "recall.sqlite")})
    provider.initialize("session-1", hermes_home=tmp_path, cwd="/work")
    try:
        rejected_id = provider.store.add_observation(
            content="Recall Memory: rejected but high-signal marker RECALL-REJECTED-PROMOTE should need override.",
            type="fact",
            scope="profile",
            trust_level="builtin-mirror",
            confidence=0.95,
            importance=0.9,
            status="rejected",
        )

        blocked = json.loads(provider.handle_tool_call(
            "memory_promote_candidate",
            {"id": rejected_id, "target": "memory", "confirm": True},
        ))
        overridden = json.loads(provider.handle_tool_call(
            "memory_promote_candidate",
            {"id": rejected_id, "target": "memory", "confirm": True, "allow_rejected": True, "reason": "operator reversed rejection"},
        ))

        assert blocked["success"] is False
        assert "rejected" in blocked["error"].lower()
        assert overridden["success"] is True
        assert provider.store.get_observation(rejected_id)["status"] == "promoted"
        assert "RECALL-REJECTED-PROMOTE" in (tmp_path / "memories" / "MEMORY.md").read_text(encoding="utf-8")
    finally:
        provider.shutdown()

def test_save_config_persists_recall_settings_under_plugins_namespace(monkeypatch, tmp_path):
    Provider = _load_provider_class()
    captured = {}

    hermes_cli_module = types.ModuleType("hermes_cli")
    config_module = types.ModuleType("hermes_cli.config")
    config = {"memory": {"provider": "recall"}, "plugins": {}}
    config_module.load_config = lambda: config

    def fake_save_config(value):
        captured["config"] = value

    config_module.save_config = fake_save_config
    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli_module)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", config_module)

    Provider().save_config(
        {
            "db_path": "$HERMES_HOME/custom-recall.sqlite",
            "auto_capture": "false",
            "prefetch_enabled": "true",
            "max_prefetch_results": "5",
            "audit_enabled": "true",
        },
        str(tmp_path),
    )

    assert captured["config"]["memory"]["provider"] == "recall"
    assert captured["config"]["plugins"]["recall"] == {
        "db_path": "$HERMES_HOME/custom-recall.sqlite",
        "auto_capture": "false",
        "prefetch_enabled": "true",
        "max_prefetch_results": "5",
        "audit_enabled": "true",
    }


def test_provider_exposes_explicit_version_and_build_info(tmp_path):
    Provider = _load_provider_class()
    provider = Provider({"db_path": str(tmp_path / "recall.sqlite")})
    provider.initialize("session-1", hermes_home=tmp_path, cwd="/work")
    try:
        import plugins.memory.recall as recall_module

        names = {schema["name"] for schema in provider.get_tool_schemas()}
        info = json.loads(provider.handle_tool_call("memory_recall_build_info", {}))

        assert recall_module.__version__ == "0.3.9"
        assert provider.version == recall_module.__version__
        assert "memory_recall_build_info" in names
        assert info["name"] == "recall"
        assert info["version"] == "0.3.9"
        assert info["schema_version"]
        assert info["db_path"].endswith("recall.sqlite")
        assert info["provider_module"] == "plugins.memory.recall"
    finally:
        provider.shutdown()


def test_consolidation_apply_rejects_duplicates_and_audits_decision(tmp_path):
    Provider = _load_provider_class()
    provider = Provider({"db_path": str(tmp_path / "recall.sqlite")})
    provider.initialize("session-1", hermes_home=tmp_path, cwd="/work")
    try:
        stale_id = provider.store.add_observation(
            content="Recall Memory: dashboard curation is basic and has no search.",
            type="fact",
            scope="profile",
            trust_level="archive",
            confidence=0.55,
            importance=0.55,
            status="active",
            project_path="/work",
        )
        canonical_id = provider.store.add_observation(
            content="Recall Memory: dashboard curation supports search, detail review, and audited consolidation apply.",
            type="fact",
            scope="profile",
            trust_level="builtin-mirror",
            confidence=0.95,
            importance=0.9,
            status="active",
            project_path="/work",
        )

        dry = json.loads(provider.handle_tool_call(
            "memory_consolidation_apply",
            {"canonical_id": canonical_id, "duplicate_ids": [stale_id], "reason": "operator reviewed"},
        ))
        assert dry["success"] is False
        assert dry["requires_confirm"] is True
        assert provider.store.get_observation(stale_id)["status"] == "active"

        applied = json.loads(provider.handle_tool_call(
            "memory_consolidation_apply",
            {"canonical_id": canonical_id, "duplicate_ids": [stale_id], "confirm": True, "reason": "operator reviewed"},
        ))
        current_ids = {row["id"] for row in provider.store.current_observations(limit=20, project_path="/work")}
        audit = provider.store.audit_events(limit=5)

        assert applied == {
            "success": True,
            "canonical_id": canonical_id,
            "duplicate_ids": [stale_id],
            "duplicates_rejected": 1,
        }
        assert provider.store.get_observation(stale_id)["status"] == "rejected"
        assert canonical_id in current_ids
        assert stale_id not in current_ids
        assert any(event["operation"] == "consolidation_apply" and "operator reviewed" in event["metadata_json"] for event in audit)
    finally:
        provider.shutdown()

def test_dashboard_plugin_backend_lists_marks_and_promotes_recall_rows(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT))
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")
    FastAPI = fastapi.FastAPI
    TestClient = testclient.TestClient
    from store import RecallStore
    import importlib.util

    db_path = tmp_path / "recall_memory.sqlite"
    store = RecallStore(db_path)
    candidate_id = store.add_observation(
        content="Recall Memory: dashboard promote marker RECALL-DASH-731 is durable.",
        type="fact",
        scope="profile",
        trust_level="builtin-mirror",
        confidence=0.95,
        importance=0.9,
        status="candidate",
    )
    store.close()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    spec = importlib.util.spec_from_file_location("recall_dashboard_plugin_api", ROOT / "dashboard" / "plugin_api.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["recall_dashboard_plugin_api"] = module
    spec.loader.exec_module(module)

    app = FastAPI()
    app.include_router(module.router, prefix="/api/plugins/recall")
    client = TestClient(app)

    overview = client.get("/api/plugins/recall/overview").json()
    assert overview["diagnose"]["ok"] is True
    assert overview["stats"]["observations_by_status"]["candidate"] == 1

    queue = client.get("/api/plugins/recall/observations?status=candidate&limit=10").json()
    assert queue["results"][0]["id"] == candidate_id
    assert queue["results"][0]["quality_score"] >= 0.75

    mark = client.post(f"/api/plugins/recall/observations/{candidate_id}/mark", json={"status": "active", "reason": "reviewed"}).json()
    assert mark == {"success": True, "id": candidate_id, "status": "active"}

    promote = client.post(
        f"/api/plugins/recall/observations/{candidate_id}/promote",
        json={"target": "memory", "confirm": True, "reason": "dashboard reviewed"},
    ).json()
    assert promote["success"] is True
    assert promote["status"] == "promoted"
    assert "RECALL-DASH-731" in (tmp_path / "memories" / "MEMORY.md").read_text(encoding="utf-8")



def test_dashboard_plugin_backend_supports_search_detail_and_consolidation_apply(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT))
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")
    FastAPI = fastapi.FastAPI
    TestClient = testclient.TestClient
    from store import RecallStore
    import importlib.util

    db_path = tmp_path / "recall_memory.sqlite"
    store = RecallStore(db_path)
    duplicate_id = store.add_observation(
        content="Recall Memory: dashboard search missing old marker RECALL-DASH-SEARCH-OLD.",
        type="fact",
        scope="profile",
        trust_level="archive",
        confidence=0.55,
        importance=0.55,
        status="active",
    )
    canonical_id = store.add_observation(
        content="Recall Memory: dashboard search and detail marker RECALL-DASH-SEARCH-NEW.",
        type="fact",
        scope="profile",
        trust_level="builtin-mirror",
        confidence=0.95,
        importance=0.9,
        status="active",
    )
    store.close()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    spec = importlib.util.spec_from_file_location("recall_dashboard_plugin_api", ROOT / "dashboard" / "plugin_api.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["recall_dashboard_plugin_api"] = module
    spec.loader.exec_module(module)

    app = FastAPI()
    app.include_router(module.router, prefix="/api/plugins/recall")
    client = TestClient(app)

    overview = client.get("/api/plugins/recall/overview").json()
    searched = client.get("/api/plugins/recall/observations?status=all&q=RECALL-DASH-SEARCH-NEW&limit=10").json()
    filtered = client.get("/api/plugins/recall/observations?status=all&type=fact&recommended_action=keep&min_quality_score=0.8&exclude_episode=true&limit=10").json()
    detail = client.get(f"/api/plugins/recall/observations/{canonical_id}").json()
    dry = client.post(
        "/api/plugins/recall/consolidations/apply",
        json={"canonical_id": canonical_id, "duplicate_ids": [duplicate_id], "reason": "dashboard reviewed"},
    ).json()
    applied = client.post(
        "/api/plugins/recall/consolidations/apply",
        json={"canonical_id": canonical_id, "duplicate_ids": [duplicate_id], "confirm": True, "reason": "dashboard reviewed"},
    ).json()

    assert overview["build_info"]["version"] == "0.3.9"
    assert searched["query"] == "RECALL-DASH-SEARCH-NEW"
    assert [row["id"] for row in searched["results"]] == [canonical_id]
    assert filtered["filters"]["recommended_action"] == "keep"
    assert filtered["filters"]["exclude_episode"] is True
    assert [row["id"] for row in filtered["results"]] == [canonical_id]
    assert detail["id"] == canonical_id
    assert detail["quality_score"] >= 0.75
    assert dry["requires_confirm"] is True
    assert applied["success"] is True
    assert applied["duplicates_rejected"] == 1

def test_dashboard_plugin_manifest_and_assets_are_installed(tmp_path):
    hermes_home = tmp_path / "custom-hermes-home"
    install_script = ROOT / "scripts" / "install.sh"
    env = {**os.environ, "HERMES_HOME": str(hermes_home)}

    subprocess.run([str(install_script)], text=True, capture_output=True, check=True, env=env)

    dashboard = hermes_home / "plugins" / "recall" / "dashboard"
    manifest = json.loads((dashboard / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "recall"
    assert manifest["tab"]["path"] == "/recall"
    assert manifest["api"] == "plugin_api.py"
    assert (dashboard / "plugin_api.py").exists()
    bundle = (dashboard / "dist" / "index.js").read_text(encoding="utf-8")
    assert "Minimum quality" in bundle
    assert "Fact rows" in bundle
    assert "Hide episodes" in bundle
    assert "How to use this page" in bundle
    assert "The tiles below are navigation buttons" in bundle
    assert "No candidate inbox right now" in bundle
    assert "Click Review / promote on any row first" in bundle


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
