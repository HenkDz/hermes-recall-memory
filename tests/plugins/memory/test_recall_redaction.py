from plugins.memory.recall.redaction import redact_text


def test_redacts_common_secret_shapes():
    text = "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234567890 and Authorization: Bearer abc.def.ghi"

    redacted = redact_text(text)

    assert "sk-proj" not in redacted
    assert "abc.def.ghi" not in redacted
    assert redacted.count("[REDACTED]") >= 2


def test_redaction_preserves_non_secret_text():
    text = "Paperclip dev uses port 3102 and config 54329"

    assert redact_text(text) == text
