from __future__ import annotations

import json

from plugins.memory.recall import RecallMemoryProvider


def _provider(tmp_path):
    provider = RecallMemoryProvider({"db_path": str(tmp_path / "recall.sqlite")})
    provider.initialize("session-1", hermes_home=tmp_path, cwd="/workspace/project")
    return provider


def test_archive_search_returns_explainable_redacted_results(tmp_path):
    provider = _provider(tmp_path)
    try:
        store = provider.store
        assert store is not None
        store.add_observation(
            content="Model gpt-5.5 lives in recall profile and API_KEY=supersecret-token-value.",
            type="fact",
            scope="profile",
            trust_level="archive",
            confidence=0.77,
            status="active",
            source_session_id="source-session",
            project_path="/workspace/project",
        )

        payload = json.loads(provider.handle_tool_call("memory_archive_search", {"query": "what model gpt-5.5", "limit": 1}))

        result = payload["results"][0]
        assert result["score"] is not None
        assert "gpt-5.5" in result["matched_query_terms"]
        assert result["created_at"]
        assert result["source_session_id"] == "source-session"
        assert result["project_path"] == "/workspace/project"
        assert result["trust_level"] == "archive"
        assert result["confidence"] == 0.77
        assert "supersecret-token-value" not in result["content"]
        assert "API_KEY=[REDACTED]" in result["content"]
    finally:
        provider.shutdown()


def test_candidate_review_filters_and_mark_lifecycle(tmp_path):
    provider = _provider(tmp_path)
    try:
        store = provider.store
        assert store is not None
        keep_id = store.add_observation(
            content="Candidate project fact",
            type="fact",
            scope="project",
            status="candidate",
        )
        store.add_observation(
            content="Candidate user preference",
            type="preference",
            scope="user",
            status="candidate",
        )

        review = json.loads(
            provider.handle_tool_call(
                "memory_candidate_review",
                {"status": "candidate", "type": "fact", "scope": "project", "limit": 10},
            )
        )
        assert [item["id"] for item in review["results"]] == [keep_id]

        marked = json.loads(
            provider.handle_tool_call(
                "memory_candidate_mark",
                {"id": keep_id, "status": "promoted", "reason": "useful project convention"},
            )
        )
        assert marked == {"success": True, "id": keep_id, "status": "promoted"}
        assert store.get_observation(keep_id)["status"] == "promoted"

        promoted = json.loads(
            provider.handle_tool_call("memory_candidate_review", {"status": "promoted", "limit": 10})
        )
        assert [item["id"] for item in promoted["results"]] == [keep_id]
    finally:
        provider.shutdown()


def test_candidate_mark_rejects_invalid_status(tmp_path):
    provider = _provider(tmp_path)
    try:
        store = provider.store
        assert store is not None
        observation_id = store.add_observation(content="Candidate fact", status="candidate")

        payload = json.loads(provider.handle_tool_call("memory_candidate_mark", {"id": observation_id, "status": "deleted"}))

        assert "error" in payload
        assert store.get_observation(observation_id)["status"] == "candidate"
    finally:
        provider.shutdown()


def test_memory_archive_stats_returns_health_summary(tmp_path):
    provider = _provider(tmp_path)
    try:
        store = provider.store
        assert store is not None
        store.add_observation(content="Active fact", type="fact", status="active")
        store.add_observation(content="Candidate preference", type="preference", status="candidate")
        store.add_episode(session_id="session-1", project_path="/workspace/project", user_text="hello", assistant_text="world")

        stats = json.loads(provider.handle_tool_call("memory_archive_stats", {}))

        assert stats["db_path"].endswith("recall.sqlite")
        assert stats["observations_by_status"]["active"] == 1
        assert stats["observations_by_status"]["candidate"] == 1
        assert stats["observations_by_type"]["fact"] == 1
        assert stats["observations_by_type"]["preference"] == 1
        assert stats["episode_count"] == 1
        assert stats["audit"]["ok"] is True
        assert stats["oldest_observation_at"]
        assert stats["newest_observation_at"]
        assert stats["db_size_bytes"] > 0
    finally:
        provider.shutdown()
