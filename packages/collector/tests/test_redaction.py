from nomore500s_collector.redaction import redact_payload


def test_redacts_response_text_but_keeps_usage_and_ids() -> None:
    cleaned = redact_payload(
        {
            "hook_event_name": "afterAgentResponse",
            "conversation_id": "conv-1",
            "generation_id": "gen-9",
            "text": "assistant secret",
            "input_tokens": 11,
            "output_tokens": 4,
            "cache_read_tokens": 2,
            "model_id": "composer-2.5",
        }
    )
    assert "text" not in cleaned
    assert cleaned["conversation_id"] == "conv-1"
    assert cleaned["generation_id"] == "gen-9"
    assert cleaned["input_tokens"] == 11
    assert cleaned["output_tokens"] == 4
    assert cleaned["cache_read_tokens"] == 2
    assert cleaned["model_id"] == "composer-2.5"


def test_redacts_sensitive_fields_and_cwd() -> None:
    cleaned = redact_payload(
        {
            "session_id": "s1",
            "cwd": "/Users/me/project",
            "prompt": "secret",
            "tool_input": {"cmd": "ls"},
            "tool_name": "Bash",
            "transcript_path": "/tmp/a.jsonl",
        }
    )
    assert cleaned["cwd"] == "project"
    assert "prompt" not in cleaned
    assert "tool_input" not in cleaned
    assert "transcript_path" not in cleaned
    assert cleaned["tool_name"] == "Bash"
