import pytest
from no_more_500s_api.domain.models import Span, SpanType, Trace, TraceCreate
from no_more_500s_api.services.failure_detection import detect_repeated_tool_calls


def _tool(name: str, query: str, tokens: int = 10, cost: float = 0.01) -> Span:
    return Span(
        type=SpanType.TOOL,
        name=name,
        duration_ms=5,
        input={"query": query},
        token_count=tokens,
        cost_usd=cost,
    )


def _trace(spans: list[Span]) -> Trace:
    return Trace.from_create(TraceCreate(project_id="demo", name="run", spans=spans))


def test_flags_identical_tool_call_repeated_three_times() -> None:
    trace = _trace([_tool("search", "weather") for _ in range(3)])

    findings = detect_repeated_tool_calls(trace)

    assert len(findings) == 1
    assert findings[0].span_name == "search"
    assert findings[0].repeat_count == 3
    assert len(findings[0].span_ids) == 3


def test_wasted_usage_excludes_the_first_call() -> None:
    trace = _trace([_tool("search", "weather", tokens=100, cost=0.5) for _ in range(3)])

    finding = detect_repeated_tool_calls(trace)[0]

    assert finding.wasted_tokens == 200
    assert finding.wasted_cost_usd == pytest.approx(1.0)


def test_different_inputs_are_not_a_loop() -> None:
    trace = _trace([_tool("search", f"query {index}") for index in range(5)])

    assert detect_repeated_tool_calls(trace) == []


def test_below_threshold_is_not_flagged() -> None:
    trace = _trace([_tool("search", "weather") for _ in range(2)])

    assert detect_repeated_tool_calls(trace) == []


def test_non_tool_spans_are_ignored() -> None:
    llm_calls = [
        Span(type=SpanType.LLM, name="plan", duration_ms=5, input={"query": "x"})
        for _ in range(4)
    ]

    assert detect_repeated_tool_calls(_trace(llm_calls)) == []


def test_most_repeated_call_is_listed_first() -> None:
    spans = [_tool("search", "a") for _ in range(3)] + [_tool("fetch", "b") for _ in range(5)]

    findings = detect_repeated_tool_calls(_trace(spans))

    assert [finding.span_name for finding in findings] == ["fetch", "search"]


def test_threshold_below_two_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        detect_repeated_tool_calls(_trace([]), threshold=1)
