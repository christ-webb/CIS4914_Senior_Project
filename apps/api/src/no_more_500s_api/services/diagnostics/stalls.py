from datetime import UTC, datetime, timedelta

from no_more_500s_api.domain.diagnostics import (
    DetectionType,
    DiagnosticFinding,
    FindingConfidence,
    FindingSeverity,
)
from no_more_500s_api.domain.models import RunLifecycle, SpanStatus, Trace
from no_more_500s_api.services.diagnostics.findings import build_finding
from no_more_500s_api.services.diagnostics.ids import TRACE_ANCHOR

DETECTOR_ID = "lifecycle"
DETECTOR_VERSION = "1"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def analyze_stalls(
    trace: Trace,
    *,
    now: datetime | None = None,
    inactivity_seconds: float = 120,
    long_running_ms: float = 30_000,
) -> list[DiagnosticFinding]:
    clock = now or datetime.now(UTC)
    findings: list[DiagnosticFinding] = []
    last_activity = _aware(trace.last_activity_at or trace.started_at)
    idle = max(0.0, (_aware(clock) - last_activity).total_seconds())

    if trace.lifecycle is RunLifecycle.INTERRUPTED:
        findings.append(
            build_finding(
                trace=trace,
                detector_id=DETECTOR_ID,
                detector_version=DETECTOR_VERSION,
                detection_type=DetectionType.INTERRUPTION,
                severity=FindingSeverity.INFO,
                confidence=FindingConfidence.CONFIRMED,
                title="Run interrupted or inactive",
                explanation=(
                    "No new telemetry arrived within the inactivity window. "
                    "Interrupted is not a confirmed agent failure."
                ),
                suggested_action=(
                    "Check whether the process is still running or lost connectivity."
                ),
                spans=[],
                signature="interrupted",
                anchor=TRACE_ANCHOR,
                evidence=[
                    f"lifecycle={trace.lifecycle.value}",
                    f"idle_seconds={idle:.0f}",
                    f"inactivity_threshold_seconds={inactivity_seconds:.0f}",
                ],
                evidence_data={"idle_seconds": idle, "lifecycle": trace.lifecycle.value},
            )
        )

    if trace.lifecycle is RunLifecycle.RUNNING and idle >= inactivity_seconds:
        findings.append(
            build_finding(
                trace=trace,
                detector_id=DETECTOR_ID,
                detector_version=DETECTOR_VERSION,
                detection_type=DetectionType.STALL,
                severity=FindingSeverity.WARNING,
                confidence=FindingConfidence.HEURISTIC,
                title="Extended inactivity on a running trace",
                explanation=(
                    "The run is still marked running, but no telemetry arrived "
                    "for longer than the inactivity window."
                ),
                suggested_action=(
                    "Check heartbeats, collector connectivity, and blocked processes."
                ),
                spans=[],
                signature="running-idle",
                anchor=TRACE_ANCHOR,
                evidence=[
                    f"lifecycle={trace.lifecycle.value}",
                    f"idle_seconds={idle:.0f}",
                ],
                evidence_data={"idle_seconds": idle},
            )
        )

    long_spans = [
        span
        for span in trace.spans
        if span.status is SpanStatus.UNSET and span.duration_ms >= long_running_ms
    ]
    open_long = [
        span
        for span in trace.spans
        if span.status is SpanStatus.UNSET
        and (_aware(clock) - _aware(span.started_at)) >= timedelta(milliseconds=long_running_ms)
        and span not in long_spans
    ]
    candidates = long_spans + open_long
    if candidates and trace.lifecycle in {RunLifecycle.RUNNING, RunLifecycle.INTERRUPTED}:
        findings.append(
            build_finding(
                trace=trace,
                detector_id=DETECTOR_ID,
                detector_version=DETECTOR_VERSION,
                detection_type=DetectionType.STALL,
                severity=FindingSeverity.INFO,
                confidence=FindingConfidence.HEURISTIC,
                title="Long-running operation without completion",
                explanation=(
                    f"{len(candidates)} span(s) are still unset after the "
                    "long-running threshold. Missing completion is not a failure."
                ),
                suggested_action="Inspect unfinished spans for missing completion events.",
                spans=candidates,
                signature="long-running",
                evidence=[
                    f"long_running_threshold_ms={long_running_ms:.0f}",
                    f"unset_span_count={len(candidates)}",
                ],
                evidence_data={"span_names": [span.name for span in candidates]},
            )
        )
    return findings
