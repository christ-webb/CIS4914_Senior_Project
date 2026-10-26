from typing import Any

import pytest
from no_more_500s_api.domain.models import Span, SpanStatus, SpanType, Trace, TraceCreate
from no_more_500s_api.services.parameter_errors import (
    MAX_SAMPLE_LENGTH,
    detect_invalid_parameter_errors,
)


def _failed_tool(
    name: str = "get_weather",
    error: str = "Validation error: missing required field 'city'",
    tokens: int = 10,
    cost: float = 0.01,
) -> Span:
    return Span(
        type=SpanType.TOOL,
        name=name,
        duration_ms=5,
        status=SpanStatus.ERROR,
        output={"error": error},
        token_count=tokens,
        cost_usd=cost,
    )


def _trace(spans: list[Span]) -> Trace:
    return Trace.from_create(TraceCreate(project_id="demo", name="run", spans=spans))


def test_flags_tool_that_repeatedly_rejects_parameters() -> None:
    trace = _trace([_failed_tool(), _failed_tool()])

    findings = detect_invalid_parameter_errors(trace)

    assert len(findings) == 1
    assert findings[0].span_name == "get_weather"
    assert findings[0].error_count == 2
    assert len(findings[0].span_ids) == 2
    assert "missing required field" in findings[0].sample_error


def test_every_failed_call_counts_as_wasted_usage() -> None:
    trace = _trace([_failed_tool(tokens=100, cost=0.5), _failed_tool(tokens=50, cost=0.25)])

    finding = detect_invalid_parameter_errors(trace)[0]

    assert finding.wasted_tokens == 150
    assert finding.wasted_cost_usd == pytest.approx(0.75)


def test_errors_unrelated_to_parameters_are_ignored() -> None:
    trace = _trace([_failed_tool(error="Request timed out"), _failed_tool(error="503 upstream")])

    assert detect_invalid_parameter_errors(trace) == []


def test_successful_span_mentioning_invalid_is_not_flagged() -> None:
    ok_spans = [
        Span(
            type=SpanType.TOOL,
            name="lint",
            duration_ms=5,
            status=SpanStatus.OK,
            output={"message": "no invalid input found"},
        )
        for _ in range(3)
    ]

    assert detect_invalid_parameter_errors(_trace(ok_spans)) == []


def test_error_text_is_found_in_nested_attributes() -> None:
    nested: dict[str, Any] = {"exception": {"details": ["Unknown parameter 'cty'"]}}
    spans = [
        Span(
            type=SpanType.TOOL,
            name="get_weather",
            duration_ms=5,
            status=SpanStatus.ERROR,
            attributes=nested,
        )
        for _ in range(2)
    ]

    findings = detect_invalid_parameter_errors(_trace(spans))

    assert findings[0].sample_error == "Unknown parameter 'cty'"


def test_single_error_is_below_the_default_threshold() -> None:
    trace = _trace([_failed_tool()])

    assert detect_invalid_parameter_errors(trace) == []
    assert len(detect_invalid_parameter_errors(trace, min_errors=1)) == 1


def test_most_failing_tool_is_listed_first() -> None:
    spans = [_failed_tool("search") for _ in range(2)] + [_failed_tool("fetch") for _ in range(4)]

    findings = detect_invalid_parameter_errors(_trace(spans))

    assert [finding.span_name for finding in findings] == ["fetch", "search"]


def test_long_error_text_is_truncated() -> None:
    long_error = "Validation error: " + "x" * 1000
    trace = _trace([_failed_tool(error=long_error), _failed_tool(error=long_error)])

    sample = detect_invalid_parameter_errors(trace)[0].sample_error

    assert len(sample) == MAX_SAMPLE_LENGTH


def test_min_errors_below_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        detect_invalid_parameter_errors(_trace([]), min_errors=0)
