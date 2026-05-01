from __future__ import annotations

from plugins.memory.recall.store import RecallStore, _query_terms


def test_exact_marker_query_retrieves_marker(tmp_path):
    store = RecallStore(tmp_path / "recall.sqlite")
    try:
        marker = "LOVELY_PHASE2_MARKER"
        store.add_observation(
            content=f"Recall dogfood marker is {marker} for the recall-test profile.",
            type="fact",
            scope="profile",
            status="active",
        )

        results = store.search_observations(marker, limit=5)

        assert results
        assert marker in results[0]["redacted_content"]
    finally:
        store.close()


def test_natural_question_retrieves_marker_after_stopword_filtering(tmp_path):
    store = RecallStore(tmp_path / "recall.sqlite")
    try:
        marker = "lovely-phase2-marker"
        store.add_observation(
            content=f"When asked about phase 2, the memorable marker was {marker}.",
            type="fact",
            scope="profile",
            status="active",
        )

        results = store.search_observations("what was the lovely phase 2 marker using your recall?", limit=5)

        assert results
        assert marker in results[0]["redacted_content"]
    finally:
        store.close()


def test_path_query_retrieves_windows_wsl_path_note(tmp_path):
    store = RecallStore(tmp_path / "recall.sqlite")
    try:
        store.add_observation(
            content="Paperclip work lives at Windows path E:\\Projects\\AI\\paperclip and WSL path /mnt/e/Projects/AI/paperclip.",
            type="fact",
            scope="profile",
            status="active",
        )

        results = store.search_observations("where is /mnt/e/Projects/AI/paperclip?", limit=5)

        assert results
        assert "/mnt/e/Projects/AI/paperclip" in results[0]["redacted_content"]
    finally:
        store.close()


def test_rejected_observation_is_not_retrieved(tmp_path):
    store = RecallStore(tmp_path / "recall.sqlite")
    try:
        marker = "STALE_REJECTED_MARKER"
        store.add_observation(
            content=f"This stale rejected note contains {marker}.",
            type="fact",
            scope="profile",
            status="rejected",
        )

        assert store.search_observations(marker, limit=5) == []
    finally:
        store.close()


def test_secret_shaped_content_is_redacted_in_retrieved_result(tmp_path):
    store = RecallStore(tmp_path / "recall.sqlite")
    try:
        store.add_observation(
            content="The recall redaction marker is secret-marker and API_KEY=supersecret-token-value.",
            type="fact",
            scope="profile",
            status="active",
        )

        results = store.search_observations("secret-marker", limit=5)

        assert results
        content = results[0]["redacted_content"]
        assert "secret-marker" in content
        assert "supersecret-token-value" not in content
        assert "API_KEY=[REDACTED]" in content
    finally:
        store.close()


def test_query_terms_drop_common_stopwords_and_keep_signal_tokens():
    terms = _query_terms("What was the gpt-5.5 model using /mnt/e/Projects/AI/paperclip if needed?")

    assert "what" not in terms
    assert "was" not in terms
    assert "the" not in terms
    assert "using" not in terms
    assert "if" not in terms
    assert "needed" not in terms
    assert "gpt-5.5" in terms
    assert "mnt" in terms
    assert "paperclip" in terms
