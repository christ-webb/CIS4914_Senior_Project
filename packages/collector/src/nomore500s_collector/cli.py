from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import uvicorn

from nomore500s_collector.hook import run_hook
from nomore500s_collector.install import default_codex_home, install_hooks, uninstall_hooks
from nomore500s_collector.server import CollectorApp, create_app
from nomore500s_collector.settings import CollectorSettings


def collector_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="No More 500s external collector")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = CollectorSettings()
    if args.host:
        settings.collector_host = args.host
    if args.port:
        settings.collector_port = args.port
    if settings.collector_host not in {"127.0.0.1", "localhost", "::1"}:
        print("collector host must be localhost", file=sys.stderr)
        return 2
    runtime = CollectorApp(settings)
    app = create_app(runtime)
    uvicorn.run(app, host=settings.collector_host, port=settings.collector_port, log_level="info")
    return 0


def hook_main() -> int:
    return run_hook()


def install_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install No More 500s Codex hooks")
    parser.add_argument("--codex-home", type=Path, default=None)
    parser.add_argument("--command", default=None)
    parser.add_argument(
        "--include-model-hooks",
        action="store_true",
        help="Also register PreModelRequest/PostModelRequest (Codex >= 0.164)",
    )
    args = parser.parse_args(argv)
    path = install_hooks(
        codex_home=args.codex_home or default_codex_home(),
        command=args.command,
        include_model_hooks=args.include_model_hooks,
    )
    print(f"Installed collector hooks in {path}")
    print("Review and trust them in Codex with /hooks if prompted.")
    print("SessionEnd is never treated as optional Stop; Stop does not end the run.")
    return 0


def uninstall_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remove No More 500s Codex hooks")
    parser.add_argument("--codex-home", type=Path, default=None)
    args = parser.parse_args(argv)
    path = uninstall_hooks(codex_home=args.codex_home or default_codex_home())
    print(f"Removed collector hooks from {path}")
    return 0
