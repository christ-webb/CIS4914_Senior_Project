
import os
from pathlib import Path

from google import genai
from google.genai import types
from nomore500s import NoMore500sClient

ROOT = Path(__file__).resolve().parents[2]
MODEL = "gemini-3.5-flash-lite"

TASK = (
    "Analyze the No More 500s software project. "
    "Read its README, then explain its purpose, "
    "architecture, and current limitations."
)


def read_project_readme():
    """Read the local senior design README."""
    return (ROOT / "README.md").read_text()[:12000]


def recorded_llm_call(run, client, contents, config, name):
    with run.llm(
        name,
        attributes={
            "gen_ai.provider": "google",
            "gen_ai.request.model": MODEL,
        },
    ) as span:
        response = client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=config,
        )
        usage = response.usage_metadata
        span.token_count = (usage.total_token_count or 0) if usage else 0
        span.output = {
            "tool_requested": bool(response.function_calls),
            "response_characters": len(response.text or "")
            if not response.function_calls else 0,
        }
        return response


def main():
    api_url = os.environ.get("NM5_API_URL", "http://localhost:8000")
    telemetry = NoMore500sClient(api_url=api_url, trust_environment=False)
    with telemetry.start_run(
        project_id="gemini-agent-demo",
        name="Gemini Project Research Agent",
        agent_name="Analyze project with Gemini",
        agent_input={"task": TASK},
        attributes={"gen_ai.provider": "google", "gen_ai.request.model": MODEL},
    ) as run:
        with genai.Client() as client:
            first = recorded_llm_call(
                run,
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

            with run.tool("read_project_readme") as tool:
                readme = read_project_readme()
                tool.output = {
                    "status": "ok",
                    "characters_returned": len(readme),
                }

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
                run,
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

            print("\nGEMINI ANSWER:\n")
            print(answer)

        print("\nTelemetry submitted")
        print("Trace ID:", run.trace_id)
        if run.telemetry_errors:
            print("Telemetry delivery issues:", len(run.telemetry_errors))


if __name__ == "__main__":
    main()
