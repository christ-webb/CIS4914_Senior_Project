from collections import defaultdict

from no_more_500s_api.domain.diagnostics import (
    DetectionType,
    DiagnosticFinding,
    FindingConfidence,
    FindingSeverity,
)
from no_more_500s_api.domain.models import Span, SpanStatus, SpanType, Trace
from no_more_500s_api.services.diagnostics.coverage import reported_numeric
from no_more_500s_api.services.diagnostics.findings import build_finding
from no_more_500s_api.services.diagnostics.ids import normalize_payload
from no_more_500s_api.services.failure_detection import DEFAULT_REPEAT_THRESHOLD

DETECTOR_ID = "repeated_tool_call"
DETECTOR_VERSION = "1"


def _usage_sum(trace: Trace, spans: list[Span], kind: str) -> float | None:
    total = 0.0
    any_reported = False
    for span in spans:
        value = reported_numeric(trace, "token" if kind == "token" else "cost", span)
        if value is None:
            continue
        any_reported = True
        total += value
    return total if any_reported else None


def analyze_repeated_tool_calls(
    trace: Trace,
    *,
    threshold: int = DEFAULT_REPEAT_THRESHOLD,
) -> list[DiagnosticFinding]:
    if threshold < 2:
        raise ValueError("threshold must be at least 2")
    groups: dict[tuple[str, str], list[Span]] = defaultdict(list)
    for span in trace.spans:
        if span.type is not SpanType.TOOL:
            continue
        groups[(span.name, normalize_payload(span.input))].append(span)

    findings: list[DiagnosticFinding] = []
    for (name, payload), spans in groups.items():
        if len(spans) < threshold:
            continue
        failed = [span for span in spans if span.status is SpanStatus.ERROR]
        extra_spans = spans[1:]
        wasted_tokens = _usage_sum(trace, extra_spans, "token")
        wasted_cost = _usage_sum(trace, extra_spans, "cost")
        elapsed_ms = sum(span.duration_ms for span in spans)
        all_ok = len(failed) == 0
        severity = FindingSeverity.INFO if all_ok else FindingSeverity.WARNING
        confidence = FindingConfidence.HEURISTIC if all_ok else FindingConfidence.CONFIRMED
        title = (
            f"Repeated successful tool calls: {name}"
            if all_ok
            else f"Repeated tool invocations: {name}"
        )
        explanation = (
            f"`{name}` was invoked {len(spans)} times with equivalent normalized arguments."
        )
        if all_ok:
            explanation += " All recorded statuses were ok; this may be legitimate repetition."
        else:
            explanation += f" {len(failed)} of those invocations recorded status=error."
        evidence = [
            f"{len(spans)} related tool spans",
            "Identical normalized arguments",
            f"{len(failed)} explicitly reported failures",
            f"Observed elapsed time across the group: {elapsed_ms:.0f} ms",
        ]
        if wasted_tokens is not None:
            evidence.append(
                f"Reported tokens on repeats after the first call: {int(wasted_tokens)}"
            )
        if wasted_cost is not None:
            evidence.append(f"Reported cost on repeats after the first call: {wasted_cost:.4f} USD")
        action = (
            "Confirm whether repeated arguments are expected; constrain retries if not."
            if all_ok
            else "Inspect the failed attempts before retrying the same arguments."
        )
        findings.append(
            build_finding(
                trace=trace,
                detector_id=DETECTOR_ID,
                detector_version=DETECTOR_VERSION,
                detection_type=DetectionType.REPEATED_TOOL_CALL,
                severity=severity,
                confidence=confidence,
                title=title,
                explanation=explanation,
                suggested_action=action,
                spans=spans,
                signature=f"{name}:{payload}",
                evidence=evidence,
                evidence_data={
                    "tool_name": name,
                    "repeat_count": len(spans),
                    "failed_count": len(failed),
                    "elapsed_ms": elapsed_ms,
                    "wasted_tokens": wasted_tokens,
                    "wasted_cost_usd": wasted_cost,
                },
            )
        )
    return sorted(
        findings, key=lambda finding: finding.evidence_data.get("repeat_count", 0), reverse=True
    )
