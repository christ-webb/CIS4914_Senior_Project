from datetime import UTC, datetime

from no_more_500s_api.domain.models import SpanStatus, Trace, TraceSummary
from no_more_500s_api.services.trace_service import TraceListQuery, apply_trace_list_query


def test_summary_preserves_provenance_pricing_and_turn_evidence() -> None:
    trace = Trace(
        project_id="p",
        name="example",
        started_at=datetime.now(UTC),
        attributes={
            "nm5.source": "cursor",
            "nm5.conversation_id": "chat",
            "nm5.is_fixture": True,
            "nm5.last_turn_completed": True,
            "nm5.scenario_standard_usd": 0.2,
            "nm5.scenario_fast_usd": 0.4,
            "nm5.pricing_source": "https://example.test/published-pricing",
        },
        spans=[],
    )
    summary = TraceSummary.from_trace(trace)
    assert summary.is_fixture and summary.last_turn_completed
    assert summary.scenario_standard_usd == 0.2
    assert summary.scenario_fast_usd == 0.4
    assert summary.logical_run_id is not None
    assert summary.pricing_source == "https://example.test/published-pricing"
    ordinary = TraceSummary.from_trace(trace.model_copy(update={"attributes": {}}))
    assert apply_trace_list_query([summary, ordinary], TraceListQuery(fixtures="exclude")) == [
        ordinary
    ]
    assert apply_trace_list_query([summary, ordinary], TraceListQuery(fixtures="only")) == [summary]


def test_activity_filters_use_effective_backend_fields() -> None:
    from no_more_500s_api.domain.models import ExecutionState, RunLifecycle

    base = TraceSummary(
        project_id="p",
        name="run",
        trace_id="00000000-0000-0000-0000-000000000001",
        started_at=datetime.now(UTC),
        duration_ms=0,
        total_tokens=0,
        total_cost_usd=0,
        status=SpanStatus.UNSET,
    )
    interrupted = base.model_copy(
        update={"lifecycle": RunLifecycle.INTERRUPTED, "execution_state": ExecutionState.EXECUTING}
    )
    idle = base.model_copy(
        update={"lifecycle": RunLifecycle.RUNNING, "execution_state": ExecutionState.IDLE}
    )
    active = base.model_copy(
        update={"lifecycle": RunLifecycle.RUNNING, "execution_state": ExecutionState.EXECUTING}
    )
    assert apply_trace_list_query(
        [base, interrupted, idle, active], TraceListQuery(activity="active")
    ) == [active]
    assert apply_trace_list_query(
        [base, interrupted, idle, active], TraceListQuery(activity="idle")
    ) == [idle]
