from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

SpanType = Literal["agent", "llm", "tool", "workflow"]
SpanStatus = Literal["unset", "ok", "error"]


@dataclass(slots=True)
class SpanData:
    type: SpanType
    name: str
    duration_ms: float
    status: SpanStatus = "unset"
    span_id: UUID = field(default_factory=uuid4)
    parent_span_id: UUID | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    token_count: int = 0
    cost_usd: float = 0

    def to_dict(self) -> dict[str, Any]:
        return cast("dict[str, Any]", _serialize(asdict(self)))


@dataclass(slots=True)
class TraceData:
    project_id: str
    name: str
    status: SpanStatus = "unset"
    trace_id: UUID = field(default_factory=uuid4)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    attributes: dict[str, Any] = field(default_factory=dict)
    spans: list[SpanData] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return cast("dict[str, Any]", _serialize(asdict(self)))


def _serialize(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value
