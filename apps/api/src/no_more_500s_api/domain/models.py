from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class SpanType(StrEnum):
    AGENT = "agent"
    LLM = "llm"
    TOOL = "tool"
    WORKFLOW = "workflow"


class SpanStatus(StrEnum):
    UNSET = "unset"
    OK = "ok"
    ERROR = "error"


class RunLifecycle(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class ExecutionState(StrEnum):
    EXECUTING = "executing"
    IDLE = "idle"
    UNKNOWN = "unknown"


class RunEventType(StrEnum):
    RUN_START = "run_start"
    SPAN_START = "span_start"
    SPAN_COMPLETE = "span_complete"
    HEARTBEAT = "heartbeat"
    FINALIZE = "finalize"


class Span(BaseModel):
    span_id: UUID = Field(default_factory=uuid4)
    parent_span_id: UUID | None = None
    type: SpanType
    name: str = Field(min_length=1, max_length=200)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: float = Field(ge=0)
    status: SpanStatus = SpanStatus.UNSET
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    token_count: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)


class TraceCreate(BaseModel):
    trace_id: UUID = Field(default_factory=uuid4)
    project_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: SpanStatus = SpanStatus.UNSET
    attributes: dict[str, Any] = Field(default_factory=dict)
    spans: list[Span] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_parent_references(self) -> "TraceCreate":
        span_ids = {span.span_id for span in self.spans}
        for span in self.spans:
            if span.parent_span_id is not None and span.parent_span_id not in span_ids:
                raise ValueError(f"parent span {span.parent_span_id} is not part of this trace")
        return self


def lifecycle_from_batch_status(status: SpanStatus) -> RunLifecycle:
    if status is SpanStatus.OK:
        return RunLifecycle.COMPLETED
    if status is SpanStatus.ERROR:
        return RunLifecycle.FAILED
    return RunLifecycle.UNKNOWN


class Trace(TraceCreate):
    duration_ms: float = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    total_cost_usd: float = Field(default=0, ge=0)
    lifecycle: RunLifecycle = RunLifecycle.UNKNOWN
    execution_state: ExecutionState = ExecutionState.UNKNOWN
    last_activity_at: datetime | None = None
    ended_at: datetime | None = None

    @classmethod
    def from_create(cls, trace: TraceCreate) -> "Trace":
        duration = max((span.duration_ms for span in trace.spans), default=0)
        lifecycle = lifecycle_from_batch_status(trace.status)
        ended_at = None
        if lifecycle in {RunLifecycle.COMPLETED, RunLifecycle.FAILED, RunLifecycle.CANCELLED}:
            ended_at = trace.started_at
        return cls(
            **trace.model_dump(),
            duration_ms=duration,
            total_tokens=int(
                _sum_with_status(trace.spans, "nm5.token_status", lambda span: span.token_count)
            ),
            total_cost_usd=float(
                _sum_with_status(trace.spans, "nm5.cost_status", lambda span: span.cost_usd)
            ),
            lifecycle=lifecycle,
            execution_state=ExecutionState.UNKNOWN,
            last_activity_at=trace.started_at,
            ended_at=ended_at,
        )


def _sum_with_status(
    spans: list[Span], status_key: str, numeric: Callable[[Span], float]
) -> float:
    statuses = [
        span.attributes.get(status_key)
        for span in spans
        if isinstance(span.attributes.get(status_key), str) and span.attributes.get(status_key)
    ]
    if not statuses:
        return sum(numeric(span) for span in spans)
    return sum(numeric(span) for span in spans if span.attributes.get(status_key) == "reported")


def _attr_str(attributes: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _attr_float(attributes: dict[str, Any], key: str) -> float | None:
    value = attributes.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _attr_int(attributes: dict[str, Any], key: str) -> int | None:
    value = attributes.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    return None


class TraceSummary(BaseModel):
    trace_id: UUID
    project_id: str
    name: str
    started_at: datetime
    duration_ms: float
    total_tokens: int
    total_cost_usd: float
    status: SpanStatus
    lifecycle: RunLifecycle = RunLifecycle.UNKNOWN
    execution_state: ExecutionState = ExecutionState.UNKNOWN
    last_activity_at: datetime | None = None
    ended_at: datetime | None = None
    source: str | None = None
    agent_framework: str | None = None
    provider: str | None = None
    workspace: str | None = None
    cost_status: str | None = None
    token_status: str | None = None
    usage_scope: str | None = None
    instrumentation_coverage: str | None = None
    estimated_cost_usd: float | None = None
    estimated_cost_status: str | None = None
    estimated_subtotal_usd: float | None = None
    model: str | None = None
    model_variant: str | None = None
    estimate_basis: str | None = None
    scenario_standard_usd: float | None = None
    scenario_fast_usd: float | None = None
    covered_generations: int | None = None
    total_generations: int | None = None

    @classmethod
    def from_trace(cls, trace: Trace) -> "TraceSummary":
        attributes = trace.attributes
        dump = trace.model_dump(exclude={"spans", "attributes"})
        dump.pop("estimated_cost_usd", None)
        return cls(
            **dump,
            source=_attr_str(attributes, "nm5.source"),
            agent_framework=_attr_str(attributes, "agent.framework", "nm5.agent"),
            provider=_attr_str(attributes, "gen_ai.provider"),
            workspace=_attr_str(attributes, "nm5.workspace"),
            cost_status=_attr_str(attributes, "nm5.cost_status"),
            token_status=_attr_str(attributes, "nm5.token_status"),
            usage_scope=_attr_str(attributes, "nm5.usage_scope"),
            instrumentation_coverage=_attr_str(attributes, "nm5.coverage"),
            estimated_cost_usd=_attr_float(attributes, "nm5.estimated_cost_usd"),
            estimated_cost_status=_attr_str(attributes, "nm5.estimated_cost_status"),
            estimated_subtotal_usd=_attr_float(attributes, "nm5.estimated_subtotal_usd"),
            model=_attr_str(attributes, "nm5.model_id", "gen_ai.request.model"),
            model_variant=_attr_str(attributes, "nm5.model_variant"),
            estimate_basis=_attr_str(attributes, "nm5.estimate_basis"),
            scenario_standard_usd=_attr_float(attributes, "nm5.scenario_standard_usd"),
            scenario_fast_usd=_attr_float(attributes, "nm5.scenario_fast_usd"),
            covered_generations=_attr_int(attributes, "nm5.estimate_covered_generations"),
            total_generations=_attr_int(attributes, "nm5.estimate_total_generations"),
        )


class RunStart(BaseModel):
    trace_id: UUID = Field(default_factory=uuid4)
    project_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    attributes: dict[str, Any] = Field(default_factory=dict)
    event_id: UUID = Field(default_factory=uuid4)
    agent_name: str | None = Field(default=None, max_length=200)
    agent_span_id: UUID | None = None
    agent_input: dict[str, Any] | None = None


class RunEventIn(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    event_type: Literal["span_start", "span_complete", "heartbeat"]
    sequence: int = Field(ge=0)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    span_id: UUID | None = None
    parent_span_id: UUID | None = None
    type: SpanType | None = None
    name: str | None = Field(default=None, max_length=200)
    status: SpanStatus | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    token_count: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    progress: str | None = None

    @model_validator(mode="after")
    def validate_event_fields(self) -> "RunEventIn":
        if self.event_type in {"span_start", "span_complete"} and self.span_id is None:
            raise ValueError("span events require span_id")
        if self.event_type == "span_start" and (self.type is None or not self.name):
            raise ValueError("span_start requires type and name")
        if self.event_type == "span_complete" and self.status not in {
            SpanStatus.OK,
            SpanStatus.ERROR,
        }:
            raise ValueError("span_complete requires status ok or error")
        return self


class RunFinalize(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    sequence: int = Field(default=0, ge=0)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: Literal["completed", "failed", "cancelled"]
    error: dict[str, Any] | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class StoredRunEvent(BaseModel):
    event_id: UUID
    trace_id: UUID
    event_type: RunEventType
    sequence: int = Field(ge=0)
    occurred_at: datetime
    received_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
