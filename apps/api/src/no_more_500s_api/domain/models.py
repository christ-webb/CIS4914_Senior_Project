from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
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


class Trace(TraceCreate):
    duration_ms: float = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    total_cost_usd: float = Field(default=0, ge=0)

    @classmethod
    def from_create(cls, trace: TraceCreate) -> "Trace":
        duration = max((span.duration_ms for span in trace.spans), default=0)
        return cls(
            **trace.model_dump(),
            duration_ms=duration,
            total_tokens=sum(span.token_count for span in trace.spans),
            total_cost_usd=sum(span.cost_usd for span in trace.spans),
        )


class TraceSummary(BaseModel):
    trace_id: UUID
    project_id: str
    name: str
    started_at: datetime
    duration_ms: float
    total_tokens: int
    total_cost_usd: float
    status: SpanStatus

    @classmethod
    def from_trace(cls, trace: Trace) -> "TraceSummary":
        return cls(**trace.model_dump(exclude={"spans", "attributes"}))

