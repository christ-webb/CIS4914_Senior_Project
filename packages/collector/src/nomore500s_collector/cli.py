from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
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


def codex_exec_main(argv: list[str] | None = None) -> int:
    """Optional launcher: `nm5-codex-exec [codex exec args...]`.

    Runs `codex exec` unchanged (stdio passed through) and, after it exits,
    reports the process exit status for the session the hooks observed. Only
    runs started through this launcher get exit-status evidence.
    """
    from nomore500s_collector.hook import LAUNCH_FILE_ENV, LAUNCHER_ENV, post_hook

    args = list(sys.argv[1:] if argv is None else argv)
    codex = os.environ.get("NM5_CODEX_BIN") or shutil.which("codex")
    if not codex:
        print("nm5-codex-exec: codex executable not found on PATH", file=sys.stderr)
        return 127
    with tempfile.TemporaryDirectory(prefix="nm5-codex-exec-") as tmp:
        launch_file = Path(tmp) / "session"
        env = dict(os.environ)
        env[LAUNCHER_ENV] = "nm5-codex-exec"
        env[LAUNCH_FILE_ENV] = str(launch_file)
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            child = subprocess.Popen([codex, "exec", *args], env=env)
            returncode = child.wait()
        finally:
            signal.signal(signal.SIGINT, previous)
        session_id = ""
        try:
            session_id = launch_file.read_text(encoding="utf-8").strip()
        except OSError:
            session_id = ""
        if session_id:
            payload: dict[str, object] = {
                "hook_event_name": "CodexExecExit",
                "session_id": session_id,
                "_nm5_launcher": "nm5-codex-exec",
            }
            if returncode < 0:
                payload["signal"] = -returncode
            else:
                payload["exit_code"] = returncode
            post_hook(payload, str(uuid.uuid4()), source="codex")
        else:
            print(
                "nm5-codex-exec: no Codex SessionStart hook observed; exit status not reported "
                "(are the hooks installed and trusted? run nm5-codex-doctor)",
                file=sys.stderr,
            )
    return returncode if returncode >= 0 else 128 - returncode


def cursor_hook_main() -> int:
    return run_hook(source="cursor")


def vscode_hook_main() -> int:
    return run_hook(source="vscode-copilot")


def install_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install No More 500s Codex hooks")
    parser.add_argument("--codex-home", type=Path, default=None)
    parser.add_argument("--command", default=None)
    parser.add_argument("--include-model-hooks", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true", help="Enable bounded hook debug logging")
    args = parser.parse_args(argv)
    path = install_hooks(
        codex_home=args.codex_home or default_codex_home(),
        command=args.command,
        include_model_hooks=args.include_model_hooks,
        debug=args.debug,
    )
    print(f"Installed collector hooks in {path}")
    print(
        "Hooks changed: open `codex`, run /hooks, and trust the No More 500s collector hooks.\n"
        "Trust is required for interactive codex, codex exec, and the Codex IDE extension\n"
        "(they share ~/.codex). Re-trust after every reinstall. Verify with nm5-codex-doctor."
    )
    return 0


def codex_doctor_main(argv: list[str] | None = None) -> int:
    import json as _json

    from nomore500s_collector.doctor import as_json, render, run_doctor

    parser = argparse.ArgumentParser(description="Read-only Codex integration preflight")
    parser.add_argument("--codex-home", type=Path, default=None)
    parser.add_argument("--api-url", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--hook-smoke",
        action="store_true",
        help="Run the installed wrapper once with an unsupported probe event (creates no run)",
    )
    args = parser.parse_args(argv)
    report = run_doctor(codex_home=args.codex_home, api_url=args.api_url, smoke=args.hook_smoke)
    print(_json.dumps(as_json(report), indent=2) if args.json else render(report))
    return 0 if report.configured_ready else 1


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
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Bake NM5_HOOK_DEBUG=1 into the observe-only wrapper (still overridable)",
    )
    args = parser.parse_args(argv)
    path = install_cursor_hooks(
        project=args.project,
        cursor_home=args.cursor_home or default_cursor_home(),
        command=args.command,
        debug=args.debug,
    )
    print(f"Installed Cursor collector hooks in {path}")
    print("Hooks are observe-only and always return {} with exit 0.")
    print("Permission-sensitive hooks are not registered so Cursor keeps its own allow/deny flow.")
    print("User-level hooks apply locally; cloud agents require project .cursor/hooks.json.")
    print("Set NM5_HOOK_DEBUG=1, or pass --debug, for bounded wrapper and Python hook diagnostics.")
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
