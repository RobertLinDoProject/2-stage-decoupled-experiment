from __future__ import annotations

from uuid import uuid4


def new_id(prefix: str) -> str:
    normalized = prefix.strip().upper().replace("_", "-")
    return f"{normalized}-{uuid4().hex[:16]}"
