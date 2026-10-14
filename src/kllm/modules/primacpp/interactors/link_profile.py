"""Preparation-owned measurements of exact directed private rank links."""

import asyncio
import ipaddress
import math
import statistics
import time
from uuid import UUID, uuid4

from kllm.modules.managed_network.entities import NodeIdentity
from kllm.modules.managed_network.interactors.verification import StreamScope
from kllm.modules.primacpp.interactors.execution import ExecutionManifest, profile_pairs
from kllm.modules.primacpp.interactors.execution import authority_owner
from kllm.shared.runtime_resources import prima_scope


def _guard(control, preparation_job_id, scope, owner, authorize):
    authorize()
    service = control.service
    service.store.check_authority(scope.authority)
    if authority_owner(scope.authority)["authority_digest"] != owner["authority_digest"]:
        raise ValueError("Private link master authority changed")
    with control.lock:
        if service.state().node_id == scope.authority.master_node_id:
            job = control.jobs[preparation_job_id]
            control._check(preparation_job_id)
            if (
                job.get("manifest_digest") != scope.contract_digest
                or preparation_job_id != str(scope.operation_id)
                or job["phase"] not in ("preparing", "prepared")
            ):
                raise ValueError("Private link requires its current master preparation")
            manifest = ExecutionManifest.model_validate(job["manifest"])
        else:
            job = control._command_job(preparation_job_id, str(scope.operation_id), owner)
            if (
                job["phase"] != "prepared"
                or job.get("expires_at", 0) <= time.time()
                or not any(
                    a["kind"] == "manifest" and a["sha256"] == scope.contract_digest
                    for a in job["assets"]
                )
            ):
                raise ValueError("Private link requires a current complete signed preparation")
            manifest = ExecutionManifest.model_validate_json(
                control.cached_model(scope.contract_digest).read_text()
            )
    if (
        manifest.digest != scope.contract_digest
        or manifest.network_id != str(scope.network_id)
        or manifest.ranks[0].node_id != scope.authority.master_node_id
        or (scope.source.node_id, scope.target.node_id) not in profile_pairs(manifest)
    ):
        raise ValueError("Private link is absent from the approved manifest")
    assignment = service.prima_admission.validate_worker(manifest)
    preparation = service.executor.preparation.get(manifest.network_id)
    if preparation is None:
        raise ValueError("Private link network preparation disappeared")
    assignments = {a.node_id: a for a in preparation.participants}
    ranks = {rank.node_id: rank for rank in manifest.ranks}
    for peer, revision in (
        (scope.source, scope.source_revision),
        (scope.target, scope.target_revision),
    ):
        selected = assignments.get(peer.node_id)
        if (
            selected is None
            or selected.assignment_revision != revision
            or peer.cluster_binding_id != scope.authority.cluster_binding_id
            or selected.address != ranks[peer.node_id].address
        ):
            raise ValueError("Private link assignment or peer identity changed")
        pinned = service.store.peer(peer.node_id)
        if pinned is not None and (
            pinned.public_key != peer.public_key or pinned.key_id != peer.key_id
        ):
            raise ValueError("Private link conflicts with a pinned peer identity")
    prepared = service.executor.prepared[manifest.network_id]
    snapshot = service.executor.platform.observe(prepared)
    service.executor.platform.eligible(snapshot)
    if (
        ipaddress.IPv4Interface(f"{assignment.address}/{assignment.prefix_length}")
        not in snapshot.state.addresses
    ):
        raise ValueError("Private link source address disappeared")
    expected = next(
        a
        for a in preparation.participants
        if a.node_id != assignment.node_id
        and a.node_id in (scope.source.node_id, scope.target.node_id)
    )
    service.executor.platform.check_source_route(snapshot, assignment.address, expected.address)


async def execute_link(control, value, operation_id, owner, authorize):
    if set(value) != {"preparation_job_id", "scope", "mode"} or value["mode"] not in (
        "receive",
        "send",
    ):
        raise ValueError("Invalid signed Prima private link request")
    scope = StreamScope.model_validate(value["scope"])
    if str(scope.operation_id) != operation_id or owner["sender"] != scope.authority.master_node_id:
        raise ValueError("Private link does not belong to this signed preparation")

    def check():
        _guard(control, value["preparation_job_id"], scope, owner, authorize)

    await asyncio.to_thread(check)
    diagnostic = control.service.executor.diagnostics.get(str(scope.network_id))
    if diagnostic is None:
        raise ValueError("Approved private diagnostic listener disappeared")
    local = scope.target if value["mode"] == "receive" else scope.source
    if local.node_id != control.service.state().node_id:
        raise ValueError("Private link command targets another participant")
    lease = control.resources.acquire(prima_scope(operation_id, owner, scope.contract_digest), "link")
    try:
        grant = await diagnostic.grant_stream(scope, check, release=lease.release)
    except BaseException:
        lease.release()
        raise
    if value["mode"] == "receive":
        return {"phase": "ready", "grant_id": grant}
    try:
        preparation = control.service.executor.preparation[str(scope.network_id)]
        measured = await diagnostic.measure(
            scope.target.node_id, scope.target_revision, preparation.diagnostic_port, grant_id=grant
        )
        await asyncio.to_thread(check)
        return {"phase": "measured", "grant_id": grant, "measurement": measured}
    finally:
        await diagnostic.revoke_stream(grant)


def collect_links(control, manifest, operation_id, check, progress):
    service = control.service
    authority = service.channels.master_authority().model_copy(update={"accepted_channel_id": None})
    assignments = {a.node_id: a for a in service.store.assignments(UUID(manifest.network_id))}
    peers = {}
    for rank in manifest.ranks:
        if rank.node_id == service.identity.node_id:
            peers[rank.node_id] = service.identity.public(authority.cluster_binding_id)
        else:
            pinned = service.store.peer(rank.node_id)
            if pinned is None:
                raise ValueError("Private link participant identity disappeared")
            peers[rank.node_id] = NodeIdentity.model_validate(
                pinned.model_dump(exclude={"expected_role"})
            )

    def call(node, scope, mode):
        check()
        preparation = (
            operation_id
            if node == service.identity.node_id
            else control.jobs[operation_id]["nodes"][node]["job_id"]
        )
        value = {
            "preparation_job_id": preparation,
            "scope": scope.model_dump(mode="json"),
            "mode": mode,
        }
        if node == service.identity.node_id:
            owner = {**authority_owner(authority), "sender": node}
            return service.admission.call(execute_link(control, value, operation_id, owner, check))
        return control._call(node, operation_id, "prima_link_profile", value)

    measured = []
    pairs = sorted(profile_pairs(manifest))
    for source, target in pairs:
        scope = StreamScope(
            probe_id=uuid4(),
            operation_id=UUID(operation_id),
            contract_digest=manifest.digest,
            network_id=UUID(manifest.network_id),
            authority=authority,
            source=peers[source],
            target=peers[target],
            source_revision=assignments[source].assignment_revision,
            target_revision=assignments[target].assignment_revision,
        )
        if call(target, scope, "receive") != {"phase": "ready", "grant_id": scope.digest}:
            raise ValueError("Private link target did not approve its exact scope")
        result = call(source, scope, "send")
        check()
        receipt = result.get("measurement", {})
        durations = receipt.get("sample_seconds", [])
        if (
            result.get("phase") != "measured"
            or result.get("grant_id") != scope.digest
            or receipt.get("grant_id") != scope.digest
            or receipt.get("method") != "signed_private_tcp_application_v1"
            or receipt.get("source_node_id") != source
            or receipt.get("target_node_id") != target
            or receipt.get("network_id") != manifest.network_id
            or receipt.get("source") != assignments[source].address
            or receipt.get("peer") != assignments[target].address
            or receipt.get("source_revision") != scope.source_revision
            or receipt.get("target_revision") != scope.target_revision
            or receipt.get("payload_bytes") != 1048576
            or receipt.get("samples") != 3
            or len(durations) != 3
            or any(
                type(v) not in (int, float) or not math.isfinite(v) or not 0 < v < 10
                for v in durations
            )
            or type(receipt.get("latency_ms")) not in (int, float)
            or not 0 < receipt["latency_ms"] < 10000
            or type(receipt.get("bandwidth_bytes_s")) not in (int, float)
            or not math.isfinite(receipt["bandwidth_bytes_s"])
            or type(receipt.get("measured_at")) not in (int, float)
            or not math.isfinite(receipt["measured_at"])
            or not math.isclose(
                receipt.get("bandwidth_bytes_s", 0),
                1048576 / statistics.median(durations),
                rel_tol=1e-9,
            )
        ):
            raise ValueError("Private link receipt differs from the approved measurement")
        measured.append(
            {"scope": scope.model_dump(mode="json"), **receipt, "received_at": time.time()}
        )
        progress()
    return measured
