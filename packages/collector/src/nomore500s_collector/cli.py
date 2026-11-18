from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import uvicorn

from nomore500s_collector.hook import run_hook
from nomore500s_collector.install import default_codex_home, install_hooks, uninstall_hooks
from nomore500s_collector.install_cursor import (
    default_cursor_home,
    install_cursor_hooks,
    uninstall_cursor_hooks,
)
from nomore500s_collector.install_vscode import (
    collector_token_value,
    default_copilot_home,
    enable_vscode_otel,
    install_vscode_hooks,
    restore_vscode_otel,
    uninstall_vscode_hooks,
    vscode_user_settings_path,
)
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
    return run_hook(source="codex")


def cursor_hook_main() -> int:
    return run_hook(source="cursor")


def vscode_hook_main() -> int:
    return run_hook(source="vscode-copilot")


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


def cursor_install_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install No More 500s Cursor hooks")
    parser.add_argument("--cursor-home", type=Path, default=None)
    parser.add_argument("--project", type=Path, default=None)
    parser.add_argument("--command", default=None)
    args = parser.parse_args(argv)
    path = install_cursor_hooks(
        project=args.project,
        cursor_home=args.cursor_home or default_cursor_home(),
        command=args.command,
    )
    print(f"Installed Cursor collector hooks in {path}")
    print("Hooks are observe-only and always return {} with exit 0.")
    print("Permission-sensitive hooks are not registered so Cursor keeps its own allow/deny flow.")
    print("User-level hooks apply locally; cloud agents require project .cursor/hooks.json.")
    print("Set NM5_HOOK_DEBUG=1 for bounded wrapper and Python hook diagnostics.")
    return 0


def cursor_uninstall_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remove No More 500s Cursor hooks")
    parser.add_argument("--cursor-home", type=Path, default=None)
    parser.add_argument("--project", type=Path, default=None)
    args = parser.parse_args(argv)
    path = uninstall_cursor_hooks(
        project=args.project,
        cursor_home=args.cursor_home or default_cursor_home(),
    )
    print(f"Removed Cursor collector hooks from {path}")
    return 0


def vscode_install_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install No More 500s VS Code Copilot hooks")
    parser.add_argument("--copilot-home", type=Path, default=None)
    parser.add_argument("--project", type=Path, default=None)
    parser.add_argument("--command", default=None)
    parser.add_argument("--enable-otel", action="store_true")
    parser.add_argument("--otlp-path-token", action="store_true")
    parser.add_argument("--vscode-user-dir", type=Path, default=None)
    parser.add_argument("--collector-url", default="http://127.0.0.1:8788")
    args = parser.parse_args(argv)
    path = install_vscode_hooks(
        copilot_home=args.copilot_home or default_copilot_home(),
        project=args.project,
        command=args.command,
    )
    print(f"Installed GitHub Copilot collector hooks in {path}")
    print("This monitors GitHub Copilot agent sessions only, not other VS Code agents.")
    print("Default is hook-only. OTel model/token spans require --enable-otel.")
    if args.enable_otel:
        settings_path = vscode_user_settings_path(args.vscode_user_dir)
        enable_vscode_otel(
            settings_path=settings_path,
            collector_url=args.collector_url,
            token=collector_token_value(),
            path_token=args.otlp_path_token,
        )
        print(f"Enabled Copilot OTel export in {settings_path}")
        print("Content capture remains disabled unless you already authorized it.")
        if args.otlp_path_token:
            print("Using opt-in localhost path-token OTLP auth. Prefer header auth when possible.")
        else:
            print("OTLP auth uses github.copilot.chat.otel.headers Authorization Bearer.")
        print("Set NM5_OTLP_PATH_TOKEN=true on the collector if using --otlp-path-token.")
    return 0


def vscode_uninstall_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remove No More 500s VS Code Copilot hooks")
    parser.add_argument("--copilot-home", type=Path, default=None)
    parser.add_argument("--project", type=Path, default=None)
    parser.add_argument("--vscode-user-dir", type=Path, default=None)
    parser.add_argument("--restore-otel", action="store_true", default=True)
    parser.add_argument("--keep-otel", action="store_true")
    args = parser.parse_args(argv)
    path = uninstall_vscode_hooks(
        copilot_home=args.copilot_home or default_copilot_home(),
        project=args.project,
    )
    print(f"Removed GitHub Copilot collector hooks from {path}")
    if args.restore_otel and not args.keep_otel:
        settings_path = vscode_user_settings_path(args.vscode_user_dir)
        if settings_path.exists():
            restore_vscode_otel(settings_path=settings_path)
            print(f"Restored previous Copilot OTel settings in {settings_path}")
    return 0
