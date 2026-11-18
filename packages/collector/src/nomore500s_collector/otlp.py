from __future__ import annotations

import json
from typing import Any

CONTENT_KEYS = {
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
    "gen_ai.tool.definitions",
    "user.name",
}


def _attr_value(item: dict[str, Any]) -> Any:
    value = item.get("value") or item
    for key in (
        "stringValue",
        "string_value",
        "intValue",
        "int_value",
        "doubleValue",
        "double_value",
        "boolValue",
        "bool_value",
    ):
        if key in value:
            return value[key]
    if "arrayValue" in value or "array_value" in value:
        values = (value.get("arrayValue") or value.get("array_value") or {}).get("values") or []
        return [_attr_value(entry) if isinstance(entry, dict) else entry for entry in values]
    return value if not isinstance(value, dict) else None


def attributes_to_dict(items: list[Any] | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(items, dict):
        return {str(key): value for key, value in items.items() if key not in CONTENT_KEYS}
    result: dict[str, Any] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if not key or key in CONTENT_KEYS:
            continue
        result[key] = _attr_value(item)
    return result


def _nanos_to_iso(raw: Any) -> str | None:
    if raw is None or raw == "":
        return None
    try:
        nanos = int(raw)
    except (TypeError, ValueError):
        return None
    seconds = nanos / 1_000_000_000
    from datetime import UTC, datetime

    return datetime.fromtimestamp(seconds, tz=UTC).isoformat()


def flatten_resource_spans(document: dict[str, Any]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for resource_span in document.get("resourceSpans") or document.get("resource_spans") or []:
        resource_attrs = attributes_to_dict((resource_span.get("resource") or {}).get("attributes"))
        for scope in resource_span.get("scopeSpans") or resource_span.get("scope_spans") or []:
            for span in scope.get("spans") or []:
                attrs = attributes_to_dict(span.get("attributes"))
                operation = str(
                    attrs.get("gen_ai.operation.name")
                    or span.get("name")
                    or ""
                )
                conversation = (
                    attrs.get("gen_ai.conversation.id")
                    or resource_attrs.get("gen_ai.conversation.id")
                    or resource_attrs.get("session.id")
                )
                start = _nanos_to_iso(
                    span.get("startTimeUnixNano") or span.get("start_time_unix_nano")
                )
                end = _nanos_to_iso(span.get("endTimeUnixNano") or span.get("end_time_unix_nano"))
                status = span.get("status") or {}
                code = status.get("code") if isinstance(status, dict) else None
                spans.append(
                    {
                        "hook_event_name": "OtelSpan",
                        "operation": operation,
                        "name": str(span.get("name") or operation or "span")[:200],
                        "session_id": str(conversation or ""),
                        "conversation_id": str(conversation or ""),
                        "resource_session_id": str(resource_attrs.get("session.id") or ""),
                        "otel_trace_id": str(span.get("traceId") or span.get("trace_id") or ""),
                        "otel_span_id": str(span.get("spanId") or span.get("span_id") or ""),
                        "otel_parent_span_id": str(
                            span.get("parentSpanId") or span.get("parent_span_id") or ""
                        ),
                        "occurred_at": start or end,
                        "ended_at": end,
                        "attributes": {**resource_attrs, **attrs},
                        "status_code": code,
                        "status_message": (
                            status.get("message") if isinstance(status, dict) else None
                        ),
                    }
                )
    return spans


def decode_otlp(body: bytes, content_type: str) -> list[dict[str, Any]]:
    lowered = content_type.lower()
    if "proto" in lowered:
        return decode_otlp_protobuf(body)
    document = json.loads(body.decode("utf-8") or "{}")
    if not isinstance(document, dict):
        raise ValueError("OTLP JSON must be an object")
    return flatten_resource_spans(document)


def decode_otlp_protobuf(body: bytes) -> list[dict[str, Any]]:
    try:
        from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (  # type: ignore[import-not-found]
            ExportTraceServiceRequest,
        )
    except ImportError as error:
        raise ValueError("protobuf OTLP support is unavailable") from error
    message = ExportTraceServiceRequest()
    message.ParseFromString(body)
    document = MessageToDict(message, preserving_proto_field_name=False)
    return flatten_resource_spans(document)
