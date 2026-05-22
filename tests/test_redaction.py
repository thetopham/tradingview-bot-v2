from tvbot_v2.redaction import redact_secret_text


def test_redacts_secret_assignments():
    text = "SERVICE_ROLE_KEY=abc123 password: hunter2 token = xyz"
    redacted = redact_secret_text(text)
    assert "abc123" not in redacted
    assert "hunter2" not in redacted
    assert "xyz" not in redacted
    assert "[REDACTED]" in redacted


def test_redacts_bearer_and_query_secret():
    text = "Authorization: Bearer abc.def https://x.test/path?token=secretvalue&ok=1"
    redacted = redact_secret_text(text)
    assert "abc.def" not in redacted
    assert "secretvalue" not in redacted
    assert "[REDACTED]" in redacted
