"""Local-only maintenance coordination. No HTTP administration or browser secrets.

A fixed-size Bloom filter retains deny identities indefinitely: false positives
fail closed; there are no false negatives and cleanup never reauthorizes an ID.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, BinaryIO

FILTER_BYTES = 131072


def identity_bits(namespace: str, identity: str) -> list[int]:
    digest = hashlib.sha256(f"{namespace}\0{identity}".encode()).digest()
    return [int.from_bytes(digest[i : i + 4], "big") % (FILTER_BYTES * 8) for i in range(0, 24, 4)]


def remember(bits: bytearray, namespace: str, identity: str) -> None:
    for bit in identity_bits(namespace, identity):
        bits[bit // 8] |= 1 << (bit % 8)


def remembered(bits: bytes | bytearray, namespace: str, identity: str) -> bool:
    return all(bits[bit // 8] & (1 << (bit % 8)) for bit in identity_bits(namespace, identity))


def history_home() -> Path:
    return Path(os.environ.get("NM5_HISTORY_HOME", str(Path.home() / ".nomore500s/history")))


def private_directory(home: Path, *, create: bool = False) -> None:
    if create:
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = home.lstat()
    if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError("History directory must be owned by this account with mode 0700")


def read_state(home: Path) -> dict[str, Any]:
    private_directory(home)
    path = home / "state.json"
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError("History state must be a private, account-owned regular file")
    result: dict[str, Any] = json.loads(path.read_text())
    return result


def write_state(home: Path, state: dict[str, Any]) -> None:
    private_directory(home)
    temp = home / "state.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(state, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temp, home / "state.json")
    directory = os.open(home, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def acquire_lock(path: Path, *, exclusive: bool) -> BinaryIO:
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    handle = os.fdopen(fd, "a+b")
    try:
        fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError(
            "A collector/API/store is still running; stop it before maintenance"
        ) from None
    return handle


def runtime_lock(
    *, store: Path | None = None, api_url: str | None = None, database: str | None = None
) -> BinaryIO | None:
    home = history_home()
    # Once initialized, protection cannot be disabled by unsetting the enable flag.
    if not (home / "state.json").exists():
        return None
    state = read_state(home)
    handle = acquire_lock(home / "maintenance.lock", exclusive=False)
    try:
        state = read_state(home)
        if state["phase"] != "ready":
            raise RuntimeError("History reset requires recovery before ingestion can restart")
        if store is not None and (
            str(store.resolve()) not in state["stores"] or api_url != state["api_url"]
        ):
            raise RuntimeError("Unregistered collector store or API target; register it before use")
        if database is not None and database != state["database"]:
            raise RuntimeError("API database differs from registered history target")
        with acquire_lock(home / "registration.lock", exclusive=True):
            state = read_state(home)
            if store is not None:
                state["activated_stores"] = sorted(
                    set(state.get("activated_stores", [])) | {str(store.resolve())}
                )
            if database is not None:
                state["activated_api"] = True
            write_state(home, state)
        return handle
    except Exception:
        handle.close()
        raise
