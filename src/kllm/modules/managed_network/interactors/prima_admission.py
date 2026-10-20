"""Private rank-network reservations without llama.cpp RPC children or relays."""

from __future__ import annotations

import asyncio
import contextvars
from copy import deepcopy
import ipaddress
from uuid import UUID, uuid4

from kllm.modules.managed_network.entities import (
    ManagedNetwork,
    NodeAssignment,
    PRIVATE_NETWORKS,
    RunNetworkReservation,
)
from kllm.modules.primacpp.entities import PrimaTopology
from kllm.modules.primacpp.interactors.execution import inventory_identity
from kllm.modules.primacpp.interactors.placement import network_edges
from kllm.shared.platform.base import PlatformRefusal

UNMANAGED_NETWORK_ID = UUID("00000000-0000-0000-0000-000000000001")


def _canonical_private_ip(val: str | None, default: str = "192.168.1.1") -> str:
    if val:
        try:
            ip = ipaddress.IPv4Address(val)
            if any(ip in net for net in PRIVATE_NETWORKS):
                return str(ip)
        except (ValueError, TypeError):
            pass
    return default



async def _port_call(command, *args):
    # Cancelling an asyncio wait cannot cancel an in-flight OS/helper operation.
    # Settle it before callers can acknowledge cleanup or admit another run.
    operation = asyncio.get_running_loop().run_in_executor(None, contextvars.copy_context().run, command, *args)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(operation)
        except asyncio.CancelledError:
            if operation.cancelled():
                raise
            cancelled = True
            continue
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
        if cancelled:
            raise asyncio.CancelledError
        return result


class PrimaNetworkAdmission:
    def __init__(self, service):
        self.service = service

    def inventory(self, network_id, nodes=None):
        service = self.service
        network, assignments, members = service.admission.inventory(UUID(str(network_id)),
            selected=nodes, engine="primacpp")
        if nodes is not None and set(members) != set(nodes):
            raise PlatformRefusal("STALE_REVISION", "Prima participants no longer match private assignments")
        # Prima binds its own rank sockets. Both master and workers need their
        # assigned addresses rather than management hosts or RPC relay endpoints.
        for assignment in assignments:
            members[assignment.node_id].update(host=assignment.address, rpc_bind=assignment.address,
                network_revision=assignment.assignment_revision)
        return network, assignments, members

    async def preview(self, network_id):
        async with self.service.lock:
            return (await self.service.store.arun(self.inventory, network_id))[2]

    def members(self, network_id):
        return self.service.admission.call(self.preview(network_id))

    def _contract(self, manifest):
        if len(manifest.ranks) < 2:
            raise PlatformRefusal("STALE_REVISION", "Distributed Prima requires at least 2 ranks")
        if not manifest.network_id:
            ranks_nodes = {r.node_id for r in manifest.ranks}
            cluster_members = self.service.state().members
            if not (ranks_nodes <= set(cluster_members)):
                raise PlatformRefusal(
                    "STALE_REVISION", "Distributed Prima ranks are not all active cluster members"
                )
            topology = PrimaTopology(
                runtime_id=manifest.runtime_id,
                master_node_id=manifest.ranks[0].node_id,
                total_layers=manifest.total_layers,
                ranks=manifest.ranks,
                data_port=manifest.data_port,
                signal_port=manifest.signal_port,
            )
            edges = network_edges(topology)
            if manifest.transfer_edges != edges:
                raise PlatformRefusal(
                    "STALE_REVISION", "Prima network edges differ from the execution manifest"
                )
            if topology.master_node_id != self.service.identity.node_id:
                raise PlatformRefusal(
                    "NOT_MASTER", "Only the manifest master can reserve Prima networking"
                )
            binding_id = (
                getattr(self.service.identity, "cluster_binding_id", None)
                or (
                    getattr(self.service.channels.master_authority(), "cluster_binding_id", None)
                    if hasattr(self.service, "channels")
                    and hasattr(self.service.channels, "master_authority")
                    else None
                )
                or UUID("00000000-0000-0000-0000-000000000000")
            )
            key_id = getattr(self.service.identity, "key_id", "0" * 64)
            fingerprint = getattr(self.service.identity, "fingerprint", "") or ("0" * 64)

            assignments = []
            for idx, rank in enumerate(manifest.ranks, start=1):
                m = cluster_members[rank.node_id]
                raw_addr = rank.address or m.get("lan_host") or m.get("host")
                addr = _canonical_private_ip(raw_addr, f"192.168.1.{idx}")
                assignments.append(
                    NodeAssignment(
                        network_id=UNMANAGED_NETWORK_ID,
                        node_id=rank.node_id,
                        assignment_revision=1,
                        adapter_id="lan",
                        os_interface="lan",
                        identity_fingerprint=fingerprint,
                        address=addr,
                        prefix_length=24,
                        ownership="user_owned",
                        state="ready",
                    )
                )
            network = ManagedNetwork(
                network_id=UNMANAGED_NETWORK_ID,
                cluster_binding_id=binding_id,
                master_node_id=self.service.identity.node_id,
                master_key_id=key_id,
                subnet="192.168.1.0/24",
                revision=1,
                desired_state="active",
                observed_state="ready",
                current_operation_id=uuid4(),
            )
            return network, assignments, edges
        topology = PrimaTopology(runtime_id=manifest.runtime_id,
            master_node_id=manifest.ranks[0].node_id, total_layers=manifest.total_layers,
            ranks=manifest.ranks, data_port=manifest.data_port, signal_port=manifest.signal_port)
        edges = network_edges(topology)
        if manifest.transfer_edges != edges:
            raise PlatformRefusal("STALE_REVISION", "Prima network edges differ from the execution manifest")
        if topology.master_node_id != self.service.identity.node_id:
            raise PlatformRefusal("NOT_MASTER", "Only the manifest master can reserve Prima networking")
        network, assignments, members = self.inventory(manifest.network_id, [r.node_id for r in manifest.ranks])
        if any(members[r.node_id]["rpc_bind"] != r.address for r in manifest.ranks):
            raise PlatformRefusal("STALE_REVISION", "Prima ranks must bind their exact private assignments")
        # Preparation binds the entire selected network inventory, including any
        # enrolled node the planner omitted from execution.
        complete = self.inventory(manifest.network_id)[2]
        if inventory_identity(complete, manifest.network_id) != manifest.inventory_digest:
            raise PlatformRefusal("STALE_REVISION", "Prima private inventory changed; prepare the current plan")
        return network, assignments, edges

    async def reserve(self, manifest, *, native_ports=False):
        service = self.service
        async with service.lock:
            if service.state().role != "master":
                raise PlatformRefusal("NOT_MASTER", "Prima admission requires master role")
            if not manifest.network_id:
                network, assignments, edges = self._contract(manifest)
                runs = await service.store.arun(service.store.runs, UNMANAGED_NETWORK_ID)
                if any(r.state != "released" for r in runs):
                    raise PlatformRefusal("INFERENCE_ACTIVE", "Drain the previous managed run before admitting Prima")
                if service.state().process_pid:
                    raise PlatformRefusal("INFERENCE_ACTIVE", "Stop inference before admitting Prima ranks")
                authority = service.channels.master_authority()
                reservation = RunNetworkReservation(
                    run_id=uuid4(),
                    network_id=UNMANAGED_NETWORK_ID,
                    engine="primacpp",
                    execution_manifest_digest=manifest.digest,
                    authority=authority,
                    assignment_revisions={a.node_id: a.assignment_revision for a in assignments},
                    logical_endpoints=[
                        f"{e['source']}->{e['target']}@{e['address']}:{e['port']}"
                        for e in edges
                    ],
                    launch_endpoints=[f"{e['address']}:{e['port']}" for e in edges],
                    relay_instance_ids=[],
                    state="reserved",
                    prima_ports_required=native_ports,
                )
                await service.store.arun(service.store.reservation, reservation)
                return reservation
            network, assignments, edges = await service.store.arun(self._contract, manifest)
            runs = []
            for candidate in await service.store.arun(service.store.networks):
                runs.extend(await service.store.arun(service.store.runs, candidate.network_id))
            if any(r.state != "released" for r in runs):
                raise PlatformRefusal("INFERENCE_ACTIVE", "Drain the previous managed run before admitting Prima")
            if service.state().process_pid:
                raise PlatformRefusal("INFERENCE_ACTIVE", "Stop inference before admitting Prima ranks")
            operation = await service.store.arun(service.store.operation, network.current_operation_id)
            if operation is None:
                raise PlatformRefusal("STALE_REVISION", "Selected network has no current signed operation")
            await asyncio.wait_for(service._health(network, operation, assignments), 5)
            # Recheck after the signed route/address diagnostic, before ownership.
            network, assignments, edges = await service.store.arun(self._contract, manifest)
            authority = service.channels.master_authority()
            reservation = RunNetworkReservation(run_id=uuid4(), network_id=network.network_id,
                engine="primacpp", execution_manifest_digest=manifest.digest, authority=authority,
                assignment_revisions={a.node_id: a.assignment_revision for a in assignments},
                logical_endpoints=[f"{e['source']}->{e['target']}@{e['address']}:{e['port']}" for e in edges],
                launch_endpoints=[f"{e['address']}:{e['port']}" for e in edges],
                relay_instance_ids=[], state="reserved", prima_ports_required=native_ports)
            await service.store.arun(service.store.reservation, reservation)
            return reservation

    async def admit_ports(self, manifest, command, check):
        from kllm.modules.managed_network.interactors.prima_ports import port_contract

        service = self.service
        # Durable reservation prevents topology changes. Do not hold service.lock
        # across signed calls: command cancellation checks also inspect inventory.
        for rank in manifest.ranks:
            await asyncio.to_thread(check)
            async with service.lock:
                _, assignments, _ = await service.store.arun(self._contract, manifest)
                reservation = await service.store.arun(self._owned_reservation, manifest)
                if (reservation is None or not reservation.prima_ports_required or reservation.state != "reserved"
                        or reservation.authority != service.channels.master_authority()):
                    raise PlatformRefusal("STALE_REVISION", "Prima native listener reservation changed")
                contract = port_contract(manifest, rank.node_id, assignments)
                reservation.prima_port_nodes.append(rank.node_id)
                await service.store.arun(service.store.reservation, reservation)
            result = await _port_call(command, rank.node_id, "prima_admit_ports", manifest,
                                     {"contract": contract.model_dump(mode="json")})
            await asyncio.to_thread(check)
            if result != {"phase": "admitted", "manifest_digest": manifest.digest}:
                raise PlatformRefusal("CLEANUP_PENDING", "Prima participant did not acknowledge exact native listeners")
            async with service.lock:
                reservation = await service.store.arun(self._owned_reservation, manifest)
                if reservation is None or reservation.state != "reserved":
                    raise PlatformRefusal("CLEANUP_PENDING", "Prima listener admission was cancelled")
                reservation.prima_admitted_nodes.append(rank.node_id)
                await service.store.arun(service.store.reservation, reservation)

    def _owned_reservation(self, manifest):
        if not manifest.network_id:
            net_id = UNMANAGED_NETWORK_ID
        else:
            try:
                net_id = UUID(str(manifest.network_id))
            except (ValueError, TypeError):
                return None
        runs = self.service.store.runs(net_id)
        matches = [
            r
            for r in runs
            if r.engine == "primacpp"
            and r.execution_manifest_digest == manifest.digest
            and r.state != "released"
        ]
        if len(matches) > 1:
            raise PlatformRefusal("CLEANUP_PENDING", "Ambiguous Prima private-network ownership")
        return matches[0] if matches else None

    async def validate(self, manifest):
        async with self.service.lock:
            await self.service.store.arun(self._contract, manifest)
            reservation = await self.service.store.arun(self._owned_reservation, manifest)
            if reservation is None or reservation.authority != self.service.channels.master_authority():
                raise PlatformRefusal("STALE_REVISION", "Prima private-network reservation or authority changed")
            if reservation.state not in ("reserved", "active"):
                raise PlatformRefusal("CLEANUP_PENDING", "Prima network admission is being released")
            if reservation.prima_ports_required and set(reservation.prima_admitted_nodes) != {r.node_id for r in manifest.ranks}:
                raise PlatformRefusal("CLEANUP_PENDING", "Prima native listeners are not admitted on every rank")
            return reservation

    async def serving(self, manifest, owner):
        service = self.service
        async with service.lock:
            reservation = await service.store.arun(self._owned_reservation, manifest)
            if reservation is None or reservation.authority != service.channels.master_authority():
                raise PlatformRefusal("STALE_REVISION", "Prima private-network reservation changed before readiness")
            reservation.child_pid, reservation.child_start_token = owner["pid"], owner["start_token"]
            reservation.state = "active"
            await service.store.arun(service.store.reservation, reservation)

    async def release(self, manifest, port_command=None):
        # Called only by the owned coordinator after every signed/local stop has
        # acknowledged idle. A dead master alone does not prove workers stopped.
        service = self.service
        async with service.lock:
            reservation = await service.store.arun(self._owned_reservation, manifest)
            if reservation is None:
                return  # Admission may have failed before ownership was written.
            reservation.state = "releasing"
            await service.store.arun(service.store.reservation, reservation)
        pending = []
        for node in list(reservation.prima_port_nodes):
            if port_command is None:
                raise PlatformRefusal("CLEANUP_PENDING", "Owned Prima native listeners require participant release")
            try:
                result = await _port_call(port_command, node, "prima_release_ports", manifest,
                                         {"network_id": manifest.network_id})
                if result != {"phase": "released"}:
                    raise ValueError("Native listeners have not drained")
            except Exception:
                pending.append(node)
                continue
            async with service.lock:
                reservation = await service.store.arun(self._owned_reservation, manifest)
                if node in reservation.prima_admitted_nodes:
                    reservation.prima_admitted_nodes.remove(node)
                reservation.prima_port_nodes.remove(node)
                await service.store.arun(service.store.reservation, reservation)
        if pending:
            raise PlatformRefusal("CLEANUP_PENDING", "Prima native listener cleanup pending on " + ", ".join(pending))
        async with service.lock:
            reservation = await service.store.arun(self._owned_reservation, manifest)
            reservation.state = "released"
            await service.store.arun(service.store.reservation, reservation)
            if hasattr(service, "freshness") and service.freshness is not None:
                service.freshness.invalidate(reservation.network_id, "Prima run ended; obtain fresh private route proof")

    def validate_worker(self, manifest):
        service = self.service
        if not manifest.network_id:
            member = service.state().members.get(service.identity.node_id)
            if member:
                rank = next((r for r in manifest.ranks if r.node_id == service.identity.node_id), None)
                raw_addr = (rank.address if rank else None) or member.get("lan_host") or member.get("host")
                addr = _canonical_private_ip(raw_addr, "192.168.1.1")
                fingerprint = getattr(service.identity, "fingerprint", "") or ("0" * 64)
                return NodeAssignment(
                    network_id=UNMANAGED_NETWORK_ID,
                    node_id=service.identity.node_id,
                    assignment_revision=1,
                    adapter_id="lan",
                    os_interface="lan",
                    identity_fingerprint=fingerprint,
                    address=addr,
                    prefix_length=24,
                    ownership="user_owned",
                    state="ready",
                )
            raise ValueError("Distributed Prima requires private-network admission")
        rank = next((r for r in manifest.ranks if r.node_id == service.identity.node_id), None)
        assignment = next((a for a in service.store.assignments(UUID(manifest.network_id))
            if a.node_id == service.identity.node_id), None)
        prepared = service.executor.prepared.get(manifest.network_id)
        fields = ("assignment_revision", "address", "prefix_length", "identity_fingerprint",
                  "adapter_id", "os_interface")
        network = service.store.network(UUID(manifest.network_id))
        if (rank is None or assignment is None or prepared is None
                or network is None or network.master_node_id != manifest.ranks[0].node_id
                or network.desired_state != "active" or assignment.address != rank.address
                or any(getattr(prepared.assignment, k) != getattr(assignment, k) for k in fields)
                or prepared.ownership_conflict):
            raise ValueError("Prima worker private assignment changed; verify the network again")
        # Signed health checks observe the interface/route before master admission;
        # retain that prepared owner through worker startup and subsequent renewals.
        return deepcopy(assignment)
