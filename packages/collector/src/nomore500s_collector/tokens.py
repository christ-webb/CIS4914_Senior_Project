from __future__ import annotations

from typing import Any


def normalize_token_count(usage: Any) -> tuple[int | None, str]:
    """Return (count, status) without double-counting component and total fields."""
    if not isinstance(usage, dict) or not usage:
        return None, "unavailable"

    def _int(name: str) -> int | None:
        value = usage.get(name)
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, float) and value >= 0:
            return int(value)
        return None

    def _first(*names: str) -> int | None:
        for name in names:
            value = _int(name)
            if value is not None:
                return value
        return None

    prompt = _first("prompt_tokens", "input_tokens", "prompt")
    completion = _first("completion_tokens", "output_tokens", "completion")
    if prompt is not None or completion is not None:
        return (prompt or 0) + (completion or 0), "reported"

    total = _int("total_tokens") or _int("total")
    if total is not None:
        return total, "reported"
    return None, "unavailable"
