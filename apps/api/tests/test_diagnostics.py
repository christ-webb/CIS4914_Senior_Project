from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from no_more_500s_api.domain.diagnostics import DetectionType, FindingConfidence, FindingSeverity
from no_more_500s_api.domain.models import (
    RunLifecycle,
    Span,
    SpanStatus,
    SpanType,
    Trace,
    TraceCreate,
)
from no_more_500s_api.services.diagnostics import DetectionContext, analyze_trace
from no_more_500s_api.services.diagnostics.ids import finding_id


def _tool(
    name: str = "read_file",
    *,
    status: SpanStatus = SpanStatus.OK,
    query: str = "README.md",
    output: dict | None = None,
    attributes: dict | None = None,
    tokens: int = 0,
    cost: float = 0,
    duration_ms: float = 5,
    started_at: datetime | None = None,
) -> Span:
    return Span(
        type=SpanType.TOOL,
        name=name,
        duration_ms=duration_ms,
        status=status,
        input={"path": query},
        output=output,
        attributes=attributes or {},
        token_count=tokens,
        cost_usd=cost,
        started_at=started_at or datetime.now(UTC),
    )


def _llm(
    *,
    status: SpanStatus = SpanStatus.OK,
    output: dict | None = None,
    attributes: dict | None = None,
) -> Span:
    return Span(
        type=SpanType.LLM,
        name="generate",
        duration_ms=12,
        status=status,
        output=output,
        attributes=attributes or {},
    )


def _trace(
    spans: list[Span],
    *,
    lifecycle: RunLifecycle = RunLifecycle.COMPLETED,
    attributes: dict | None = None,
    status: SpanStatus = SpanStatus.OK,
    trace_id=None,
) -> Trace:
    created = Trace.from_create(
        TraceCreate(
            trace_id=trace_id or uuid4(),
            project_id="demo",
            name="run",
            status=status,
            attributes=attributes or {},
            spans=spans,
        )
    )
    return created.model_copy(update={"lifecycle": lifecycle})


def _ctx(**overrides: object) -> DetectionContext:
    values = dict(
        now=datetime.now(UTC),
        inactivity_seconds=120,
        repeat_threshold=3,
        recovery_threshold=3,
        parameter_error_min=2,
        span_latency_ms=30_000,
        token_anomaly_threshold=50_000,
        cost_anomaly_threshold=1.0,
        historical_min_peers=5,
        historical_anomaly_ratio=3.0,
        peers=(),
    )
    values.update(overrides)
    return DetectionContext(**values)  # type: ignore[arg-type]


def _types(result) -> set[str]:
    return {finding.detection_type.value for finding in result.findings}


def test_tool_failure_is_confirmed_from_status() -> None:
    span = _tool(status=SpanStatus.ERROR, output={"error": "file not found"})
    result = analyze_trace(_trace([span]), _ctx())
    finding = next(
        item for item in result.findings if item.detection_type is DetectionType.TOOL_FAILURE
    )
    assert finding.confidence is FindingConfidence.CONFIRMED
    assert finding.severity is FindingSeverity.CRITICAL
    assert span.span_id in finding.span_ids
    assert "file not found" in " ".join(finding.evidence)


def test_successful_tool_text_is_not_a_failure() -> None:
    span = _tool(output={"result": "the previous call returned an error status in docs"})
    result = analyze_trace(_trace([span]), _ctx())
    assert DetectionType.TOOL_FAILURE.value not in _types(result)


def test_successful_repeats_are_info_not_failures() -> None:
    spans = [_tool(query="weather") for _ in range(3)]
    result = analyze_trace(_trace(spans), _ctx())
    finding = next(
        item for item in result.findings if item.detection_type is DetectionType.REPEATED_TOOL_CALL
    )
    assert finding.severity is FindingSeverity.INFO
    assert finding.confidence is FindingConfidence.HEURISTIC
    assert DetectionType.INEFFECTIVE_RECOVERY.value not in _types(result)


def test_repeated_identical_failures_are_recovery_and_tool_failure() -> None:
    spans = [_tool(status=SpanStatus.ERROR, output={"error": "file not found"}) for _ in range(4)]
    result = analyze_trace(_trace(spans), _ctx())
    types = _types(result)
    assert DetectionType.TOOL_FAILURE.value in types
    assert DetectionType.REPEATED_TOOL_CALL.value in types
    assert DetectionType.INEFFECTIVE_RECOVERY.value in types
    recovery = next(
        item
        for item in result.findings
        if item.detection_type is DetectionType.INEFFECTIVE_RECOVERY
    )
    assert recovery.confidence is FindingConfidence.CONFIRMED
    assert len(recovery.span_ids) == 4


def test_parameter_errors_are_heuristic_without_schema() -> None:
    spans = [
        _tool(
            status=SpanStatus.ERROR,
            output={"error": "missing required parameter 'path'"},
        )
        for _ in range(2)
    ]
    result = analyze_trace(_trace(spans), _ctx())
    finding = next(
        item for item in result.findings if item.detection_type is DetectionType.SCHEMA_MISMATCH
    )
    assert finding.confidence is FindingConfidence.HEURISTIC


def test_timeout_tool_failure() -> None:
    span = _tool(
        status=SpanStatus.ERROR,
        output={"error": "deadline exceeded"},
        attributes={"timed_out": True},
    )
    result = analyze_trace(_trace([span]), _ctx())
    finding = next(
        item for item in result.findings if item.detection_type is DetectionType.TOOL_FAILURE
    )
    assert any("Timeout" in line or "timed_out" in line for line in finding.evidence)


def test_interrupted_run_is_not_a_failed_agent() -> None:
    result = analyze_trace(_trace([], lifecycle=RunLifecycle.INTERRUPTED), _ctx())
    finding = next(
        item for item in result.findings if item.detection_type is DetectionType.INTERRUPTION
    )
    assert finding.severity is FindingSeverity.INFO
    assert "not a confirmed agent failure" in finding.explanation
    assert DetectionType.TOOL_FAILURE.value not in _types(result)


def test_incomplete_telemetry_does_not_invent_provider_errors() -> None:
    result = analyze_trace(
        _trace(
            [_tool()],
            attributes={
                "nm5.source": "cli",
                "agent.framework": "codex",
                "nm5.token_status": "unavailable",
                "nm5.cost_status": "unavailable",
                "nm5.coverage": "hooks: tool,session; model: unavailable",
            },
        ),
        _ctx(),
    )
    assert DetectionType.PROVIDER_ERROR.value in result.coverage.unsupported_detectors
    assert DetectionType.TOKEN_ANOMALY.value in result.coverage.unsupported_detectors
    assert DetectionType.COST_ANOMALY.value in result.coverage.unsupported_detectors
    assert DetectionType.PROVIDER_ERROR.value not in _types(result)


def test_provider_error_from_llm_span() -> None:
    span = _llm(
        status=SpanStatus.ERROR,
        output={"error": "429 rate limit"},
        attributes={"http.status_code": 429},
    )
    result = analyze_trace(_trace([span]), _ctx())
    finding = next(
        item for item in result.findings if item.detection_type is DetectionType.PROVIDER_ERROR
    )
    assert finding.confidence is FindingConfidence.CONFIRMED
    assert span.span_id in finding.span_ids


def test_missing_tokens_are_not_zero_usage() -> None:
    span = _tool(tokens=0, cost=0, duration_ms=5)
    result = analyze_trace(
        _trace(
            [span], attributes={"nm5.token_status": "unavailable", "nm5.cost_status": "unavailable"}
        ),
        _ctx(token_anomaly_threshold=1, cost_anomaly_threshold=0.0001),
    )
    assert DetectionType.TOKEN_ANOMALY.value not in _types(result)
    assert DetectionType.COST_ANOMALY.value not in _types(result)


def test_absolute_latency_threshold() -> None:
    span = _tool(duration_ms=45_000)
    result = analyze_trace(_trace([span]), _ctx(span_latency_ms=30_000))
    finding = next(
        item for item in result.findings if item.detection_type is DetectionType.LATENCY_ANOMALY
    )
    assert finding.evidence_data["basis"] == "absolute_threshold"


def test_historical_comparison_requires_peers() -> None:
    current = _trace([_tool(duration_ms=100)], attributes={"nm5.token_status": "reported"})
    current = current.model_copy(update={"duration_ms": 900, "total_tokens": 10})
    result = analyze_trace(current, _ctx(historical_min_peers=5, peers=()))
    assert DetectionType.LATENCY_ANOMALY.value in result.coverage.insufficient_data_detectors
    historical = [
        item
        for item in result.findings
        if item.detection_type is DetectionType.LATENCY_ANOMALY
        and item.evidence_data.get("basis") == "historical_comparison"
    ]
    assert historical == []


def test_historical_comparison_with_enough_peers() -> None:
    peers = []
    for _ in range(5):
        peer = _trace([_tool(duration_ms=10)])
        peers.append(
            peer.model_copy(update={"duration_ms": 10, "lifecycle": RunLifecycle.COMPLETED})
        )
    current = _trace([_tool(duration_ms=100)])
    current = current.model_copy(update={"duration_ms": 100, "lifecycle": RunLifecycle.COMPLETED})
    result = analyze_trace(
        current, _ctx(historical_min_peers=5, historical_anomaly_ratio=3.0, peers=peers)
    )
    historical = [
        item
        for item in result.findings
        if item.detection_type is DetectionType.LATENCY_ANOMALY
        and item.evidence_data.get("basis") == "historical_comparison"
    ]
    assert len(historical) == 1


def test_finding_ids_stay_stable_when_spans_are_added() -> None:
    first = _tool(
        status=SpanStatus.ERROR,
        output={"error": "file not found"},
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    second = _tool(
        status=SpanStatus.ERROR,
        output={"error": "file not found"},
        started_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
    )
    third = _tool(
        status=SpanStatus.ERROR,
        output={"error": "file not found"},
        started_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
    )
    trace_id = uuid4()
    two = analyze_trace(_trace([first, second], trace_id=trace_id), _ctx())
    three = analyze_trace(_trace([first, second, third], trace_id=trace_id), _ctx())
    two_id = next(
        item.finding_id
        for item in two.findings
        if item.detection_type is DetectionType.TOOL_FAILURE
    )
    three_id = next(
        item.finding_id
        for item in three.findings
        if item.detection_type is DetectionType.TOOL_FAILURE
    )
    assert two_id == three_id
    assert len(next(item.span_ids for item in three.findings if item.finding_id == three_id)) == 3


def test_separate_signatures_get_distinct_ids() -> None:
    a = _tool(name="read_file", status=SpanStatus.ERROR, output={"error": "missing"})
    b = _tool(name="write_file", status=SpanStatus.ERROR, output={"error": "permission"})
    result = analyze_trace(_trace([a, b]), _ctx())
    ids = [
        item.finding_id
        for item in result.findings
        if item.detection_type is DetectionType.TOOL_FAILURE
    ]
    assert len(set(ids)) == 2


def test_provisional_flag_is_independent_of_confidence() -> None:
    span = _tool(status=SpanStatus.ERROR, output={"error": "boom"})
    running = analyze_trace(_trace([span], lifecycle=RunLifecycle.RUNNING), _ctx())
    finding = next(
        item for item in running.findings if item.detection_type is DetectionType.TOOL_FAILURE
    )
    assert finding.confidence is FindingConfidence.CONFIRMED
    assert finding.provisional is True
    done = analyze_trace(_trace([span], lifecycle=RunLifecycle.COMPLETED), _ctx())
    finalized = next(
        item for item in done.findings if item.detection_type is DetectionType.TOOL_FAILURE
    )
    assert finalized.confidence is FindingConfidence.CONFIRMED
    assert finalized.provisional is False


def test_detectors_do_not_mutate_trace() -> None:
    span = _tool(status=SpanStatus.ERROR, output={"error": "boom"})
    trace = _trace([span])
    before = deepcopy(trace.model_dump())
    analyze_trace(trace, _ctx())
    assert trace.model_dump() == before


def test_nonzero_exit_code_is_tool_failure() -> None:
    span = _tool(status=SpanStatus.ERROR, output={"exit_code": 2, "error": "command failed"})
    result = analyze_trace(_trace([span]), _ctx())
    finding = next(
        item for item in result.findings if item.detection_type is DetectionType.TOOL_FAILURE
    )
    assert any("exit code" in line.lower() for line in finding.evidence)


def test_finding_id_helper_is_deterministic() -> None:
    trace_id = uuid4()
    first = finding_id(
        detector_id="tool_failure",
        detector_version="1",
        trace_id=trace_id,
        anchor="span-a",
        signature="read_file:missing",
    )
    second = finding_id(
        detector_id="tool_failure",
        detector_version="1",
        trace_id=trace_id,
        anchor="span-a",
        signature="read_file:missing",
    )
    assert first == second


def test_below_repeat_threshold_is_silent() -> None:
    result = analyze_trace(_trace([_tool(), _tool()]), _ctx(repeat_threshold=3))
    assert DetectionType.REPEATED_TOOL_CALL.value not in _types(result)
