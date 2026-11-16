
import time
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types
from nomore500s import NoMore500sClient, SpanData, TraceData

ROOT = Path(__file__).resolve().parents[2]
MODEL = "gemini-3.5-flash-lite"

TASK = (
    "Analyze the No More 500s software project. "
    "Read its README, then explain its purpose, "
    "architecture, and current limitations."
)


def now():
    return datetime.now(timezone.utc)


def read_project_readme():
    """Read the local senior design README."""
    return (ROOT / "README.md").read_text()[:12000]


def main():
    trace = TraceData(
        project_id="gemini-agent-demo",
        name="Gemini Project Research Agent",
        status="unset",
    )

    agent = SpanData(
        type="agent",
        name="Analyze project with Gemini",
        duration_ms=0,
        status="unset",
        started_at=now(),
        input={"task": TASK},
    )
    trace.spans.append(agent)

    started = time.perf_counter()

    def recorded_llm_call(client, contents, config, name):
        call_started = time.perf_counter()
        call_time = now()
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            trace.spans.append(SpanData(
                type="llm",
                name=name,
                parent_span_id=agent.span_id,
                started_at=call_time,
                duration_ms=(
                    time.perf_counter() - call_started
                ) * 1000,
                status="error",
                output={"error_type": type(exc).__name__},
            ))
            raise

        usage = response.usage_metadata
        tokens = (
            (usage.total_token_count or 0)
            if usage else 0
        )

        trace.spans.append(SpanData(
            type="llm",
            name=name,
            parent_span_id=agent.span_id,
            started_at=call_time,
            duration_ms=(
                time.perf_counter() - call_started
            ) * 1000,
            status="ok",
            token_count=tokens,
            attributes={
                "gen_ai.provider": "google",
                "gen_ai.request.model": MODEL,
            },
            output={
                "tool_requested": bool(response.function_calls),
                "response_characters": len(response.text or "")
                if not response.function_calls else 0,
            },
        ))
        return response

    try:
        with genai.Client() as client:
            first = recorded_llm_call(
                client,
                TASK,
                types.GenerateContentConfig(
                    tools=[read_project_readme],
                    automatic_function_calling=(
                        types.AutomaticFunctionCallingConfig(
                            disable=True
                        )
                    ),
                    tool_config=types.ToolConfig(
                        function_calling_config=(
                            types.FunctionCallingConfig(
                                mode="ANY"
                            )
                        )
                    ),
                ),
                "Gemini: Select research tool",
            )

            calls = first.function_calls or []
            if len(calls) != 1 or calls[0].name != "read_project_readme":
                raise RuntimeError("Unexpected Gemini tool request")

            tool_started = time.perf_counter()
            tool_time = now()

            try:
                readme = read_project_readme()
                tool_status = "ok"
            except Exception as exc:
                readme = f"Tool error: {type(exc).__name__}"
                tool_status = "error"

            trace.spans.append(SpanData(
                type="tool",
                name="read_project_readme",
                parent_span_id=agent.span_id,
                started_at=tool_time,
                duration_ms=(
                    time.perf_counter() - tool_started
                ) * 1000,
                status=tool_status,
                output={
                    "status": tool_status,
                    "characters_returned": len(readme),
                },
            ))

            if tool_status == "error":
                raise RuntimeError(readme)

            tool_result = types.Content(
                role="user",
                parts=[
                    types.Part.from_function_response(
                        name="read_project_readme",
                        response={"content": readme},
                    )
                ],
            )

            second = recorded_llm_call(
                client,
                [
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=TASK)],
                    ),
                    first.candidates[0].content,
                    tool_result,
                ],
                types.GenerateContentConfig(
                    system_instruction=(
                        "Analyze only the supplied project "
                        "information. Be concise and factual."
                    ),
                ),
                "Gemini: Analyze project",
            )

            answer = second.text or ""
            if not answer:
                raise RuntimeError("Gemini returned no answer")

            trace.status = "ok"
            agent.status = "ok"
            agent.output = {
                "answer_length": len(answer),
                "result": "Analysis completed",
            }

            print("\nGEMINI ANSWER:\n")
            print(answer)

    except Exception as exc:
        trace.status = "error"
        agent.status = "error"
        agent.output = {
            "error_type": type(exc).__name__,
            "message": str(exc)[:300],
        }
        print("\nAgent failed:", exc)

    finally:
        agent.duration_ms = (
            time.perf_counter() - started
        ) * 1000

        stored = NoMore500sClient(
            api_url="http://localhost:8000",
            trust_environment=False,
        ).send_trace(trace)

        print("\nTelemetry submitted")
        print("Trace ID:", stored["trace_id"])
        print("Status:", trace.status)


if __name__ == "__main__":
    main()
