"""HTTP handlers for cluster worker registration and liveness."""

from __future__ import annotations

from kllm.exceptions import PersistenceError

import time
from collections.abc import Callable
import sqlite3
from typing import Any

from fastapi import HTTPException, Request

from kllm import membership, netsec, usage
from kllm.agent.lifecycle_bus import LifecycleEvent
from kllm.agent.schemas import RegisterBody, UnregisterBody
from kllm.state import ClusterState


def observed_control_source(request: Request) -> str | None:
    """Return the TCP peer the worker used to reach this master.

    The dashboard compares this address with the managed subnet to tell a
    router path from the dedicated fast subnet. IPv4-mapped IPv6 peers are
    stored as dotted IPv4 so that comparison stays exact.
    """
    peer = request.client.host if request.client else None
    if not isinstance(peer, str):
        return None
    peer = peer.strip()
    mapped = "::ffff:"
    if peer.lower().startswith(mapped):
        peer = peer[len(mapped) :]
    return peer or None


class MembershipHandlers:
    """Handle membership routes through injected agent collaborators."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        sync_liveness: Callable[..., list[str]],
        resources: usage.ResourceState,
        resource_probe_available: Callable[[], bool],
        store: Callable[[], usage.UsageStore],
        master_lan_host: Callable[[], str],
        inference_activity: Callable[[], dict[str, Any]],
        shrink_cluster: Callable[[ClusterState], None] | None = None,
        directory_sync: Callable[[], None] | None = None,
        directory_snapshot: Callable[[], list[dict[str, Any]]] | None = None,
        directory_remove: Callable[[str], None] | None = None,
        lifecycle_publisher: Callable[[LifecycleEvent, str | None], None] | None = None,
    ) -> None:
        self._state = state
        self._sync_liveness = sync_liveness
        self._resources = resources
        self._resource_probe_available = resource_probe_available
        self._store = store
        self._master_lan_host = master_lan_host
        self._inference_activity = inference_activity
        self._shrink_cluster = shrink_cluster or membership.on_membership_shrink
        self._directory_sync = directory_sync
        self._directory_snapshot = directory_snapshot
        self._directory_remove = directory_remove
        self._lifecycle_publisher = lifecycle_publisher

    def _emit(self, event: LifecycleEvent, subject: str | None) -> None:
        if self._lifecycle_publisher is not None:
            self._lifecycle_publisher(event, subject)

    def _refresh_directory(self) -> None:
        if self._directory_sync is None:
            return
        try:
            self._directory_sync()
        except (sqlite3.Error, PersistenceError):
            pass  # Liveness stays independent of the identity read model.

    def _with_directory(self, response: dict[str, Any]) -> dict[str, Any]:
        if self._directory_snapshot is not None:
            try:
                response["node_directory"] = self._directory_snapshot()
            except (sqlite3.Error, PersistenceError):
                response["node_directory"] = []
        return response

    def register(self, body: RegisterBody, request: Request) -> dict[str, Any]:
        state = self._state()
        if state.role != "master":
            raise HTTPException(400, "not a master")
        self._sync_liveness(shrink=True)
        self._resources.prune(state.members)
        if body.resource_session is not None:
            if (
                body.node_id not in state.members
                or body.node_id == state.node_id
                or not self._resource_probe_available()
            ):
                raise HTTPException(
                    400, "resource session requires a registered worker and native support"
                )
            try:
                generation = self._resources.establish(body.node_id, body.resource_session)
            except usage.ResourceConflict as exc:
                raise HTTPException(
                    409, {"message": str(exc), "resource_generation": exc.generation}
                ) from exc
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            return {
                "ok": True,
                "request_id": body.resource_session["request_id"],
                "resource_generation": generation,
            }

        advertised = body.lan_host or body.host
        peer = observed_control_source(request)
        response: dict[str, Any] = {"ok": True}
        if (
            state.overlay == netsec.OVERLAY_NONE
            and isinstance(peer, str)
            and netsec.is_usable_lan_peer(peer)
            and peer != advertised
        ):
            response["rpc_bind_hint"] = peer

        member: dict[str, Any] = {
            **body.model_dump(exclude={"resource_session"}),
            "host": advertised,
            "lan_host": advertised,
            "role": "worker",
            "last_seen": time.time(),
        }
        if peer:
            member["control_source"] = peer
        if state.overlay == netsec.OVERLAY_WIREGUARD:
            if not body.wg_public_key:
                raise HTTPException(400, "wg_public_key required when master overlay=wireguard")
            wg_addr = netsec.next_wg_address(state.members, master=False)
            member["wg_address"] = wg_addr
            member["wg_public_key"] = body.wg_public_key
            member["host"] = wg_addr
            member["lan_host"] = body.lan_host or body.host
            psk = netsec.derive_wg_psk(state.join_key or "")
            endpoint = f"{member['lan_host']}:{body.wg_listen_port or netsec.WG_LISTEN_PORT}"
            live = netsec.add_peer_to_running(
                public_key=body.wg_public_key,
                allowed_ips=f"{wg_addr}/32",
                endpoint=endpoint,
                psk=psk,
            )
            if not live.get("ok") and live.get("warning"):
                state.netsec_warnings = list(state.netsec_warnings or []) + [str(live["warning"])]
            self.rewrite_master_wg_peers()
            master_lan = self._master_lan_host()
            response["wg_address"] = wg_addr
            response["wg_peers"] = [
                {
                    "public_key": state.wg_public_key or "",
                    "endpoint": f"{master_lan}:{netsec.WG_LISTEN_PORT}",
                    "allowed_ips": f"{state.wg_address}/32",
                    "address": state.wg_address or "",
                }
            ]
            response["wg_listen_port"] = netsec.WG_LISTEN_PORT

        state.members[body.node_id] = member
        state.save()
        membership.on_membership_grow(state)
        self._refresh_directory()
        self._emit(LifecycleEvent.WORKER_JOINED, body.node_id)
        response["members"] = len(state.members)
        response.update(membership.snapshot_payload(state, self._inference_activity()))
        return self._with_directory(response)

    def rewrite_master_wg_peers(self) -> None:
        """Rewrite the live WireGuard config with every registered worker peer."""
        state = self._state()
        if state.overlay != netsec.OVERLAY_WIREGUARD or not state.wg_address or not state.join_key:
            return
        try:
            private_key, _public_key = netsec.load_or_create_keypair()
            psk = netsec.derive_wg_psk(state.join_key)
            peers: list[dict[str, str]] = []
            for member in state.members.values():
                if member.get("role") != "worker":
                    continue
                public_key = member.get("wg_public_key")
                address = member.get("wg_address")
                if not public_key or not address:
                    continue
                lan = member.get("lan_host") or member.get("host")
                peer: dict[str, str] = {
                    "public_key": public_key,
                    "allowed_ips": f"{address}/32",
                    "address": address,
                }
                if lan and not str(lan).startswith(netsec.WG_PREFIX):
                    peer["endpoint"] = (
                        f"{lan}:{member.get('wg_listen_port', netsec.WG_LISTEN_PORT)}"
                    )
                peers.append(peer)
            config = netsec.build_wg_conf(
                private_key=private_key,
                address=state.wg_address,
                listen_port=netsec.WG_LISTEN_PORT,
                psk=psk,
                peers=peers,
            )
            netsec.bring_up_wireguard(config)
        except Exception as exc:
            state.netsec_warnings = list(state.netsec_warnings or []) + [f"wg peer rewrite: {exc}"]

    def unregister(self, body: UnregisterBody) -> dict[str, Any]:
        state = self._state()
        self._sync_liveness(shrink=True)
        node_id = body.node_id
        if node_id and node_id != state.node_id and node_id in state.members:
            state.members.pop(node_id, None)
            self._resources.prune(state.members)
            if self._directory_remove is not None:
                self._directory_remove(node_id)
            self._shrink_cluster(state)
            self._emit(LifecycleEvent.WORKER_LEFT, node_id)
        else:
            state.save()
        return {"ok": True}

    def heartbeat(self, body: RegisterBody, request: Request) -> dict[str, Any]:
        state = self._state()
        self._sync_liveness(shrink=True)
        accepted = body.node_id in state.members
        if accepted:
            update = body.model_dump(exclude={"resource_session"})
            if not update.get("process_pid") and state.members[body.node_id].get("process_pid"):
                try:
                    self._store().end_node(body.node_id)
                except (OSError, sqlite3.Error, PersistenceError):
                    pass
            state.members[body.node_id].update(update)
            peer = observed_control_source(request)
            if peer:
                state.members[body.node_id]["control_source"] = peer
            state.members[body.node_id]["last_seen"] = time.time()
            state.save()
            self._refresh_directory()
        response = {
            "ok": True,
            "accepted": accepted,
            **membership.snapshot_payload(state, self._inference_activity()),
        }
        return self._with_directory(response)
