from __future__ import annotations

import re

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|service[_-]?role[_-]?key|service[_-]?role|secret|token|password|passwd|pwd|private[_-]?key|client[_-]?secret|authorization)"
    r"(\s*[:=]\s*)"
    r"([^\s#,'\"]+)"
)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9*._~+/=-]+")
_QUERY_SECRET = re.compile(r"(?i)([?&](?:key|token|secret|password|apikey|api_key)=)[^&\s]+")


def redact_secret_text(text: object) -> str:
    s = str(text)
    s = _BEARER.sub("Bearer [REDACTED]", s)
    s = _QUERY_SECRET.sub(r"\1[REDACTED]", s)
    s = _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", s)
    return s
