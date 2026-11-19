from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ParsedUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    uncached_input_tokens: int | None = None
    total_tokens: int | None = None
    status: str = "unavailable"
    accounting: str = "raw"
    total_completeness: str = "unavailable"
    billable_breakdown: str = "unavailable"


def _nonneg_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value >= 0:
        return int(value)
    return None


def _first(usage: dict[str, Any], *names: str) -> int | None:
    for name in names:
        value = _nonneg_int(usage.get(name))
        if value is not None:
            return value
    return None


def parse_token_usage(usage: Any) -> ParsedUsage:
    """Preserve source token categories without inventing missing values.

    Cache is not subtracted from input unless input is greater than or equal to
    the reported cache-read and cache-write sum for that payload. Inconsistent
    cache/input relationships keep the raw fields and mark accounting instead
    of clamping totals to zero.
    """
    if not isinstance(usage, dict) or not usage:
        return ParsedUsage()

    input_tokens = _first(usage, "input_tokens", "prompt_tokens", "prompt")
    output_tokens = _first(usage, "output_tokens", "completion_tokens", "completion")
    cache_read = _first(usage, "cache_read_tokens", "cacheReadTokens")
    cache_write = _first(usage, "cache_write_tokens", "cacheWriteTokens")
    if all(value is None for value in (input_tokens, output_tokens, cache_read, cache_write)):
        total = _first(usage, "total_tokens", "total")
        if total is None:
            return ParsedUsage()
        return ParsedUsage(
            total_tokens=total,
            status="reported",
            accounting="raw",
            total_completeness="complete",
            billable_breakdown="unavailable",
        )

    accounting = "raw"
    uncached: int | None = None
    cache_present = cache_read is not None or cache_write is not None
    if input_tokens is not None and cache_present:
        cache_total = (cache_read or 0) + (cache_write or 0)
        if input_tokens >= cache_total:
            accounting = "inclusive_cache"
            uncached = input_tokens - cache_total
        else:
            accounting = "inconsistent"

    if accounting == "inclusive_cache" and input_tokens is not None:
        total = input_tokens + (output_tokens or 0)
    elif accounting == "inconsistent":
        parts = [value for value in (input_tokens, output_tokens) if value is not None]
        total = sum(parts) if parts else None
    else:
        parts = [
            value
            for value in (input_tokens, output_tokens, cache_read, cache_write)
            if value is not None
        ]
        total = sum(parts) if parts else None

    total_completeness = "complete" if total is not None else "unavailable"
    if accounting == "inconsistent":
        billable = "unavailable"
    elif input_tokens is not None and output_tokens is not None:
        billable = "complete"
    elif input_tokens is not None or output_tokens is not None:
        billable = "partial"
    else:
        billable = "unavailable"

    return ParsedUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        uncached_input_tokens=uncached,
        total_tokens=total,
        status="reported",
        accounting=accounting,
        total_completeness=total_completeness,
        billable_breakdown=billable,
    )


def verified_model_variant(*values: str | None) -> str | None:
    """Return Fast/Standard only when a payload names the variant explicitly."""
    for value in values:
        if not value:
            continue
        lowered = value.strip().lower()
        if not lowered:
            continue
        compact = lowered.replace("_", "-").replace(" ", "-")
        if compact.endswith("-fast") or compact.endswith("(fast)") or "(fast)" in lowered:
            return "fast"
        if " fast" in f" {lowered} " or lowered.endswith(" fast"):
            return "fast"
        if (
            compact.endswith("-standard")
            or "(standard)" in lowered
            or " standard" in f" {lowered} "
        ):
            return "standard"
    return None


def usage_attribute_map(
    parsed: ParsedUsage,
    *,
    source: str,
    event: str,
    generation_id: str,
    measured_at: str,
    conversation_id: str | None = None,
    model_id: str | None = None,
    model_variant: str | None = None,
) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "nm5.usage_source": source,
        "nm5.usage_event": event,
        "nm5.usage_scope": "parent_agent",
        "nm5.usage_measured_at": measured_at,
        "nm5.generation_id": generation_id,
        "nm5.token_accounting": parsed.accounting,
        "nm5.token_status": parsed.status,
        "nm5.token_total_completeness": parsed.total_completeness,
        "nm5.billable_breakdown": parsed.billable_breakdown,
        "nm5.cost_status": "unavailable",
    }
    if conversation_id:
        attributes["nm5.conversation_id"] = conversation_id
    if model_id:
        attributes["nm5.model_id"] = model_id
    if model_variant:
        attributes["nm5.model_variant"] = model_variant
    if parsed.input_tokens is not None:
        attributes["gen_ai.usage.input_tokens"] = parsed.input_tokens
    if parsed.output_tokens is not None:
        attributes["gen_ai.usage.output_tokens"] = parsed.output_tokens
    if parsed.cache_read_tokens is not None:
        attributes["gen_ai.usage.cache_read_tokens"] = parsed.cache_read_tokens
    if parsed.cache_write_tokens is not None:
        attributes["gen_ai.usage.cache_write_tokens"] = parsed.cache_write_tokens
    if parsed.uncached_input_tokens is not None:
        attributes["gen_ai.usage.uncached_input_tokens"] = parsed.uncached_input_tokens
    return attributes


def normalize_token_count(usage: Any) -> tuple[int | None, str]:
    """Return (count, status) without double-counting component and total fields."""
    if not isinstance(usage, dict) or not usage:
        return None, "unavailable"

    def _int(name: str) -> int | None:
        return _nonneg_int(usage.get(name))

    def first(*names: str) -> int | None:
        for name in names:
            value = _int(name)
            if value is not None:
                return value
        return None

    prompt = first("prompt_tokens", "input_tokens", "prompt")
    completion = first("completion_tokens", "output_tokens", "completion")
    if prompt is not None or completion is not None:
        return (prompt or 0) + (completion or 0), "reported"
    total = _int("total_tokens") or _int("total")
    if total is not None:
        return total, "reported"
    return None, "unavailable"
