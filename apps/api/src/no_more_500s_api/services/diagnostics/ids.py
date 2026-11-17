import json
from datetime import UTC
from uuid import UUID, uuid5

from no_more_500s_api.domain.models import Span

FINDING_NAMESPACE = UUID("6b1a4d7e-0c2f-4a91-9e33-7d5f8a12b0c4")
TRACE_ANCHOR = "trace"


def normalize_payload(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def span_sort_key(span: Span) -> tuple[object, UUID]:
    started = span.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return started, span.span_id


def occurrence_anchor(spans: list[Span]) -> str:
    if not spans:
        return TRACE_ANCHOR
    earliest = min(spans, key=span_sort_key)
    return str(earliest.span_id)


def finding_id(
    *,
    detector_id: str,
    detector_version: str,
    trace_id: UUID,
    anchor: str,
    signature: str,
) -> UUID:
    name = f"{detector_id}:{detector_version}:{trace_id}:{anchor}:{signature}"
    return uuid5(FINDING_NAMESPACE, name)
