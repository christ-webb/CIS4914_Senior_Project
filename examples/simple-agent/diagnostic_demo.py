import os
import time

import httpx
from nomore500s import NoMore500sClient


def main() -> None:
    api_url = os.environ.get("NM5_API_URL", "http://localhost:8000")
    delay = float(os.environ.get("NM5_DEMO_DELAY_SECONDS", "0.4"))
    client = NoMore500sClient(
        api_url=api_url,
        trust_environment=False,
        heartbeat_seconds=min(5.0, max(delay, 0.5)),
    )
    with client.start_run(
        project_id="diagnostic-demo",
        name="repeated missing file",
        agent_name="diagnostic-agent",
        agent_input={"task": "read missing path"},
        attributes={"nm5.token_status": "reported", "nm5.cost_status": "reported"},
    ) as run:
        time.sleep(delay)
        with run.tool("search", input={"query": "project overview"}) as search:
            search.output = {"results": ["README.md"]}
            search.token_count = 8
            search.cost_usd = 0.0001
        for _ in range(4):
            time.sleep(delay)
            try:
                with run.tool("read_file", input={"path": "missing.txt"}) as tool:
                    tool.output = {
                        "error": "No such file or directory: missing.txt",
                        "error_type": "FileNotFoundError",
                    }
                    raise FileNotFoundError("No such file or directory: missing.txt")
            except FileNotFoundError:
                pass
        print(f"Diagnostic demo run {run.trace_id} finalized toward {api_url}")

    response = httpx.get(f"{api_url}/v1/traces/{run.trace_id}/diagnostics", timeout=10)
    response.raise_for_status()
    body = response.json()
    print(f"analysis_status={body['analysis_status']} findings={len(body['findings'])}")
    for finding in body["findings"]:
        print(
            f"- {finding['detection_type']} {finding['severity']} "
            f"confidence={finding['confidence']} "
            f"spans={len(finding['span_ids'])}: {finding['title']}"
        )


if __name__ == "__main__":
    main()
