from collections.abc import Iterator
from typing import Literal

from no_more_500s_api.domain.diagnostics import DetectionType, InstrumentationCoverage
from no_more_500s_api.domain.models import Span, SpanType, Trace

UsageKind = Literal["token", "cost"]
Availability = Literal["reported", "unavailable", "unknown"]


def _iter_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def attr_str(source: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def usage_availability(trace: Trace, kind: UsageKind, span: Span | None = None) -> Availability:
    key = "nm5.token_status" if kind == "token" else "nm5.cost_status"
    if span is not None:
        span_status = attr_str(span.attributes, key)
        if span_status in {"reported", "unavailable"}:
            return span_status  # type: ignore[return-value]
        numeric = span.token_count if kind == "token" else span.cost_usd
        if numeric > 0:
            return "reported"
    trace_status = attr_str(trace.attributes, key)
    if trace_status in {"reported", "unavailable"}:
        return trace_status  # type: ignore[return-value]
    if span is None:
        numeric = trace.total_tokens if kind == "token" else trace.total_cost_usd
        if numeric > 0:
            return "reported"
    return "unknown"


def reported_numeric(trace: Trace, kind: UsageKind, span: Span) -> float | None:
    if usage_availability(trace, kind, span) != "reported":
        return None
    return float(span.token_count if kind == "token" else span.cost_usd)


def extract_error_text(span: Span) -> str | None:
    for source in (span.output, span.attributes):
        if not source:
            continue
        for key in ("error", "message", "exception.message", "error_type"):
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, str) and value.strip():
                return value.strip()
        for text in _iter_strings(source):
            if text.strip():
                return text.strip()
    return None


def has_llm_spans(trace: Trace) -> bool:
    return any(span.type is SpanType.LLM for span in trace.spans)


def is_codex_style(trace: Trace) -> bool:
    source = attr_str(trace.attributes, "nm5.source")
    framework = attr_str(trace.attributes, "agent.framework", "nm5.agent")
    return source in {"cli", "codex-cli", "codex-exec", "codex-ide"} or framework == "codex"


def is_hooks_without_llm(trace: Trace) -> bool:
    source = attr_str(trace.attributes, "nm5.source")
    instrumentation = attr_str(trace.attributes, "nm5.instrumentation") or ""
    if has_llm_spans(trace):
        return False
    if source in {"cli", "cursor", "vscode", "codex", "codex-cli", "codex-exec", "codex-ide"}:
        return "otel" not in instrumentation
    return False


def coverage_for_trace(
    trace: Trace,
    *,
    insufficient: list[str] | None = None,
) -> InstrumentationCoverage:
    token_status = usage_availability(trace, "token")
    cost_status = usage_availability(trace, "cost")
    unsupported: list[str] = []
    if token_status == "unavailable":
        unsupported.append(DetectionType.TOKEN_ANOMALY.value)
    if cost_status == "unavailable":
        unsupported.append(DetectionType.COST_ANOMALY.value)
    if is_hooks_without_llm(trace) or (not has_llm_spans(trace) and is_codex_style(trace)):
        unsupported.append(DetectionType.PROVIDER_ERROR.value)
    unique_unsupported = list(dict.fromkeys(unsupported))
    return InstrumentationCoverage(
        token_status=token_status,
        cost_status=cost_status,
        instrumentation_coverage=attr_str(trace.attributes, "nm5.coverage"),
        unsupported_detectors=unique_unsupported,
        insufficient_data_detectors=list(dict.fromkeys(insufficient or [])),
    )
