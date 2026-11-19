import os
import time

from nomore500s import NoMore500sClient


def fake_search(query: str) -> list[str]:
    time.sleep(1.5)
    return [f"Result for {query}"]


def main() -> None:
    api_url = os.environ.get("NM5_API_URL", "http://localhost:8000")
    delay = float(os.environ.get("NM5_DEMO_DELAY_SECONDS", "1.5"))
    client = NoMore500sClient(
        api_url=api_url,
        trust_environment=False,
        heartbeat_seconds=min(5.0, max(delay, 0.5)),
    )
    with client.start_run(
        project_id="simple-agent",
        attributes={"nm5.is_fixture": True},
        name="research request",
        agent_name="research-agent",
        agent_input={"query": "agent observability"},
    ) as run:
        time.sleep(delay)
        with run.llm("plan-search", input={"task": "research"}) as llm:
            time.sleep(delay)
            llm.output = {"plan": "search then summarize"}
            llm.token_count = 12
            llm.cost_usd = 0.001
        with run.tool("search", input={"query": "agent observability"}) as tool:
            results = fake_search("agent observability")
            tool.output = {"results": results}
        time.sleep(delay)
        print(f"Live run {run.trace_id} finalized toward {api_url}")
        if run.telemetry_errors:
            print(f"Telemetry delivery issues: {len(run.telemetry_errors)}")


if __name__ == "__main__":
    main()
