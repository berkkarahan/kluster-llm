"""Three real HTTP/WS agents; hardware facts controlled, host changes forbidden.

The local runtime is not replaced. TCP-only/hardware readiness is exercised by
physical acceptance; this fixture verifies the real signed orchestration/refusal
boundary. Native byte forwarding is checked separately in network::tests.
"""

from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from kllm.modules.managed_network.router_http import build_router
from kllm.modules.managed_network.interactors.service import ManagedNetworkService
from kllm.shared.platform import base
from kllm.shared.platform.base import AdapterSnapshot, PlatformAdapter
from kllm.shared.platform.inspection import InterfaceState


@pytest.mark.asyncio
async def test_three_agent_signed_start_idempotency_and_safe_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "is_admin", lambda: True)  # controlled privilege fact
    mutations = []

    class ControlledHardware(PlatformAdapter):
        def inventory(self):
            return [self.inspect("ethernet")]

        def inspect(self, adapter_id):
            return AdapterSnapshot(
                adapter_id,
                adapter_id,
                1,
                "d" * 64,
                "02:00:00:00:00:01",
                InterfaceState(adapter_id, True, 10000, False, (), ()),
            )

        def prepare(self, *args):
            raise base.PlatformRefusal(
                "UNSUPPORTED_CAPABILITY", "Controlled adapter refuses host mutation"
            )

        def add_exact(self, *args):
            mutations.append("add")
            raise AssertionError("The unverified runtime must never reach host mutation")

        def delete_exact(self, *args):
            mutations.append("delete")
            raise AssertionError("Refused preparation must have nothing to delete")

        def check_source_route(self, *args):
            raise AssertionError("Refused preparation must not become ready")

    listeners = []
    for _ in range(3):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.setblocking(False)
        listeners.append(listener)
    master_port = listeners[0].getsockname()[1]
    nodes = ("master", "worker-a", "worker-b")
    members = {
        node: {
            "host": "127.0.0.1",
            "role": "master" if i == 0 else "worker",
            "control_port": listener.getsockname()[1],
        }
        for i, (node, listener) in enumerate(zip(nodes, listeners))
    }
    services, servers, server_tasks = [], [], []
    tokens = {node: f"Bearer isolated-{node}" for node in nodes}
    try:
        for index, node in enumerate(nodes):
            state = SimpleNamespace(
                role="master" if index == 0 else "worker",
                node_id=node,
                members=members,
                master_host=None if index == 0 else "127.0.0.1",
                master_port=master_port,
                process_pid=None,
                process_role=None,
            )
            service = ManagedNetworkService(
                tmp_path / node,
                lambda state=state: state,
                lambda: f"http://127.0.0.1:{master_port}",
                platform=ControlledHardware(),
            )
            services.append(service)

            @asynccontextmanager
            async def lifespan(app, service=service):
                await service.start()
                try:
                    yield
                finally:
                    await service.close()

            app = FastAPI(lifespan=lifespan)
            app.include_router(
                build_router(
                    lambda service=service: service, lambda token, node=node: token == tokens[node]
                )
            )
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    host="127.0.0.1",
                    port=listeners[index].getsockname()[1],
                    log_level="error",
                    lifespan="on",
                )
            )
            servers.append(server)
            server_tasks.append(asyncio.create_task(server.serve(sockets=[listeners[index]])))
        deadline = asyncio.get_running_loop().time() + 5
        while not all(server.started for server in servers):
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.02)
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:

            async def request(index, method, path, **kwargs):
                return await client.request(
                    method,
                    f"http://127.0.0.1:{listeners[index].getsockname()[1]}{path}",
                    headers={"authorization": tokens[nodes[index]]},
                    **kwargs,
                )

            identities = [
                (await request(i, "GET", "/v2/node/network-identity")).json() for i in range(3)
            ]
            binding = identities[0]["cluster_binding_id"]
            for index in (1, 2):
                worker = identities[index]
                paired = await request(
                    0,
                    "PUT",
                    f"/v2/node/network-identity/peers/{worker['node_id']}",
                    json={key: worker[key] for key in ("node_id", "key_id", "public_key")}
                    | {"cluster_binding_id": binding, "expected_role": "worker"},
                )
                assert paired.status_code == 200, paired.text
                paired = await request(
                    index,
                    "PUT",
                    "/v2/node/network-identity/peers/master",
                    json={key: identities[0][key] for key in ("node_id", "key_id", "public_key")}
                    | {"cluster_binding_id": binding, "expected_role": "master"},
                )
                assert paired.status_code == 200, paired.text
            deadline = asyncio.get_running_loop().time() + 5
            while len(services[0].channels.sessions) < 2:
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.02)
            inventory = (await request(0, "GET", "/v2/cluster/network-adapters")).json()["nodes"]
            assert set(inventory) == set(nodes)
            for index in (1, 2):
                chosen = inventory[nodes[index]]["adapters"][0]
                response = await request(
                    index,
                    "PUT",
                    "/v2/node/network-selection",
                    json={
                        "adapter_id": chosen["adapter_id"],
                        "expected_inventory_revision": chosen["inventory_revision"],
                    },
                )
                assert response.status_code == 200, response.text
            participants = [
                {
                    "node_id": node,
                    "adapter_id": "ethernet",
                    "inventory_revision": inventory[node]["adapters"][0]["inventory_revision"],
                }
                for node in nodes
            ]
            payload = {
                "request_id": str(uuid4()),
                "subnet": "192.168.234.0/24",
                "participants": participants,
            }
            denied = await client.post(
                f"http://127.0.0.1:{master_port}/v2/cluster/networks", json=payload
            )
            assert denied.status_code == 401
            started = await request(0, "POST", "/v2/cluster/networks", json=payload)
            assert started.status_code == 202, started.text
            accepted = started.json()
            await services[0].wait_operation(accepted["operation_id"])
            result = (
                await request(
                    0, "GET", f"/v2/cluster/network-operations/{accepted['operation_id']}"
                )
            ).json()
            assert result["phase"] == "failed"
            assert {item["node_id"] for item in result["results"]} == set(nodes)
            assert all(
                item["error_code"] == "UNSUPPORTED_CAPABILITY"
                for item in result["results"]
                if item["stage"] == "prepare"
            )
            network = (
                await request(0, "GET", f"/v2/cluster/networks/{accepted['network_id']}")
            ).json()
            assert len({item["address"] for item in network["participants"]}) == 3
            assert network["observed_state"] == "failed"
            assert mutations == []
            duplicate = await request(0, "POST", "/v2/cluster/networks", json=payload)
            assert duplicate.json()["operation_id"] == accepted["operation_id"]
            altered = await request(
                0, "POST", "/v2/cluster/networks", json=payload | {"subnet": "172.29.234.0/24"}
            )
            assert altered.status_code == 409
            retried = await request(
                0,
                "POST",
                f"/v2/cluster/networks/{accepted['network_id']}/operations",
                json={
                    "request_id": str(uuid4()),
                    "expected_revision": network["revision"],
                    "action": "retry",
                },
            )
            assert retried.status_code == 202, retried.text
            await services[0].wait_operation(retried.json()["operation_id"])
            network = (
                await request(0, "GET", f"/v2/cluster/networks/{accepted['network_id']}")
            ).json()
            assert network["observed_state"] == "failed"
            assert len({item["address"] for item in network["participants"]}) == 3
            assert mutations == []
            stopped = await request(
                0,
                "POST",
                f"/v2/cluster/networks/{accepted['network_id']}/operations",
                json={
                    "request_id": str(uuid4()),
                    "expected_revision": network["revision"],
                    "action": "stop",
                },
            )
            assert stopped.status_code == 202, stopped.text
            await services[0].wait_operation(stopped.json()["operation_id"])
            final = (
                await request(0, "GET", f"/v2/cluster/networks/{accepted['network_id']}")
            ).json()
            assert final["observed_state"] == "stopped"
            assert mutations == []
            # Cancel an outstanding preparation over the same authenticated
            # HTTP boundary. Only the coordinator phase gate is controlled.
            entered = asyncio.Event()
            phase = services[0]._phase

            async def paused_phase(operation, assignments, stage, bodies, budget):
                if stage == "prepare":
                    entered.set()
                    await asyncio.Event().wait()
                return await phase(operation, assignments, stage, bodies, budget)

            monkeypatch.setattr(services[0], "_phase", paused_phase)
            second = await request(
                0, "POST", "/v2/cluster/networks", json=payload | {"request_id": str(uuid4())}
            )
            assert second.status_code == 202, second.text
            await asyncio.wait_for(entered.wait(), 5)
            cancel_request = {"request_id": str(uuid4()), "expected_operation_revision": 1}
            canceled = await request(
                0,
                "POST",
                f"/v2/cluster/network-operations/{second.json()['operation_id']}/cancel",
                json=cancel_request,
            )
            assert canceled.status_code == 202, canceled.text
            await services[0].wait_operation(second.json()["operation_id"])
            canceled_result = services[0].operation_view(second.json()["operation_id"])
            assert canceled_result["operation_revision"] == 2
            assert canceled_result["phase"] == "failed"
            duplicate_cancel = await request(
                0,
                "POST",
                f"/v2/cluster/network-operations/{second.json()['operation_id']}/cancel",
                json=cancel_request,
            )
            assert duplicate_cancel.status_code == 202
            assert (
                services[0].operation_view(second.json()["operation_id"])["operation_revision"] == 2
            )
            assert mutations == []
    finally:
        for server in servers:
            server.should_exit = True
        if server_tasks:
            await asyncio.wait_for(asyncio.gather(*server_tasks, return_exceptions=True), 10)
        for service in services:
            await service.close()
        for listener in listeners:
            listener.close()


@pytest.mark.asyncio
async def test_native_three_agent_ready_admission_and_partial_restart(tmp_path, monkeypatch):
    """Combined actual native sockets; see lab module for OS/runtime mappings."""
    from managed_network_lab import NativeAgentLab
    from kllm.shared import native
    from kllm.modules.managed_network.interactors.allocation import OperationRequest

    lab = NativeAgentLab(tmp_path, monkeypatch)
    try:
        await lab.start()
        master = lab.nodes[0].service
        started = await lab.setup()
        await master.wait_operation(started["operation_id"])
        status = master.network_view(started["network_id"])
        assert status["observed_state"] == "ready", master.operation_view(started["operation_id"])
        assert len({a["address"] for a in status["participants"]}) == 3
        assert all(a["verification_freshness"] == "fresh" for a in status["participants"])
        assert all("launch_rpc_endpoint" not in a for a in status["participants"])
        original = {node: dict(member) for node, member in lab.nodes[0].state.members.items()}
        response = await lab.request(
            0,
            "POST",
            "/v2/inference/runs",
            json={"model": "controlled.gguf", "network_id": started["network_id"]},
        )
        assert response.status_code == 200, response.text
        await lab.wait(lambda: (tmp_path / "inference-ready").exists())
        run = master.store.runs(started["network_id"])[0]
        assert run.state == "active"
        assert run.launch_endpoints == response.json()["plan"]["rpc_endpoints"]
        assert all(endpoint.startswith("127.0.0.1:") for endpoint in run.launch_endpoints)
        assert lab.nodes[0].state.members == original
        assert all(
            native.network_bridge_status(bridge)["source_host"] == lab.source
            for bridge in master.master_bridges.values()
        )
        counts = [node.rpc_events.read_text().count("accepted") for node in lab.nodes[1:]]
        # One actual health/renewal tick while the workload owns both RPC streams.
        await asyncio.sleep(5.5)
        assert [node.rpc_events.read_text().count("accepted") for node in lab.nodes[1:]] == counts
        status = master.network_view(started["network_id"])
        blocked = await lab.request(
            0,
            "POST",
            f"/v2/cluster/networks/{started['network_id']}/operations",
            json={
                "request_id": str(uuid4()),
                "expected_revision": status["revision"],
                "action": "stop",
            },
        )
        assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "INFERENCE_ACTIVE"
        stopped = await lab.request(0, "POST", "/v2/inference/runs/stop")
        assert stopped.status_code == 200, stopped.text
        assert master.store.runs(started["network_id"])[0].state == "released"
        # Omission exercises the actual original LAN sockets. A busy legacy run
        # must survive a refused dedicated admission and no fallback occurs.
        legacy = await lab.request(
            0, "POST", "/v2/inference/runs", json={"model": "controlled.gguf"}
        )
        assert legacy.status_code == 200, legacy.text
        await lab.wait(lambda: (tmp_path / "inference-ready").exists())
        old_pid = lab.nodes[0].state.process_pid
        busy = await lab.request(
            0,
            "POST",
            "/v2/inference/runs",
            json={"model": "controlled.gguf", "network_id": started["network_id"]},
        )
        assert busy.status_code == 409 and busy.json()["detail"]["code"] == "RPC_BUSY", busy.text
        assert lab.nodes[0].state.process_pid == old_pid and native.process_alive(old_pid)
        stopped = await lab.request(0, "POST", "/v2/inference/runs/stop")
        assert stopped.status_code == 200, stopped.text
        network = master.store.network(started["network_id"])
        stop = await master.begin_operation(
            network.network_id,
            OperationRequest(request_id=uuid4(), expected_revision=network.revision, action="stop"),
        )
        await master.wait_operation(stop["operation_id"])
        assert master.network_view(network.network_id)["observed_state"] == "stopped"
        assert all(not node.adapter.addresses for node in lab.nodes)
        # Lose worker observation after all three durable applies, before verify.
        entered, proceed = asyncio.Event(), asyncio.Event()
        verification = master._verify_configuration

        async def pause_verification(*args):
            entered.set()
            await proceed.wait()
            return await verification(*args)

        monkeypatch.setattr(master, "_verify_configuration", pause_verification)
        interrupted = await lab.setup()
        await asyncio.wait_for(entered.wait(), 8)
        preserved = list(lab.nodes[2].adapter.addresses)
        await lab.restart_worker(2)
        assert any(
            str(j.network_id) == interrupted["network_id"] and j.observer_health == "gap"
            for j in lab.nodes[2].service.store.journals()
        )
        proceed.set()
        await master.wait_operation(interrupted["operation_id"])
        failed = master.network_view(interrupted["network_id"])
        assert failed["observed_state"] == "cleanup_pending", master.operation_view(
            interrupted["operation_id"]
        )
        assert lab.nodes[2].adapter.addresses == preserved  # No deletion after gap.
        assert not lab.nodes[0].adapter.addresses and not lab.nodes[1].adapter.addresses
        journal = next(
            j
            for j in lab.nodes[2].service.store.journals()
            if str(j.network_id) == interrupted["network_id"] and j.phase != "removed_observed"
        )
        review = await lab.request(2, "GET", f"/v2/node/network-cleanup/{journal.journal_id}")
        assert review.status_code == 200, review.text
        resolved = await lab.request(
            2,
            "POST",
            f"/v2/node/network-cleanup/{journal.journal_id}/resolve",
            json={"decision": "confirm_owned", "snapshot_digest": review.json()["snapshot_digest"]},
        )
        assert resolved.status_code == 200, resolved.text
        stop = await master.begin_operation(
            UUID(interrupted["network_id"]),
            OperationRequest(
                request_id=uuid4(), expected_revision=failed["revision"], action="stop"
            ),
        )
        await master.wait_operation(stop["operation_id"])
        assert master.network_view(interrupted["network_id"])["observed_state"] == "stopped"
        assert all(not node.adapter.addresses for node in lab.nodes)
    finally:
        await lab.close()
