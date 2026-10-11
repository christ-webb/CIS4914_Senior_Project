"""Heuristic failure detection over stored traces.

MVP rule: flag an agent that makes the same tool call with identical input
over and over. This is the signature of the "exponential token waste" loop
from the project pitch: the agent keeps retrying a call that is not
changing its situation while token spend grows.

The detector is a pure function over the existing ``Trace`` model, so it
needs no storage and works with either repository implementation.
"""

import json
from collections import defaultdict
from uuid import UUID

from pydantic import BaseModel

from no_more_500s_api.domain.models import Span, SpanType, Trace

DEFAULT_REPEAT_THRESHOLD = 3


class LoopFinding(BaseModel):
    """One repeated tool call found in a trace."""

    span_name: str
    repeat_count: int
    span_ids: list[UUID]
    wasted_tokens: int
    wasted_cost_usd: float


def detect_repeated_tool_calls(
    trace: Trace,
    threshold: int = DEFAULT_REPEAT_THRESHOLD,
) -> list[LoopFinding]:
    """Return tool calls repeated with identical input at least ``threshold`` times.

    The first call is treated as legitimate, so wasted tokens and cost only
    count the repeats. Findings are ordered with the most-repeated call first.
    """
    if threshold < 2:
        raise ValueError("threshold must be at least 2")

    groups: dict[tuple[str, str], list[Span]] = defaultdict(list)
    for span in trace.spans:
        if span.type is not SpanType.TOOL:
            continue
        key = (span.name, json.dumps(span.input, sort_keys=True, default=str))
        groups[key].append(span)

    findings = [
        LoopFinding(
            span_name=name,
            repeat_count=len(spans),
            span_ids=[span.span_id for span in spans],
            wasted_tokens=sum(span.token_count for span in spans[1:]),
            wasted_cost_usd=sum(span.cost_usd for span in spans[1:]),
        )
        for (name, _), spans in groups.items()
        if len(spans) >= threshold
    ]
    return sorted(findings, key=lambda finding: finding.repeat_count, reverse=True)
