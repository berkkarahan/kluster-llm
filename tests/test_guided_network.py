"""Critical guided setup contracts; no administrator or host mutation needed."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from kllm.agent.handlers.managed_network import build_router
from kllm.agent.services.managed_network import ManagedNetworkService
from kllm.managed_network.bootstrap import BootstrapCommit, BootstrapRequest, proof
from kllm.managed_network.guided_setup import GuidedRequest, GuidedSetup
from kllm.managed_network.identity import IdentityError
from kllm.managed_network.store import StoreConflict
from kllm.managed_network.platform.base import PlatformRefusal


def service(tmp_path, role="master", node_id="master"):
    state = SimpleNamespace(
        role=role,
        node_id=node_id,
        join_key="isolated-join-secret",
        members={"master": {"host": "192.168.68.1"}, "worker": {"host": "192.168.68.2"}},
        master_host=None if role == "master" else "192.168.68.1",
    )
    return ManagedNetworkService(
        tmp_path / node_id, lambda: state, lambda: "http://192.168.68.1:18765"
    )


def test_mutual_bootstrap_refuses_bad_proof_replay_and_changed_identity(tmp_path):
    master, worker = service(tmp_path), service(tmp_path, "worker", "worker")
    try:
        identity = worker.identity.public().model_dump(mode="json")
        nonce = "a" * 64
        request = BootstrapRequest(identity=worker.identity.public(), nonce=nonce, proof="b" * 64)
        with pytest.raises(IdentityError):
            master.bootstrap.challenge(request)
        request.proof = proof(
            master.state().join_key, "request", {"identity": identity, "nonce": nonce}
        )
        challenge = master.bootstrap.challenge(request)
        transcript = challenge["transcript"]
        assert proof(worker.state().join_key, "challenge", transcript) == challenge["proof"]
        commit = BootstrapCommit(
            node_id="worker",
            nonce=transcript["master_nonce"],
            signed_transcript=worker.identity.sign(transcript, 8192),
            proof=proof(worker.state().join_key, "commit", transcript),
        )
        assert master.bootstrap.commit(commit) == {"paired": True}
        assert master.store.peer("worker").public_key == worker.identity.public_key
        with pytest.raises(IdentityError):
            master.bootstrap.commit(commit)
        changed = service(tmp_path / "replacement", "worker", "worker")
        try:
            value = changed.identity.public().model_dump(mode="json")
            with pytest.raises(IdentityError):
                master.bootstrap.challenge(
                    BootstrapRequest(
                        identity=changed.identity.public(),
                        nonce=nonce,
                        proof=proof(
                            master.state().join_key, "request", {"identity": value, "nonce": nonce}
                        ),
                    )
                )
        finally:
            changed.store.close()
    finally:
        master.store.close()
        worker.store.close()


@pytest.mark.asyncio
async def test_guided_assessment_and_automatic_subnet_avoid_all_interfaces(tmp_path, monkeypatch):
    master = service(tmp_path)
    adapter = {
        "adapter_id": "eth-dedicated",
        "os_interface": "eth-dedicated",
        "mac": "02:00:00:00:00:01",
        "interface_index": 2,
        "identity_fingerprint": "a" * 64,
        "inventory_revision": "b" * 64,
        "physical_ethernet": True,
        "connected": True,
        "link_speed_mbps": 10000,
        "dhcp": True,
        "permission": True,
        "addresses": [],
        "routes": [],
        "facts": {},
    }
    setup = {"capability": "managed-network-v2", "administrator_enabled": True, "error_code": None}
    management = {
        **adapter,
        "adapter_id": "eth-management",
        "addresses": ["192.168.68.1/24"],
        "routes": ["192.168.234.0/24"],
        "facts": {"default_route": True},
    }

    async def inventories():
        return {
            "master": {"adapters": [adapter, management], "setup": setup},
            "worker": {"adapters": [adapter], "setup": setup},
        }

    monkeypatch.setattr(master, "inventories", inventories)
    monkeypatch.setattr(master, "_management_hosts", lambda: ["192.168.68.1", "192.168.68.2"])
    accepted = []

    async def begin(request):
        accepted.append(request)
        return {"operation_id": str(uuid4())}

    monkeypatch.setattr(master, "begin_start", begin)
    try:
        assessment = await master.guided.assess()
        assert all(c["selected_adapter"] == "eth-dedicated" for c in assessment["computers"])
        assert assessment["computers"][0]["adapters"][1]["reason"]
        request = GuidedRequest(
            request_id=uuid4(),
            nodes=["master", "worker"],
            assessment_revision=assessment["assessment_revision"],
        )
        await master.guided.start(request)
        assert accepted[0].subnet == "172.29.234.0/24"
        with pytest.raises(StoreConflict):
            await master.guided.start(request.model_copy(update={"subnet": "10.234.234.0/24"}))
        with pytest.raises(PlatformRefusal, match="private address range"):
            await master.guided.start(
                request.model_copy(update={"request_id": uuid4(), "subnet": "192.168.234.0/24"})
            )

        assert all(p.adapter_id == "eth-dedicated" for p in accepted[0].participants)
        adapter["link_speed_mbps"] = 1000
        await master.guided.start(request)  # Exact retry reuses the original resolved proposal.
        assert accepted[-1] == accepted[0]
        with pytest.raises(PlatformRefusal, match="network changed|Network changed"):
            await master.guided.start(request.model_copy(update={"request_id": uuid4()}))
        assert len(accepted) == 2
        adapter["link_speed_mbps"] = None
        assert "could not be verified" in GuidedSetup.adapter_reason(adapter, set())
    finally:
        master.store.close()


@pytest.mark.asyncio
async def test_administrator_notice_local_only_and_no_silent_elevation(tmp_path, monkeypatch):
    master = service(tmp_path)
    calls = []

    async def enable(value):
        if not value:
            raise PlatformRefusal(
                "ADMIN_REQUIRED", "Read the administrator access notice before continuing"
            )
        calls.append(value)
        return {"ready": True}

    monkeypatch.setattr(master.guided, "enable", enable)
    app = FastAPI()
    app.include_router(build_router(lambda: master, lambda token: token == "Bearer local-token"))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)),
            base_url="http://127.0.0.1",
            headers={"authorization": "Bearer local-token"},
        ) as client:
            r = await client.get("/v2/node/network-setup")
            assert (
                r.status_code == 200
                and "Administrator access is required" in r.json()["administrator_notice"]
            )
            assert calls == []
            r = await client.post(
                "/v2/node/network-setup/administrator", json={"acknowledged": False}
            )
            assert r.status_code == 422 and calls == []
            r = await client.post(
                "/v2/node/network-setup/administrator", json={"acknowledged": True}
            )
            assert r.status_code == 200 and calls == [True]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("192.168.68.2", 123)),
            base_url="http://192.168.68.1",
            headers={"authorization": "Bearer local-token"},
        ) as client:
            r = await client.post(
                "/v2/node/network-setup/administrator", json={"acknowledged": True}
            )
            assert r.status_code == 403 and calls == [True]
        with pytest.raises(PlatformRefusal):
            await GuidedSetup(master).enable(False)
    finally:
        master.store.close()


@pytest.mark.asyncio
async def test_three_real_agents_bootstrap_and_guided_setup(tmp_path, monkeypatch):
    """Real HTTP, signing, native bridges and RPC fixtures; hardware is controlled."""
    from managed_network_lab import NativeAgentLab

    lab = NativeAgentLab(tmp_path, monkeypatch)
    try:
        for node in lab.nodes:
            monkeypatch.setattr(
                node.service.guided,
                "local_status",
                lambda: {
                    "capability": "managed-network-v2",
                    "administrator_enabled": True,
                    "error_code": None,
                    "cleanup_pending": [],
                },
            )
            monkeypatch.setattr(
                node.service.executor, "guided_status", node.service.guided.local_status
            )
        await lab.start(automatic_pairing=True)
        assert len(lab.nodes[0].service.store.peers()) == 2
        for node in lab.nodes[1:]:
            assert (
                node.service.store.peer("master").public_key
                == lab.nodes[0].service.identity.public_key
            )
            assert node.service.store.metadata("network_selection") is None
        assessment = (await lab.request(0, "GET", "/v2/cluster/network-setup")).json()
        assert all(c["ready"] for c in assessment["computers"]), assessment
        response = await lab.request(
            0,
            "POST",
            "/v2/cluster/network-setup",
            json={
                "request_id": str(uuid4()),
                "nodes": ["master", "worker-a", "worker-b"],
                "assessment_revision": assessment["assessment_revision"],
            },
        )
        assert response.status_code == 202, response.text
        network_id = response.json()["network_id"]
        await lab.wait(
            lambda: lab.nodes[0].service.store.network(network_id).observed_state == "ready"
        )
        network = lab.nodes[0].service.network_view(network_id)
        assert len(network["participants"]) == 3
        assert all(p["state"] == "ready" for p in network["participants"])
        assert not lab.configurations, "Network setup must verify connectivity without creating RPC relays"
        response = await lab.request(
            0,
            "POST",
            f"/v2/cluster/networks/{network_id}/operations",
            json={
                "request_id": str(uuid4()),
                "expected_revision": network["revision"],
                "action": "stop",
            },
        )
        assert response.status_code == 202, response.text
        await lab.wait(
            lambda: lab.nodes[0].service.store.network(network_id).observed_state == "stopped"
        )
        assert all(not node.adapter.addresses for node in lab.nodes)
    finally:
        await lab.close()
