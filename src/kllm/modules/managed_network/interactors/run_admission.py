"""Explicit inference reservations over immutable copies of cluster inventory."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException

from kllm.shared import native
from kllm.modules.managed_network.entities import AdmissionLease, RunNetworkReservation
from kllm.shared.platform.base import PlatformRefusal
from kllm.modules.managed_network.gateways.store import StoreConflict


class RunAdmission:
    def __init__(self, service: Any):
        self.service = service

    def call(self, coroutine):
        try:
            # HTTP inference handlers run in FastAPI's thread pool. No blocking
            # wait is allowed on the event loop that owns the control sockets.
            try:
                if asyncio.get_running_loop() is self.service.loop:
                    coroutine.close()
                    raise RuntimeError("network inference must execute in the HTTP worker thread")
            except RuntimeError as error:
                if str(error) != "no running event loop":
                    raise
            return asyncio.run_coroutine_threadsafe(coroutine, self.service.loop).result()
        except (PlatformRefusal, StoreConflict, KeyError) as error:
            raise HTTPException(
                409,
                detail={"code": getattr(error, "code", "STALE_REVISION"), "message": str(error)},
            ) from error

    def members(self, network_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self.call(self.preview(UUID(network_id)))
        return result

    def inventory(
        self, network_id: UUID, *, runtime: bool = False, selected: list[str] | None = None
    ) -> tuple[Any, list, dict]:
        service = self.service
        network = service.store.network(network_id)
        if (
            network is None
            or network.desired_state != "active"
            or network.observed_state not in ("ready", "verifying")
        ):
            raise PlatformRefusal("STALE_REVISION", "Select a current verified dedicated network")
        authority = service._authority()
        if (network.cluster_binding_id, network.master_node_id, network.master_key_id) != (
            authority.cluster_binding_id,
            authority.master_node_id,
            authority.master_key_id,
        ):
            raise PlatformRefusal(
                "UNPAIRED_IDENTITY", "Selected network belongs to another authority"
            )
        assignments = service.store.assignments(network_id)
        if selected is not None:
            assignments = [
                a
                for a in assignments
                if a.node_id == service.identity.node_id or a.node_id in selected
            ]
        members = {}
        for assignment in assignments:
            original = service.state().members.get(assignment.node_id)
            if original is None:
                raise PlatformRefusal(
                    "STALE_REVISION", "Selected participant is no longer enrolled"
                )
            member = deepcopy(original)
            if assignment.node_id != service.identity.node_id:
                if not runtime:
                    endpoint = assignment.logical_rpc_endpoint
                    if not endpoint:
                        raise PlatformRefusal(
                            "STALE_REVISION", "Network participant has no configured RPC endpoint"
                        )
                    host, port = endpoint.rsplit(":", 1)
                    member.update(host=host, rpc_bind=host, rpc_port=int(port))
                    members[assignment.node_id] = member
                    continue
                bridge = service.master_bridges.get((str(network_id), assignment.node_id))
                if (
                    bridge is None
                    or not assignment.launch_rpc_endpoint
                    or not assignment.logical_rpc_endpoint
                    or assignment.rpc_child_identity is None
                ):
                    raise PlatformRefusal("STALE_REVISION", "Selected bridge disappeared")
                status = native.network_bridge_status(bridge)
                if (
                    status["closed"]
                    or status["last_error"]
                    or status["listener"] != assignment.launch_rpc_endpoint
                ):
                    raise PlatformRefusal("STALE_REVISION", "Selected bridge changed")
                host, port = assignment.launch_rpc_endpoint.rsplit(":", 1)
                member.update(host=host, rpc_bind=host, rpc_port=int(port))
            members[assignment.node_id] = member
        if service.identity.node_id not in members or (selected is None and len(members) < 2):
            raise PlatformRefusal(
                "STALE_REVISION", "Selected network requires its master and a worker"
            )
        return network, assignments, members

    def diagnostics(self, network_id: str) -> dict[str, Any]:
        assignments = self.service.store.assignments(network_id)
        run = next(
            (r for r in self.service.store.runs(network_id) if r.state in ("reserved", "active")),
            None,
        )
        return {
            "network_id": network_id,
            "network_run_id": str(run.run_id) if run else None,
            "network_endpoints": [
                {
                    "node_id": a.node_id,
                    "logical_endpoint": a.logical_rpc_endpoint,
                    "launch_endpoint": a.launch_rpc_endpoint,
                    "assignment_revision": a.assignment_revision,
                }
                for a in assignments
                if a.node_id != self.service.identity.node_id
            ],
        }

    async def preview(self, network_id: UUID) -> dict:
        async with self.service.lock:
            return (await self.service.store.arun(self.inventory, network_id))[2]

    async def reserve(self, network_id: str | None, selected: list[str] | None = None):
        service = self.service
        await service.lock.acquire()
        reservation = None
        try:
            active_runs = []
            for network in await service.store.arun(service.store.networks):
                active_runs.extend(await service.store.arun(service.store.runs, network.network_id))
            if any(run.state != "released" for run in active_runs):
                raise PlatformRefusal(
                    "INFERENCE_ACTIVE",
                    "Stop and drain the current managed inference before starting another run",
                )
            if network_id is None:
                return None, deepcopy(service.state().members)
            network, assignments, members = await self.service.store.arun(
                self.inventory, UUID(network_id), selected=selected
            )
            operation = await service.store.arun(
                service.store.operation, network.current_operation_id
            )
            if operation is None:
                raise PlatformRefusal("STALE_REVISION", "Selected network has no current operation")
            # Network proof is independent of process readiness. RPC workers have
            # already been started by the master's runtime ownership context.
            await asyncio.wait_for(service._health(network, operation, assignments), 5)
            await service.prepare_rpc(network, operation, assignments)
            network, assignments, members = await self.service.store.arun(
                self.inventory, UUID(network_id), runtime=True, selected=selected
            )
            workers = [a for a in assignments if a.node_id != service.identity.node_id]
            reservation = RunNetworkReservation(
                run_id=uuid4(),
                network_id=network.network_id,
                assignment_revisions={a.node_id: a.assignment_revision for a in assignments},
                logical_endpoints=[a.logical_rpc_endpoint for a in workers],
                launch_endpoints=[a.launch_rpc_endpoint for a in workers],
                relay_instance_ids=[
                    UUID(
                        native.network_bridge_status(
                            service.master_bridges[(str(network.network_id), a.node_id)]
                        )["instance_id"]
                    )
                    for a in workers
                ],
                state="reserved",
            )
            (await service.store.arun(service.store.reservation, reservation))
            await asyncio.wait_for(service._health(network, operation, assignments), 2)
            # Fresh probe admission precedes stopping the existing legacy child.
            if not await service.verify_idle(network, operation, assignments):
                raise PlatformRefusal(
                    "RPC_BUSY", "Worker RPC is occupied; current inference was preserved"
                )
            leases = {}
            for assignment in workers:
                key = (str(network.network_id), assignment.node_id)
                evidence = service.freshness.require(network.network_id, assignment.node_id)
                if evidence["child"] != assignment.rpc_child_identity.model_dump(mode="json"):
                    raise PlatformRefusal("STALE_REVISION", "Verified worker child changed")
                status = native.network_bridge_status(service.master_bridges[key])
                worker = service.verified_results[key]["bridge"]
                lease = AdmissionLease(
                    expected_rpc_child_identity=assignment.rpc_child_identity,
                    lease_id=uuid4(),
                    network_id=network.network_id,
                    node_id=assignment.node_id,
                    generation=status["generation"] + 1,
                    kind="run",
                    run_id=reservation.run_id,
                    assignment_revision=assignment.assignment_revision,
                    authority=(await service.store.arun(service._authority)),
                    relay_instance_ids=[UUID(worker["instance_id"]), UUID(status["instance_id"])],
                    state="requested",
                )
                (await service.store.arun(service.store.lease, lease))
                leases[assignment.node_id] = lease
            granted = await service._phase(
                operation,
                workers,
                "admit_run",
                {node: lease.model_dump(mode="json") for node, lease in leases.items()},
                5,
            )
            for assignment in workers:
                lease = leases[assignment.node_id]
                if granted[assignment.node_id].get("grant_ack") is not True:
                    lease.state = "uncertain"
                    (await service.store.arun(service.store.lease, lease))
                    raise PlatformRefusal("RPC_BUSY", "Worker run admission was not acknowledged")
                service.master_bridges[(str(network.network_id), assignment.node_id)].grant(
                    lease.generation, 15000, False
                )
                lease.state, lease.grant_ack = "granted", True
                (await service.store.arun(service.store.lease, lease))
            return reservation, members
        except BaseException:
            try:
                if reservation:
                    await self.release(reservation)
            finally:
                service.lock.release()
            raise

    async def finish(self, reservation, success: bool, previous_pid: int | None):
        service = self.service
        try:
            if reservation is None:
                return
            pid = service.state().process_pid
            if success and pid and pid != previous_pid:
                token, _ = native._managed_native()._process_identity(pid)
                reservation.child_pid, reservation.child_start_token = pid, token
                reservation.state = "active"
                (await service.store.arun(service.store.reservation, reservation))
            else:
                # A launch finisher may fail after recording the newly started
                # child; stop only that process before releasing its bridges.
                if pid and pid != previous_pid:
                    await asyncio.to_thread(native.stop_process, pid, force=True)
                    if native.process_alive(pid):
                        reservation.state = "uncertain"
                        (await service.store.arun(service.store.reservation, reservation))
                        raise PlatformRefusal(
                            "INFERENCE_ACTIVE", "Failed launch child has not stopped"
                        )
                await self.release(reservation)
        finally:
            service.lock.release()

    async def release(self, reservation):
        service = self.service
        reservation.state = "releasing"
        (await service.store.arun(service.store.reservation, reservation))
        network = await service.store.arun(service.store.network, reservation.network_id)
        operation = await service.store.arun(service.store.operation, network.current_operation_id)
        leases = {
            item.node_id: item
            for item in (await service.store.arun(service.store.leases, reservation.network_id))
            if item.run_id == reservation.run_id and item.state != "released"
        }
        assignments = [
            a
            for a in (await service.store.arun(service.store.assignments, reservation.network_id))
            if a.node_id in leases
        ]
        results = await service._phase(
            operation,
            assignments,
            "release_admission",
            {node: lease.model_dump(mode="json") for node, lease in leases.items()},
            5,
        )
        drained = True
        for assignment in assignments:
            lease = leases[assignment.node_id]
            bridge = service.master_bridges.get((str(reservation.network_id), assignment.node_id))
            local = bridge is None or bool(
                bridge
                and (
                    native.network_bridge_status(bridge)["generation"] < lease.generation
                    or bridge.release(lease.generation)
                )
            )
            until = (
                self.service.loop.time() + 2
                if self.service.loop
                else asyncio.get_running_loop().time() + 2
            )
            while bridge and not local and asyncio.get_running_loop().time() < until:
                await asyncio.sleep(0.01)
                local = bool(bridge.release(lease.generation))
            acknowledged = local and results[assignment.node_id].get("drained") is True
            lease.state = "released" if acknowledged else "uncertain"
            lease.release_ack = results[assignment.node_id].get("drained") is not None
            lease.drain_ack = acknowledged
            (await service.store.arun(service.store.lease, lease))
            drained = drained and acknowledged
        participants = [
            a
            for a in (await service.store.arun(service.store.assignments, reservation.network_id))
            if a.node_id in reservation.assignment_revisions
            and a.node_id != service.identity.node_id
        ]
        if drained:
            closed = await service._phase(
                operation, participants, "release_runtime", {a.node_id: {} for a in participants}, 5
            )
            drained = all(result.get("drained") is True for result in closed.values())
        reservation.state = "released" if drained else "uncertain"
        (await service.store.arun(service.store.reservation, reservation))
        service.freshness.invalidate(
            reservation.network_id, "Run ended; obtain a new idle protocol proof"
        )
        if drained:
            for assignment in participants:
                key = (str(reservation.network_id), assignment.node_id)
                bridge = service.master_bridges.get(key)
                if bridge:
                    status = native.network_bridge_status(bridge)
                    if not await asyncio.to_thread(
                        native.stop_network_bridge, bridge, status["instance_id"]
                    ):
                        raise PlatformRefusal("CLEANUP_PENDING", "Master relay has not drained")
                    service.master_bridges.pop(key, None)
                assignment.launch_rpc_endpoint = None
                assignment.rpc_child_identity = None
                (
                    await service.store.arun(
                        service.store.put_assignment,
                        assignment,
                        expected_revision=assignment.assignment_revision,
                    )
                )

    async def release_stopped(self, *, lock_held: bool = False):
        service = self.service
        if not lock_held:
            async with service.lock:
                return await self.release_stopped(lock_held=True)
        for network in await service.store.arun(service.store.networks):
            for run in await service.store.arun(service.store.runs, network.network_id):
                if run.state == "released":
                    continue
                if (
                    run.child_pid
                    and native.process_alive(run.child_pid)
                    and native._managed_native()._process_identity(run.child_pid)[0]
                    == run.child_start_token
                ) or (
                    run.child_pid is None
                    and service.state().process_pid
                    and native.process_alive(service.state().process_pid)
                ):
                    raise PlatformRefusal(
                        "INFERENCE_ACTIVE", "Recorded dependent child is still alive"
                    )
                await self.release(run)
                if run.state != "released":
                    raise PlatformRefusal(
                        "CLEANUP_PENDING", "Runtime release is awaiting a worker acknowledgement"
                    )

    @contextmanager
    def stop_context(self):
        self.call(self.service.lock.acquire())
        try:
            yield
        finally:

            async def unlock():
                self.service.lock.release()

            self.call(unlock())

    @contextmanager
    def context(self, network_id: str | None, selected: list[str] | None = None):
        previous_pid = self.service.state().process_pid
        reservation, members = self.call(self.reserve(network_id, selected))
        success = False
        try:
            yield members
            success = True
        finally:
            self.call(self.finish(reservation, success, previous_pid))
