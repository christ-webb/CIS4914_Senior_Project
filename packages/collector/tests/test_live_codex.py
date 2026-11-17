from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from no_more_500s_api.main import create_app
from no_more_500s_api.services.trace_service import InMemoryTraceRepository
from nomore500s_collector.install import install_hooks
from nomore500s_collector.server import CollectorApp
from nomore500s_collector.server import create_app as create_collector
from nomore500s_collector.settings import CollectorSettings

pytestmark = pytest.mark.skipif(
    os.environ.get("NM5_TEST_CODEX") != "1" or shutil.which("codex") is None,
    reason="live Codex test requires NM5_TEST_CODEX=1 and a codex binary",
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _serve(app: object, port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.05)
    return server


def test_codex_exec_session_appears_on_api(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello from no more 500s\n", encoding="utf-8")
    codex_home = tmp_path / "codex-home"
    collector_home = tmp_path / "collector"
    codex_home.mkdir()
    collector_home.mkdir()
    user_codex = Path.home() / ".codex"
    if (user_codex / "auth.json").is_file():
        shutil.copy2(user_codex / "auth.json", codex_home / "auth.json")
    (codex_home / "config.toml").write_text('model = "gpt-5"\n', encoding="utf-8")

    api_port = _free_port()
    collector_port = _free_port()
    wrapper = collector_home / "nm5-codex-hook.sh"
    wrapper.write_text(
        "#!/bin/sh\n"
        f"export NM5_COLLECTOR_URL='http://127.0.0.1:{collector_port}'\n"
        "export NM5_COLLECTOR_TOKEN='live-token'\n"
        f"export NM5_COLLECTOR_HOME='{collector_home}'\n"
        f"exec '{sys.executable}' -m nomore500s_collector.hook\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    install_hooks(codex_home=codex_home, command=str(wrapper))

    repository = InMemoryTraceRepository()
    api_server = _serve(create_app(repository), api_port)
    settings = CollectorSettings(
        collector_home=collector_home,
        collector_token="live-token",
        api_url=f"http://127.0.0.1:{api_port}",
        collector_port=collector_port,
    )
    runtime = CollectorApp(settings)
    collector_server = _serve(create_collector(runtime), collector_port)
    try:
        import httpx

        health = httpx.get(f"http://127.0.0.1:{collector_port}/health", timeout=2)
        assert health.status_code == 200
        env = os.environ.copy()
        env["CODEX_HOME"] = str(codex_home)
        completed = subprocess.run(
            [
                "codex",
                "exec",
                "--dangerously-bypass-hook-trust",
                "--skip-git-repo-check",
                "Reply with the word pong and list files in this directory.",
            ],
            cwd=workspace,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        traces = []
        for _ in range(40):
            runtime.pipeline.flush_once()
            traces = httpx.get(f"http://127.0.0.1:{api_port}/v1/traces", timeout=2).json()
            if traces:
                break
            time.sleep(0.25)
        assert traces, (
            "no traces captured from a live Codex session\n"
            f"exit={completed.returncode}\n"
            f"stdout={completed.stdout[-2000:]}\n"
            f"stderr={completed.stderr[-2000:]}"
        )
        trace = traces[0]
        assert trace["source"] == "cli"
        assert trace["agent_framework"] == "codex"
        detail = httpx.get(f"http://127.0.0.1:{api_port}/v1/traces/{trace['trace_id']}", timeout=2)
        assert detail.status_code == 200
        body = detail.json()
        assert any(span["type"] == "agent" for span in body["spans"])
        collector_server.should_exit = True
        runtime.close()
        restarted = CollectorApp(settings)
        restarted.pipeline.flush_once()
        again = httpx.get(f"http://127.0.0.1:{api_port}/v1/traces/{trace['trace_id']}", timeout=2)
        assert again.status_code == 200
        restarted.close()
    finally:
        api_server.should_exit = True
        collector_server.should_exit = True
