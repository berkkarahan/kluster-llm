"""Atomic node work reservations; durable process/listener owners remain authoritative."""

from contextlib import contextmanager
import threading
from uuid import uuid4


class NodeResources:
    def __init__(self):
        self.lock = threading.RLock()
        self.claims = {}

    def acquire(self, scope, kind):
        scope = tuple(scope)
        with self.lock:
            if self.claims and any(value[0] != scope for value in self.claims.values()):
                raise ValueError("Node resources are busy with another preparation, profile or run")
            active = {value[1] for value in self.claims.values()}
            if (kind in ("profile", "link") and active - {"prepare"}
                    or kind == "prepare" and active & {"profile", "link"}
                    or kind in ("run", "listener", "rank_start", "rpc_start") and active & {"prepare", "profile", "link"}):
                raise ValueError("Node resources are still executing measurement work")
            if kind in ("profile", "prepare") and any(value[1] == kind for value in self.claims.values()):
                raise ValueError(f"Node resources already have active {kind} work")
            token = str(uuid4())
            self.claims[token] = (scope, kind)
            return Reservation(self, token)

    @contextmanager
    def hold(self, scope, kind):
        lease = self.acquire(scope, kind)
        try:
            yield lease
        finally:
            lease.release()

    @property
    def busy(self):
        with self.lock:
            return bool(self.claims)


class Reservation:
    def __init__(self, resources, token):
        self.resources, self.token = resources, token

    def bind(self, scope):
        with self.resources.lock:
            if self.token not in self.resources.claims or len(self.resources.claims) != 1:
                raise ValueError("Resource reservation cannot change while child work is active")
            _, kind = self.resources.claims[self.token]
            self.resources.claims[self.token] = (tuple(scope), kind)

    def release(self):
        with self.resources.lock:
            self.resources.claims.pop(self.token, None)


def node_resources(registry):
    with registry.lock:
        resources = getattr(registry, "node_resources", None)
        if resources is None:
            resources = NodeResources()
            registry.node_resources = resources
        return resources


def prima_scope(operation_id, owner, contract_digest):
    return ("primacpp", str(operation_id), (owner or {}).get("authority_digest", ""), contract_digest)
