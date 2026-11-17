from __future__ import annotations

import secrets
from pathlib import Path

from nomore500s_collector.paths import ensure_home, token_path


def load_or_create_token(home: Path | None = None, provided: str | None = None) -> str:
    if provided:
        return provided
    directory = ensure_home(home)
    path = token_path(directory)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    path.chmod(0o600)
    return token


def read_token(home: Path | None = None) -> str | None:
    path = token_path(home)
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None
