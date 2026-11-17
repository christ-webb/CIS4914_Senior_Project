import re
from collections.abc import Iterator
from typing import Any

from no_more_500s_api.domain.models import Span

STRUCTURED_ERROR_KEYS = (
    "exception.type",
    "error.type",
    "error_type",
    "exception.message",
    "gen_ai.response.status",
    "http.status_code",
    "rpc.grpc.status_code",
)

PROVIDER_MARKERS = (
    "rate limit",
    "resource exhausted",
    "429",
    "503",
    "504",
    "timeout",
    "timed out",
    "service unavailable",
    "internal server error",
    "provider error",
)

TIMEOUT_MARKERS = ("timeout", "timed out", "deadline exceeded")


def iter_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)


def nested_get(source: dict[str, Any] | None, key: str) -> object:
    if not source:
        return None
    if key in source:
        return source[key]
    return None


def structured_error(span: Span) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for source in (span.output, span.attributes):
        if not isinstance(source, dict):
            continue
        for key in STRUCTURED_ERROR_KEYS:
            value = nested_get(source, key)
            if value not in (None, ""):
                data[key] = value
        exit_code = source.get("exit_code") or source.get("process.exit_code")
        if exit_code not in (None, "", 0):
            data["exit_code"] = exit_code
        if source.get("timed_out") is True:
            data["timed_out"] = True
    return data


def error_signature(text: str) -> str:
    lowered = text.lower().strip()
    return re.sub(r"\d+", "#", lowered)[:180]


def contains_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def collected_text(span: Span) -> str:
    parts: list[str] = []
    for source in (span.output, span.attributes):
        parts.extend(iter_strings(source))
    return " ".join(parts)
