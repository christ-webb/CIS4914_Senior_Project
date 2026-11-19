"""Read-only Codex integration preflight (`nm5-codex-doctor`).

Separates *configured* (files, trust entries, services) from *observed live*
(the collector actually accepted events from a surface). Never edits Codex
config, hook trust, collector state, or history state. Output contains no
tokens, prompts, or session ids.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from nomore500s_collector.codex_surface import CODEX_SURFACES
from nomore500s_collector.hook import collector_token, collector_url
from nomore500s_collector.install import (
    CODEX_EVENTS_V153,
    default_codex_home,
    hooks_path,
    is_nm5_handler,
)
from nomore500s_collector.paths import collector_home, sqlite_path

# Verified by inspecting the installed binaries and a live `codex exec` probe.
CAPABILITIES = {
    "lifecycle hooks (SessionStart/End, UserPromptSubmit, Stop, Interrupt)": "supported",
    "tool hooks (PreToolUse/PostToolUse with tool_use_id, turn_id)": "supported",
    "tool exit status": "unavailable (tool_response is free text)",
    "model request hooks (PreModelRequest/PostModelRequest)": "absent from installed binaries",
    "surface attribution": "rollout session_meta.originator via transcript_path",
    "token usage / cost": "unavailable via hooks (Codex OTel export is the upgrade path)",
}


QUIET_STATUSES = frozenset({"accepted", "duplicate_occurrence", "duplicate_delivery"})


def _snake(event: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()


@dataclass
class Check:
    name: str
    ok: bool | None  # None = informational / not verifiable
    detail: str


@dataclass
class Report:
    configured: list[Check] = field(default_factory=list)
    live: list[Check] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)

    @property
    def configured_ready(self) -> bool:
        return all(item.ok is not False for item in self.configured)


def _run(argv: list[str], timeout: float = 8.0) -> str | None:
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (done.stdout or done.stderr or "").strip()


def _http_json(url: str, token: str | None = None) -> tuple[int | None, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    try:
        with urlopen(Request(url, headers=headers), timeout=2.0) as response:
            return int(getattr(response, "status", 200)), json.loads(response.read() or b"null")
    except HTTPError as error:
        return error.code, None
    except (URLError, OSError, ValueError):
        return None, None


def ide_binaries(home: Path | None = None) -> list[Path]:
    base = home or Path.home()
    found: list[Path] = []
    for root in (base / ".cursor" / "extensions", base / ".vscode" / "extensions"):
        if not root.is_dir():
            continue
        for ext in sorted(root.glob("openai.chatgpt-*")):
            for candidate in sorted((ext / "bin").glob("*/codex")):
                if candidate.is_file():
                    found.append(candidate)
    return found


def _wrapper_python(command: Path) -> Path | None:
    try:
        text = command.read_text(encoding="utf-8", errors="replace")[:8192]
    except OSError:
        return None
    match = re.search(r"^PYTHON=(?:'([^']+)'|(\S+))$", text, re.MULTILINE)
    if not match:
        return None
    return Path(match.group(1) or match.group(2))


def check_hooks(codex_home: Path, report: Report) -> list[tuple[str, int, int, str]]:
    path = hooks_path(codex_home)
    handlers: list[tuple[str, int, int, str]] = []
    if not path.is_file():
        report.configured.append(
            Check("hooks.json", False, f"missing at {path} (run nm5-codex-install-hooks)")
        )
        return handlers
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        hooks = document.get("hooks") or {}
    except (OSError, ValueError, AttributeError):
        report.configured.append(Check("hooks.json", False, "unreadable or invalid JSON"))
        return handlers
    report.configured.append(Check("hooks.json", True, str(path)))
    missing = []
    for event in CODEX_EVENTS_V153:
        found = False
        for g_index, group in enumerate(hooks.get(event) or []):
            if not isinstance(group, dict):
                continue
            for h_index, handler in enumerate(group.get("hooks") or []):
                if isinstance(handler, dict) and is_nm5_handler(handler):
                    handlers.append((event, g_index, h_index, str(handler.get("command") or "")))
                    found = True
        if not found:
            missing.append(event)
    report.configured.append(
        Check(
            "nm5 handlers registered",
            not missing,
            "all lifecycle/tool events" if not missing else "missing: " + ", ".join(missing),
        )
    )
    commands = {item[3] for item in handlers}
    for command in sorted(commands):
        target = Path(command)
        if not target.is_absolute():
            report.configured.append(
                Check(
                    "hook command",
                    False,
                    f"relative command {command!r} (Codex/IDE PATH will not resolve it; reinstall)",
                )
            )
            continue
        executable = target.is_file() and os.access(target, os.X_OK)
        report.configured.append(
            Check(
                "hook command",
                executable,
                f"{target} {'executable' if executable else 'missing or not executable'}",
            )
        )
        python = _wrapper_python(target) if executable else None
        if python is not None:
            ok = python.is_file() and os.access(python, os.X_OK)
            report.configured.append(
                Check("hook interpreter", ok, f"{python} {'present' if ok else 'missing'}")
            )
    return handlers


def check_trust(
    codex_home: Path, handlers: list[tuple[str, int, int, str]], report: Report
) -> None:
    if not handlers:
        return
    config = codex_home / "config.toml"
    try:
        state = tomllib.loads(config.read_text(encoding="utf-8")).get("hooks", {}).get("state", {})
    except (OSError, tomllib.TOMLDecodeError):
        state = {}
    path = str(hooks_path(codex_home))
    untrusted = []
    for event, g_index, h_index, _ in handlers:
        key = f"{path}:{_snake(event)}:{g_index}:{h_index}"
        entry = state.get(key) if isinstance(state, dict) else None
        if not (isinstance(entry, dict) and entry.get("trusted_hash")):
            untrusted.append(event)
    if untrusted:
        report.configured.append(
            Check(
                "hook trust",
                False,
                "untrusted: " + ", ".join(untrusted)
                + " — open `codex`, run /hooks and trust the No More 500s collector hooks",
            )
        )
    else:
        report.configured.append(
            Check(
                "hook trust",
                True,
                "trust entries present (hash not verifiable offline; re-trust after any reinstall)",
            )
        )


def check_binaries(report: Report) -> None:
    cli = shutil.which("codex")
    if cli is None:
        report.configured.append(Check("codex CLI", False, "not on PATH"))
    else:
        version = _run([cli, "--version"]) or "unknown"
        report.configured.append(Check("codex CLI", True, f"{version} at {cli}"))
        features = _run([cli, "features", "list"]) or ""
        hooks_on = bool(re.search(r"^hooks\s+\S+\s+true\b", features, re.MULTILINE))
        report.configured.append(
            Check("codex hooks feature", hooks_on, "enabled" if hooks_on else "disabled or unknown")
        )
    ide = ide_binaries()
    if not ide:
        report.info["ide_extension"] = "no openai.chatgpt extension found in Cursor or VS Code"
    for binary in ide:
        version = _run([str(binary), "--version"]) or "unknown"
        host = "cursor" if "/.cursor/" in str(binary) else "vscode"
        report.configured.append(
            Check(
                f"Codex IDE extension ({host})",
                None,
                f"bundled {version} (shares ~/.codex hooks)",
            )
        )


def check_services(report: Report, api_url: str) -> dict[str, Any] | None:
    base = collector_url()
    status, health = _http_json(f"{base}/health")
    report.configured.append(
        Check(
            "collector reachable",
            status == 200,
            f"{base} -> {status or 'unreachable'}"
            + (f", pending={health.get('pending')}" if isinstance(health, dict) else ""),
        )
    )
    diagnostics: dict[str, Any] | None = None
    if status == 200:
        code, body = _http_json(f"{base}/v1/collector/diagnostics", collector_token())
        if code == 200 and isinstance(body, dict):
            diagnostics = body
            report.configured.append(Check("collector auth", True, "hook token accepted"))
        elif code == 404:
            report.configured.append(
                Check("collector auth", None, "collector predates diagnostics endpoint; restart it")
            )
        else:
            report.configured.append(
                Check("collector auth", False, f"diagnostics returned {code} (token mismatch?)")
            )
        if isinstance(health, dict) and health.get("last_flush_error"):
            report.configured.append(
                Check(
                    "collector delivery", False, "last flush error present (API delivery failing)"
                )
            )
    api_status, _ = _http_json(f"{api_url.rstrip('/')}/health")
    report.configured.append(
        Check("API reachable", api_status == 200, f"{api_url} -> {api_status or 'unreachable'}")
    )
    return diagnostics


def check_history(report: Report) -> None:
    from nomore500s_collector.history_guard import history_home, read_state

    home = history_home()
    if not (home / "state.json").exists():
        report.configured.append(
            Check("history reset", None, "no reset generation; guard inactive")
        )
        return
    try:
        state = read_state(home)
    except (OSError, PermissionError, ValueError) as error:
        report.configured.append(Check("history reset", False, f"state unreadable: {error}"))
        return
    store = str(sqlite_path(collector_home()).resolve())
    ready = state.get("phase") == "ready"
    registered = store in state.get("stores", [])
    activated = store in state.get("activated_stores", [])
    report.configured.append(
        Check(
            "history reset",
            ready and registered and activated,
            f"generation={state.get('generation')} phase={state.get('phase')} "
            f"store_registered={registered} store_activated={activated}; "
            "new Codex sessions need SessionStart(source=startup) observed live",
        )
    )


def check_live(report: Report, diagnostics: dict[str, Any] | None) -> None:
    if diagnostics is None:
        report.live.append(Check("observed live", None, "collector diagnostics unavailable"))
        return
    seen = diagnostics.get("last_accepted") or {}
    for surface in sorted(CODEX_SURFACES):
        last = seen.get(surface)
        report.live.append(
            Check(
                surface,
                True if last else None,
                f"last accepted event {last}" if last else "not yet observed by this collector",
            )
        )
    for item in diagnostics.get("outcomes") or []:
        if item.get("agent") != "codex" or item.get("status") in QUIET_STATUSES:
            continue
        report.live.append(
            Check(
                f"codex {item.get('status')}",
                None,
                f"surface={item.get('surface')} reason={item.get('reason')} "
                f"count={item.get('count')} last={item.get('last_at')}",
            )
        )


def hook_smoke(codex_home: Path, handlers: list[tuple[str, int, int, str]], report: Report) -> None:
    commands = sorted({item[3] for item in handlers if Path(item[3]).is_absolute()})
    if not commands:
        report.configured.append(Check("hook smoke", False, "no absolute hook command to run"))
        return
    payload = json.dumps(
        {"hook_event_name": "Nm5DoctorProbe", "session_id": f"nm5-doctor-{uuid.uuid4()}"}
    )
    try:
        done = subprocess.run(
            [commands[0]], input=payload, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError) as error:
        report.configured.append(Check("hook smoke", False, f"wrapper failed to run: {error}"))
        return
    ok = done.returncode == 0 and done.stdout.strip() == "{}"
    report.configured.append(
        Check(
            "hook smoke",
            ok,
            "wrapper ran and returned {} (an unsupported probe event; creates no run)"
            if ok
            else f"exit={done.returncode} stdout={done.stdout.strip()[:40]!r}",
        )
    )


def run_doctor(
    *, codex_home: Path | None = None, api_url: str | None = None, smoke: bool = False
) -> Report:
    home = codex_home or default_codex_home()
    report = Report()
    check_binaries(report)
    handlers = check_hooks(home, report)
    check_trust(home, handlers, report)
    if smoke:
        hook_smoke(home, handlers, report)
    diagnostics = check_services(report, api_url or os.environ.get("NM5_API_URL", "http://127.0.0.1:8000"))
    check_history(report)
    check_live(report, diagnostics)
    report.info["capabilities"] = CAPABILITIES
    return report


def render(report: Report) -> str:
    def mark(value: bool | None) -> str:
        return {True: "ok  ", False: "FAIL", None: "info"}[value]

    lines = ["Codex integration — configuration (read-only)"]
    lines += [f"  [{mark(c.ok)}] {c.name}: {c.detail}" for c in report.configured]
    lines.append("")
    lines.append("Observed live (requires a real Codex session; configuration alone is not proof)")
    lines += [f"  [{mark(c.ok)}] {c.name}: {c.detail}" for c in report.live]
    lines.append("")
    lines.append("Capabilities on installed versions")
    lines += [f"  - {k}: {v}" for k, v in report.info.get("capabilities", {}).items()]
    if report.info.get("ide_extension"):
        lines.append(f"  - IDE: {report.info['ide_extension']}")
    lines.append("")
    lines.append(
        "Configuration: " + ("READY" if report.configured_ready else "NOT READY (see FAIL lines)")
    )
    return "\n".join(lines)


def as_json(report: Report) -> dict[str, Any]:
    return {
        "configured_ready": report.configured_ready,
        "configured": [vars(c) for c in report.configured],
        "live": [vars(c) for c in report.live],
        "info": report.info,
    }
