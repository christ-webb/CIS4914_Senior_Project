import os
from time import perf_counter

from nomore500s import NoMore500sClient, SpanData, TraceData


def fake_search(query: str) -> list[str]:
    return [f"Result for {query}"]


def main() -> None:
    started = perf_counter()
    results = fake_search("agent observability")
    elapsed_ms = (perf_counter() - started) * 1_000

    trace = TraceData(
        project_id="simple-agent",
        name="research request",
        status="ok",
        spans=[
            SpanData(
                type="agent",
                name="research-agent",
                duration_ms=elapsed_ms + 30,
                status="ok",
            ),
            SpanData(
                type="tool",
                name="search",
                duration_ms=elapsed_ms,
                status="ok",
                input={"query": "agent observability"},
                output={"results": results},
            ),
        ],
    )
    api_url = os.environ.get("NM5_API_URL", "http://localhost:8000")
    stored = NoMore500sClient(api_url=api_url, trust_environment=False).send_trace(trace)
    print(f"Sent trace {stored['trace_id']} to {api_url}")


if __name__ == "__main__":
    main()
