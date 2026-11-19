from nomore500s_collector.adapters.cursor import CursorAdapter
from nomore500s_collector.ids import span_id_for


def test_session_start_and_stop_vs_end() -> None:
    adapter = CursorAdapter()
    start = adapter.plan(
        {
            "hook_event_name": "sessionStart",
            "session_id": "conv-1",
            "conversation_id": "conv-1",
            "workspace_roots": ["/tmp/demo"],
            "model_id": "composer",
            "cursor_version": "3.30.14",
        },
        delivery_id="d1",
    )
    assert start.events[0].kind == "start"
    assert start.events[0].payload["attributes"]["nm5.source"] == "cursor"
    assert start.events[0].payload["attributes"]["nm5.cursor_version"] == "3.30.14"
    stop = adapter.plan(
        {"hook_event_name": "stop", "conversation_id": "conv-1", "status": "completed"},
        delivery_id="d2",
    )
    assert stop.events[0].payload["event_type"] == "heartbeat"
    ended = adapter.plan(
        {"hook_event_name": "sessionEnd", "conversation_id": "conv-1", "reason": "aborted"},
        delivery_id="d3",
    )
    assert ended.events[0].kind == "finalize"
    assert ended.events[0].payload["status"] == "cancelled"


def test_window_close_is_cancelled() -> None:
    plan = CursorAdapter().plan(
        {"hook_event_name": "sessionEnd", "conversation_id": "c", "reason": "window_close"},
        delivery_id="x",
    )
    assert plan.events[0].payload["status"] == "cancelled"
    assert plan.events[0].payload["attributes"]["nm5.outcome"] == "cancelled"


def test_tool_failure_interrupt_is_not_model_failure() -> None:
    plan = CursorAdapter().plan(
        {
            "hook_event_name": "postToolUseFailure",
            "conversation_id": "c",
            "tool_use_id": "t1",
            "tool_name": "Shell",
            "is_interrupt": True,
            "failure_type": "error",
            "error_message": "interrupted",
        },
        delivery_id="d",
    )
    assert plan.events[0].payload["status"] == "ok"
    assert plan.events[0].payload["attributes"]["nm5.outcome"] == "cancelled"


def test_tool_ids_are_stable() -> None:
    adapter = CursorAdapter()
    start = adapter.plan(
        {
            "hook_event_name": "preToolUse",
            "conversation_id": "c",
            "tool_use_id": "t1",
            "tool_name": "Read",
        },
        delivery_id="a",
    )
    done = adapter.plan(
        {
            "hook_event_name": "postToolUse",
            "conversation_id": "c",
            "tool_use_id": "t1",
            "tool_name": "Read",
            "duration": 12,
        },
        delivery_id="b",
    )
    assert start.events[0].payload["span_id"] == done.events[0].payload["span_id"]


def test_stop_without_generation_does_not_invent_usage() -> None:
    plan = CursorAdapter().plan(
        {"hook_event_name": "stop", "conversation_id": "c", "status": "completed"},
        delivery_id="d-stop",
    )
    assert len(plan.events) == 1
    assert plan.events[0].payload["event_type"] == "heartbeat"
    assert plan.events[0].payload["progress"] == "stop"
    assert plan.token_status is None
    assert "input_tokens" not in plan.events[0].payload["attributes"]["nm5.hook_fields"].split(",")
    assert "status" in plan.events[0].payload["attributes"]["nm5.hook_fields"].split(",")


def test_ambiguous_usage_without_ids_is_ignored_for_tokens() -> None:
    plan = CursorAdapter().plan(
        {
            "hook_event_name": "afterAgentResponse",
            "input_tokens": 9,
            "output_tokens": 3,
        },
        delivery_id="d-none",
    )
    assert plan.ignored is True


def test_stop_tokens_require_generation_identity() -> None:
    adapter = CursorAdapter()
    response = adapter.plan(
        {
            "hook_event_name": "afterAgentResponse",
            "conversation_id": "c",
            "generation_id": "g1",
            "status": "completed",
            "input_tokens": 10,
            "output_tokens": 2,
            "model_id": "composer-2.5",
        },
        delivery_id="d-resp",
    )
    stop = adapter.plan(
        {
            "hook_event_name": "stop",
            "conversation_id": "c",
            "generation_id": "g1",
            "status": "completed",
            "input_tokens": 12,
            "output_tokens": 4,
            "cache_read_tokens": 3,
            "cache_write_tokens": 1,
        },
        delivery_id="d-stop",
    )
    response_usage = next(item for item in response.events if item.payload.get("type") == "llm")
    stop_usage = next(item for item in stop.events if item.payload.get("type") == "llm")
    assert response_usage.payload["span_id"] == stop_usage.payload["span_id"]
    assert response_usage.payload["span_id"] == str(span_id_for("llm", "cursor:c:g1"))
    assert stop_usage.payload["token_count"] == 16
    assert stop_usage.payload["attributes"]["nm5.usage_event"] == "stop"
    assert stop_usage.payload["attributes"]["nm5.usage_scope"] == "parent_agent"
    assert stop_usage.payload["attributes"]["nm5.token_accounting"] == "inclusive_cache"
    assert "cost_usd" not in stop_usage.payload
    assert response_usage.payload["attributes"]["nm5.model_id"] == "composer-2.5"
    assert "nm5.model_variant" not in response_usage.payload["attributes"]
    thought = adapter.plan(
        {
            "hook_event_name": "afterAgentThought",
            "conversation_id": "c",
            "generation_id": "g1",
            "text": "should be redacted before plan",
        },
        delivery_id="d-thought",
    )
    assert thought.events[0].payload["progress"] == "afterAgentThought"
    assert "text" not in thought.events[0].payload


def test_stores_display_model_and_verified_fast_variant() -> None:
    plan = CursorAdapter().plan(
        {
            "hook_event_name": "stop",
            "conversation_id": "c",
            "generation_id": "g1",
            "model": "Composer 2.5 (Fast)",
            "model_id": "composer-2.5",
            "input_tokens": 10,
            "output_tokens": 1,
        },
        delivery_id="d-fast",
    )
    usage = next(item for item in plan.events if item.payload.get("type") == "llm")
    assert usage.payload["attributes"]["gen_ai.request.model"] == "Composer 2.5 (Fast)"
    assert usage.payload["attributes"]["nm5.model_id"] == "composer-2.5"
    assert usage.payload["attributes"]["nm5.model_variant"] == "fast"


def test_precompact_is_context_not_usage() -> None:
    plan = CursorAdapter().plan(
        {
            "hook_event_name": "preCompact",
            "conversation_id": "c",
            "context_tokens": 22200,
            "context_window_size": 200000,
            "context_usage_percent": 11,
        },
        delivery_id="d-compact",
    )
    attrs = plan.events[0].payload["attributes"]
    assert attrs["nm5.context_tokens"] == 22200
    assert "token_count" not in plan.events[0].payload
    assert plan.token_status is None
    assert "context_tokens" in attrs["nm5.hook_fields"].split(",")
    assert "input_tokens" not in attrs["nm5.hook_fields"].split(",")
