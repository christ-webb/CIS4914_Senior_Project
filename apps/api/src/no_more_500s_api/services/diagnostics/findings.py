from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from no_more_500s_api.domain.diagnostics import (
    DetectionType,
    DiagnosticFinding,
    FindingConfidence,
    FindingSeverity,
)
from no_more_500s_api.domain.models import RunLifecycle, Span, Trace
from no_more_500s_api.services.diagnostics.ids import finding_id, occurrence_anchor, span_sort_key


def is_provisional(trace: Trace) -> bool:
    return trace.lifecycle in {RunLifecycle.RUNNING, RunLifecycle.INTERRUPTED}


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def span_window(spans: list[Span]) -> tuple[datetime | None, datetime | None]:
    if not spans:
        return None, None
    starts = [_aware(span.started_at) for span in spans]
    return min(starts), max(starts)


def build_finding(
    *,
    trace: Trace,
    detector_id: str,
    detector_version: str,
    detection_type: DetectionType,
    severity: FindingSeverity,
    confidence: FindingConfidence,
    title: str,
    explanation: str,
    suggested_action: str,
    spans: list[Span] | None = None,
    signature: str,
    evidence: list[str] | None = None,
    evidence_data: dict[str, Any] | None = None,
    anchor: str | None = None,
) -> DiagnosticFinding:
    related = spans or []
    first_at, last_at = span_window(related)
    return DiagnosticFinding(
        finding_id=finding_id(
            detector_id=detector_id,
            detector_version=detector_version,
            trace_id=trace.trace_id,
            anchor=anchor or occurrence_anchor(related),
            signature=signature,
        ),
        trace_id=trace.trace_id,
        span_ids=[span.span_id for span in related],
        detector_id=detector_id,
        detection_type=detection_type,
        severity=severity,
        confidence=confidence,
        title=title,
        explanation=explanation,
        evidence=evidence or [],
        evidence_data=evidence_data or {},
        suggested_action=suggested_action,
        first_observed_at=first_at or trace.started_at,
        last_observed_at=last_at or trace.last_activity_at or trace.started_at,
        detector_version=detector_version,
        provisional=is_provisional(trace),
    )


def sorted_span_ids(spans: list[Span]) -> list[UUID]:
    return [span.span_id for span in sorted(spans, key=span_sort_key)]
