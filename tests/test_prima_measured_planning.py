"""Observable typed candidate selection; rates and hardware are controlled fixtures."""

from copy import deepcopy
from hashlib import sha256
import threading
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from kllm.modules.inference.entities import RunBody
from kllm.modules.managed_network.entities import MasterAuthority
from kllm.modules.managed_network.interactors.verification import StreamScope
from kllm.modules.primacpp.entities import DeviceProfile
from kllm.modules.primacpp.interactors.distributed_planning import score_candidates, measured_candidates
from kllm.modules.primacpp.interactors.execution import Asset, ExecutionManifest, Segment, authority_owner, inventory_identity
from kllm.modules.primacpp.interactors.kernel_profile import kernel_hardware, kernel_settings
from kllm.modules.primacpp.interactors.participants import ParticipantEvidence
from kllm.modules.primacpp.interactors.placement import topology_from_plan, network_edges
from kllm.modules.runtimes.entities import body_digest


@pytest.fixture
def measurements(tmp_path):
    now = time.time()
    network = str(uuid4())
    model = tmp_path / "fixture.gguf"
    model.write_bytes(b"controlled model bytes")
    digest = sha256(model.read_bytes()).hexdigest()
    body = RunBody(model=model.name, network_id=network, context=512, threads=2,
        cache_type_k="f16", cache_type_v="f16", flash_attention="off", checkpoints=0, cache_ram=0, parallel_slots=1)
    members = {node: {"role": "master" if i == 0 else "worker", "rpc_bind": f"192.168.234.{i + 1}",
        "host": f"192.168.234.{i + 1}", "network_revision": 1, "backend": "cpu"}
        for i, node in enumerate(("master", "a", "b"))}
    def plan(rows):
        return {"fit_status": "fit", "estimated_layers": 4, "cpu_layers": 0, "device_effective": "cpu",
            "resolved_launch_settings": {"parallel_slots": 1, "prima_kv_sequences": 2},
            "device_allocations": [{"node_id": node, "layer_start": start, "layer_end": end,
                "layers": end - start, "memory_kind": "ram"} for node, start, end in rows]}
    feasible = [(plan([("a", 0, 2), ("b", 2, 4)]), members),
        (plan([("b", 0, 4)]), {k: v for k, v in members.items() if k != "a"})]
    runtime = {"runtime_id": "a" * 64}
    topology = topology_from_plan(feasible[0][0], members, runtime["runtime_id"])
    host = {"os": "Windows fixture", "cpu": "fixture CPU", "physical_cores": 2, "logical_cores": 4, "ram_bytes": 2**30}
    storage = {"filesystem_device": "fixture-volume", "directory": str(tmp_path)}
    profiles, contexts = {}, {}
    for rank in topology.ranks:
        evidence = ParticipantEvidence(node_id=rank.node_id, runtime_id=runtime["runtime_id"], source_commit="1" * 40,
            recipe_digest="2" * 64, build_id="fixture", artifacts_digest="3" * 64, platform="Windows",
            build_backend="cpu", backend="cpu", allocation_digest=body_digest(rank.model_dump()), host=host, devices=[])
        costs = {"master": [10., 10., 10., 10.], "a": [1., 1., 50., 50.], "b": [50., 50., 1., 1.]}[rank.node_id]
        profiles[rank.node_id] = DeviceProfile(runtime_id=runtime["runtime_id"], model_sha256=digest,
            node_id=rank.node_id, hardware_digest=body_digest(kernel_hardware(host, [], "cpu")),
            settings_digest=body_digest(kernel_settings(512, 2, [])), backend="cpu", measured_at=now,
            cpu_layer_ms=sum(costs) / 4, kernel_profile_version=2, kernel_context=512, kernel_threads=2,
            total_layers=4, cpu_layer_ms_by_index=costs, weights_bytes_by_index=[4096] * 4,
            input_ms=0.1, output_cpu_ms=0.2, disk_read_bytes_s=1e6, disk_random_read_bytes_s=1e5,
            disk_random_block_bytes=4096, memory_available_bytes=2**30, storage_identity=storage).model_dump()
        contexts[rank.node_id] = {"participant_evidence": evidence.model_dump(), "storage_identity": storage}
    links = {(source, target): {"received_at": now, "latency_ms": 0.1, "bandwidth_bytes_s": 1e6,
        "method": "signed_private_tcp_application_v1", "payload_bytes": 1048576, "samples": 3,
        "sample_seconds": [1.048576] * 3, "source_node_id": source, "target_node_id": target,
        "source": members[source]["rpc_bind"], "peer": members[target]["rpc_bind"],
        "source_revision": 1, "target_revision": 1}
        for source in members for target in members if source != target}
    layout = {"architecture": "llama", "confidence": "metadata", "metadata": {"llama.embedding_length": 1024},
        "layers": [4096] * 4, "input_bytes": 4096, "output_bytes": 4096}
    return SimpleNamespace(now=now, network=network, model=model, digest=digest, body=body, runtime=runtime,
        members=members, topology=topology, profiles=profiles, contexts=contexts, links=links, layout=layout)


def score(m):
    return score_candidates(m.feasible, m.body, m.runtime, m.digest, m.layout, m.profiles, m.contexts, m.links, now=m.now)


def choices(m):
    def plan(rows):
        return {"fit_status": "fit", "estimated_layers": 4, "cpu_layers": 0, "device_effective": "cpu",
            "resolved_launch_settings": {"parallel_slots": 1, "prima_kv_sequences": 2},
            "device_allocations": [{"node_id": node, "layer_start": start, "layer_end": end,
                "layers": end-start, "memory_kind": "ram"} for node, start, end in rows]}
    m.feasible = [(plan([("a", 0, 2), ("b", 2, 4)]), m.members),
        (plan([("b", 0, 4)]), {k: v for k, v in m.members.items() if k != "a"})]
    return m


def test_absolute_layers_and_zero_work_coordinator_determine_score(measurements):
    m = choices(measurements)
    selected, result = score(m)
    assert selected == m.feasible[0]
    assert result["status"] == "profiled_estimate"
    assert result["compute_ms"] == pytest.approx(4.3)
    assert result["candidate_estimates"][1]["compute_ms"] == pytest.approx(102.3)
    assert result["predicted_weight_read_ms"] == pytest.approx(24.576)
    assert result["application_link_ms"] == pytest.approx(3 * (0.1 + 4.157) + 2 * (0.1 + 0.074))


def test_actual_link_cost_can_choose_fewer_workers(measurements):
    m = choices(measurements)
    for receipt in m.links.values():
        receipt["latency_ms"] = 100.
    selected, result = score(m)
    assert selected == m.feasible[1]
    assert result["predicted_decode_ms"] < result["candidate_estimates"][0]["predicted_decode_ms"]


@pytest.mark.parametrize("fault", ["missing_profile", "storage", "host", "context", "model", "old_profile",
    "missing_link", "old_link", "address", "revision", "layout", "bandwidth", "method"])
def test_partial_or_stale_evidence_cannot_eliminate_unmeasured_candidate(measurements, fault):
    m = choices(measurements)
    if fault == "missing_profile":
        del m.profiles["b"]
    elif fault == "storage":
        m.contexts["b"]["storage_identity"]["filesystem_device"] = "changed-volume"
    elif fault == "host":
        m.contexts["b"]["participant_evidence"]["host"]["cpu"] = "changed CPU"
    elif fault in ("context", "model", "old_profile"):
        key, value = {"context": ("kernel_context", 1024), "model": ("model_sha256", "f" * 64),
            "old_profile": ("measured_at", m.now - 86401)}[fault]
        m.profiles["b"][key] = value
    elif fault == "missing_link":
        del m.links[("master", "b")]
    elif fault == "old_link":
        m.links[("master", "b")]["received_at"] = m.now - 301
    elif fault in ("address", "revision"):
        m.links[("master", "b")]["peer" if fault == "address" else "target_revision"] = "192.168.234.99" if fault == "address" else 2
    elif fault in ("bandwidth", "method"):
        m.links[("master", "b")]["bandwidth_bytes_s" if fault == "bandwidth" else "method"] = float("inf") if fault == "bandwidth" else "management_socket"
    else:
        m.layout["metadata"].clear()
    selected, result = score(m)
    assert selected == m.feasible[0] and result["status"] == "unmeasured"


def test_gpu_suffix_uses_matching_completed_host_handoffs(measurements):
    m = choices(measurements)
    m.feasible = m.feasible[:1]
    m.members["b"]["backend"] = "vulkan"
    row = m.feasible[0][0]["device_allocations"][1]
    row.update(memory_kind="gpu", device="Vulkan0", local=True)
    device = {"backend": "vulkan", "name": "Vulkan0", "device_identity": "1" * 32,
        "driver_identity": "fixture-driver", "physical_index": 0, "memory_bytes": 2**30}
    evidence = m.contexts["b"]["participant_evidence"]
    evidence.update(backend="vulkan", devices=[device])
    value = m.profiles["b"]
    value.update(backend="vulkan", gpu_layer_ms=0.25, gpu_layer_ms_by_index=[0.25]*4,
        output_gpu_ms=0.5, gpu_kv_copy_ms=0.01, gpu_transfer_bytes=4096, gpu_upload_ms=0.2, gpu_download_ms=0.3,
        device_identity=device["device_identity"], driver_identity=device["driver_identity"],
        hardware_digest=body_digest(kernel_hardware(evidence["host"], [device], "vulkan")),
        settings_digest=body_digest(kernel_settings(512, 2, [device])))
    _, result = score(m)
    assert result["compute_ms"] == pytest.approx(2.8)
    assert result["host_handoff_ms"] == pytest.approx(0.5)
    value["gpu_transfer_bytes"] = 8192
    assert score(m)[1]["status"] == "unmeasured"


@pytest.mark.parametrize("distributed", [False, True])
def test_gpu_output_uses_logits_width_and_actual_graph_handoffs(measurements, distributed):
    m = measurements
    members = {n: v for n, v in m.members.items() if n in (("master", "a") if distributed else ("master",))}
    members["master"]["backend"] = "vulkan"
    rows = []
    if distributed:
        rows.append({"node_id": "a", "layer_start": 0, "layer_end": 2, "layers": 2, "memory_kind": "ram"})
    rows.append({"node_id": "master", "layer_start": 2 if distributed else 0, "layer_end": 5,
        "layers": 3 if distributed else 5, "memory_kind": "gpu", "device": "Vulkan0", "local": True})
    plan = {"fit_status": "fit", "estimated_layers": 4, "cpu_layers": 0, "device_effective": "gpu",
        "resolved_launch_settings": {"parallel_slots": 1, "prima_kv_sequences": 2}, "device_allocations": rows}
    m.feasible = [(plan, members)]
    m.layout["tensors"] = [{"name": "token_embd.weight", "dimensions": (1024, 32768)}]
    device = {"backend": "vulkan", "name": "Vulkan0", "device_identity": "1" * 32,
        "driver_identity": "fixture-driver", "physical_index": 0, "memory_bytes": 2**30}
    evidence = m.contexts["master"]["participant_evidence"]
    evidence.update(backend="vulkan", devices=[device])
    value = m.profiles["master"]
    value.update(backend="vulkan", gpu_layer_ms=0.25, gpu_layer_ms_by_index=[0.25]*4,
        output_gpu_ms=0.05, gpu_kv_copy_ms=0.01, gpu_transfer_bytes=4096,
        gpu_upload_ms=0.2, gpu_download_ms=0.3, gpu_logits_bytes=131072, gpu_logits_download_ms=0.1,
        device_identity=device["device_identity"], driver_identity=device["driver_identity"],
        hardware_digest=body_digest(kernel_hardware(evidence["host"], [device], "vulkan")),
        settings_digest=body_digest(kernel_settings(512, 2, [device])))
    _, result = score(m)
    assert result["status"] == "profiled_estimate"
    assert result["host_handoff_ms"] == pytest.approx(0.8 if distributed else 0.3)
    assert result["compute_ms"] == pytest.approx(2.65 if distributed else 1.15)
    value["gpu_logits_bytes"] = 4096
    assert score(m)[1]["status"] == "unmeasured"
    value["gpu_logits_bytes"] = None
    value["gpu_logits_download_ms"] = None
    assert score(m)[1]["status"] == "unmeasured"


@pytest.mark.parametrize("receipt_fault", [None, "unapproved", "duplicate"])
def test_preview_consumes_owned_context_without_profile_or_cache_mutation(measurements, monkeypatch, receipt_fault):
    from kllm.modules.primacpp.interactors import distributed_planning
    from kllm.modules.managed_network.entities import NodeIdentity
    m = choices(measurements)
    authority = MasterAuthority(cluster_binding_id=uuid4(), master_node_id="master", master_key_id="1" * 64,
        master_term=1, master_session_id=uuid4())
    operation = str(uuid4())
    manifest = ExecutionManifest(runtime_id=m.runtime["runtime_id"], run_id=str(uuid4()), plan_digest="c" * 64,
        total_layers=4, ranks=m.topology.ranks, assets=[Asset(name=m.model.name, kind="model", sha256=m.digest, size=m.model.stat().st_size)],
        settings=m.body.model_dump(), inventory_digest=inventory_identity(m.members, m.network), network_id=m.network,
        transfer_edges=network_edges(m.topology),
        segments=[Segment(start=0,end=2,node_id="a"), Segment(start=2,end=4,node_id="b")],
        backends={n:"cpu" for n in m.members}, participant_evidence={n:ctx["participant_evidence"] for n,ctx in m.contexts.items()})
    peers = {n: NodeIdentity(node_id=n,key_id=sha256(bytes(32)).hexdigest(),public_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",cluster_binding_id=authority.cluster_binding_id) for n in m.members}
    receipts=[]
    for (source,target), raw in m.links.items():
        if (source, target) not in {(e["source"], e["target"]) for e in manifest.transfer_edges}:
            continue
        scope=StreamScope(probe_id=uuid4(),operation_id=uuid4(),contract_digest=manifest.digest,network_id=uuid4(),
            authority=authority,source=peers[source],target=peers[target],source_revision=1,target_revision=1)
        scope=scope.model_copy(update={"operation_id":UUID(operation),"network_id":UUID(m.network)})
        receipts.append({**raw,"scope":scope.model_dump(mode="json"),"grant_id":scope.digest})
    if receipt_fault == "duplicate":
        receipts.append(deepcopy(receipts[0]))
    elif receipt_fault == "unapproved":
        scope = StreamScope(probe_id=uuid4(), operation_id=UUID(operation), contract_digest=manifest.digest,
            network_id=UUID(m.network), authority=authority, source=peers["b"], target=peers["a"],
            source_revision=1, target_revision=1)
        receipts.append({**m.links[("b", "a")], "scope": scope.model_dump(mode="json"), "grant_id": scope.digest})
    job={"job_id":operation,"phase":"prepared","expires_at":m.now+600,"command_owner":authority_owner(authority),
        "request_settings_digest":body_digest(m.body.model_dump()),"manifest":manifest.model_dump(),"links":receipts,
        "nodes":{n:{"profile":{"job_id":str(uuid4()),"profile":p}} for n,p in m.profiles.items()}}
    calls=[]
    def context(node):
        return {**m.contexts[node],"manifest_digest":manifest.digest}
    control=SimpleNamespace(lock=threading.RLock(),jobs={operation:job},
        service=SimpleNamespace(state=lambda:SimpleNamespace(role="master",node_id="master"),
            channels=SimpleNamespace(master_authority=lambda:authority)),
        profile_context=lambda *_:context("master"),
        _call=lambda node,op,action,value:(calls.append((node,op,action)),context(node))[1])
    monkeypatch.setattr(distributed_planning,"read_model_layout",lambda _:m.layout)
    before=deepcopy(job)
    selected,result=measured_candidates(control,m.feasible,m.body,m.runtime,m.model,m.digest)
    assert result["status"] == ("unmeasured" if receipt_fault else "profiled_estimate") and selected == m.feasible[0]
    assert {c[2] for c in calls}=={"prima_profile_context"}
    assert job==before
    job["expires_at"]=0
    assert measured_candidates(control,m.feasible,m.body,m.runtime,m.model,m.digest)[1]["status"]=="unmeasured"
