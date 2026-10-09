"""SQLite usage history, explicit atomic ingestion, and bounded retention."""

from __future__ import annotations

from contextlib import contextmanager
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, inspect, literal, select, update
from sqlalchemy.dialects.sqlite import insert

from kllm.exceptions import PersistenceError, ValidationError
from kllm.persistence import SQLiteOptions, SQLiteStore
from kllm import usage_schema as s
from kllm.usage_store_queries import UsageHistoryQueries
from kllm.usage_support import MAX_BYTES, WINDOW, utc, validate


class UsageStore:
    def __init__(
        self, path: Path, *, max_bytes: int = MAX_BYTES, options: SQLiteOptions | None = None
    ) -> None:
        self.path, self.max_bytes = path, max_bytes
        self.lock = threading.RLock()  # Short recorder metadata lock; never encloses SQL.
        self.recording, self.error, self.last_successful_save = "interrupted", None, None
        self._store = SQLiteStore(
            path, s.metadata, profile="usage", options=options, upgrade=self._upgrade
        )
        try:
            with self._store.connection(write=True) as db:
                db.execute(
                    insert(s.meta)
                    .values(key="cursor_secret", value=secrets.token_hex(32))
                    .on_conflict_do_nothing()
                )
            base_bytes = str(min(self._physical_bytes(), 64 * 1024))
            with self._store.connection(write=True) as db:
                db.execute(
                    insert(s.meta)
                    .values(key="base_bytes", value=base_bytes)
                    .on_conflict_do_nothing()
                )
            self._base_bytes = int(self._meta("base_bytes") or 0)
            self.last_successful_save = self._meta("last_successful_save")
            with self._store.writer():
                self._prune(datetime.now(timezone.utc))
        except BaseException:
            self._store.close()
            raise

    @staticmethod
    def _upgrade(db) -> None:
        if "nodes" in inspect(db).get_table_names():
            if "context" not in {c["name"] for c in inspect(db).get_columns("nodes")}:
                db.exec_driver_sql(
                    "ALTER TABLE nodes ADD COLUMN context TEXT NOT NULL DEFAULT '{}' "
                )

    def __enter__(self) -> UsageStore:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self, *, deadline: float | None = None) -> None:
        self._store.close(deadline=deadline)

    async def aclose(self, *, deadline: float | None = None) -> None:
        await self._store.aclose(deadline=deadline)

    @contextmanager
    def snapshot(self):
        with self._store.connection():
            yield self

    def maintenance(self, *, write: bool = True):
        """Scoped Core connection for known upgrades and test data/fault seeding."""
        return self._store.connection(write=write)

    def _meta(self, name: str) -> str | None:
        return self._store.read(
            lambda db: db.execute(
                select(s.meta.c.value).where(s.meta.c.key == name)
            ).scalar_one_or_none()
        )

    @staticmethod
    def _put_meta(db, key: str, value: str) -> None:
        statement = insert(s.meta).values(key=key, value=value)
        db.execute(
            statement.on_conflict_do_update(
                index_elements=[s.meta.c.key], set_={"value": statement.excluded.value}
            )
        )

    def _status(self):
        with self.lock:
            return self.recording, self.error, self.last_successful_save

    def _query(self, name, *args, **kwargs):
        def query(db):
            queries = UsageHistoryQueries(
                db,
                lambda key: db.execute(
                    select(s.meta.c.value).where(s.meta.c.key == key)
                ).scalar_one_or_none(),
                self._status,
            )
            return getattr(queries, name)(*args, **kwargs)

        return self._store.read(query)

    def count(self) -> int:
        return self._query("count")

    def latest(self, node_id: str) -> dict[str, Any] | None:
        return self._query("latest", node_id)

    def known_nodes(self) -> dict[str, dict[str, Any]]:
        return self._query("known_nodes")

    def coverage(self, node_id: str | None = None) -> dict[str, Any]:
        return self._query("coverage", node_id)

    def lifetimes(self, node_id: str) -> list[dict[str, Any]]:
        return self._query("lifetimes", node_id)

    def end_node(self, node_id: str, *, state: str = "ended") -> None:
        if state not in ("ended", "unobserved"):
            raise ValidationError("invalid lifetime state")
        with self._store.connection(write=True) as db:
            ended = (
                select(func.max(s.readings.c.observed_at))
                .where(s.readings.c.lifetime_id == s.lifetimes.c.lifetime_id)
                .scalar_subquery()
            )
            db.execute(
                update(s.lifetimes)
                .where(s.lifetimes.c.node_id == node_id, s.lifetimes.c.state == "running")
                .values(state=state, ended_at=ended)
            )
            observed_until = (
                select(s.lifetimes.c.ended_at)
                .where(s.lifetimes.c.lifetime_id == s.associations.c.lifetime_id)
                .scalar_subquery()
            )
            db.execute(
                update(s.associations)
                .where(
                    s.associations.c.observed_until.is_(None),
                    s.associations.c.lifetime_id.in_(
                        select(s.lifetimes.c.lifetime_id).where(
                            s.lifetimes.c.node_id == node_id, s.lifetimes.c.state != "running"
                        )
                    ),
                )
                .values(observed_until=observed_until)
            )

    def series(
        self, node_id: str, life_id: str, window: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        return UsageHistoryQueries.aggregate_series(
            *self._query("series_snapshot", node_id, life_id, window, now=now), window
        )

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
        return self._query(
            "readings", node_id, life_id, window, limit=limit, cursor=cursor, now=now
        )

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
        # This helper belongs to the caller's insertion transaction; no checkout here.
        db = self._store.current_connection
        life_id = point["lifetime_id"]
        accepted = db.execute(
            insert(s.readings)
            .values(
                lifetime_id=life_id,
                node_id=node_id,
                observed_at=point["observed_at"],
                received_at=received,
                payload=json.dumps(point, allow_nan=False),
            )
            .on_conflict_do_nothing()
        ).rowcount
        if not accepted:
            return False
        node = insert(s.nodes).values(
            node_id=node_id,
            host=host,
            role=point["process_role"],
            context=json.dumps(context or {}, allow_nan=False),
        )
        db.execute(
            node.on_conflict_do_update(
                index_elements=[s.nodes.c.node_id],
                set_={name: node.excluded[name] for name in ("host", "role", "context")},
            )
        )
        prior = db.execute(
            select(s.readings.c.lifetime_id, s.readings.c.observed_at)
            .where(s.readings.c.node_id == node_id, s.readings.c.lifetime_id != life_id)
            .order_by(s.readings.c.observed_at.desc())
            .limit(1)
        ).first()
        if prior and prior[1] < point["observed_at"]:
            db.execute(
                update(s.lifetimes)
                .where(s.lifetimes.c.lifetime_id == prior[0], s.lifetimes.c.state == "running")
                .values(state="ended", ended_at=prior[1])
            )
            db.execute(
                update(s.associations)
                .where(
                    s.associations.c.lifetime_id == prior[0],
                    s.associations.c.observed_until.is_(None),
                )
                .values(observed_until=prior[1])
            )
        lifetime = insert(s.lifetimes).values(
            lifetime_id=life_id,
            node_id=node_id,
            pid=point["pid"],
            role=point["process_role"],
            started_at=point["process_started_at"],
            ended_at=None,
            state="running",
            model=model,
        )
        db.execute(
            lifetime.on_conflict_do_update(
                index_elements=[s.lifetimes.c.lifetime_id],
                set_={"model": func.coalesce(lifetime.excluded.model, s.lifetimes.c.model)},
            )
        )
        later = db.execute(
            select(literal(1))
            .where(
                s.readings.c.node_id == node_id,
                s.readings.c.lifetime_id != life_id,
                s.readings.c.observed_at > point["observed_at"],
            )
            .limit(1)
        ).first()
        if later:
            latest = (
                select(func.max(s.readings.c.observed_at))
                .where(s.readings.c.lifetime_id == life_id)
                .scalar_subquery()
            )
            db.execute(
                update(s.lifetimes)
                .where(s.lifetimes.c.lifetime_id == life_id, s.lifetimes.c.state == "running")
                .values(state="ended", ended_at=latest)
            )
            ended = (
                select(s.lifetimes.c.ended_at)
                .where(s.lifetimes.c.lifetime_id == s.associations.c.lifetime_id)
                .scalar_subquery()
            )
            db.execute(
                update(s.associations)
                .where(
                    s.associations.c.lifetime_id == life_id,
                    s.associations.c.observed_until.is_(None),
                )
                .values(observed_until=ended)
            )
        latest_observation = db.execute(
            select(func.max(s.readings.c.observed_at)).where(s.readings.c.lifetime_id == life_id)
        ).scalar()
        if model and latest_observation == point["observed_at"]:
            previous = db.execute(
                select(s.associations.c.model)
                .where(s.associations.c.lifetime_id == life_id)
                .order_by(s.associations.c.observed_from.desc())
                .limit(1)
            ).first()
            if previous is None or previous[0] != model:
                db.execute(
                    update(s.associations)
                    .where(
                        s.associations.c.lifetime_id == life_id,
                        s.associations.c.observed_until.is_(None),
                    )
                    .values(observed_until=point["observed_at"])
                )
                db.execute(
                    insert(s.associations).values(
                        lifetime_id=life_id,
                        model=model,
                        observed_from=point["observed_at"],
                        observed_until=None,
                    )
                )
        self._put_meta(db, "last_successful_save", received)
        return True

    def _prune(self, now: datetime) -> None:
        with self._store.connection(write=True) as db:
            db.execute(delete(s.readings).where(s.readings.c.observed_at < utc(now - WINDOW)))
        # Physical accounting follows commit/auto-vacuum, never an open transaction.
        while self._physical_bytes() > self.max_bytes:
            with self._store.connection(write=True) as db:
                oldest = db.execute(
                    select(s.readings.c.id, s.readings.c.observed_at)
                    .order_by(s.readings.c.observed_at, s.readings.c.id)
                    .limit(1)
                ).first()
                if oldest is None:
                    raise OSError("usage history budget cannot hold the database schema")
                db.execute(delete(s.readings).where(s.readings.c.id == oldest[0]))
                self._put_meta(db, "storage_truncated", "1")
                self._put_meta(db, "storage_truncated_at", oldest[1])
        with self._store.connection(write=True) as db:
            db.execute(
                delete(s.lifetimes).where(
                    s.lifetimes.c.lifetime_id.not_in(select(s.readings.c.lifetime_id).distinct())
                )
            )
            db.execute(
                delete(s.associations).where(
                    s.associations.c.lifetime_id.not_in(select(s.lifetimes.c.lifetime_id))
                )
            )
            db.execute(
                delete(s.nodes).where(
                    s.nodes.c.node_id.not_in(select(s.readings.c.node_id).distinct())
                )
            )

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
        with self._store.writer():
            try:
                page_bytes = self._store.read(
                    lambda db: db.exec_driver_sql("PRAGMA page_size").scalar()
                )
                required = (
                    self._base_bytes
                    + page_bytes
                    + context_bytes
                    + max(len(json.dumps(point, allow_nan=False).encode()) for point in points)
                )
                if required > self.max_bytes:
                    raise OSError("usage history budget cannot hold this reading")
                with self._store.connection(write=True):
                    accepted = sum(
                        self._write(node_id, host, point, utc(now), model, context)
                        for point in points
                    )
                if accepted:
                    self._prune(now)
                    retained = self._store.read(
                        lambda db: any(
                            db.execute(
                                select(literal(1)).where(
                                    s.readings.c.lifetime_id == point["lifetime_id"],
                                    s.readings.c.observed_at == point["observed_at"],
                                )
                            ).first()
                            for point in points
                        )
                    )
                    if not retained:
                        raise OSError("usage history budget retired the entire new batch")
                    saved = self._meta("last_successful_save")
                    with self.lock:
                        self.last_successful_save = saved
                        self.recording, self.error = "recording", None
                return accepted
            except (sqlite3.Error, OSError, PersistenceError) as exc:
                # Earlier insertion/retention units may already be durable.
                with self.lock:
                    self.recording, self.error = "failed", str(exc)
                raise
