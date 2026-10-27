"""SQLite persistence for feature flags in the unified application database."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from sqlalchemy import Column, Float, Integer, MetaData, Table, Text, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from kllm.shared.persistence.sqlite import SQLiteStore
from kllm.shared.state import app_db_path, ensure_home, home_dir

schema = MetaData()

feature_flags = Table(
    "feature_flags",
    schema,
    Column("name", Text, primary_key=True, nullable=False),
    Column("enabled", Integer, nullable=False),
    Column("updated_at", Float, nullable=False),
)


def _active_store(target_home: Path) -> SQLiteStore | None:
    """Return an already-open in-process SQLiteStore for the unified app DB if available."""
    try:
        from kllm.modules.managed_network.gateways.store import NetworkStore

        net_store = NetworkStore.active_for(target_home)
        if net_store is not None:
            return net_store._store
    except Exception:
        pass
    try:
        from kllm.modules.primacpp.gateways.store import ProfilingStore

        prof_store = ProfilingStore.active_for(target_home)
        if prof_store is not None:
            return prof_store._store
    except Exception:
        pass
    return None


def _upsert_flag_stmt(name: str, enabled: bool, now: float):
    stmt = sqlite_insert(feature_flags).values(
        name=name,
        enabled=1 if enabled else 0,
        updated_at=now,
    )
    return stmt.on_conflict_do_update(
        index_elements=[feature_flags.c.name],
        set_={
            "enabled": stmt.excluded.enabled,
            "updated_at": stmt.excluded.updated_at,
        },
    )


def read_persisted_flags(home: Path | None = None) -> dict[str, bool]:
    """Read persisted feature flags from the unified SQLite database."""
    target_home = Path(home) if home is not None else home_dir()
    active = _active_store(target_home)
    if active is not None:
        def _read_active(db) -> dict[str, bool]:
            exists = db.exec_driver_sql(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feature_flags'"
            ).fetchone()
            if not exists:
                return {}
            rows = db.execute(select(feature_flags.c.name, feature_flags.c.enabled)).fetchall()
            return {str(row[0]): bool(row[1]) for row in rows}

        return active.read(_read_active)

    db_path = app_db_path(target_home)
    if not db_path.is_file() or db_path.is_symlink():
        return {}

    with SQLiteStore(db_path, schema, profile="network") as store:
        return store.read(
            lambda db: {
                str(row[0]): bool(row[1])
                for row in db.execute(
                    select(feature_flags.c.name, feature_flags.c.enabled)
                ).fetchall()
            }
        )


def write_persisted_flag(name: str, enabled: bool, home: Path | None = None) -> None:
    """Persist a single feature flag into the unified SQLite database."""
    write_persisted_flags({name: enabled}, home=home)


def write_persisted_flags(flags: dict[str, Any], home: Path | None = None) -> None:
    """Persist multiple feature flags atomically into the unified SQLite database."""
    target_home = Path(home) if home is not None else ensure_home()
    target_home.mkdir(parents=True, exist_ok=True)
    now = time.time()

    def _write(db) -> None:
        schema.create_all(db)
        for name, enabled in flags.items():
            db.execute(_upsert_flag_stmt(str(name), bool(enabled), now))

    active = _active_store(target_home)
    if active is not None:
        active.write(_write)
        return

    db_path = app_db_path(target_home)
    with SQLiteStore(db_path, schema, profile="network") as store:
        store.write(_write)
