"""Local-only listener recovery through HTTP, ownership and the helper gateway."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from kllm.modules.managed_network.interactors.guided_setup import GuidedSetup
from kllm.modules.managed_network.interactors.helper_platform import HelperPlatform
from kllm.modules.managed_network.router_http import build_router
from kllm.modules.primacpp.interactors.port_runs import PortSupervisor
from kllm.shared.platform.base import PlatformRefusal
import test_prima_ports as port_contract_tests

prepared = port_contract_tests.prepared


@pytest.fixture
def recovery_app(prepared):
    manifest, _, path = prepared
    calls = []
    remaining = {manifest.run_id: True}
    firewall = {"InstanceID": "changed-rule", "Remote": ["192.168.234.2"]}
    review = {"snapshot_digest": "a" * 64, "rules": [{"ownership": "changed", "current": firewall}]}

    def request(action, **values):
        calls.append((action, values))
        assert values["network"] == manifest.network_id and values["run_id"] == manifest.run_id
        if action == "prima_ports_apply":
            return {"phase": "admitted", "manifest_digest": manifest.digest}
        if action == "prima_ports_review":
            return review
        if action == "prima_ports_resolve":
            if values["snapshot_digest"] != review["snapshot_digest"]:
                raise PlatformRefusal("HELPER_REFUSED", "Prima rules changed; review them again")
            if values["decision"] == "restore_owned":
                raise PlatformRefusal("HELPER_REFUSED", "Prima firewall ownership changed; cleanup retained")
            remaining.clear()
            assert firewall == {"InstanceID": "changed-rule", "Remote": ["192.168.234.2"]}
            return {"phase": "released", "decision": values["decision"]}
        if action == "prima_ports_release":
            if remaining:
                raise PlatformRefusal("HELPER_REFUSED", "Changed rule retained")
            return {"phase": "released"}
        pytest.fail(f"Unexpected helper operation: {action}")

    platform = HelperPlatform(SimpleNamespace(), SimpleNamespace(request=request,
        status=lambda: {"prima_ports_recovery_version": 1}), lambda: None)
    rank = SimpleNamespace(owner=None)
    ports = PortSupervisor(path.parent / "ports.json", platform=lambda: platform, rank=lambda: rank)
    ports.admit(manifest, {"sender": "master", "authority_digest": "b" * 64},
                lambda: {"phase": "admitted", "manifest_digest": manifest.digest}, lambda: None)
    with pytest.raises(PlatformRefusal):
        ports.release(manifest.network_id, manifest.run_id)
    calls.clear()
    guided = object.__new__(GuidedSetup)
    service = SimpleNamespace(lock=asyncio.Lock(), runtimes=SimpleNamespace(prima=SimpleNamespace(ports=ports)),
                              guided=guided)
    guided.service = service
    app = FastAPI()
    app.include_router(build_router(lambda: service, token_verifier=lambda token: token == "local-token"))
    route = f"/v2/node/network-setup/recovery/{manifest.network_id}/prima/{manifest.run_id}"
    return app, route, ports, rank, calls, remaining


@pytest.mark.asyncio
async def test_review_then_explicit_keep_preserves_rule_and_allows_normal_cleanup_retry(recovery_app):
    app, route, ports, _, calls, remaining = recovery_app
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 9000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1",
                                headers={"x-api-key": "local-token"}) as client:
        response = await client.get(route)
        assert response.status_code == 200 and response.json()["rules"][0]["ownership"] == "changed"
        stale = await client.post(route, json={"decision": "keep_current", "snapshot_digest": "f" * 64})
        assert stale.status_code == 422 and ports.owner and remaining
        restore = await client.post(route, json={"decision": "restore_owned", "snapshot_digest": "a" * 64})
        assert restore.status_code == 422 and ports.owner and remaining
        response = await client.post(route, json={"decision": "keep_current", "snapshot_digest": "a" * 64})
        assert response.status_code == 200 and response.json() == {"phase": "released", "decision": "keep_current"}
    assert ports.owner is None and json.loads(ports.path.read_text()) is None
    assert not remaining
    # Simulated native rule operations; the helper gateway received bounded IDs,
    # decision and reviewed digest, never caller-supplied snapshots or shell text.
    assert calls[-1][1] == {"network": route.split("/")[5], "run_id": route.split("/")[7],
                           "decision": "keep_current", "snapshot_digest": "a" * 64}
    assert ports.release(route.split("/")[5], route.split("/")[7]) == {"phase": "released"}


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["remote", "token", "origin", "payload", "live", "foreign"])
async def test_local_recovery_refuses_unauthorized_or_live_changes_before_helper(recovery_app, denial):
    app, route, ports, rank, calls, _ = recovery_app
    peer = "10.1.2.3" if denial == "remote" else "127.0.0.1"
    headers = {"x-api-key": "bad" if denial == "token" else "local-token"}
    if denial == "origin":
        headers["origin"] = "https://foreign.invalid"
    body = {"decision": "keep_current", "snapshot_digest": "a" * 64}
    if denial == "payload":
        body["snapshot"] = {"InstanceID": "adopt-me"}
    if denial == "live":
        rank.owner = {"run_id": ports.owner["manifest"]["run_id"]}
    if denial == "foreign":
        route = route.rsplit("/", 1)[0] + "/" + str(uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(peer, 9000)),
                                base_url="http://127.0.0.1", headers=headers) as client:
        response = await client.post(route, json=body)
    assert response.status_code in (401, 403, 409, 422)
    assert not calls and ports.owner


@pytest.mark.asyncio
async def test_helper_only_orphan_can_be_reviewed_without_inventing_node_owner(recovery_app):
    app, route, ports, _, calls, _ = recovery_app
    ports.owner = None
    ports._save()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("::1", 9000)),
                                base_url="http://localhost", headers={"x-api-key": "local-token"}) as client:
        response = await client.get(route)
    assert response.status_code == 200 and ports.owner is None
    assert calls[0][0] == "prima_ports_review"


def test_setup_status_surfaces_listener_only_cleanup_without_address_recovery():
    guided = object.__new__(GuidedSetup)
    resources = [{"network_id": str(uuid4()), "run_id": str(uuid4()), "manifest_digest": "a" * 64}]
    guided.grant = lambda: {"node_id": "fixture"}
    guided.helper = SimpleNamespace(status=lambda _: {"ready": True, "cleanup_pending": [],
                                                      "prima_listener_recovery": resources})
    status = guided.local_status()
    assert status["administrator_enabled"] is True and status["error_code"] == "CLEANUP_PENDING"
    assert status["prima_listener_recovery"] == resources and not status["cleanup_pending"]


@pytest.mark.parametrize("version", [None, True, 0, 2, "1"])
def test_old_helper_refuses_review_and_resolve_before_request(version):
    client = SimpleNamespace(status=lambda: {"prima_ports_recovery_version": version},
                             request=lambda *_args, **_kwargs: pytest.fail("Unsupported recovery cannot mutate"))
    platform = HelperPlatform(SimpleNamespace(), client, lambda: None)
    for action in (lambda: platform.review_prima_ports(str(uuid4()), str(uuid4())),
                   lambda: platform.resolve_prima_ports(str(uuid4()), str(uuid4()), "keep_current", "a" * 64)):
        with pytest.raises(PlatformRefusal, match="Update the protected helper"):
            action()
