"""Immutable pre-adoption DDL for real-file compatibility and process tests."""

import sqlite3
from pathlib import Path

USAGE_DDL = "\n            CREATE TABLE IF NOT EXISTS nodes (node_id TEXT PRIMARY KEY, host TEXT NOT NULL, role TEXT NOT NULL, context TEXT NOT NULL DEFAULT '{}');\n            CREATE TABLE IF NOT EXISTS lifetimes (\n                lifetime_id TEXT PRIMARY KEY, node_id TEXT NOT NULL, pid INTEGER NOT NULL, role TEXT NOT NULL,\n                started_at TEXT NOT NULL, ended_at TEXT, state TEXT NOT NULL, model TEXT);\n            CREATE TABLE IF NOT EXISTS associations (\n                lifetime_id TEXT NOT NULL, model TEXT NOT NULL, observed_from TEXT NOT NULL,\n                observed_until TEXT, PRIMARY KEY(lifetime_id, observed_from));\n            CREATE TABLE IF NOT EXISTS readings (\n                id INTEGER PRIMARY KEY, lifetime_id TEXT NOT NULL, node_id TEXT NOT NULL,\n                observed_at TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL,\n                UNIQUE(lifetime_id, observed_at));\n            CREATE INDEX IF NOT EXISTS readings_time ON readings(observed_at, id);\n            CREATE INDEX IF NOT EXISTS readings_filter ON readings(node_id, lifetime_id, observed_at, id);\n            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n        "

NETWORK_DDL = "\n        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n        CREATE TABLE IF NOT EXISTS peers (node_id TEXT PRIMARY KEY, role TEXT NOT NULL, body TEXT NOT NULL);\n        CREATE UNIQUE INDEX IF NOT EXISTS one_master ON peers(role) WHERE role='master';\n        CREATE TABLE IF NOT EXISTS node_directory (node_id TEXT PRIMARY KEY, body TEXT NOT NULL);\n        CREATE TABLE IF NOT EXISTS authority (binding TEXT PRIMARY KEY, body TEXT NOT NULL);\n        CREATE TABLE IF NOT EXISTS channels (peer TEXT PRIMARY KEY, binding TEXT NOT NULL, term INTEGER NOT NULL, session TEXT NOT NULL, channel TEXT NOT NULL);\n        CREATE TABLE IF NOT EXISTS networks (id TEXT PRIMARY KEY, binding TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL);\n        CREATE UNIQUE INDEX IF NOT EXISTS one_live_network ON networks(binding) WHERE state!='stopped';\n        CREATE TABLE IF NOT EXISTS assignments (network TEXT REFERENCES networks(id), node TEXT, address TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(network,node), UNIQUE(network,address));\n        CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, network TEXT REFERENCES networks(id), binding TEXT NOT NULL, request TEXT NOT NULL, digest TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, UNIQUE(binding,request));\n        CREATE TABLE IF NOT EXISTS results (operation TEXT REFERENCES operations(id), node TEXT, phase_sequence INTEGER, body TEXT NOT NULL, PRIMARY KEY(operation,node,phase_sequence));\n        CREATE TABLE IF NOT EXISTS journals (id TEXT PRIMARY KEY, network TEXT REFERENCES networks(id), operation TEXT REFERENCES operations(id), node TEXT NOT NULL, phase TEXT NOT NULL, body TEXT NOT NULL);\n        CREATE TABLE IF NOT EXISTS commands (key TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL, result TEXT);\n        CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, network TEXT REFERENCES networks(id), state TEXT NOT NULL, body TEXT NOT NULL);\n        CREATE UNIQUE INDEX IF NOT EXISTS one_live_run ON reservations(network) WHERE state!='released';\n        CREATE TABLE IF NOT EXISTS leases (id TEXT PRIMARY KEY, network TEXT REFERENCES networks(id), node TEXT NOT NULL, generation INTEGER NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL);\n        CREATE UNIQUE INDEX IF NOT EXISTS one_live_lease ON leases(network,node) WHERE state!='released';\n        CREATE TABLE IF NOT EXISTS evidence (network TEXT REFERENCES networks(id), node TEXT, body TEXT NOT NULL, PRIMARY KEY(network,node));\n        "


def legacy_file(path: Path, domain: str, *, missing_context: bool = False) -> None:
    """Create a pre-adoption file without invoking the implementation under test."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ddl = USAGE_DDL if domain == "usage" else NETWORK_DDL
    if missing_context:
        ddl = ddl.replace(", context TEXT NOT NULL DEFAULT '{}'", "")
    with sqlite3.connect(path) as db:
        if domain == "usage":
            db.execute("PRAGMA auto_vacuum=FULL")
        db.executescript(ddl)
        if domain == "usage":
            db.execute("INSERT INTO meta VALUES (?, ?)", ("cursor_secret", "ab" * 32))


def seed_legacy_rows(path: Path, rows: dict[str, list[dict]]) -> None:
    """Seed valid domain-model rows supplied by fixtures using the legacy driver."""
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, entries in rows.items():
            if table not in tables:
                raise ValueError("unknown legacy table")
            columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
            for row in entries:
                if not set(row) <= columns:
                    raise ValueError("unknown legacy column")
                names = ",".join(f'"{name}"' for name in row)
                placeholders = ",".join("?" for _ in row)
                db.execute(
                    f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})', tuple(row.values())
                )
