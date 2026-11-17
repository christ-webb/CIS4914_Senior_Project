from no_more_500s_api.domain.diagnostics import (
    DetectionType,
    DiagnosticFinding,
    FindingConfidence,
    FindingSeverity,
)
from no_more_500s_api.domain.models import SpanStatus, SpanType, Trace
from no_more_500s_api.services.diagnostics.coverage import (
    extract_error_text,
    has_llm_spans,
    is_codex_style,
)
from no_more_500s_api.services.diagnostics.findings import build_finding
from no_more_500s_api.services.diagnostics.text import (
    PROVIDER_MARKERS,
    collected_text,
    contains_marker,
    error_signature,
    structured_error,
)

DETECTOR_ID = "provider_error"
DETECTOR_VERSION = "1"


def analyze_provider_errors(trace: Trace) -> list[DiagnosticFinding]:
    if not has_llm_spans(trace):
        return []
    findings: list[DiagnosticFinding] = []
    for span in trace.spans:
        if span.type is not SpanType.LLM or span.status is not SpanStatus.ERROR:
            continue
        structured = structured_error(span)
        text = (
            extract_error_text(span) or collected_text(span) or f"{span.name} reported status=error"
        )
        has_structured = bool(structured)
        looks_like_provider = has_structured or contains_marker(text, PROVIDER_MARKERS)
        if not looks_like_provider and not has_structured:
            # Explicit LLM error status is still a confirmed execution error.
            looks_like_provider = True
        confidence = (
            FindingConfidence.CONFIRMED
            if has_structured or span.status is SpanStatus.ERROR
            else FindingConfidence.HEURISTIC
        )
        findings.append(
            build_finding(
                trace=trace,
                detector_id=DETECTOR_ID,
                detector_version=DETECTOR_VERSION,
                detection_type=DetectionType.PROVIDER_ERROR,
                severity=FindingSeverity.CRITICAL,
                confidence=confidence,
                title=f"Model or provider error: {span.name}",
                explanation=(
                    f"LLM span `{span.name}` recorded status=error. "
                    "The finding uses provider/status telemetry rather than inferred model intent."
                ),
                suggested_action=(
                    "Inspect provider status and retry policy. Codex without "
                    "model hooks will not emit these events."
                ),
                spans=[span],
                signature=f"{span.name}:{error_signature(text)}",
                evidence=[
                    "LLM span status=error",
                    f"Sample: {text[:300]}",
                    f"Structured metadata: {structured or 'none'}",
                ],
                evidence_data={"structured": structured, "sample_error": text[:300]},
            )
        )
    if not findings and is_codex_style(trace) and not has_llm_spans(trace):
        return []
    return findings
