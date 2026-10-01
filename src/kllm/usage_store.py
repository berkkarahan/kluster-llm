"""SQLite-backed usage history and bounded retention."""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kllm.exceptions import ValidationError
from kllm.usage_support import (
    MAX_BYTES,
    WINDOW,
    utc,
    validate,
)


class UsageStore:
    def __init__(self, path: Path, *, max_bytes: int = MAX_BYTES) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.max_bytes = max_bytes
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA auto_vacuum=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (node_id TEXT PRIMARY KEY, host TEXT NOT NULL, role TEXT NOT NULL, context TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS lifetimes (
                lifetime_id TEXT PRIMARY KEY, node_id TEXT NOT NULL, pid INTEGER NOT NULL, role TEXT NOT NULL,
                started_at TEXT NOT NULL, ended_at TEXT, state TEXT NOT NULL, model TEXT);
            CREATE TABLE IF NOT EXISTS associations (
                lifetime_id TEXT NOT NULL, model TEXT NOT NULL, observed_from TEXT NOT NULL,
                observed_until TEXT, PRIMARY KEY(lifetime_id, observed_from));
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY, lifetime_id TEXT NOT NULL, node_id TEXT NOT NULL,
                observed_at TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL,
                UNIQUE(lifetime_id, observed_at));
            CREATE INDEX IF NOT EXISTS readings_time ON readings(observed_at, id);
            CREATE INDEX IF NOT EXISTS readings_filter ON readings(node_id, lifetime_id, observed_at, id);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        if "context" not in {column[1] for column in self.db.execute("PRAGMA table_info(nodes)")}:
            self.db.execute("ALTER TABLE nodes ADD COLUMN context TEXT NOT NULL DEFAULT '{}' ")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO meta VALUES('cursor_secret', ?)", (secrets.token_hex(32),)
            )
        base_bytes = str(min(self._physical_bytes(), 64 * 1024))
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('base_bytes', ?)", (base_bytes,))
        stored_base_bytes = self._meta("base_bytes")
        self._base_bytes = int(stored_base_bytes) if stored_base_bytes is not None else 0
        self.recording = "interrupted"
        self.error: str | None = None
        self.last_successful_save = self._meta("last_successful_save")
        from kllm.usage_store_queries import UsageHistoryQueries

        self._queries = UsageHistoryQueries(
            self.db,
            self.lock,
            self._meta,
            lambda: (self.recording, self.error, self.last_successful_save),
        )
        self._prune(datetime.now(timezone.utc))

    def __enter__(self) -> UsageStore:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def _meta(self, name: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (name,)).fetchone()
        return row[0] if row else None

    def count(self) -> int:
        return self._queries.count()

    def latest(self, node_id: str) -> dict[str, Any] | None:
        return self._queries.latest(node_id)

    def known_nodes(self) -> dict[str, dict[str, Any]]:
        return self._queries.known_nodes()

    def coverage(self, node_id: str | None = None) -> dict[str, Any]:
        return self._queries.coverage(node_id)

    def lifetimes(self, node_id: str) -> list[dict[str, Any]]:
        return self._queries.lifetimes(node_id)

    def end_node(self, node_id: str, *, state: str = "ended") -> None:
        if state not in ("ended", "unobserved"):
            raise ValidationError("invalid lifetime state")
        with self.lock, self.db:
            self.db.execute(
                "UPDATE lifetimes SET state=?, ended_at=(SELECT MAX(observed_at) FROM readings WHERE readings.lifetime_id=lifetimes.lifetime_id) WHERE node_id=? AND state='running'",
                (state, node_id),
            )
            self.db.execute(
                "UPDATE associations SET observed_until=(SELECT ended_at FROM lifetimes WHERE lifetimes.lifetime_id=associations.lifetime_id) WHERE observed_until IS NULL AND lifetime_id IN (SELECT lifetime_id FROM lifetimes WHERE node_id=? AND state!= 'running')",
                (node_id,),
            )

    def series(
        self, node_id: str, life_id: str, window: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        return self._queries.series(node_id, life_id, window, now=now)

    def readings(
        self,
        node_id: str,
        life_id: str,
        window: str,
        *,
        limit: int = 500,
        cursor: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        return self._queries.readings(node_id, life_id, window, limit=limit, cursor=cursor, now=now)

    def _physical_bytes(self) -> int:
        return sum(
            file.stat().st_size
            for file in (
                self.path,
                Path(str(self.path) + "-journal"),
                Path(str(self.path) + "-wal"),
                Path(str(self.path) + "-shm"),
            )
            if file.exists()
        )

    def _write(
        self,
        node_id: str,
        host: str,
        point: dict[str, Any],
        received: str,
        model: str | None,
        context: dict[str, Any] | None = None,
    ) -> bool:
        before = self.db.total_changes
        self.db.execute(
            "INSERT OR IGNORE INTO readings(lifetime_id,node_id,observed_at,received_at,payload) VALUES(?,?,?,?,?)",
            (
                point["lifetime_id"],
                node_id,
                point["observed_at"],
                received,
                json.dumps(point, allow_nan=False),
            ),
        )
        if self.db.total_changes == before:
            return False
        self.db.execute(
            "INSERT INTO nodes VALUES(?,?,?,?) ON CONFLICT(node_id) DO UPDATE SET host=excluded.host,role=excluded.role,context=excluded.context",
            (node_id, host, point["process_role"], json.dumps(context or {}, allow_nan=False)),
        )
        prior = self.db.execute(
            "SELECT lifetime_id,observed_at FROM readings WHERE node_id=? AND lifetime_id!=? ORDER BY observed_at DESC LIMIT 1",
            (node_id, point["lifetime_id"]),
        ).fetchone()
        if prior and prior[1] < point["observed_at"]:
            self.db.execute(
                "UPDATE lifetimes SET state='ended',ended_at=? WHERE lifetime_id=? AND state='running'",
                (prior[1], prior[0]),
            )
            self.db.execute(
                "UPDATE associations SET observed_until=? WHERE lifetime_id=? AND observed_until IS NULL",
                (prior[1], prior[0]),
            )
        self.db.execute(
            "INSERT INTO lifetimes VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(lifetime_id) DO UPDATE SET model=COALESCE(excluded.model,lifetimes.model)",
            (
                point["lifetime_id"],
                node_id,
                point["pid"],
                point["process_role"],
                point["process_started_at"],
                None,
                "running",
                model,
            ),
        )
        # A delayed observation may enrich an old lifetime, never make it current again.
        later = self.db.execute(
            "SELECT 1 FROM readings WHERE node_id=? AND lifetime_id!=? AND observed_at>? LIMIT 1",
            (node_id, point["lifetime_id"], point["observed_at"]),
        ).fetchone()
        if later:
            self.db.execute(
                "UPDATE lifetimes SET state='ended', ended_at=(SELECT MAX(observed_at) FROM readings WHERE lifetime_id=?) WHERE lifetime_id=? AND state='running'",
                (point["lifetime_id"], point["lifetime_id"]),
            )
            self.db.execute(
                "UPDATE associations SET observed_until=(SELECT ended_at FROM lifetimes WHERE lifetimes.lifetime_id=associations.lifetime_id) WHERE lifetime_id=? AND observed_until IS NULL",
                (point["lifetime_id"],),
            )
        latest_observation = self.db.execute(
            "SELECT MAX(observed_at) FROM readings WHERE lifetime_id=?", (point["lifetime_id"],)
        ).fetchone()[0]
        if model and latest_observation == point["observed_at"]:
            previous = self.db.execute(
                "SELECT model FROM associations WHERE lifetime_id=? ORDER BY observed_from DESC LIMIT 1",
                (point["lifetime_id"],),
            ).fetchone()
            if previous is None or previous[0] != model:
                self.db.execute(
                    "UPDATE associations SET observed_until=? WHERE lifetime_id=? AND observed_until IS NULL",
                    (point["observed_at"], point["lifetime_id"]),
                )
                self.db.execute(
                    "INSERT INTO associations VALUES(?,?,?,NULL)",
                    (point["lifetime_id"], model, point["observed_at"]),
                )
        self.db.execute(
            "INSERT INTO meta VALUES('last_successful_save',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (received,),
        )
        return True

    def _prune(self, now: datetime) -> None:
        self.db.execute("DELETE FROM readings WHERE observed_at<?", (utc(now - WINDOW),))
        self.db.commit()
        # FULL auto-vacuum reclaims physical pages; leave headroom for rollback journal.
        while self._physical_bytes() > self.max_bytes:
            oldest = self.db.execute(
                "SELECT id,observed_at FROM readings ORDER BY observed_at,id LIMIT 1"
            ).fetchone()
            if oldest is None:
                raise OSError("usage history budget cannot hold the database schema")
            self.db.execute("DELETE FROM readings WHERE id=?", (oldest[0],))
            self.db.execute(
                "INSERT INTO meta VALUES('storage_truncated','1') ON CONFLICT(key) DO UPDATE SET value='1'"
            )
            self.db.execute(
                "INSERT INTO meta VALUES('storage_truncated_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (oldest[1],),
            )
            self.db.commit()
        self.db.execute(
            "DELETE FROM lifetimes WHERE lifetime_id NOT IN (SELECT DISTINCT lifetime_id FROM readings)"
        )
        self.db.execute(
            "DELETE FROM associations WHERE lifetime_id NOT IN (SELECT lifetime_id FROM lifetimes)"
        )
        self.db.execute(
            "DELETE FROM nodes WHERE node_id NOT IN (SELECT DISTINCT node_id FROM readings)"
        )
        self.db.commit()

    def record(
        self,
        node_id: str,
        host: str,
        point: dict[str, Any],
        *,
        now: datetime | None = None,
        expected_pid: int | None = None,
        expected_start: str | None = None,
        expected_role: str | None = None,
        model: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> bool:
        return bool(
            self.record_many(
                node_id,
                host,
                [point],
                now=now,
                expected_pid=expected_pid,
                expected_start=expected_start,
                expected_role=expected_role,
                model=model,
                context=context,
            )
        )

    def record_many(
        self,
        node_id: str,
        host: str,
        points: list[dict[str, Any]],
        *,
        now: datetime | None = None,
        expected_pid: int | None = None,
        expected_start: str | None = None,
        expected_role: str | None = None,
        model: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> int:
        now = now or datetime.now(timezone.utc)
        for point in points:
            validate(
                node_id,
                point,
                now=now,
                expected_pid=expected_pid,
                expected_start=expected_start,
                expected_role=expected_role,
            )
        context_bytes = len(json.dumps(context or {}, allow_nan=False).encode())
        if context_bytes > 8192:
            raise ValidationError("node metadata exceeds 8 KiB")
        with self.lock:
            try:
                # Reserve an index page so a point too large to retain alone cannot retire good history.
                required = self._base_bytes + self.db.execute("PRAGMA page_size").fetchone()[0]
                required += context_bytes + max(
                    len(json.dumps(point, allow_nan=False).encode()) for point in points
                )
                if required > self.max_bytes:
                    raise OSError("usage history budget cannot hold this reading")
                with self.db:
                    accepted = sum(
                        self._write(node_id, host, point, utc(now), model, context)
                        for point in points
                    )
                if accepted:
                    self._prune(now)
                    if not any(
                        self.db.execute(
                            "SELECT 1 FROM readings WHERE lifetime_id=? AND observed_at=?",
                            (point["lifetime_id"], point["observed_at"]),
                        ).fetchone()
                        for point in points
                    ):
                        raise OSError("usage history budget retired the entire new batch")
                    self.last_successful_save = self._meta("last_successful_save")
                    self.recording, self.error = "recording", None
                return accepted
            except (sqlite3.Error, OSError) as exc:
                self.db.rollback()
                self.recording, self.error = "failed", str(exc)
                raise
