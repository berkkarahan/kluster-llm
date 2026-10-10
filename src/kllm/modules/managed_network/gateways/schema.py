"""Core declarations preserving managed-network identity and ownership records."""

from sqlalchemy import Column, ForeignKey, Index, Integer, MetaData, Table, Text, UniqueConstraint

schema = MetaData()
metadata = Table(
    "metadata",
    schema,
    Column("key", Text, primary_key=True, nullable=True),
    Column("value", Text, nullable=False),
)
peers = Table(
    "peers",
    schema,
    Column("node_id", Text, primary_key=True, nullable=True),
    Column("role", Text, nullable=False),
    Column("body", Text, nullable=False),
)
Index("one_master", peers.c.role, unique=True, sqlite_where=peers.c.role == "master")
node_directory = Table(
    "node_directory",
    schema,
    Column("node_id", Text, primary_key=True, nullable=True),
    Column("body", Text, nullable=False),
)
authority = Table(
    "authority",
    schema,
    Column("binding", Text, primary_key=True, nullable=True),
    Column("body", Text, nullable=False),
)
channels = Table(
    "channels",
    schema,
    Column("peer", Text, primary_key=True, nullable=True),
    Column("binding", Text, nullable=False),
    Column("term", Integer, nullable=False),
    Column("session", Text, nullable=False),
    Column("channel", Text, nullable=False),
)
networks = Table(
    "networks",
    schema,
    Column("id", Text, primary_key=True, nullable=True),
    Column("binding", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("body", Text, nullable=False),
)
Index(
    "one_live_network", networks.c.binding, unique=True, sqlite_where=networks.c.state != "stopped"
)
assignments = Table(
    "assignments",
    schema,
    Column("network", Text, ForeignKey("networks.id"), primary_key=True, nullable=True),
    Column("node", Text, primary_key=True, nullable=True),
    Column("address", Text, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("body", Text, nullable=False),
    UniqueConstraint("network", "address"),
)
operations = Table(
    "operations",
    schema,
    Column("id", Text, primary_key=True, nullable=True),
    Column("network", Text, ForeignKey("networks.id")),
    Column("binding", Text, nullable=False),
    Column("request", Text, nullable=False),
    Column("digest", Text, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("body", Text, nullable=False),
    UniqueConstraint("binding", "request"),
)
results = Table(
    "results",
    schema,
    Column("operation", Text, ForeignKey("operations.id"), primary_key=True, nullable=True),
    Column("node", Text, primary_key=True, nullable=True),
    Column("phase_sequence", Integer, primary_key=True, nullable=True),
    Column("body", Text, nullable=False),
)
journals = Table(
    "journals",
    schema,
    Column("id", Text, primary_key=True, nullable=True),
    Column("network", Text, ForeignKey("networks.id")),
    Column("operation", Text, ForeignKey("operations.id")),
    Column("node", Text, nullable=False),
    Column("phase", Text, nullable=False),
    Column("body", Text, nullable=False),
)
commands = Table(
    "commands",
    schema,
    Column("key", Text, primary_key=True, nullable=True),
    Column("digest", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("result", Text),
)
reservations = Table(
    "reservations",
    schema,
    Column("id", Text, primary_key=True, nullable=True),
    Column("network", Text, ForeignKey("networks.id")),
    Column("state", Text, nullable=False),
    Column("body", Text, nullable=False),
)
Index(
    "one_live_run",
    reservations.c.network,
    unique=True,
    sqlite_where=reservations.c.state != "released",
)
leases = Table(
    "leases",
    schema,
    Column("id", Text, primary_key=True, nullable=True),
    Column("network", Text, ForeignKey("networks.id")),
    Column("node", Text, nullable=False),
    Column("generation", Integer, nullable=False),
    Column("state", Text, nullable=False),
    Column("body", Text, nullable=False),
)
Index(
    "one_live_lease",
    leases.c.network,
    leases.c.node,
    unique=True,
    sqlite_where=leases.c.state != "released",
)
evidence = Table(
    "evidence",
    schema,
    Column("network", Text, ForeignKey("networks.id"), primary_key=True, nullable=True),
    Column("node", Text, primary_key=True, nullable=True),
    Column("body", Text, nullable=False),
)
