from plugins.memory.recall.redaction import redact_text


def test_redacts_common_secret_shapes():
    text = "\n".join(
        [
            "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",
            "Authorization: Bearer abc.def.ghi",
            "github=ghp_abcdefghijklmnopqrstuvwxyz1234567890",
            "aws=AKIAIOSFODNN7EXAMPLE",
            "jwt=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturepart",
            "-----BEGIN PRIVATE KEY-----\nMIICfixturekeymaterial\n-----END PRIVATE KEY-----",
        ]
    )

    redacted = redact_text(text)

    assert "sk-proj" not in redacted
    assert "abc.def.ghi" not in redacted
    assert "ghp_" not in redacted
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted
    assert "eyJhbGci" not in redacted
    assert "MIICfixturekeymaterial" not in redacted
    assert redacted.count("[REDACTED]") >= 6


def test_redaction_preserves_non_secret_text():
    text = "Paperclip dev uses port 3102 and config 54329"

    assert redact_text(text) == text
