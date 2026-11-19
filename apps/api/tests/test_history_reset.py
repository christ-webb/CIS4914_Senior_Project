"""All destructive assertions use temporary SQLite stores / isolated test databases."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from no_more_500s_api.core.config import Settings
from no_more_500s_api.history import clear_history, initialize
from nomore500s_collector.api_client import LiveRunClient
from nomore500s_collector.creation_evidence import OBSERVED_AT_FIELD, stamp
from nomore500s_collector.history_guard import (
    acquire_lock,
    read_state,
    runtime_lock,
)
from nomore500s_collector.ids import store_session_key
from nomore500s_collector.pipeline import Pipeline
from nomore500s_collector.store import CollectorStore


class FakeClickHouse:
    def __init__(self):
        self.rows = {
            "traces": [str(uuid4())],
            "spans": [str(uuid4())],
            "run_events": [str(uuid4())],
        }
        self.fail = False
        self.unrelated = ["preserved"]

    def query(self, sql):
        if sql == "SHOW TABLES":
            return SimpleNamespace(result_rows=[(name,) for name in self.rows])
        table = sql.split()[-1]
        if "count()" in sql:
            return SimpleNamespace(result_rows=[(len(self.rows[table]),)])
        return SimpleNamespace(result_rows=[(value,) for value in self.rows[table]])

    def command(self, sql):
        if self.fail:
            raise RuntimeError("simulated storage failure")
        self.rows[sql.split()[2]] = []


@pytest.fixture
def local(tmp_path, monkeypatch):
    home = tmp_path / "history"
    monkeypatch.setenv("NM5_HISTORY_HOME", str(home))
    monkeypatch.setenv("NM5_LOCAL_HISTORY_ENABLED", "1")
    store = CollectorStore(tmp_path / "collector.sqlite")
    pipeline = Pipeline(store, LiveRunClient("http://127.0.0.1:8000"))
    pipeline.ingest(
        {"session_id": "old-chat", "hook_event_name": "sessionStart"}, "old", source="cursor"
    )
    store.close()
    settings = Settings(api_host="127.0.0.1", clickhouse_database="isolated_test")
    initialize(home, settings, [str(tmp_path / "collector.sqlite")])
    return home, settings, tmp_path / "collector.sqlite"


def test_reset_recovery_and_old_session_suppression(local):
    home, settings, path = local
    client = FakeClickHouse()
    client.fail = True
    with pytest.raises(RuntimeError, match="simulated"):
        clear_history(home, settings, client, confirm=True, check_stopped=False)
    assert read_state(home)["phase"] == "collectors_cleared"
    with pytest.raises(RuntimeError, match="recovery"):
        runtime_lock(database=settings.clickhouse_database)
    generation = read_state(home)["generation"]
    client.fail = False
    assert clear_history(home, settings, client, confirm=True, check_stopped=False)[
        "verified_empty"
    ]
    assert read_state(home)["generation"] == generation
    assert client.unrelated == ["preserved"]
    for _ in range(2):  # restart never removes the boundary
        store = CollectorStore(path)
        assert store.pending_count() == 0
        assert store._meta("schema_version") is not None
        pipeline = Pipeline(store, LiveRunClient("http://127.0.0.1:8000"))
        now = datetime.now(UTC).isoformat()
        for payload in [
            {
                "session_id": "old-chat",
                "hook_event_name": "sessionStart",
                "session_created_at": now,
            },
            {"session_id": "old-chat", "hook_event_name": "preToolUse"},
            {"hook_event_name": "sessionStart", "session_created_at": now},
            {"session_id": "ambiguous", "hook_event_name": "sessionStart", "timestamp": now},
            {
                "session_id": "delayed",
                "hook_event_name": "sessionStart",
                "session_created_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            },
        ]:
            assert (
                pipeline.ingest(payload, str(uuid4()), source="cursor")["status"]
                == "rejected_reset_boundary"
            )
        store.close()
    store = CollectorStore(path)
    pipeline = Pipeline(store, LiveRunClient("http://127.0.0.1:8000"))
    assert (
        pipeline.ingest(
            {
                "session_id": "new-chat",
                "hook_event_name": "sessionStart",
                "session_created_at": datetime.now(UTC).isoformat(),
            },
            "new",
            source="cursor",
        )["status"]
        == "accepted"
    )
    assert (
        pipeline.ingest(
            {"session_id": "new-chat", "hook_event_name": "afterAgentThought"},
            "next",
            source="cursor",
        )["status"]
        == "accepted"
    )
    store.close()


def test_post_reset_hook_observed_creation_evidence(local):
    home, settings, path = local
    clear_history(home, settings, FakeClickHouse(), confirm=True, check_stopped=False)
    store = CollectorStore(path)
    pipeline = Pipeline(store, LiveRunClient("http://127.0.0.1:8000"))

    old = {"session_id": "old-chat", "hook_event_name": "sessionStart"}
    stamp(old, "cursor")
    denied = pipeline.ingest(old, str(uuid4()), source="cursor")
    assert denied["status"] == "rejected_reset_boundary"
    assert denied["reason"] == "denied_identity"

    continuation = pipeline.ingest(
        {"session_id": "fresh-chat", "hook_event_name": "afterAgentThought"},
        str(uuid4()),
        source="cursor",
    )
    assert continuation["status"] == "rejected_reset_boundary"
    assert continuation["reason"] == "not_session_start"

    stale = {
        "session_id": "stale-chat",
        "hook_event_name": "sessionStart",
        OBSERVED_AT_FIELD: (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
    }
    stale_result = pipeline.ingest(stale, str(uuid4()), source="cursor")
    assert stale_result["reason"] == "stale_creation_evidence"

    delayed = {
        "session_id": "delayed-chat",
        "hook_event_name": "sessionStart",
        "session_created_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        OBSERVED_AT_FIELD: datetime.now(UTC).isoformat(),
    }
    delayed_result = pipeline.ingest(delayed, str(uuid4()), source="cursor")
    assert delayed_result["reason"] == "stale_creation_evidence"

    missing = pipeline.ingest(
        {"session_id": "no-evidence", "hook_event_name": "sessionStart"},
        str(uuid4()),
        source="cursor",
    )
    assert missing["reason"] == "missing_creation_evidence"

    fresh = {
        "session_id": "new-cursor",
        "conversation_id": "new-cursor",
        "hook_event_name": "sessionStart",
    }
    stamp(fresh, "cursor")
    accepted = pipeline.ingest(fresh, "new-cursor-start", source="cursor")
    assert accepted["status"] == "accepted"
    assert store.get_session(store_session_key("cursor", "new-cursor")) is not None
    assert store.pending_count() >= 1

    follow = pipeline.ingest(
        {"session_id": "new-cursor", "hook_event_name": "afterAgentThought"},
        "thought",
        source="cursor",
    )
    assert follow["status"] == "accepted"
    store.close()

    restarted = CollectorStore(path)
    restarted_pipeline = Pipeline(restarted, LiveRunClient("http://127.0.0.1:8000"))
    tool = restarted_pipeline.ingest(
        {
            "session_id": "new-cursor",
            "hook_event_name": "postToolUse",
            "tool_use_id": "t1",
            "tool_name": "Read",
        },
        "tool",
        source="cursor",
    )
    assert tool["status"] == "accepted"

    resume = {
        "session_id": "codex-resume",
        "hook_event_name": "SessionStart",
        "source": "resume",
        "cwd": "/tmp",
    }
    stamp(resume, "codex")
    assert (
        restarted_pipeline.ingest(resume, "codex-r", source="codex")["reason"]
        == "untrusted_session_source"
    )

    inferred = {
        "session_id": "codex-inferred",
        "hook_event_name": "SessionStart",
        "source": "inferred",
        "cwd": "/tmp",
    }
    stamp(inferred, "codex")
    assert (
        restarted_pipeline.ingest(inferred, "codex-i", source="codex")["reason"]
        == "untrusted_session_source"
    )

    startup = {
        "session_id": "codex-new",
        "hook_event_name": "SessionStart",
        "source": "startup",
        "cwd": "/tmp",
        "model": "gpt-5",
    }
    stamp(startup, "codex")
    startup_result = restarted_pipeline.ingest(startup, "codex-s", source="codex")
    assert startup_result["status"] == "accepted"
    restarted.close()


def test_locks_authorization_and_confirmation(local, monkeypatch):
    home, settings, path = local
    client = FakeClickHouse()
    with acquire_lock(home / "maintenance.lock", exclusive=False), pytest.raises(RuntimeError):
        clear_history(home, settings, client, confirm=True, check_stopped=False)
    with (
        acquire_lock(Path(str(path) + ".runtime.lock"), exclusive=True),
        pytest.raises(RuntimeError),
    ):
        clear_history(home, settings, client, confirm=True, check_stopped=False)
    monkeypatch.setattr("builtins.input", lambda _: "no")
    with pytest.raises(ValueError, match="Confirmation"):
        clear_history(home, settings, client, confirm=False, check_stopped=False)
    assert client.rows["traces"]
    monkeypatch.delenv("NM5_LOCAL_HISTORY_ENABLED")
    with pytest.raises(ValueError, match="ENABLED"):
        clear_history(home, settings, client, confirm=True, check_stopped=False)
    assert read_state(home)["generation"] == 0


def test_private_state_and_target_validation(local):
    home, settings, _ = local
    with pytest.raises(ValueError, match="target"):
        clear_history(
            home,
            settings.model_copy(update={"clickhouse_database": "unrelated"}),
            FakeClickHouse(),
            confirm=True,
            check_stopped=False,
        )
    home.chmod(0o755)
    with pytest.raises(PermissionError):
        read_state(home)


@pytest.mark.skipif(os.getenv("NM5_TEST_CLICKHOUSE") != "1", reason="requires isolated ClickHouse")
def test_live_reset_removes_only_owned_records(tmp_path, monkeypatch):
    import clickhouse_connect

    settings = Settings(api_host="127.0.0.1")
    assert settings.clickhouse_database.startswith("nm5_test_")
    monkeypatch.setenv("NM5_LOCAL_HISTORY_ENABLED", "1")
    home = tmp_path / "history"
    monkeypatch.setenv("NM5_HISTORY_HOME", str(home))
    store = CollectorStore(tmp_path / "collector.sqlite")
    store.close()
    initialize(home, settings, [str(tmp_path / "collector.sqlite")])
    client = clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
    )
    try:
        client.command("CREATE TABLE unrelated_config (value String) ENGINE=Memory")
        client.command("INSERT INTO unrelated_config VALUES ('keep')")
        clear_history(home, settings, client, confirm=True, check_stopped=False)
        assert client.query("SELECT value FROM unrelated_config").result_rows == [("keep",)]
        assert all(
            client.command(f"SELECT count() FROM {table}") == 0
            for table in ("traces", "spans", "run_events")
        )
        assert json.loads((home / "state.json").read_text())["phase"] == "ready"
    finally:
        client.close()


def test_partial_sqlite_reset_recovers_without_losing_boundary(local):
    import sqlite3

    from nomore500s_collector.history_guard import write_state

    home, settings, path = local
    second_path = path.with_name("second.sqlite")
    second = CollectorStore(second_path)
    pipeline = Pipeline(second, LiveRunClient("http://127.0.0.1:8000"))
    pipeline.ingest(
        {"session_id": "second-old", "hook_event_name": "sessionStart"}, "second", source="cursor"
    )
    second.close()
    state = read_state(home)
    state["stores"].append(str(second_path))
    write_state(home, state)
    with sqlite3.connect(second_path) as db:
        db.execute(
            "CREATE TRIGGER simulate_failure BEFORE DELETE ON pending "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
    client = FakeClickHouse()
    with pytest.raises(sqlite3.IntegrityError):
        clear_history(home, settings, client, confirm=True, check_stopped=False)
    assert read_state(home)["phase"] == "prepared"
    first = CollectorStore(path)
    assert first.pending_count() == 0
    assert first._meta("history_boundary") is not None
    first.close()
    with sqlite3.connect(second_path) as db:
        db.execute("DROP TRIGGER simulate_failure")
    clear_history(home, settings, client, confirm=True, check_stopped=False)
    for location in (path, second_path):
        store = CollectorStore(location)
        assert store.pending_count() == 0
        assert store._meta("history_generation") == "1"
        store.close()


def test_reset_preserves_credentials_and_other_sqlite_tables(local):
    import sqlite3

    home, settings, path = local
    credentials = path.with_name("token")
    credentials.write_text("do-not-change")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated_settings (value TEXT)")
        db.execute("INSERT INTO unrelated_settings VALUES ('keep')")
    clear_history(home, settings, FakeClickHouse(), confirm=True, check_stopped=False)
    assert credentials.read_text() == "do-not-change"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM unrelated_settings").fetchall() == [("keep",)]


def test_http_never_exposes_reset_or_credentials(local, monkeypatch):
    from fastapi.testclient import TestClient
    from no_more_500s_api.core.config import get_settings
    from no_more_500s_api.main import create_app
    from no_more_500s_api.services.trace_service import InMemoryTraceRepository

    home, settings, _ = local
    clear_history(home, settings, FakeClickHouse(), confirm=True, check_stopped=False)
    monkeypatch.setenv("NM5_CLICKHOUSE_DATABASE", settings.clickhouse_database)
    get_settings.cache_clear()
    try:
        with TestClient(create_app(InMemoryTraceRepository())) as client:
            assert client.post("/v1/history/clear").status_code == 404
            assert client.get("/health").headers["X-NM5-History-Generation"] == "1"
            response = client.post(
                "/v1/runs",
                json={
                    "project_id": "p",
                    "name": "delayed",
                    "started_at": "2020-01-01T00:00:00Z",
                },
            )
            assert response.status_code == 409
    finally:
        get_settings.cache_clear()
