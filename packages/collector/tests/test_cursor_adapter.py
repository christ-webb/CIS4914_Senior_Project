from nomore500s_collector.adapters.cursor import CursorAdapter


def test_session_start_and_stop_vs_end() -> None:
    adapter = CursorAdapter()
    start = adapter.plan(
        {
            "hook_event_name": "sessionStart",
            "session_id": "conv-1",
            "conversation_id": "conv-1",
            "workspace_roots": ["/tmp/demo"],
            "model_id": "composer",
        },
        delivery_id="d1",
    )
    assert start.events[0].kind == "start"
    assert start.events[0].payload["attributes"]["nm5.source"] == "cursor"
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
