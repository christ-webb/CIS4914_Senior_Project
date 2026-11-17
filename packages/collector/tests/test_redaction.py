from nomore500s_collector.redaction import redact_payload


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
