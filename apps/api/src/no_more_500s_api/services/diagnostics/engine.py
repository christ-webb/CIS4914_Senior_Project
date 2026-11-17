from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from no_more_500s_api.core.config import Settings, get_settings
from no_more_500s_api.domain.diagnostics import (
    AnalysisStatus,
    DetectionType,
    DiagnosticFinding,
    DiagnosticsAnalysisScope,
    DiagnosticSummary,
    FindingConfidence,
    FindingSeverity,
    TraceDiagnostics,
)
from no_more_500s_api.domain.models import RunLifecycle, Trace
from no_more_500s_api.services.diagnostics.anomalies import analyze_anomalies
from no_more_500s_api.services.diagnostics.coverage import coverage_for_trace
from no_more_500s_api.services.diagnostics.findings import is_provisional
from no_more_500s_api.services.diagnostics.provider import analyze_provider_errors
from no_more_500s_api.services.diagnostics.recovery import analyze_ineffective_recovery
from no_more_500s_api.services.diagnostics.repeated import analyze_repeated_tool_calls
from no_more_500s_api.services.diagnostics.schema_errors import analyze_schema_errors
from no_more_500s_api.services.diagnostics.stalls import analyze_stalls
from no_more_500s_api.services.diagnostics.tool_failures import analyze_tool_failures

SCOPE_NOTE = (
    "Counts cover only the most recent listed runs in this analysis window, "
    "not all historical data."
)


@dataclass
class DetectionContext:
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    inactivity_seconds: float = 120
    repeat_threshold: int = 3
    recovery_threshold: int = 3
    parameter_error_min: int = 2
    span_latency_ms: float = 30_000
    token_anomaly_threshold: int = 50_000
    cost_anomaly_threshold: float = 1.0
    historical_min_peers: int = 5
    historical_anomaly_ratio: float = 3.0
    peers: Sequence[Trace] = field(default_factory=tuple)


DetectorFn = Callable[[Trace, DetectionContext], list[DiagnosticFinding]]


def context_from_settings(
    settings: Settings | None = None,
    *,
    peers: Sequence[Trace] | None = None,
    now: datetime | None = None,
) -> DetectionContext:
    config = settings or get_settings()
    return DetectionContext(
        now=now or datetime.now(UTC),
        inactivity_seconds=config.run_inactivity_seconds,
        repeat_threshold=config.repeat_threshold,
        recovery_threshold=config.recovery_threshold,
        parameter_error_min=config.parameter_error_min,
        span_latency_ms=config.span_latency_ms,
        token_anomaly_threshold=config.token_anomaly_threshold,
        cost_anomaly_threshold=config.cost_anomaly_threshold,
        historical_min_peers=config.historical_min_peers,
        historical_anomaly_ratio=config.historical_anomaly_ratio,
        peers=tuple(peers or ()),
    )


def _tool_failures(trace: Trace, _ctx: DetectionContext) -> list[DiagnosticFinding]:
    return analyze_tool_failures(trace)


def _repeated(trace: Trace, ctx: DetectionContext) -> list[DiagnosticFinding]:
    return analyze_repeated_tool_calls(trace, threshold=ctx.repeat_threshold)


def _schema(trace: Trace, ctx: DetectionContext) -> list[DiagnosticFinding]:
    return analyze_schema_errors(trace, min_errors=ctx.parameter_error_min)


def _recovery(trace: Trace, ctx: DetectionContext) -> list[DiagnosticFinding]:
    return analyze_ineffective_recovery(trace, threshold=ctx.recovery_threshold)


def _stalls(trace: Trace, ctx: DetectionContext) -> list[DiagnosticFinding]:
    return analyze_stalls(
        trace,
        now=ctx.now,
        inactivity_seconds=ctx.inactivity_seconds,
        long_running_ms=ctx.span_latency_ms,
    )


def _provider(trace: Trace, _ctx: DetectionContext) -> list[DiagnosticFinding]:
    return analyze_provider_errors(trace)


DETECTORS: list[tuple[str, DetectorFn]] = [
    ("tool_failure", _tool_failures),
    ("repeated_tool_call", _repeated),
    ("schema_mismatch", _schema),
    ("ineffective_recovery", _recovery),
    ("lifecycle", _stalls),
    ("provider_error", _provider),
]


def analyze_trace(trace: Trace, ctx: DetectionContext | None = None) -> TraceDiagnostics:
    original = id(trace.spans)
    context = ctx or context_from_settings()
    findings: list[DiagnosticFinding] = []
    for _name, detector in DETECTORS:
        findings.extend(detector(trace, context))
    anomaly_findings, insufficient = analyze_anomalies(
        trace,
        peers=list(context.peers),
        span_latency_ms=context.span_latency_ms,
        token_threshold=context.token_anomaly_threshold,
        cost_threshold=context.cost_anomaly_threshold,
        min_peers=context.historical_min_peers,
        ratio=context.historical_anomaly_ratio,
    )
    findings.extend(anomaly_findings)
    if id(trace.spans) != original:
        raise RuntimeError("detectors must not mutate the source trace")
    coverage = coverage_for_trace(trace, insufficient=insufficient)
    skipped = set(coverage.unsupported_detectors)
    findings = [finding for finding in findings if finding.detection_type.value not in skipped]
    unique: dict[str, DiagnosticFinding] = {}
    for finding in findings:
        unique[str(finding.finding_id)] = finding
    ordered = sorted(
        unique.values(),
        key=lambda item: (
            item.severity is not FindingSeverity.CRITICAL,
            item.severity is not FindingSeverity.WARNING,
            item.title,
        ),
    )
    return TraceDiagnostics(
        trace_id=trace.trace_id,
        analysis_status=(
            AnalysisStatus.PROVISIONAL if is_provisional(trace) else AnalysisStatus.FINALIZED
        ),
        coverage=coverage,
        findings=ordered,
    )


def filter_findings(
    diagnostics: TraceDiagnostics,
    *,
    severity: FindingSeverity | None = None,
    detection_type: DetectionType | None = None,
    confidence: FindingConfidence | None = None,
    span_id: str | None = None,
) -> TraceDiagnostics:
    findings = diagnostics.findings
    if severity is not None:
        findings = [item for item in findings if item.severity is severity]
    if detection_type is not None:
        findings = [item for item in findings if item.detection_type is detection_type]
    if confidence is not None:
        findings = [item for item in findings if item.confidence is confidence]
    if span_id is not None:
        findings = [item for item in findings if span_id in {str(value) for value in item.span_ids}]
    return diagnostics.model_copy(update={"findings": findings})


def summarize_traces(
    traces: Sequence[Trace],
    *,
    ctx: DetectionContext,
    limit: int,
    offset: int = 0,
    project_id: str | None = None,
) -> DiagnosticSummary:
    all_findings: list[DiagnosticFinding] = []
    coverage_notes: list[str] = []
    affected: set[str] = set()
    for trace in traces:
        peer_ctx = DetectionContext(
            now=ctx.now,
            inactivity_seconds=ctx.inactivity_seconds,
            repeat_threshold=ctx.repeat_threshold,
            recovery_threshold=ctx.recovery_threshold,
            parameter_error_min=ctx.parameter_error_min,
            span_latency_ms=ctx.span_latency_ms,
            token_anomaly_threshold=ctx.token_anomaly_threshold,
            cost_anomaly_threshold=ctx.cost_anomaly_threshold,
            historical_min_peers=ctx.historical_min_peers,
            historical_anomaly_ratio=ctx.historical_anomaly_ratio,
            peers=tuple(item for item in traces if item.trace_id != trace.trace_id),
        )
        result = analyze_trace(trace, peer_ctx)
        if result.coverage.unsupported_detectors:
            coverage_notes.append(
                f"{trace.trace_id}: unsupported {', '.join(result.coverage.unsupported_detectors)}"
            )
        if result.coverage.insufficient_data_detectors:
            coverage_notes.append(
                f"{trace.trace_id}: insufficient data for "
                f"{', '.join(result.coverage.insufficient_data_detectors)}"
            )
        if result.findings:
            affected.add(str(trace.trace_id))
        all_findings.extend(result.findings)

    totals_by_severity = {item.value: 0 for item in FindingSeverity}
    totals_by_type: dict[str, int] = {}
    for finding in all_findings:
        totals_by_severity[finding.severity.value] += 1
        totals_by_type[finding.detection_type.value] = (
            totals_by_type.get(finding.detection_type.value, 0) + 1
        )
    def _when(value: datetime | None) -> datetime:
        stamp = value or datetime.min.replace(tzinfo=UTC)
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)

    recent = sorted(
        [
            finding
            for finding in all_findings
            if finding.severity in {FindingSeverity.CRITICAL, FindingSeverity.WARNING}
        ],
        key=lambda item: _when(item.last_observed_at),
        reverse=True,
    )[:20]
    started = [_when(trace.started_at) for trace in traces]
    return DiagnosticSummary(
        analysis_scope=DiagnosticsAnalysisScope(
            limit=limit,
            offset=offset,
            project_id=project_id,
            analyzed_run_count=len(traces),
            newest_started_at=max(started) if started else None,
            oldest_started_at=min(started) if started else None,
            note=SCOPE_NOTE,
        ),
        totals_by_severity=totals_by_severity,
        totals_by_type=totals_by_type,
        affected_trace_count=len(affected),
        recent_findings=recent,
        coverage_notes=list(dict.fromkeys(coverage_notes))[:20],
    )


def active_lifecycle(trace: Trace) -> bool:
    return trace.lifecycle in {RunLifecycle.RUNNING, RunLifecycle.INTERRUPTED}
