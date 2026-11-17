from collections import defaultdict

from no_more_500s_api.domain.diagnostics import (
    DetectionType,
    DiagnosticFinding,
    FindingConfidence,
    FindingSeverity,
)
from no_more_500s_api.domain.models import Span, SpanStatus, SpanType, Trace
from no_more_500s_api.services.diagnostics.coverage import extract_error_text
from no_more_500s_api.services.diagnostics.findings import build_finding
from no_more_500s_api.services.diagnostics.ids import normalize_payload
from no_more_500s_api.services.diagnostics.text import (
    TIMEOUT_MARKERS,
    collected_text,
    contains_marker,
    error_signature,
    structured_error,
)

DETECTOR_ID = "tool_failure"
DETECTOR_VERSION = "1"


def _is_timeout(span: Span, structured: dict[str, object], text: str) -> bool:
    if structured.get("timed_out") is True:
        return True
    return contains_marker(text, TIMEOUT_MARKERS)


def analyze_tool_failures(trace: Trace) -> list[DiagnosticFinding]:
    groups: dict[tuple[str, str], list[Span]] = defaultdict(list)
    details: dict[tuple[str, str], dict[str, object]] = {}
    for span in trace.spans:
        if span.type is not SpanType.TOOL or span.status is not SpanStatus.ERROR:
            continue
        structured = structured_error(span)
        text = (
            extract_error_text(span) or collected_text(span) or f"{span.name} reported status=error"
        )
        signature = error_signature(text)
        key = (span.name, signature)
        groups[key].append(span)
        details[key] = {
            "sample_error": text[:300],
            "structured": structured,
            "timeout": _is_timeout(span, structured, text),
            "exit_code": structured.get("exit_code"),
        }

    findings: list[DiagnosticFinding] = []
    for (name, signature), spans in groups.items():
        meta = details[(name, signature)]
        timeout = bool(meta["timeout"])
        exit_code = meta["exit_code"]
        evidence = [
            f"{len(spans)} {name} tool span(s) recorded status=error",
            f"Sample error: {meta['sample_error']}",
        ]
        if timeout:
            evidence.append("Timeout markers or timed_out=true were present on the failed span(s)")
        if exit_code not in (None, "", 0):
            evidence.append(f"Nonzero exit code {exit_code} was recorded")
        findings.append(
            build_finding(
                trace=trace,
                detector_id=DETECTOR_ID,
                detector_version=DETECTOR_VERSION,
                detection_type=DetectionType.TOOL_FAILURE,
                severity=FindingSeverity.CRITICAL,
                confidence=FindingConfidence.CONFIRMED,
                title=f"Tool execution failure: {name}",
                explanation=(
                    f"The agent invoked `{name}` {len(spans)} time(s) and telemetry recorded "
                    "an error status based on span status, not a successful payload."
                ),
                suggested_action=(
                    "Inspect the failed tool spans, confirm the arguments and runtime environment, "
                    "and retry only after the recorded error is addressed."
                ),
                spans=spans,
                signature=f"{name}:{signature}",
                evidence=evidence,
                evidence_data={
                    "tool_name": name,
                    "error_count": len(spans),
                    "sample_error": meta["sample_error"],
                    "structured": meta["structured"],
                    "normalized_inputs": [normalize_payload(span.input) for span in spans[:5]],
                },
            )
        )
    return findings
