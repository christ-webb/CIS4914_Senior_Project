"""Authenticated OS-local history CLI; intentionally no destructive HTTP route."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sqlite3
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import clickhouse_connect  # type: ignore[import-untyped]
from nomore500s_collector.history_guard import (
    FILTER_BYTES,
    acquire_lock,
    history_home,
    private_directory,
    read_state,
    remember,
    write_state,
)

from no_more_500s_api.core.config import Settings

TABLES = ("spans", "traces", "run_events")
STORE_TABLES = (
    "pending",
    "deliveries",
    "occurrences",
    "open_spans",
    "span_identities",
    "identity_links",
    "sessions",
)
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def enabled(settings: Settings) -> None:
    if os.environ.get("NM5_LOCAL_HISTORY_ENABLED") != "1":
        raise ValueError("Set NM5_LOCAL_HISTORY_ENABLED=1 for this local development operation")
    if settings.api_host not in LOOPBACK or settings.clickhouse_host not in LOOPBACK:
        raise ValueError("API and ClickHouse must use explicit loopback hosts")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", settings.clickhouse_database):
        raise ValueError("Invalid database identifier")


def store_connection(path: str) -> sqlite3.Connection:
    location = Path(path)
    info = location.lstat()
    if location.is_symlink() or info.st_uid != os.getuid():
        raise PermissionError("Collector stores must be existing account-owned regular files")
    db = sqlite3.connect(f"file:{location}?mode=rw", uri=True)
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {*STORE_TABLES, "meta"}.issubset(tables):
        db.close()
        raise ValueError("Unknown collector schema; refusing to modify store")
    return db


def initialize(home: Path, settings: Settings, stores: list[str]) -> None:
    enabled(settings)
    private_directory(home, create=True)
    with acquire_lock(home / "maintenance.lock", exclusive=True):
        if (home / "state.json").exists():
            raise ValueError(
                "History management is already initialized; state must never be replaced"
            )
        if not stores:
            raise ValueError("Explicit --collector-store paths are required")
        resolved = sorted({str(Path(path).expanduser().resolve()) for path in stores})
        for path in resolved:
            with store_connection(path) as db:
                db.execute("SELECT COUNT(*) FROM pending").fetchone()
        write_state(
            home,
            {
                "database": settings.clickhouse_database,
                "host": settings.clickhouse_host,
                "port": settings.clickhouse_port,
                "api_url": f"http://127.0.0.1:{settings.api_port}",
                "stores": resolved,
                "generation": 0,
                "phase": "ready",
                "boundary": None,
                "deny_traces": "",
                "journal": [],
            },
        )


def ensure_stopped(state: dict[str, Any]) -> None:
    url = urlparse(state["api_url"])
    try:
        with socket.create_connection((url.hostname or "127.0.0.1", url.port or 8000), timeout=0.3):
            raise RuntimeError("Stop the API before clearing history")
    except (ConnectionRefusedError, TimeoutError):
        pass


def clear_history(
    home: Path, settings: Settings, client: Any, *, confirm: bool, check_stopped: bool = True
) -> dict[str, Any]:
    enabled(settings)
    private_directory(home)
    with ExitStack() as stack:
        stack.enter_context(acquire_lock(home / "maintenance.lock", exclusive=True))
        state = read_state(home)
        if (state["database"], state["host"], state["port"]) != (
            settings.clickhouse_database,
            settings.clickhouse_host,
            settings.clickhouse_port,
        ):
            raise ValueError("Configured target differs from registered database")
        if check_stopped:
            ensure_stopped(state)
            if not state.get("activated_api") or set(state.get("activated_stores", [])) != set(
                state["stores"]
            ):
                raise RuntimeError(
                    "Restart registered API/collectors, then stop them before reset"
                )
        stores = []
        for path in state["stores"]:
            stack.enter_context(acquire_lock(Path(path + ".runtime.lock"), exclusive=True))
            db = store_connection(path)
            stack.callback(db.close)
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN EXCLUSIVE")
            stores.append((path, db))
        # Only a known schema is eligible; never run arbitrary names from client input.
        existing = {row[0] for row in client.query("SHOW TABLES").result_rows}
        if not set(TABLES).issubset(existing):
            raise ValueError("Required application tables are missing")
        counts = {
            table: client.query(f"SELECT count() FROM {table}").result_rows[0][0]
            for table in TABLES
        }
        if not confirm:
            print(
                json.dumps(
                    {
                        "database": state["database"],
                        "stores": state["stores"],
                        "records": counts,
                        "phase": state["phase"],
                    },
                    indent=2,
                )
            )
            if input("Permanently delete local history? Type CLEAR LOCAL RUN HISTORY: ") != (
                "CLEAR LOCAL RUN HISTORY"
            ):
                raise ValueError("Confirmation did not match; nothing deleted")
        if state["phase"] == "ready":
            state["generation"] += 1
            state["boundary"] = datetime.now(UTC).isoformat()
            state["operation"] = str(uuid4())
            state["phase"] = "prepared"
            state["counts"] = counts
            # Capture deny identities before any deletion. The filter is cumulative.
            bits = (
                bytearray.fromhex(state["deny_traces"])
                if state["deny_traces"]
                else bytearray(FILTER_BYTES)
            )
            for table in ("traces", "run_events", "spans"):
                for row in client.query(
                    f"SELECT DISTINCT toString(trace_id) FROM {table}"
                ).result_rows:
                    remember(bits, "trace", str(row[0]))
            state["deny_traces"] = bits.hex()
            write_state(home, state)
        # SQLite commit and ClickHouse truncation are deliberately separate recoverable phases.
        for path, db in stores:
            prior = dict(db.execute("SELECT key, value FROM meta"))
            bits = (
                bytearray.fromhex(prior["history_deny"])
                if prior.get("history_deny")
                else bytearray(FILTER_BYTES)
            )
            for session, agent, trace in db.execute(
                "SELECT session_id, agent, trace_id FROM sessions"
            ):
                remember(bits, agent, session)
                remember(bits, agent, session.removeprefix(f"{agent}:"))
                remember(bits, "trace", trace)
            for agent, source, canonical in db.execute(
                "SELECT agent, source_id, canonical_id FROM identity_links"
            ):
                remember(bits, agent, source)
                remember(bits, agent, canonical)
            for key, value in {
                "history_deny": bits.hex(),
                "history_boundary": state["boundary"],
                "history_generation": str(state["generation"]),
            }.items():
                db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (key, value))
            for table in STORE_TABLES:
                db.execute(f"DELETE FROM {table}")
            db.commit()
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if any(
                db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in STORE_TABLES
            ):
                raise RuntimeError(f"Store verification failed: {path}")
        state["phase"] = "collectors_cleared"
        write_state(home, state)
        for table in TABLES:
            client.command(f"TRUNCATE TABLE {table} SYNC")
        if any(client.query(f"SELECT count() FROM {table}").result_rows[0][0] for table in TABLES):
            raise RuntimeError("ClickHouse verification failed; rerun clear to recover")
        state["journal"] = (
            state["journal"]
            + [
                {
                    "operation": state["operation"],
                    "generation": state["generation"],
                    "boundary": state["boundary"],
                    "completed_at": datetime.now(UTC).isoformat(),
                    "counts": state["counts"],
                }
            ]
        )[-20:]
        state["phase"] = "ready"
        write_state(home, state)
        return {"generation": state["generation"], "phase": "ready", "verified_empty": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="OS-authenticated local history management")
    parser.add_argument("command", choices=["init", "status", "clear"])
    parser.add_argument("--collector-store", action="append", default=[])
    args = parser.parse_args()
    settings, home = Settings(), history_home()
    try:
        if args.command == "init":
            initialize(home, settings, args.collector_store)
            print("Registered local stores. Restart API/collector once to activate writer locks.")
        elif args.command == "status":
            state = read_state(home)
            print(
                json.dumps(
                    {k: state[k] for k in ("phase", "generation", "database", "stores")}, indent=2
                )
            )
        else:
            enabled(settings)
            client = clickhouse_connect.get_client(
                host=settings.clickhouse_host,
                port=settings.clickhouse_port,
                database=settings.clickhouse_database,
                username=settings.clickhouse_user,
                password=settings.clickhouse_password,
            )
            try:
                print(json.dumps(clear_history(home, settings, client, confirm=False)))
            finally:
                client.close()
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print(f"History operation refused or incomplete: {error}")
        return 1
