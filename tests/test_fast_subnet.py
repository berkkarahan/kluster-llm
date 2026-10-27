"""Fast-subnet reachability and worker reconnect."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kllm.modules.cluster.interactors.fast_subnet_http import FastSubnetHandlers
from kllm.modules.cluster.router_http import MembershipHandlers
from kllm.agent.schemas import FastSubnetReconnectBody, JoinBody, RegisterBody
from kllm.modules.cluster.interactors.fast_subnet import FastSubnetService
from kllm.shared.exceptions import AuthenticationError, ConflictError, ExternalServiceError, ValidationError
from kllm.shared.state import ClusterState

SUBNET = "10.10.10.0/24"
NETWORK = {
    "network_id": "net-1",
    "observed_state": "ready",
    "subnet": SUBNET,
    "participants": [
        {"node_id": "master", "address": "10.10.10.1"},
        {"node_id": "w1", "address": "10.10.10.2"},
    ],
}


def _service(
    networks: list[dict] | None = None,
    *,
    probe=None,
    post=None,
    join=None,
) -> FastSubnetService:
    return FastSubnetService(
        networks=lambda: list(NETWORK and (networks if networks is not None else [NETWORK])),
        probe=probe or (lambda *_args: True),
        post=post or (lambda *_args: {"ok": True}),
        join=join or (lambda body: {"master": body.master_host}),
    )


def _handlers(service: FastSubnetService, state: ClusterState) -> FastSubnetHandlers:
    return FastSubnetHandlers(service, lambda: state)


def _master(**members: dict) -> ClusterState:
    state = ClusterState(
        role="master",
        node_id="master",
        join_key="secret",
        control_port=18765,
        members={
            "master": {"node_id": "master", "role": "master", "host": "192.168.1.1"},
            **members,
        },
    )
    state.save = lambda: None  # type: ignore[method-assign]
    return state


def test_register_and_heartbeat_record_observed_control_source() -> None:
    state = ClusterState(role="master", node_id="master")
    state.save = lambda: None  # type: ignore[method-assign]
    handlers = MembershipHandlers(
        lambda: state,
        sync_liveness=lambda **_: [],
        resources=SimpleNamespace(prune=lambda _: None),
        resource_probe_available=lambda: False,
        store=lambda: SimpleNamespace(end_node=lambda _: None),
        inference_activity=lambda: {},
    )
    body = RegisterBody(node_id="w1", host="192.168.1.20", lan_host="192.168.1.20")
    request = SimpleNamespace(client=SimpleNamespace(host="::ffff:192.168.1.20"))
    handlers.register(body, request)
    assert state.members["w1"]["control_source"] == "192.168.1.20"

    later = RegisterBody(node_id="w1", host="192.168.1.20", lan_host="192.168.1.20")
    handlers.heartbeat(later, SimpleNamespace(client=SimpleNamespace(host="10.10.10.2")))
    assert state.members["w1"]["control_source"] == "10.10.10.2"
    assert state.members["w1"]["lan_host"] == "192.168.1.20"


def test_reachability_probes_workers_from_the_master_private_address() -> None:
    calls: list[tuple] = []

    def probe(host: str, port: int, source: str, timeout: float) -> bool:
        calls.append((host, port, source, timeout))
        return host == "10.10.10.2"

    service = _service(probe=probe)
    state = _master(
        w1={
            "node_id": "w1",
            "role": "worker",
            "host": "192.168.1.20",
            "lan_host": "192.168.1.20",
            "control_source": "192.168.1.20",
            "control_port": 19000,
        }
    )
    result = service.reachability(state)
    assert result["subnet"] == SUBNET
    assert result["master_address"] == "10.10.10.1"
    assert result["workers"] == [{"node_id": "w1", "address": "10.10.10.2", "reachable": True}]
    assert calls == [("10.10.10.2", 19000, "10.10.10.1", 2.0)]


def test_reachability_without_a_ready_network_is_empty() -> None:
    preparing = {**NETWORK, "observed_state": "verifying"}
    service = _service([preparing], probe=lambda *_args: True)
    result = service.reachability(_master())
    assert result["workers"] == []
    assert result["network_id"] is None


def test_reachability_marks_a_failed_probe_unreachable() -> None:
    service = _service(probe=lambda *_args: False)
    state = _master(w1={"node_id": "w1", "role": "worker", "control_port": 18765})
    workers = service.reachability(state)["workers"]
    assert workers == [{"node_id": "w1", "address": "10.10.10.2", "reachable": False}]


def test_master_reconnect_posts_to_the_management_path() -> None:
    posted: list[tuple] = []

    def post(url: str, payload: dict, headers: dict, timeout: float) -> dict:
        posted.append((url, payload, headers, timeout))
        return {"ok": True, "accepted": True}

    service = _service(post=post)
    state = _master(
        w1={
            "node_id": "w1",
            "role": "worker",
            "host": "10.10.10.2",
            "lan_host": "192.168.1.20",
            "control_source": "192.168.1.20",
            "control_port": 18766,
        }
    )
    out = service.reconnect(state, FastSubnetReconnectBody(node_id="w1"), None)
    assert out["master_host"] == "10.10.10.1"
    assert posted == [
        (
            "http://192.168.1.20:18766/v2/cluster/fast-subnet/reconnect",
            {"master_host": "10.10.10.1", "master_port": 18765},
            {"X-Kllm-Key": "secret"},
            45.0,
        )
    ]


def test_master_reconnect_refuses_a_worker_already_on_the_subnet() -> None:
    service = _service()
    state = _master(
        w1={
            "node_id": "w1",
            "role": "worker",
            "lan_host": "192.168.1.20",
            "control_source": "10.10.10.2",
        }
    )
    with pytest.raises(ConflictError, match="already connected"):
        service.reconnect(state, FastSubnetReconnectBody(node_id="w1"), None)


def test_master_reconnect_refuses_when_the_network_is_not_ready() -> None:
    service = _service([])
    state = _master(w1={"node_id": "w1", "role": "worker", "control_source": "192.168.1.20"})
    with pytest.raises(ConflictError, match="not ready"):
        service.reconnect(state, FastSubnetReconnectBody(node_id="w1"), None)


def test_master_reconnect_reports_an_unreachable_worker() -> None:
    def post(*_args):
        raise ExternalServiceError("Worker did not respond on its management address")

    service = _service(post=post)
    state = _master(w1={"node_id": "w1", "role": "worker", "control_source": "192.168.1.20"})
    with pytest.raises(ExternalServiceError, match="did not respond"):
        service.reconnect(state, FastSubnetReconnectBody(node_id="w1"), None)


def test_worker_reconnect_rejoins_the_private_master_without_keeping_the_old_bind() -> None:
    seen: list[JoinBody] = []

    def join(body: JoinBody) -> dict:
        seen.append(body)
        return {"master": body.master_host}

    service = _service(join=join)
    state = ClusterState(
        role="worker",
        node_id="w1",
        join_key="secret",
        master_host="192.168.1.1",
        master_port=18765,
        rpc_port=50052,
        rpc_bind="192.168.1.20",
        gpu_ids=[0, 1],
    )
    out = service.reconnect(
        state,
        FastSubnetReconnectBody(master_host="10.10.10.1", master_port=18765),
        "secret",
    )
    assert out["accepted"] is True
    assert seen[0].master_host == "10.10.10.1"
    assert seen[0].rpc_bind is None
    assert seen[0].key == "secret"
    assert seen[0].gpu_ids == [0, 1]
    assert seen[0].rpc_port == 50052


def test_worker_reconnect_rejects_a_bad_key_and_a_public_master() -> None:
    service = _service()
    state = ClusterState(role="worker", node_id="w1", join_key="secret")
    with pytest.raises(AuthenticationError):
        service.reconnect(
            state,
            FastSubnetReconnectBody(master_host="10.10.10.1"),
            "wrong",
        )
    with pytest.raises(ValidationError, match="private IPv4"):
        service.reconnect(
            state,
            FastSubnetReconnectBody(master_host="8.8.8.8"),
            "secret",
        )


def test_handlers_map_domain_errors_to_http() -> None:
    from fastapi import HTTPException

    idle = _handlers(_service([]), _master())
    assert idle.reachability()["workers"] == []

    state = _master(w1={"node_id": "w1", "role": "worker", "control_source": "10.10.10.2"})
    handlers = _handlers(_service(), state)
    with pytest.raises(HTTPException) as conflict:
        handlers.reconnect(FastSubnetReconnectBody(node_id="w1"), None)
    assert conflict.value.status_code == 409


def test_v2_routes_expose_fast_subnet() -> None:
    from kllm.modules.control_v2 import build_router

    router = build_router(
        {
            "fast_subnet_reachability": lambda: {},
            "fast_subnet_reconnect": lambda body: {},
        }
    )
    routes = {(route.path, tuple(sorted(route.methods or []))) for route in router.routes}
    assert ("/v2/cluster/fast-subnet/reachability", ("GET",)) in routes
    assert ("/v2/cluster/fast-subnet/reconnect", ("POST",)) in routes
