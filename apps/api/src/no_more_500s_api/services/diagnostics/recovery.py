from collections import defaultdict

from no_more_500s_api.domain.diagnostics import (
    DetectionType,
    DiagnosticFinding,
    FindingConfidence,
    FindingSeverity,
)
from no_more_500s_api.domain.models import Span, SpanStatus, SpanType, Trace
from no_more_500s_api.services.diagnostics.findings import build_finding
from no_more_500s_api.services.diagnostics.ids import normalize_payload, span_sort_key

DETECTOR_ID = "ineffective_recovery"
DETECTOR_VERSION = "1"


def analyze_ineffective_recovery(
    trace: Trace,
    *,
    threshold: int = 3,
) -> list[DiagnosticFinding]:
    if threshold < 2:
        raise ValueError("threshold must be at least 2")
    failed_groups: dict[tuple[str, str], list[Span]] = defaultdict(list)
    tool_spans = [
        span
        for span in sorted(trace.spans, key=span_sort_key)
        if span.type is SpanType.TOOL
    ]
    for span in tool_spans:
        if span.status is SpanStatus.ERROR:
            failed_groups[(span.name, normalize_payload(span.input))].append(span)

    findings: list[DiagnosticFinding] = []
    for (name, payload), spans in failed_groups.items():
        if len(spans) < threshold:
            continue
        unique_inputs = {normalize_payload(span.input) for span in spans}
        if len(unique_inputs) != 1:
            continue
        elapsed_ms = sum(span.duration_ms for span in spans)
        severity = (
            FindingSeverity.CRITICAL if len(spans) >= threshold + 1 else FindingSeverity.WARNING
        )
        findings.append(
            build_finding(
                trace=trace,
                detector_id=DETECTOR_ID,
                detector_version=DETECTOR_VERSION,
                detection_type=DetectionType.INEFFECTIVE_RECOVERY,
                severity=severity,
                confidence=FindingConfidence.CONFIRMED,
                title=f"Ineffective recovery loop: {name}",
                explanation=(
                    f"`{name}` failed {len(spans)} times with equivalent arguments "
                    "and no observable change in inputs."
                ),
                suggested_action=(
                    "Stop identical retries until the error is fixed, or fail fast."
                ),
                spans=spans,
                signature=f"{name}:{payload}",
                evidence=[
                    f"{len(spans)} failed attempts with equivalent arguments",
                    f"Observed elapsed time: {elapsed_ms:.0f} ms",
                ],
                evidence_data={
                    "tool_name": name,
                    "failed_attempts": len(spans),
                    "elapsed_ms": elapsed_ms,
                },
            )
        )

    cycle = _failing_cycle(trace, tool_spans)
    if cycle is not None:
        findings.append(cycle)
    return findings


def _failing_cycle(trace: Trace, tool_spans: list[Span]) -> DiagnosticFinding | None:
    failed = [span for span in tool_spans if span.status is SpanStatus.ERROR]
    if len(failed) < 4:
        return None
    names = [span.name for span in failed]
    unique = list(dict.fromkeys(names))
    if len(unique) != 2:
        return None
    first, second = unique
    expected = [first if index % 2 == 0 else second for index in range(len(names))]
    if names != expected:
        return None
    return build_finding(
        trace=trace,
        detector_id=DETECTOR_ID,
        detector_version=DETECTOR_VERSION,
        detection_type=DetectionType.INEFFECTIVE_RECOVERY,
        severity=FindingSeverity.WARNING,
        confidence=FindingConfidence.CONFIRMED,
        title=f"Failing tool cycle: {first} / {second}",
        explanation=(
            f"Telemetry shows a repeating cycle of failed `{first}` and `{second}` operations "
            f"across {len(failed)} error spans."
        ),
        suggested_action=(
            "Break the cycle by changing inputs or fixing the first failing tool."
        ),
        spans=failed,
        signature=f"cycle:{first}:{second}",
        evidence=[f"{len(failed)} failed spans alternating between {first} and {second}"],
        evidence_data={"tools": [first, second], "failed_attempts": len(failed)},
    )
