import sqlite3
from pathlib import Path

from plugins.memory.recall.audit import verify_audit_chain
from plugins.memory.recall.store import RecallStore


def test_store_initializes_schema_and_fts(tmp_path):
    db_path = tmp_path / "recall.sqlite"

    store = RecallStore(db_path)
    store.close()

    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "schema_meta" in tables
    assert "episodes" in tables
    assert "observations" in tables
    assert "observations_fts" in tables
    assert "audit_events" in tables
    assert conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "1"
    conn.close()


def test_observation_search_filters_status_and_project(tmp_path):
    store = RecallStore(tmp_path / "recall.sqlite")
    keep_id = store.add_observation(
        content="ACP Zed Windows cwd must translate E drive paths to WSL /mnt/e paths",
        type="workflow",
        scope="project",
        trust_level="archive",
        confidence=0.8,
        importance=0.7,
        source_session_id="s1",
        project_path="/mnt/e/Projects/AI/hermes-agent",
    )
    reject_id = store.add_observation(
        content="Rejected stale Zed cwd workaround",
        type="workflow",
        scope="project",
        trust_level="archive",
        confidence=0.8,
        importance=0.7,
        source_session_id="s2",
        project_path="/mnt/e/Projects/AI/hermes-agent",
    )
    other_project_id = store.add_observation(
        content="Zed cwd note for another project",
        type="workflow",
        scope="project",
        trust_level="archive",
        confidence=0.8,
        importance=0.7,
        source_session_id="s3",
        project_path="/tmp/other",
    )
    store.mark_observation_status(reject_id, "rejected")

    results = store.search_observations("Zed cwd WSL", project_path="/mnt/e/Projects/AI/hermes-agent", limit=10)

    ids = [r["id"] for r in results]
    assert keep_id in ids
    assert reject_id not in ids
    assert other_project_id not in ids
    assert results[0]["source_session_id"] == "s1"
    store.close()


def test_search_handles_fts_special_characters(tmp_path):
    store = RecallStore(tmp_path / "recall.sqlite")
    store.add_observation(
        content="Windows path E:\\Projects\\AI maps to /mnt/e/Projects/AI",
        type="workflow",
        scope="project",
        trust_level="archive",
        confidence=0.8,
        importance=0.7,
        source_session_id="s1",
        project_path="/work/project",
    )

    results = store.search_observations("E:\\Projects /mnt/e", project_path="/work/project", limit=5)

    assert results
    assert "mnt/e" in results[0]["content"]
    store.close()


def test_search_natural_question_recalls_by_relevant_terms(tmp_path):
    store = RecallStore(tmp_path / "recall.sqlite")
    store.add_observation(
        content="The Recall usecase codename is RECALL-SMOKE-ALGIERS-527.",
        type="fact",
        scope="project",
        trust_level="archive",
        confidence=0.8,
        importance=0.7,
        source_session_id="s1",
        project_path="/tmp/recall-usecase",
    )

    results = store.search_observations(
        "What was the Recall usecase codename?", project_path="/tmp/recall-usecase", limit=5
    )

    assert results
    assert "RECALL-SMOKE-ALGIERS-527" in results[0]["redacted_content"]
    store.close()



def test_store_redacts_observation_and_episode_payloads_by_default(tmp_path):
    store = RecallStore(tmp_path / "recall.sqlite")
    obs_id = store.add_observation(
        content="token OPENAI_API_KEY=sk-proj-secretsecretsecretsecretsecretsecret",
        type="fact",
        scope="project",
        trust_level="archive",
        confidence=0.8,
        importance=0.7,
    )
    episode_id = store.add_episode(
        session_id="s1",
        project_path="/work/project",
        user_text="my token is sk-proj-secretsecretsecretsecretsecretsecret",
        assistant_text="ok",
    )

    obs = store.get_observation(obs_id)
    episode = store.conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()

    assert "sk-proj" not in obs["content"]
    assert "sk-proj" not in episode["user_text"]
    assert "[REDACTED]" in obs["content"]
    store.close()



def test_audit_chain_detects_tampering(tmp_path):
    store = RecallStore(tmp_path / "recall.sqlite")
    store.append_audit_event("intent", "create", "memory", "remember stable fact", {"session_id": "s1"})
    store.append_audit_event("result", "create", "memory", "remember stable fact", {"ok": True})

    assert verify_audit_chain(store.conn)["ok"] is True

    store.conn.execute("UPDATE audit_events SET content_preview='tampered' WHERE seq=1")
    store.conn.commit()

    result = verify_audit_chain(store.conn)
    assert result["ok"] is False
    assert result["failed_seq"] == 1
    store.close()
