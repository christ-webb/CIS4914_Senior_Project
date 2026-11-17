from uuid import UUID

from no_more_500s_api.domain.diagnostics import (
    DetectionType,
    DiagnosticFinding,
    FindingConfidence,
    FindingSeverity,
)
from no_more_500s_api.domain.models import Trace
from no_more_500s_api.services.diagnostics.coverage import reported_numeric
from no_more_500s_api.services.diagnostics.findings import build_finding
from no_more_500s_api.services.diagnostics.text import structured_error
from no_more_500s_api.services.parameter_errors import (
    DEFAULT_MIN_ERRORS,
    detect_invalid_parameter_errors,
)

DETECTOR_ID = "schema_mismatch"
DETECTOR_VERSION = "1"

STRUCTURED_SCHEMA_TYPES = {
    "ValidationError",
    "schema_mismatch",
    "invalid_argument",
    "TypeError",
}


def _confidence_for_spans(trace: Trace, span_ids: list[UUID]) -> FindingConfidence:
    by_id = {span.span_id: span for span in trace.spans}
    for span_id in span_ids:
        span = by_id.get(span_id)
        if span is None:
            continue
        structured = structured_error(span)
        error_type = str(
            structured.get("exception.type")
            or structured.get("error.type")
            or structured.get("error_type")
            or ""
        )
        if error_type in STRUCTURED_SCHEMA_TYPES or "validation" in error_type.lower():
            return FindingConfidence.CONFIRMED
        schema = (span.attributes or {}).get("tool.arguments_schema")
        if schema:
            return FindingConfidence.CONFIRMED
    return FindingConfidence.HEURISTIC


def analyze_schema_errors(
    trace: Trace,
    *,
    min_errors: int = DEFAULT_MIN_ERRORS,
) -> list[DiagnosticFinding]:
    raw = detect_invalid_parameter_errors(trace, min_errors=min_errors)
    by_id = {span.span_id: span for span in trace.spans}
    findings: list[DiagnosticFinding] = []
    for item in raw:
        spans = [by_id[span_id] for span_id in item.span_ids if span_id in by_id]
        tokens = None
        cost = None
        token_parts = [reported_numeric(trace, "token", span) for span in spans]
        cost_parts = [reported_numeric(trace, "cost", span) for span in spans]
        if any(part is not None for part in token_parts):
            tokens = sum(part or 0 for part in token_parts)
        if any(part is not None for part in cost_parts):
            cost = sum(part or 0 for part in cost_parts)
        confidence = _confidence_for_spans(trace, item.span_ids)
        findings.append(
            build_finding(
                trace=trace,
                detector_id=DETECTOR_ID,
                detector_version=DETECTOR_VERSION,
                detection_type=DetectionType.SCHEMA_MISMATCH,
                severity=FindingSeverity.WARNING,
                confidence=confidence,
                title=f"Tool argument or schema errors: {item.span_name}",
                explanation=(
                    f"`{item.span_name}` recorded {item.error_count} error span(s) whose telemetry "
                    "points at invalid parameters or schema validation."
                ),
                suggested_action=(
                    "Compare the recorded arguments with the tool's actual schema. "
                    "Do not assume a schema that was not present in telemetry."
                ),
                spans=spans,
                signature=item.span_name,
                evidence=[
                    f"{item.error_count} error tool spans",
                    f"Sample error: {item.sample_error}",
                    (
                        "Structured validation metadata was present"
                        if confidence is FindingConfidence.CONFIRMED
                        else "Classification used textual error markers (heuristic)"
                    ),
                ],
                evidence_data={
                    "tool_name": item.span_name,
                    "error_count": item.error_count,
                    "sample_error": item.sample_error,
                    "wasted_tokens": tokens,
                    "wasted_cost_usd": cost,
                },
            )
        )
    return findings
