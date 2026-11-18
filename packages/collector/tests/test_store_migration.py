from __future__ import annotations

import sqlite3
from pathlib import Path

from nomore500s_collector.store import CollectorStore


def test_migrates_codex_session_keys_and_pending_sequence(tmp_path: Path) -> None:
    path = tmp_path / "collector.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            agent TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            agent_span_id TEXT NOT NULL,
            sequence INTEGER NOT NULL DEFAULT 0,
            started INTEGER NOT NULL DEFAULT 0,
            finalized INTEGER NOT NULL DEFAULT 0,
            coverage_json TEXT NOT NULL DEFAULT '[]',
            token_status TEXT NOT NULL DEFAULT 'unavailable',
            cost_status TEXT NOT NULL DEFAULT 'unavailable',
            workspace TEXT,
            model TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE deliveries (
            delivery_id TEXT PRIMARY KEY,
            occurrence_key TEXT NOT NULL,
            received_at TEXT NOT NULL
        );
        CREATE TABLE occurrences (
            occurrence_key TEXT PRIMARY KEY,
            delivery_id TEXT NOT NULL,
            event_ids_json TEXT NOT NULL
        );
        CREATE TABLE pending (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            event_id TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            critical INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT NOT NULL,
            last_error TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE open_spans (
            span_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            identity TEXT,
            started_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO sessions(
            session_id, agent, trace_id, agent_span_id, sequence, started, finalized,
            coverage_json, token_status, cost_status, workspace, model, updated_at
        ) VALUES ('thr_1', 'codex', 't', 'a', 4, 1, 0, '["SessionStart"]', 'unavailable',
                  'unavailable', 'demo', 'gpt', '2026-01-01T00:00:00+00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO pending(
            session_id, event_id, kind, payload_json, critical, attempts, next_retry_at, created_at
        ) VALUES ('thr_1', '11111111-1111-1111-1111-111111111111', 'events', '{}', 1, 0,
                  '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO open_spans(span_id, session_id, kind, identity, started_at)
        VALUES ('span', 'thr_1', 'tool', 'x', '2026-01-01T00:00:00+00:00')
        """
    )
    conn.commit()
    conn.close()

    store = CollectorStore(path)
    migrated = store.get_session("codex:thr_1")
    assert migrated is not None
    assert int(migrated["sequence"]) == 4
    assert store.get_session("thr_1") is None
    pending = store.due_pending()
    assert pending[0].session_id == "codex:thr_1"
    assert pending[0].event_id == "11111111-1111-1111-1111-111111111111"
    store.close()
