from nomore500s_collector.adapters.vscode_copilot import VSCodeCopilotAdapter


def test_camel_and_pascal_payloads() -> None:
    adapter = VSCodeCopilotAdapter()
    camel = adapter.plan(
        {"hook_event_name": "sessionStart", "sessionId": "s1", "cwd": "/tmp/app"},
        delivery_id="d1",
    )
    assert camel.session_id == "s1"
    pascal = adapter.plan(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "s1",
            "tool_name": "readFile",
            "tool_use_id": "call-1",
        },
        delivery_id="d2",
    )
    assert pascal.events[0].payload["name"] == "readFile"


def test_user_exit_is_cancelled() -> None:
    plan = VSCodeCopilotAdapter().plan(
        {"hook_event_name": "sessionEnd", "sessionId": "s1", "reason": "user_exit"},
        delivery_id="d",
    )
    assert plan.events[0].payload["status"] == "cancelled"


def test_agent_stop_is_heartbeat() -> None:
    plan = VSCodeCopilotAdapter().plan(
        {"hook_event_name": "agentStop", "sessionId": "s1"},
        delivery_id="d",
    )
    assert plan.events[0].payload["event_type"] == "heartbeat"
