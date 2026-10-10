"""Named Core declarations matching the existing usage-history file format."""

from sqlalchemy import Column, Index, Integer, MetaData, Table, Text, UniqueConstraint, text

metadata = MetaData()
nodes = Table(
    "nodes",
    metadata,
    Column("node_id", Text, primary_key=True, nullable=True),
    Column("host", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("context", Text, nullable=False, server_default=text("'{}'")),
)
lifetimes = Table(
    "lifetimes",
    metadata,
    Column("lifetime_id", Text, primary_key=True, nullable=True),
    Column("node_id", Text, nullable=False),
    Column("pid", Integer, nullable=False),
    Column("role", Text, nullable=False),
    Column("started_at", Text, nullable=False),
    Column("ended_at", Text),
    Column("state", Text, nullable=False),
    Column("model", Text),
)
associations = Table(
    "associations",
    metadata,
    Column("lifetime_id", Text, primary_key=True, nullable=False),
    Column("model", Text, nullable=False),
    Column("observed_from", Text, primary_key=True, nullable=False),
    Column("observed_until", Text),
)
readings = Table(
    "readings",
    metadata,
    Column("id", Integer, primary_key=True, nullable=True),
    Column("lifetime_id", Text, nullable=False),
    Column("node_id", Text, nullable=False),
    Column("observed_at", Text, nullable=False),
    Column("received_at", Text, nullable=False),
    Column("payload", Text, nullable=False),
    UniqueConstraint("lifetime_id", "observed_at"),
)
Index("readings_time", readings.c.observed_at, readings.c.id)
Index(
    "readings_filter",
    readings.c.node_id,
    readings.c.lifetime_id,
    readings.c.observed_at,
    readings.c.id,
)
meta = Table(
    "meta",
    metadata,
    Column("key", Text, primary_key=True, nullable=True),
    Column("value", Text, nullable=False),
)
