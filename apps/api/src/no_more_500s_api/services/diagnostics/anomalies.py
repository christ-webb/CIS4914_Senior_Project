from statistics import median

from no_more_500s_api.domain.diagnostics import (
    DetectionType,
    DiagnosticFinding,
    FindingConfidence,
    FindingSeverity,
)
from no_more_500s_api.domain.models import RunLifecycle, Trace
from no_more_500s_api.services.diagnostics.coverage import usage_availability
from no_more_500s_api.services.diagnostics.findings import build_finding
from no_more_500s_api.services.diagnostics.ids import TRACE_ANCHOR, occurrence_anchor

DETECTOR_ID = "anomalies"
DETECTOR_VERSION = "1"


def analyze_anomalies(
    trace: Trace,
    *,
    peers: list[Trace] | None = None,
    span_latency_ms: float = 30_000,
    token_threshold: int = 50_000,
    cost_threshold: float = 1.0,
    min_peers: int = 5,
    ratio: float = 3.0,
) -> tuple[list[DiagnosticFinding], list[str]]:
    findings: list[DiagnosticFinding] = []
    insufficient: list[str] = []
    comparable = [
        peer
        for peer in (peers or [])
        if peer.trace_id != trace.trace_id
        and peer.project_id == trace.project_id
        and peer.lifecycle in {RunLifecycle.COMPLETED, RunLifecycle.FAILED, RunLifecycle.UNKNOWN}
    ]

    for span in trace.spans:
        if span.duration_ms >= span_latency_ms:
            findings.append(
                build_finding(
                    trace=trace,
                    detector_id=DETECTOR_ID,
                    detector_version=DETECTOR_VERSION,
                    detection_type=DetectionType.LATENCY_ANOMALY,
                    severity=FindingSeverity.WARNING,
                    confidence=FindingConfidence.HEURISTIC,
                    title=f"Unusually long operation: {span.name}",
                    explanation=(
                        f"`{span.name}` ran for {span.duration_ms:.0f} ms, "
                        f"above the {span_latency_ms:.0f} ms absolute threshold."
                    ),
                    suggested_action=(
                        "Profile the call and check for blocking I/O or retries."
                    ),
                    spans=[span],
                    signature=f"absolute:{span.name}:{occurrence_anchor([span])}",
                    evidence=[
                        "basis=absolute_threshold",
                        f"duration_ms={span.duration_ms:.0f}",
                        f"threshold_ms={span_latency_ms:.0f}",
                    ],
                    evidence_data={"basis": "absolute_threshold", "duration_ms": span.duration_ms},
                )
            )
        if (
            usage_availability(trace, "token", span) == "reported"
            and span.token_count >= token_threshold
        ):
            findings.append(
                build_finding(
                    trace=trace,
                    detector_id=DETECTOR_ID,
                    detector_version=DETECTOR_VERSION,
                    detection_type=DetectionType.TOKEN_ANOMALY,
                    severity=FindingSeverity.WARNING,
                    confidence=FindingConfidence.HEURISTIC,
                    title=f"High token consumption: {span.name}",
                    explanation=(
                        f"`{span.name}` reported {span.token_count} tokens, "
                        f"above the {token_threshold} absolute threshold."
                    ),
                    suggested_action=(
                        "Review prompt size and retries. Missing tokens are not zero."
                    ),
                    spans=[span],
                    signature=f"absolute-tokens:{span.name}:{occurrence_anchor([span])}",
                    evidence=["basis=absolute_threshold", f"token_count={span.token_count}"],
                    evidence_data={"basis": "absolute_threshold", "token_count": span.token_count},
                )
            )
        if (
            usage_availability(trace, "cost", span) == "reported"
            and span.cost_usd >= cost_threshold
        ):
            findings.append(
                build_finding(
                    trace=trace,
                    detector_id=DETECTOR_ID,
                    detector_version=DETECTOR_VERSION,
                    detection_type=DetectionType.COST_ANOMALY,
                    severity=FindingSeverity.WARNING,
                    confidence=FindingConfidence.HEURISTIC,
                    title=f"High reported cost: {span.name}",
                    explanation=(
                        f"`{span.name}` reported ${span.cost_usd:.4f}, "
                        f"above the ${cost_threshold:.4f} absolute threshold."
                    ),
                    suggested_action=(
                        "Check retries and prompt size. Unavailable cost is not $0."
                    ),
                    spans=[span],
                    signature=f"absolute-cost:{span.name}:{occurrence_anchor([span])}",
                    evidence=["basis=absolute_threshold", f"cost_usd={span.cost_usd}"],
                    evidence_data={"basis": "absolute_threshold", "cost_usd": span.cost_usd},
                )
            )

    duration_peers = [peer.duration_ms for peer in comparable if peer.duration_ms > 0]
    if len(duration_peers) < min_peers:
        insufficient.append(DetectionType.LATENCY_ANOMALY.value)
    else:
        findings.extend(
            _historical_finding(
                trace,
                detection_type=DetectionType.LATENCY_ANOMALY,
                title="Run latency above recent peers",
                current=trace.duration_ms,
                samples=duration_peers,
                ratio=ratio,
                unit="ms",
                field="duration_ms",
            )
        )

    token_peers = [
        peer.total_tokens
        for peer in comparable
        if usage_availability(peer, "token") == "reported" and peer.total_tokens > 0
    ]
    if usage_availability(trace, "token") != "reported":
        insufficient.append(DetectionType.TOKEN_ANOMALY.value)
    elif len(token_peers) < min_peers:
        insufficient.append(DetectionType.TOKEN_ANOMALY.value)
    else:
        findings.extend(
            _historical_finding(
                trace,
                detection_type=DetectionType.TOKEN_ANOMALY,
                title="Token usage above recent peers",
                current=float(trace.total_tokens),
                samples=[float(value) for value in token_peers],
                ratio=ratio,
                unit="tokens",
                field="total_tokens",
            )
        )

    cost_peers = [
        peer.total_cost_usd
        for peer in comparable
        if usage_availability(peer, "cost") == "reported" and peer.total_cost_usd > 0
    ]
    if usage_availability(trace, "cost") != "reported":
        insufficient.append(DetectionType.COST_ANOMALY.value)
    elif len(cost_peers) < min_peers:
        insufficient.append(DetectionType.COST_ANOMALY.value)
    else:
        findings.extend(
            _historical_finding(
                trace,
                detection_type=DetectionType.COST_ANOMALY,
                title="Cost above recent peers",
                current=trace.total_cost_usd,
                samples=cost_peers,
                ratio=ratio,
                unit="usd",
                field="total_cost_usd",
            )
        )

    return findings, list(dict.fromkeys(insufficient))


def _historical_finding(
    trace: Trace,
    *,
    detection_type: DetectionType,
    title: str,
    current: float,
    samples: list[float],
    ratio: float,
    unit: str,
    field: str,
) -> list[DiagnosticFinding]:
    center = median(samples)
    if center <= 0 or current < center * ratio:
        return []
    return [
        build_finding(
            trace=trace,
            detector_id=DETECTOR_ID,
            detector_version=DETECTOR_VERSION,
            detection_type=detection_type,
            severity=FindingSeverity.WARNING,
            confidence=FindingConfidence.HEURISTIC,
            title=title,
            explanation=(
                f"This run's {field} is {current:.2f} {unit}, at least "
                f"{ratio:g}x the median ({center:.2f}) of {len(samples)} window peers."
            ),
            suggested_action="Compare this run with recent peers before treating it as a defect.",
            spans=[],
            signature=f"historical:{field}",
            anchor=TRACE_ANCHOR,
            evidence=[
                "basis=historical_comparison",
                f"current={current}",
                f"peer_median={center}",
                f"peer_count={len(samples)}",
                f"ratio_threshold={ratio}",
            ],
            evidence_data={
                "basis": "historical_comparison",
                "current": current,
                "peer_median": center,
                "peer_count": len(samples),
            },
        )
    ]
