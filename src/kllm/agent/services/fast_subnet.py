"""Move a router-joined worker onto the managed fast subnet."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any

import httpx

from kllm.agent.schemas import FastSubnetReconnectBody, JoinBody
from kllm.exceptions import (
    AuthenticationError,
    ConflictError,
    ExternalServiceError,
    ValidationError,
)
from kllm.state import DEFAULT_CONTROL_PORT, DEFAULT_RPC_PORT, ClusterState

PROBE_TIMEOUT_S = 2.0
RECONNECT_TIMEOUT_S = 45.0

NetworkList = Callable[[], list[dict[str, Any]]]
Probe = Callable[[str, int, str, float], bool]
Poster = Callable[[str, dict[str, Any], dict[str, str], float], dict[str, Any]]
Joiner = Callable[[JoinBody], dict[str, Any]]


def address_in_subnet(address: str | None, subnet: str | None) -> bool:
    """True when ``address`` is an IPv4 host inside ``subnet``."""
    if not address or not subnet:
        return False
    try:
        ip = ipaddress.IPv4Address(address)
        network = ipaddress.IPv4Network(subnet, strict=False)
    except ValueError:
        return False
    return ip in network


def private_unicast(address: str | None) -> bool:
    """True for a global-scope RFC1918 IPv4 address, not loopback or link-local."""
    if not address:
        return False
    try:
        ip = ipaddress.IPv4Address(address)
    except ValueError:
        return False
    return bool(
        ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_unspecified
    )


def probe_tcp(host: str, port: int, source: str, timeout: float) -> bool:
    """One TCP connect from ``source`` to ``host:port``. Failure is unreachable."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.bind((source, 0))
        sock.connect((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    """POST a reconnect command and surface transport failures as domain errors."""
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.post(url, json=payload, headers=headers)
    except httpx.RequestError as exc:
        raise ExternalServiceError("Worker did not respond on its management address") from exc
    if response.status_code >= 400:
        raise ExternalServiceError(_upstream_message(response))
    try:
        body = response.json()
    except ValueError as exc:
        raise ExternalServiceError("Worker returned an invalid reconnect response") from exc
    if not isinstance(body, dict):
        raise ExternalServiceError("Worker returned an invalid reconnect response")
    return body


def _upstream_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            details = error.get("details")
            message = error.get("message")
            if isinstance(details, dict) and isinstance(details.get("message"), str):
                return str(details["message"])
            if isinstance(details, str) and details.strip():
                return details.strip()
            placeholder = "The request could not be completed."
            if isinstance(message, str) and message and message != placeholder:
                return message
        detail = body.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return "Worker refused the fast subnet reconnect"


def _ready_network(networks: list[dict[str, Any]]) -> dict[str, Any] | None:
    for network in networks:
        if network.get("observed_state") == "ready" and isinstance(network.get("subnet"), str):
            return network
    return None


def _participant(network: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    for participant in network.get("participants") or []:
        if isinstance(participant, dict) and participant.get("node_id") == node_id:
            return participant
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def observed_address(member: dict[str, Any]) -> str | None:
    """Address the worker currently uses to reach the master, when known."""
    for key in ("control_source", "lan_host", "host"):
        address = _text(member.get(key))
        if address:
            return address
    return None


def management_address(member: dict[str, Any], subnet: str) -> str | None:
    """Prefer the path that is still outside the fast subnet."""
    for key in ("control_source", "lan_host", "host"):
        address = _text(member.get(key))
        if address and not address_in_subnet(address, subnet):
            return address
    return None


class FastSubnetService:
    """Probe the managed subnet and rejoin one worker through its private address."""

    def __init__(
        self,
        *,
        networks: NetworkList,
        probe: Probe = probe_tcp,
        post: Poster = post_json,
        join: Joiner,
    ) -> None:
        self._networks = networks
        self._probe = probe
        self._post = post
        self._join = join

    def reachability(self, state: ClusterState) -> dict[str, Any]:
        """Probe each joined worker once from the master's private address."""
        if state.role != "master":
            raise ValidationError("Fast subnet reachability requires the master")
        network = _ready_network(self._networks())
        if network is None:
            return {
                "network_id": None,
                "subnet": None,
                "master_address": None,
                "workers": [],
            }
        master = _participant(network, state.node_id)
        master_address = _text((master or {}).get("address"))
        subnet = str(network.get("subnet"))
        workers: list[dict[str, Any]] = []
        for participant in network.get("participants") or []:
            if not isinstance(participant, dict):
                continue
            node_id = participant.get("node_id")
            if not isinstance(node_id, str) or node_id == state.node_id:
                continue
            member = state.members.get(node_id) or {}
            if member.get("role") == "master":
                continue
            address = _text(participant.get("address"))
            port = int(member.get("control_port") or state.control_port or DEFAULT_CONTROL_PORT)
            reachable = False
            if (
                address
                and master_address
                and private_unicast(address)
                and private_unicast(master_address)
            ):
                reachable = self._probe(address, port, master_address, PROBE_TIMEOUT_S)
            workers.append(
                {
                    "node_id": node_id,
                    "address": address,
                    "reachable": reachable,
                }
            )
        return {
            "network_id": network.get("network_id"),
            "subnet": subnet,
            "master_address": master_address,
            "workers": workers,
        }

    def reconnect(
        self,
        state: ClusterState,
        body: FastSubnetReconnectBody,
        supplied_key: str | None,
    ) -> dict[str, Any]:
        """Master commands one worker; a worker re-runs join on the private master."""
        if state.role == "master":
            return self._command(state, body, supplied_key)
        if state.role == "worker":
            return self._accept(state, body, supplied_key)
        raise ValidationError("Fast subnet reconnect requires a master or worker")

    def _command(
        self,
        state: ClusterState,
        body: FastSubnetReconnectBody,
        supplied_key: str | None,
    ) -> dict[str, Any]:
        if not state.join_key:
            raise ExternalServiceError("master has no join key")
        if supplied_key is not None and supplied_key != state.join_key:
            raise AuthenticationError("invalid join key")
        node_id = _text(body.node_id)
        if not node_id:
            raise ValidationError("node_id is required")
        member = state.members.get(node_id)
        if not member or member.get("role") == "master":
            raise ValidationError("Worker is not joined")
        network = _ready_network(self._networks())
        if network is None:
            raise ConflictError("The fast subnet is not ready")
        subnet = str(network["subnet"])
        master = _participant(network, state.node_id)
        worker = _participant(network, node_id)
        master_address = _text((master or {}).get("address"))
        worker_address = _text((worker or {}).get("address"))
        if (
            not master_address
            or not worker_address
            or not private_unicast(master_address)
            or not address_in_subnet(master_address, subnet)
            or not address_in_subnet(worker_address, subnet)
        ):
            raise ConflictError("The fast subnet is not ready")
        if address_in_subnet(observed_address(member), subnet):
            raise ConflictError("This worker is already connected through the fast subnet")
        host = management_address(member, subnet)
        if not host:
            raise ValidationError("Worker has no management address to reconnect through")
        port = int(member.get("control_port") or state.control_port or DEFAULT_CONTROL_PORT)
        result = self._post(
            f"http://{host}:{port}/v2/cluster/fast-subnet/reconnect",
            {
                "master_host": master_address,
                "master_port": int(state.control_port or DEFAULT_CONTROL_PORT),
            },
            {"X-Kllm-Key": state.join_key},
            RECONNECT_TIMEOUT_S,
        )
        return {"ok": True, "node_id": node_id, "master_host": master_address, "result": result}

    def _accept(
        self,
        state: ClusterState,
        body: FastSubnetReconnectBody,
        supplied_key: str | None,
    ) -> dict[str, Any]:
        if not state.join_key or supplied_key != state.join_key:
            raise AuthenticationError("invalid join key")
        master_host = _text(body.master_host)
        if not private_unicast(master_host):
            raise ValidationError("Fast subnet master address must be a private IPv4 address")
        joined = self._join(
            JoinBody(
                key=state.join_key,
                master_host=master_host,
                master_port=int(body.master_port or state.master_port or DEFAULT_CONTROL_PORT),
                rpc_port=int(state.rpc_port or DEFAULT_RPC_PORT),
                rpc_bind=None,
                overlay=state.overlay or "none",
                gpu_ids=list(state.gpu_ids) if state.gpu_ids is not None else None,
            )
        )
        return {"ok": True, "accepted": True, "master": joined.get("master", master_host)}
