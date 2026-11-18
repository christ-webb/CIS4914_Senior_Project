from pathlib import Path

from fastapi.testclient import TestClient
from nomore500s_collector.otlp import decode_otlp
from nomore500s_collector.server import CollectorApp, create_app
from nomore500s_collector.settings import CollectorSettings

from .conftest import RecordingClient


def _attr(key: str, value: str | int) -> dict[str, object]:
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": value}}
    return {"key": key, "value": {"stringValue": value}}


SAMPLE = {
    "resourceSpans": [
        {
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": "github-copilot"}},
                    {"key": "session.id", "value": {"stringValue": "conv"}},
                ]
            },
            "scopeSpans": [
                {
                    "spans": [
                        {
                            "traceId": "aaa",
                            "spanId": "bbb",
                            "name": "invoke_agent copilot",
                            "startTimeUnixNano": "1763000000000000000",
                            "endTimeUnixNano": "1763000001000000000",
                            "attributes": [
                                _attr("gen_ai.operation.name", "invoke_agent"),
                                _attr("gen_ai.conversation.id", "conv"),
                                _attr("gen_ai.request.model", "gpt-4.1"),
                            ],
                        },
                        {
                            "traceId": "aaa",
                            "spanId": "ccc",
                            "parentSpanId": "bbb",
                            "name": "chat",
                            "startTimeUnixNano": "1763000000200000000",
                            "endTimeUnixNano": "1763000000800000000",
                            "attributes": [
                                _attr("gen_ai.operation.name", "chat"),
                                _attr("gen_ai.conversation.id", "conv"),
                                _attr("gen_ai.usage.input_tokens", 10),
                                _attr("gen_ai.usage.output_tokens", 4),
                                _attr("gen_ai.input.messages", "SECRET"),
                            ],
                        },
                    ]
                }
            ],
        }
    ]
}


def test_decode_otlp_json_redacts_content() -> None:
    import json

    spans = decode_otlp(json.dumps(SAMPLE).encode(), "application/json")
    chat = next(item for item in spans if "chat" in str(item["operation"]))
    assert chat["session_id"] == "conv"
    assert "gen_ai.input.messages" not in chat["attributes"]
    assert chat["attributes"]["gen_ai.usage.input_tokens"] == 10


def test_otlp_requires_auth(tmp_path: Path) -> None:
    settings = CollectorSettings(collector_home=tmp_path, collector_token="secret-token")
    runtime = CollectorApp(settings)
    runtime.pipeline.client = RecordingClient()  # type: ignore[method-assign]
    with TestClient(create_app(runtime)) as client:
        denied = client.post("/v1/traces", json=SAMPLE)
        assert denied.status_code == 401
        accepted = client.post(
            "/v1/traces",
            headers={"Authorization": "Bearer secret-token"},
            json=SAMPLE,
        )
        assert accepted.status_code == 202
