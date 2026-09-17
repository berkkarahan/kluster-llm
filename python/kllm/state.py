from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONTROL_PORT = 18765
DEFAULT_RPC_PORT = 50052
DEFAULT_INFER_PORT = 8080
SERVICE_TYPE = "_kllm._tcp.local."


def home_dir() -> Path:
    override = os.environ.get("KLLM_HOME")
    if override:
        return Path(override)
    return Path.home() / ".kllm"


def models_dir() -> Path:
    d = home_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_path() -> Path:
    return home_dir() / "cluster.json"


def config_path() -> Path:
    return home_dir() / "config.toml"


def ensure_home() -> Path:
    d = home_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "models").mkdir(exist_ok=True)
    (d / "logs").mkdir(exist_ok=True)
    return d


def generate_join_key() -> str:
    return secrets.token_urlsafe(12)


def repo_root() -> Path | None:
    env = os.environ.get("KLLM_REPO_ROOT")
    if env:
        return Path(env)
    # Walk up from CWD looking for marker files
    cur = Path.cwd()
    for p in [cur, *cur.parents]:
        if (p / "coordinator-macos").is_dir() and (p / "worker-windows").is_dir():
            return p
        if (p / "crates" / "kllm-native").is_dir():
            return p
    return None


@dataclass
class NodeInfo:
    node_id: str
    host: str
    control_port: int = DEFAULT_CONTROL_PORT
    rpc_port: int = DEFAULT_RPC_PORT
    role: str = "worker"
    total_ram_bytes: int = 0
    available_ram_bytes: int = 0
    vram_bytes: int | None = None
    backend: str = "unknown"
    last_seen: float = field(default_factory=time.time)

    def capacity_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "total_ram_bytes": self.total_ram_bytes,
            "available_ram_bytes": self.available_ram_bytes,
            "vram_bytes": self.vram_bytes,
            "backend": self.backend,
            "unified_memory": self.backend == "metal",
        }


@dataclass
class ClusterState:
    role: str = "idle"  # idle | master | worker
    node_id: str = ""
    join_key: str | None = None
    master_host: str | None = None
    master_port: int = DEFAULT_CONTROL_PORT
    control_port: int = DEFAULT_CONTROL_PORT
    rpc_port: int = DEFAULT_RPC_PORT
    infer_port: int = DEFAULT_INFER_PORT
    members: dict[str, dict[str, Any]] = field(default_factory=dict)
    process_pid: int | None = None
    process_role: str | None = None
    last_plan: dict[str, Any] | None = None
    model_name: str | None = None
    # Selected local GPU indices (None = not yet chosen / use all at next start)
    gpu_ids: list[int] | None = None
    # RPC netsec — LAN bind by default; optional WireGuard overlay
    rpc_bind: str | None = None
    overlay: str = "none"  # none | wireguard
    wg_address: str | None = None
    wg_public_key: str | None = None
    overlay_status: str | None = None
    netsec_warnings: list[str] = field(default_factory=list)

    def save(self) -> None:
        ensure_home()
        state_path().write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> ClusterState:
        ensure_home()
        path = state_path()
        if not path.exists():
            return cls(node_id=secrets.token_hex(4))
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
