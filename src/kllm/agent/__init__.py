"""FastAPI composition root for the Kluster agent."""

from __future__ import annotations

import importlib
import sys

from kllm.shared.state import ClusterState
from kllm.shared import state_ref

STATE: ClusterState | None = None


def _replace_state(state: ClusterState) -> None:
    global STATE
    STATE = state


state_ref.bind(lambda: STATE)
state_ref.bind_replace(_replace_state)


def _load_runtime():
    already = "kllm.agent.runtime" in sys.modules
    from kllm.agent import runtime as wiring

    if already:
        importlib.reload(wiring)
    return wiring


_wiring = _load_runtime()

app = _wiring.app
lifespan = _wiring.lifespan
legacy_v1_router = _wiring.legacy_v1_router
health = _wiring.health
native = _wiring.native
netsec = _wiring.netsec
discovery = _wiring.discovery
http_clients = _wiring.http_clients
gateway = _wiring.gateway
membership_mod = _wiring.membership_mod
httpx = _wiring.httpx
threading = _wiring.threading
model_settings_store = _wiring.model_settings_store
cluster_update = _wiring.cluster_update

__all__ = [
    "STATE",
    "ClusterState",
    "app",
    "cluster_update",
    "create_app",
    "discovery",
    "gateway",
    "health",
    "http_clients",
    "httpx",
    "legacy_v1_router",
    "lifespan",
    "membership_mod",
    "model_settings_store",
    "native",
    "netsec",
    "threading",
]


def create_app():
    return app
