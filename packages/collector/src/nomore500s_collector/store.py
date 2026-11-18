from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
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
CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id TEXT PRIMARY KEY,
    occurrence_key TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS occurrences (
    occurrence_key TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL,
    event_ids_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending (
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
CREATE TABLE IF NOT EXISTS open_spans (
    span_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    identity TEXT,
    started_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pending_retry ON pending(next_retry_at);
CREATE TABLE IF NOT EXISTS identity_links (
    agent TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    canonical_id TEXT NOT NULL,
    confidence TEXT NOT NULL,
    PRIMARY KEY (agent, kind, source_id)
);
CREATE TABLE IF NOT EXISTS span_identities (
    session_id TEXT NOT NULL,
    identity TEXT NOT NULL,
    span_id TEXT NOT NULL,
    PRIMARY KEY (session_id, identity)
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class PendingRow:
    id: int
    session_id: str
    event_id: str
    kind: str
    payload: dict[str, Any]
    critical: bool
    attempts: int


class CollectorStore:
    def __init__(self, path: Path, *, max_pending: int = 5000) -> None:
        self.path = path
        self.max_pending = max_pending
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    def _meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            """
            INSERT INTO meta(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def _migrate(self) -> None:
        version = int(self._meta("schema_version") or "1")
        if version < 2:
            self._migrate_session_keys()
            self._set_meta("schema_version", "2")

    def _migrate_session_keys(self) -> None:
        rows = list(self._conn.execute("SELECT session_id, agent FROM sessions"))
        for row in rows:
            session_id = str(row["session_id"])
            agent = str(row["agent"] or "codex")
            prefix = f"{agent}:"
            if session_id.startswith(prefix):
                continue
            new_id = f"{prefix}{session_id}"
            self._conn.execute(
                "UPDATE sessions SET session_id = ? WHERE session_id = ?",
                (new_id, session_id),
            )
            self._conn.execute(
                "UPDATE pending SET session_id = ? WHERE session_id = ?",
                (new_id, session_id),
            )
            self._conn.execute(
                "UPDATE open_spans SET session_id = ? WHERE session_id = ?",
                (new_id, session_id),
            )

    def resolve_canonical(self, agent: str, kind: str, candidates: list[str]) -> str | None:
        filtered = [item for item in candidates if item]
        if not filtered:
            return None
        for item in filtered:
            row = self._conn.execute(
                """
                SELECT canonical_id FROM identity_links
                WHERE agent = ? AND kind = ? AND source_id = ?
                """,
                (agent, kind, item),
            ).fetchone()
            if row:
                canonical = str(row["canonical_id"])
                self.link_identities(agent, kind, filtered, canonical, "exact")
                return canonical
        unique = list(dict.fromkeys(filtered))
        if len(unique) == 1:
            self.link_identities(agent, kind, unique, unique[0], "exact")
            return unique[0]
        return None

    def link_identities(
        self,
        agent: str,
        kind: str,
        source_ids: list[str],
        canonical_id: str,
        confidence: str,
    ) -> None:
        for source_id in source_ids:
            if not source_id:
                continue
            self._conn.execute(
                """
                INSERT INTO identity_links(agent, kind, source_id, canonical_id, confidence)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(agent, kind, source_id) DO UPDATE SET
                    canonical_id = excluded.canonical_id,
                    confidence = excluded.confidence
                """,
                (agent, kind, source_id, canonical_id, confidence),
            )

    def span_id_for_identities(self, session_id: str, identities: list[str]) -> str | None:
        for identity in identities:
            row = self._conn.execute(
                "SELECT span_id FROM span_identities WHERE session_id = ? AND identity = ?",
                (session_id, identity),
            ).fetchone()
            if row:
                found = str(row["span_id"])
                self.remember_span_identities(session_id, identities, found)
                return found
        return None

    def remember_span_identities(
        self, session_id: str, identities: list[str], span_id: str
    ) -> None:
        for identity in identities:
            if not identity:
                continue
            self._conn.execute(
                """
                INSERT INTO span_identities(session_id, identity, span_id)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id, identity) DO UPDATE SET span_id = excluded.span_id
                """,
                (session_id, identity, span_id),
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def pending_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM pending").fetchone()
        return int(row["n"]) if row else 0

    def heartbeat_pending_ids(self) -> list[int]:
        rows = self._conn.execute(
            "SELECT id FROM pending WHERE critical = 0 ORDER BY id ASC"
        ).fetchall()
        return [int(row["id"]) for row in rows]

    def delete_pending_ids(self, ids: list[int]) -> None:
        for pending_id in ids:
            self._conn.execute("DELETE FROM pending WHERE id = ?", (pending_id,))

    def get_delivery(self, delivery_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT occurrence_key FROM deliveries WHERE delivery_id = ?",
            (delivery_id,),
        ).fetchone()
        return str(row["occurrence_key"]) if row else None

    def get_occurrence(self, occurrence_key: str) -> list[str] | None:
        row = self._conn.execute(
            "SELECT event_ids_json FROM occurrences WHERE occurrence_key = ?",
            (occurrence_key,),
        ).fetchone()
        if row is None:
            return None
        return list(json.loads(row["event_ids_json"]))

    def record_delivery(self, delivery_id: str, occurrence_key: str, event_ids: list[UUID]) -> None:
        now = _now()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO deliveries(
                delivery_id, occurrence_key, received_at
            ) VALUES (?, ?, ?)
            """,
            (delivery_id, occurrence_key, now),
        )
        self._conn.execute(
            """
            INSERT OR IGNORE INTO occurrences(occurrence_key, delivery_id, event_ids_json)
            VALUES (?, ?, ?)
            """,
            (occurrence_key, delivery_id, json.dumps([str(item) for item in event_ids])),
        )

    def get_session(self, session_id: str) -> sqlite3.Row | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row if row is not None else None

    def upsert_session(
        self,
        *,
        session_id: str,
        agent: str,
        trace_id: UUID,
        agent_span_id: UUID,
        workspace: str | None,
        model: str | None,
    ) -> None:
        now = _now()
        self._conn.execute(
            """
            INSERT INTO sessions(
                session_id, agent, trace_id, agent_span_id, sequence, started, finalized,
                coverage_json, token_status, cost_status, workspace, model, updated_at
            ) VALUES (?, ?, ?, ?, 0, 0, 0, '[]', 'unavailable', 'unavailable', ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                workspace = COALESCE(excluded.workspace, sessions.workspace),
                model = COALESCE(excluded.model, sessions.model),
                updated_at = excluded.updated_at
            """,
            (session_id, agent, str(trace_id), str(agent_span_id), workspace, model, now),
        )

    def next_sequence(self, session_id: str) -> int:
        row = self._conn.execute(
            """
            UPDATE sessions
            SET sequence = sequence + 1, updated_at = ?
            WHERE session_id = ?
            RETURNING sequence
            """,
            (_now(), session_id),
        ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return int(row["sequence"])

    def mark_started(self, session_id: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET started = 1, updated_at = ? WHERE session_id = ?",
            (_now(), session_id),
        )

    def mark_finalized(self, session_id: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET finalized = 1, updated_at = ? WHERE session_id = ?",
            (_now(), session_id),
        )

    def add_coverage(self, session_id: str, event: str) -> None:
        row = self.get_session(session_id)
        if row is None:
            return
        coverage = list(json.loads(row["coverage_json"]))
        if event not in coverage:
            coverage.append(event)
        self._conn.execute(
            "UPDATE sessions SET coverage_json = ?, updated_at = ? WHERE session_id = ?",
            (json.dumps(coverage), _now(), session_id),
        )

    def set_token_status(self, session_id: str, status: str) -> None:
        if status != "reported":
            return
        self._conn.execute(
            "UPDATE sessions SET token_status = ?, updated_at = ? WHERE session_id = ?",
            (status, _now(), session_id),
        )

    def enqueue(
        self,
        *,
        session_id: str,
        event_id: UUID,
        kind: str,
        payload: dict[str, Any],
        critical: bool,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO pending(
                session_id, event_id, kind, payload_json, critical,
                attempts, next_retry_at, created_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                session_id,
                str(event_id),
                kind,
                json.dumps(payload),
                1 if critical else 0,
                _now(),
                _now(),
            ),
        )

    def due_pending(self, limit: int = 50) -> list[PendingRow]:
        rows = self._conn.execute(
            """
            SELECT id, session_id, event_id, kind, payload_json, critical, attempts
            FROM pending
            WHERE next_retry_at <= ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (_now(), limit),
        ).fetchall()
        return [
            PendingRow(
                id=int(row["id"]),
                session_id=row["session_id"],
                event_id=row["event_id"],
                kind=row["kind"],
                payload=json.loads(row["payload_json"]),
                critical=bool(row["critical"]),
                attempts=int(row["attempts"]),
            )
            for row in rows
        ]

    def mark_delivered(self, pending_id: int) -> None:
        self._conn.execute("DELETE FROM pending WHERE id = ?", (pending_id,))

    def mark_retry(self, pending_id: int, error: str, delay_seconds: float) -> None:
        nxt = datetime.now(UTC).timestamp() + delay_seconds
        next_at = datetime.fromtimestamp(nxt, tz=UTC).isoformat()
        self._conn.execute(
            """
            UPDATE pending
            SET attempts = attempts + 1, last_error = ?, next_retry_at = ?
            WHERE id = ?
            """,
            (error[:500], next_at, pending_id),
        )

    def put_open_span(self, session_id: str, span_id: str, kind: str, identity: str | None) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO open_spans(span_id, session_id, kind, identity, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (span_id, session_id, kind, identity, _now()),
        )

    def pop_open_span(self, span_id: str) -> None:
        self._conn.execute("DELETE FROM open_spans WHERE span_id = ?", (span_id,))

    def unmatched_model_span(self, session_id: str) -> str | None:
        row = self._conn.execute(
            """
            SELECT span_id FROM open_spans
            WHERE session_id = ? AND kind = 'llm'
            ORDER BY started_at ASC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        return str(row["span_id"]) if row else None
