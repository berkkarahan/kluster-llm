from __future__ import annotations

import json
import os
import secrets
import time
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kllm.shared.atomic_io import replace_file

DEFAULT_CONTROL_PORT = 18765
DEFAULT_RPC_PORT = 50052
DEFAULT_INFER_PORT = 8080
CLUSTER_STATE_SCHEMA_VERSION = 2
SERVICE_TYPE = "_kllm._tcp.local."


def _node_unified_memory(backend: str) -> bool:
    """Classify this node's memory pool, including a local config override."""
    from kllm.shared.accel import is_unified_memory

    return is_unified_memory(backend)


class ClusterStateLoadError(ValueError):
    """Persisted cluster state cannot be read safely."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"cannot load cluster state at {path}: {reason}")


def _read_state_data(path: Path) -> tuple[dict[str, Any], int]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ClusterStateLoadError(path, "file is not valid UTF-8") from exc
    except OSError as exc:
        raise ClusterStateLoadError(path, f"file could not be read ({type(exc).__name__})") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        reason = f"invalid JSON at line {exc.lineno}, column {exc.colno}"
        raise ClusterStateLoadError(path, reason) from exc
    if not isinstance(data, dict):
        raise ClusterStateLoadError(path, "state must contain a JSON object")
    version = data.get("schema_version", 1)
    if type(version) is not int or version < 1:
        raise ClusterStateLoadError(path, "schema_version must be a positive integer")
    return data, version


def home_dir() -> Path:
    override = os.environ.get("KLLM_HOME")
    if override:
        return Path(override)
    return Path.home() / ".kllm"


def models_dir() -> Path:
    d = home_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = home_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_path() -> Path:
    return home_dir() / "cluster.json"


def config_path() -> Path:
    return home_dir() / "config.toml"


def runtime_path() -> Path:
    """Hidden registry of processes started by kllm (agent + children)."""
    return home_dir() / ".runtime.json"


def ensure_home() -> Path:
    d = home_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "models").mkdir(exist_ok=True)
    (d / "logs").mkdir(exist_ok=True)
    return d


def generate_join_key() -> str:
    return secrets.token_urlsafe(12)


def _looks_like_repo(path: Path) -> bool:
    return (path / "crates" / "kllm-native").is_dir()


def repo_root() -> Path | None:
    env = os.environ.get("KLLM_REPO_ROOT")
    if env:
        return Path(env)

    starts: list[Path] = []
    try:
        import kllm as _kllm

        # src/kllm/__init__.py → parents[2] is the repo root in a source checkout
        starts.append(Path(_kllm.__file__).resolve().parents[2])
    except Exception:
        pass
    starts.append(Path.cwd())

    seen: set[Path] = set()
    for start in starts:
        for p in [start, *start.parents]:
            if p in seen:
                continue
            seen.add(p)
            if _looks_like_repo(p):
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
            "unified_memory": _node_unified_memory(self.backend),
        }


@dataclass
class ClusterState:
    role: str = "idle"  # idle | master | worker
    node_id: str = ""
    join_key: str | None = None
    master_host: str | None = None
    master_port: int = DEFAULT_CONTROL_PORT
    # Localhost control-plane proxy when agent Python cannot open LAN sockets
    # (macOS Local Network / Zscaler). HTTP goes to 127.0.0.1:cp_proxy_port;
    # master_host stays the real LAN IP for display / rpc toward=.
    cp_proxy_port: int | None = None
    cp_proxy_pid: int | None = None
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
    # llama.cpp install warnings that must not be silent, e.g. a cached pack
    # refused on macOS because it would not provide Metal + Vulkan.
    llama_warnings: list[str] = field(default_factory=list)
    last_settings: dict[str, Any] | None = None
    active_config: dict[str, Any] | None = None
    apply_error: str | None = None
    apply_warning: str | None = None
    # Latest master inference state, mirrored to workers through heartbeats.
    inference_activity: dict[str, Any] | None = None
    logging_enabled: bool = True
    logging_generation: str = ""
    schema_version: int = CLUSTER_STATE_SCHEMA_VERSION

    def save(self) -> None:
        ensure_home()
        # Output sinks read the policy independently; never expose partial JSON.
        fd, name = tempfile.mkstemp(prefix=".cluster-", dir=home_dir())
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(asdict(self), handle, indent=2)
            replace_file(name, state_path())
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @classmethod
    def upgrade_pending(cls) -> bool:
        """Return whether saved cluster state predates the transient-state schema."""
        path = state_path()
        if not path.exists():
            return False
        _, version = _read_state_data(path)
        return version < CLUSTER_STATE_SCHEMA_VERSION

    @classmethod
    def load(cls, *, migrate: bool = True) -> ClusterState:
        ensure_home()
        path = state_path()
        if not path.exists():
            return cls(node_id=secrets.token_hex(4))
        data, version = _read_state_data(path)
        state = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        if migrate and version < CLUSTER_STATE_SCHEMA_VERSION:
            state.reset_transient_state()
            state.save()
        return state

    def reset_transient_state(self) -> None:
        """Clear membership and process state while keeping operator run settings."""
        self.role = "idle"
        self.join_key = None
        self.master_host = None
        self.cp_proxy_port = None
        self.cp_proxy_pid = None
        self.members.clear()
        self.process_pid = None
        self.process_role = None
        self.last_plan = None
        self.model_name = None
        self.active_config = None
        self.gpu_ids = None
        self.rpc_bind = None
        self.overlay = "none"
        self.wg_address = None
        self.wg_public_key = None
        self.overlay_status = None
        self.netsec_warnings.clear()
        self.llama_warnings.clear()
        self.apply_error = None
        self.apply_warning = None
        self.inference_activity = None
        self.schema_version = CLUSTER_STATE_SCHEMA_VERSION
