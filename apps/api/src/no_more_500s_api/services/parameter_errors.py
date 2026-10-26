"""Detection of tool calls that keep failing because of bad parameters.

When an agent hallucinates arguments, a tool rejects the call with a
validation or schema error, the agent retries with similar bad arguments,
and the errors cascade. This heuristic finds that pattern: tool spans that
ended in an error whose recorded error text points at invalid parameters.

Like the repeated-call detector, it is a pure function over the existing
``Trace`` model, so it needs no storage and works with any repository.
"""

from collections import defaultdict
from collections.abc import Iterator
from uuid import UUID

from pydantic import BaseModel

from no_more_500s_api.domain.models import Span, SpanStatus, SpanType, Trace

DEFAULT_MIN_ERRORS = 2
MAX_SAMPLE_LENGTH = 200

# Lowercase phrases that tools commonly use when they reject bad arguments.
PARAMETER_ERROR_MARKERS = (
    "invalid argument",
    "invalid parameter",
    "invalid input",
    "validation error",
    "validation failed",
    "schema validation",
    "does not match schema",
    "missing required",
    "required parameter",
    "required field",
    "unexpected keyword",
    "unexpected argument",
    "unknown parameter",
    "unknown argument",
)


class ParameterErrorFinding(BaseModel):
    """One tool that repeatedly rejected its parameters in a trace."""

    span_name: str
    error_count: int
    span_ids: list[UUID]
    sample_error: str
    wasted_tokens: int
    wasted_cost_usd: float


def _iter_strings(value: object) -> Iterator[str]:
    """Yield every string found inside nested dicts and lists."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def _parameter_error_text(span: Span) -> str | None:
    """Return the first error text in a span that mentions invalid parameters."""
    for source in (span.output, span.attributes):
        for text in _iter_strings(source):
            lowered = text.lower()
            if any(marker in lowered for marker in PARAMETER_ERROR_MARKERS):
                return text[:MAX_SAMPLE_LENGTH]
    return None


def detect_invalid_parameter_errors(
    trace: Trace,
    min_errors: int = DEFAULT_MIN_ERRORS,
) -> list[ParameterErrorFinding]:
    """Return tools that failed on invalid parameters at least ``min_errors`` times.

    Only tool spans with an error status are considered, so a successful call
    that merely mentions the word "invalid" is never flagged. Every failed
    call is counted as wasted usage, since none of them did useful work.
    Findings are ordered with the most-failing tool first.
    """
    if min_errors < 1:
        raise ValueError("min_errors must be at least 1")

    groups: dict[str, list[tuple[Span, str]]] = defaultdict(list)
    for span in trace.spans:
        if span.type is not SpanType.TOOL or span.status is not SpanStatus.ERROR:
            continue
        error_text = _parameter_error_text(span)
        if error_text is not None:
            groups[span.name].append((span, error_text))

    findings = [
        ParameterErrorFinding(
            span_name=name,
            error_count=len(failures),
            span_ids=[span.span_id for span, _ in failures],
            sample_error=failures[0][1],
            wasted_tokens=sum(span.token_count for span, _ in failures),
            wasted_cost_usd=sum(span.cost_usd for span, _ in failures),
        )
        for name, failures in groups.items()
        if len(failures) >= min_errors
    ]
    return sorted(findings, key=lambda finding: finding.error_count, reverse=True)
