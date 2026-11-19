"""Codex surface attribution (CLI, exec, IDE extension).

Codex hook payloads do not say which client produced them. They do include
``transcript_path``, the rollout file Codex itself writes, whose first line is a
``session_meta`` record carrying the provider-generated ``originator``
(``codex-tui``, ``codex_exec``, ``codex_vscode``...). Only that header line is
read, with a hard size cap, and only allowlisted scalar fields are kept: no
instructions, prompts, or other session content leave this module.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MAX_HEADER_BYTES = 256 * 1024
ORIGINATOR_FIELD = "_nm5_codex_originator"
VERSION_FIELD = "_nm5_codex_cli_version"
LAUNCHER_FIELD = "_nm5_launcher"

SURFACE_BY_ORIGINATOR = {
    "codex-tui": "codex-cli",
    "codex_cli_rs": "codex-cli",
    "codex_exec": "codex-exec",
    "codex_vscode": "codex-ide",
}
CODEX_SURFACES = frozenset({"codex-cli", "codex-exec", "codex-ide"})
UNKNOWN_SURFACE = "codex"

_SAFE_VALUE = re.compile(r"^[A-Za-z0-9._+\-]{1,64}$")
_ORIGINATOR_RE = re.compile(rb'"originator"\s*:\s*"([A-Za-z0-9._+\-]{1,64})"')
_VERSION_RE = re.compile(rb'"cli_version"\s*:\s*"([A-Za-z0-9._+\-]{1,64})"')


def _safe(value: Any) -> str | None:
    if isinstance(value, str) and _SAFE_VALUE.match(value):
        return value
    return None


def read_session_header(transcript_path: Any) -> dict[str, str]:
    """Return ``{"originator", "cli_version"}`` (subset) from a rollout header."""
    if not isinstance(transcript_path, str) or not transcript_path:
        return {}
    try:
        path = Path(transcript_path).expanduser()
        if not path.is_file():
            return {}
        with path.open("rb") as handle:
            chunk = handle.read(MAX_HEADER_BYTES)
    except OSError:
        return {}
    first, newline, _ = chunk.partition(b"\n")
    result: dict[str, str] = {}
    if newline:
        try:
            record = json.loads(first.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            record = None
        if isinstance(record, dict) and record.get("type") == "session_meta":
            payload = record.get("payload")
            if isinstance(payload, dict):
                for key in ("originator", "cli_version"):
                    value = _safe(payload.get(key))
                    if value:
                        result[key] = value
                return result
        if record is not None:
            return {}
    # Oversized or truncated header line: fall back to bounded field matching.
    for key, pattern in (("originator", _ORIGINATOR_RE), ("cli_version", _VERSION_RE)):
        match = pattern.search(first)
        if match:
            result[key] = match.group(1).decode("ascii")
    return result


def stamp_surface(payload: dict[str, Any]) -> None:
    if ORIGINATOR_FIELD in payload:
        return
    header = read_session_header(payload.get("transcript_path"))
    if header.get("originator"):
        payload[ORIGINATOR_FIELD] = header["originator"]
    if header.get("cli_version"):
        payload[VERSION_FIELD] = header["cli_version"]


def surface_for(payload: dict[str, Any]) -> str:
    originator = payload.get(ORIGINATOR_FIELD)
    if isinstance(originator, str):
        return SURFACE_BY_ORIGINATOR.get(originator, UNKNOWN_SURFACE)
    return UNKNOWN_SURFACE
