from __future__ import annotations

import hashlib


def stable_trace_id(*parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]
