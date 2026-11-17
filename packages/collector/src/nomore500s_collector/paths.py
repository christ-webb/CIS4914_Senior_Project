from __future__ import annotations

import os
from pathlib import Path


def collector_home(override: str | Path | None = None) -> Path:
    if override is not None:
        return Path(override).expanduser()
    env = os.environ.get("NM5_COLLECTOR_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".nomore500s" / "collector"


def token_path(home: Path | None = None) -> Path:
    return (home or collector_home()) / "token"


def sqlite_path(home: Path | None = None) -> Path:
    return (home or collector_home()) / "collector.sqlite"


def ensure_home(home: Path | None = None) -> Path:
    path = home or collector_home()
    path.mkdir(parents=True, exist_ok=True)
    return path
