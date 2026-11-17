from nomore500s_collector.adapters.codex import CodexAdapter, occurrence_key


def test_stop_is_heartbeat_not_finalize() -> None:
    plan = CodexAdapter().plan(
        {
            "hook_event_name": "Stop",
            "session_id": "s1",
            "turn_id": "t1",
            "cwd": "/tmp/ws",
            "model": "gpt-5",
        },
        delivery_id="d1",
    )
    assert plan.events[0].kind == "events"
    assert plan.events[0].payload["event_type"] == "heartbeat"
    assert plan.events[0].payload["progress"] == "Stop"


def test_repeated_stops_are_not_collapsed_by_turn() -> None:
    first = occurrence_key(
        {"hook_event_name": "Stop", "session_id": "s1", "turn_id": "t1"}, "d1"
    )
    second = occurrence_key(
        {"hook_event_name": "Stop", "session_id": "s1", "turn_id": "t1"}, "d2"
    )
    assert first != second


def test_tool_events_share_stable_span_identity() -> None:
    adapter = CodexAdapter()
    start = adapter.plan(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "s1",
            "tool_use_id": "tool-1",
            "tool_name": "Bash",
            "cwd": "/tmp/ws",
        },
        delivery_id="d-pre",
    )
    complete = adapter.plan(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "tool_use_id": "tool-1",
            "tool_name": "Bash",
            "cwd": "/tmp/ws",
        },
        delivery_id="d-post",
    )
    assert start.events[0].payload["span_id"] == complete.events[0].payload["span_id"]
    assert start.occurrence_key != complete.occurrence_key


def test_unknown_event_is_ignored() -> None:
    plan = CodexAdapter().plan(
        {"hook_event_name": "Mystery", "session_id": "s1"},
        delivery_id="dx",
    )
    assert plan.ignored
    assert plan.events == []


def test_model_hook_maps_tokens_without_cost() -> None:
    plan = CodexAdapter().plan(
        {
            "hook_event_name": "PostModelRequest",
            "session_id": "s1",
            "model": "gpt-5",
            "request_id": "req-1",
            "status": "completed",
            "token_usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 99},
        },
        delivery_id="dm",
    )
    payload = plan.events[0].payload
    assert payload["token_count"] == 10
    assert payload["attributes"]["nm5.cost_status"] == "unavailable"
    assert "cost_usd" not in payload
