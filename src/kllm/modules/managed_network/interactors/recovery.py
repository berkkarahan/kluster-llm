"""Local operator review of exact resources after continuous ownership is lost."""

from __future__ import annotations

import asyncio
import ipaddress
from uuid import UUID

from pydantic import Field

from kllm.shared import native
from kllm.modules.managed_network.entities import Digest, Record
from kllm.shared.platform.base import PlatformRefusal
from kllm.modules.managed_network.gateways.store import StoreConflict, digest


class CleanupDecision(Record):
    decision: str = Field(pattern=r"^(confirm_owned|release_ownership)$")
    snapshot_digest: Digest


async def review(service, journal_id: UUID) -> dict:
    journal = next(
        (
            j
            for j in (await service.store.arun(service.store.journals))
            if j.journal_id == journal_id and j.node_id == service.identity.node_id
        ),
        None,
    )
    if journal is None:
        raise KeyError("local journal missing")
    assignment = next(
        (
            a
            for a in (await service.store.arun(service.store.assignments, journal.network_id))
            if a.node_id == service.identity.node_id
        ),
        None,
    )
    if assignment is None:
        raise PlatformRefusal("OWNERSHIP_CONFLICT", "Local assignment is missing")
    snapshot = await asyncio.to_thread(service.executor.platform.inspect, assignment.adapter_id)
    return {
        "journal": journal.model_dump(
            mode="json", exclude={"pre_snapshot", "intended_delta", "command_result"}
        ),
        "snapshot": snapshot.public(),
        "snapshot_digest": digest(snapshot.public()),
    }


async def resolve(service, journal_id: UUID, decision: CleanupDecision) -> dict:
    async with service.lock, service.executor.lock:
        inspected = await review(service, journal_id)
        if inspected["snapshot_digest"] != decision.snapshot_digest:
            raise StoreConflict("local snapshot changed; review it again")
        journal = next(
            j
            for j in (await service.store.arun(service.store.journals))
            if j.journal_id == journal_id
        )
        if journal.phase == "removed_observed":
            return {"settled": True}
        if any(
            r.state != "released"
            for r in (await service.store.arun(service.store.runs, journal.network_id))
        ) or any(
            lease.state != "released"
            for lease in (await service.store.arun(service.store.leases, journal.network_id))
        ):
            raise PlatformRefusal(
                "INFERENCE_ACTIVE", "Reconcile dependent processes and leases before address review"
            )
        assignment = next(
            a
            for a in (await service.store.arun(service.store.assignments, journal.network_id))
            if a.node_id == service.identity.node_id
        )
        if decision.decision == "release_ownership":
            assignment.ownership = "user_owned"
            (
                await service.store.arun(
                    service.store.put_assignment,
                    assignment,
                    expected_revision=assignment.assignment_revision,
                )
            )
            journal.command_result = "Local review released app ownership; address preserved"
        else:
            # Explicit local confirmation authorizes only the inspected exact
            # delta. Recheck under the process-wide writer lock immediately
            # before the fixed OS command, then prove absence.
            def remove():
                with service.store.execution():
                    snapshot = service.executor.platform.inspect(assignment.adapter_id)
                    if (
                        digest(snapshot.public()) != decision.snapshot_digest
                        or snapshot.fingerprint != journal.adapter_fingerprint
                    ):
                        raise StoreConflict("adapter or address changed during local review")
                    exact = ipaddress.IPv4Interface(journal.exact_address_prefix)
                    journal.phase = "removal_intent"
                    service.store.journal(journal)
                    if exact in snapshot.state.addresses:
                        service.executor.platform.delete_exact(snapshot, assignment)
                    after = service.executor.platform.inspect(assignment.adapter_id)
                    if exact in after.state.addresses:
                        raise PlatformRefusal(
                            "CLEANUP_PENDING", "Exact reviewed address is still present"
                        )
                    journal.observed_snapshot_digest = digest(after.public())

            await asyncio.to_thread(remove)
            journal.command_result = "Local review confirmed and removed exact owned address"
        journal.phase = "removed_observed"
        (await service.store.arun(service.store.journal, journal))
        await service.store.arun(settle_local_replica, service, journal.network_id)
        return {"settled": True, "decision": decision.decision}


def settle_local_replica(service, network_id: UUID) -> None:
    network = service.store.network(network_id)
    if (
        network is None
        or network.master_node_id == service.identity.node_id
        or network.desired_state != "stopped"
    ):
        return
    if any(
        j.network_id == network_id and j.phase != "removed_observed"
        for j in service.store.journals()
    ) or any(lease.state != "released" for lease in service.store.leases(network_id)):
        return
    if network.observed_state == "preparing":
        before = network.revision
        network.observed_state = "failed"
        network.revision += 1
        service.store.put_network(network, expected_revision=before)
    for state in ("stopping", "stopped"):
        if network.observed_state == "stopped":
            break
        before = network.revision
        network.observed_state = state
        network.revision += 1
        service.store.put_network(network, expected_revision=before)


async def before_reset(service) -> None:
    """Called after explicit cluster stop/leave has stopped its child."""
    await service.admission.release_stopped()
    if service.state().role == "master":
        from kllm.modules.managed_network.interactors.allocation import OperationRequest

        for network in await service.store.arun(service.store.networks):
            if network.observed_state != "stopped":
                from uuid import uuid4

                accepted = await service.begin_operation(
                    network.network_id,
                    OperationRequest(
                        request_id=uuid4(), expected_revision=network.revision, action="stop"
                    ),
                )
                await service.wait_operation(accepted["operation_id"])
    else:
        # Explicit leave is allowed to end this node's admitted streams. The
        # journals retain identity/authority independently of membership reset.
        for key, bridge in list(service.executor.bridges.items()):
            status = native.network_bridge_status(bridge)
            await asyncio.to_thread(
                native.stop_network_bridge, bridge, status["instance_id"], terminate=True
            )
            service.executor.bridges.pop(key)
        for network in await service.store.arun(service.store.networks):
            for lease in await service.store.arun(service.store.leases, network.network_id):
                child = lease.expected_rpc_child_identity
                if lease.state != "released" and not native.process_alive(child.pid):
                    lease.state = "released"
                    lease.release_ack = lease.drain_ack = True
                    (await service.store.arun(service.store.lease, lease))
        for journal in await service.store.arun(service.store.journals):
            if journal.phase != "removed_observed":
                prepared = service.executor.prepared.get(str(journal.network_id))
                if prepared is None:
                    continue  # Absence or ambiguous ownership needs local review.
                try:
                    journal.phase = "removal_intent"
                    (await service.store.arun(service.store.journal, journal))
                    await asyncio.to_thread(
                        service.executor.platform.remove_owned, prepared, journal, lambda: None
                    )
                    journal.phase = "removed_observed"
                    (await service.store.arun(service.store.journal, journal))
                except (PlatformRefusal, StoreConflict):
                    journal.phase = "cleanup_pending"
                    (await service.store.arun(service.store.journal, journal))
        for diagnostic in service.executor.diagnostics.values():
            await diagnostic.close()
        service.executor.diagnostics.clear()
        for prepared in service.executor.prepared.values():
            await asyncio.to_thread(service.executor.platform.close_prepared, prepared)
        service.executor.prepared.clear()
        for network in await service.store.arun(service.store.networks):
            if network.observed_state not in ("stopped", "cleanup_pending"):
                before = network.revision
                network.observed_state = (
                    "failed" if network.observed_state == "preparing" else "cleanup_pending"
                )
                network.desired_state = "stopped"
                network.revision += 1
                (
                    await service.store.arun(
                        service.store.put_network, network, expected_revision=before
                    )
                )
            await service.store.arun(settle_local_replica, service, network.network_id)
