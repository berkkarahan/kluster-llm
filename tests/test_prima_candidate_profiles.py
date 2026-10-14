"""Candidate link approval and real signed four-agent preparation boundaries.

Model/kernel rates and adapters are fixtures; signatures, TCP, files and owned
profile subprocesses are real. Physical distributed acceptance is deferred.
"""

import asyncio
import os
import platform
import sys
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from kllm.modules.inference.entities import RunBody
from kllm.modules.inference.interactors.worker_rpc import WorkerRpcService
from kllm.modules.primacpp.interactors.control import PrimaControl
from kllm.modules.primacpp.interactors.execution import Asset, ExecutionManifest, ProfileEdge, Segment, profile_pairs
from kllm.modules.primacpp.interactors.planner import candidates, candidate_profile_edges
from kllm.modules.primacpp.interactors.placement import network_edges, topology_from_plan
from kllm.modules.runtimes.interactors.runtime_control import RuntimeControlService


def capacity_plan(body, members):
    workers = sorted(node for node, member in members.items() if member["role"] == "worker")
    return {"fit_status": "fit", "estimated_layers": 6, "cpu_layers": 0,
        "device_effective": "cpu",
        "resolved_launch_settings": {"parallel_slots": 1, "prima_kv_sequences": 2},
        "device_allocations": [
            {"node_id": node, "layer_start": i * 6 // len(workers),
             "layer_end": (i + 1) * 6 // len(workers),
             "layers": (i + 1) * 6 // len(workers) - i * 6 // len(workers), "memory_kind": "ram"}
            for i, node in enumerate(workers)]}


@pytest.fixture
def approved():
    members = {node: {"role": "master" if i == 0 else "worker", "rpc_bind": f"192.168.234.{i+1}", "backend": "cpu"}
        for i, node in enumerate(("master", "a", "b", "c"))}
    body = RunBody(model="fixture.gguf", network_id=str(uuid4()))
    feasible = candidates(body, members, capacity_plan)
    topology = topology_from_plan(feasible[0][0], members, "a" * 64)
    manifest = ExecutionManifest(runtime_id="a" * 64, run_id=str(uuid4()), plan_digest="b" * 64,
        total_layers=6, ranks=topology.ranks, settings={}, inventory_digest="c" * 64,
        network_id=body.network_id, assets=[Asset(name=body.model, kind="model", sha256="d" * 64, size=1)],
        segments=[Segment(start=i * 2, end=(i+1) * 2, node_id=node) for i, node in enumerate(("a", "b", "c"))],
        transfer_edges=network_edges(topology), backends={node: "cpu" for node in members})
    return body, members, feasible, manifest


def test_candidate_search_keeps_distinct_bounded_worker_subsets():
    members = {node: {"role": "master" if node == "master" else "worker"}
               for node in ("master", "a", "b", "c")}
    body = RunBody(model="fixture.gguf", network_id=str(uuid4()))
    def same_capacity(_body, _members):
        return {"fit_status": "fit", "estimated_layers": 6, "cpu_layers": 6,
                "device_effective": "cpu", "device_allocations": []}
    feasible = candidates(body, members, same_capacity)
    participants = [frozenset(inventory) for _, inventory in feasible]
    assert len(participants) == 7 and len(set(participants)) == 7
    assert all("master" in nodes and len(nodes) >= 2 for nodes in participants)
    assert len(candidates(body, members, same_capacity, limit=4)) == 4
    required = body.model_copy(update={"required_nodes": ["b"]})
    assert all("b" in inventory for _, inventory in candidates(required, members, same_capacity))
    priority = body.model_copy(update={"node_priority": ["a", "b", "c"]})
    assert len(candidates(priority, members, same_capacity)) == 1


def test_candidate_removal_approves_only_missing_prepared_edge(approved):
    _, _, feasible, manifest = approved
    before = manifest.model_dump()
    extra = candidate_profile_edges(manifest, feasible)
    assert [(edge.source, edge.target) for edge in extra] == [("a", "c")]
    expanded = ExecutionManifest.model_validate({**before, "profile_edges": [edge.model_dump() for edge in extra]})
    assert expanded.transfer_edges == manifest.transfer_edges
    assert expanded.ranks == manifest.ranks and expanded.segments == manifest.segments
    assert expanded.digest != manifest.digest
    assert ("a", "c") in profile_pairs(expanded)
    assert manifest.model_dump() == before
    assert "profile_edges" not in before  # Legacy canonical contracts retain their digest.
    assert ExecutionManifest.model_validate(before).digest == manifest.digest


def test_segmented_candidate_edges_require_exact_planned_graph_visits(approved):
    _, members, _, manifest = approved
    selected = {node: member for node, member in members.items() if node != "b"}
    plan = {"fit_status": "fit", "estimated_layers": 6, "cpu_layers": 1,
        "device_effective": "cpu", "device_allocations": [
            {"node_id": node, "layer_start": start, "layer_end": end,
             "layers": end - start, "memory_kind": "ram"}
            for node, start, end in (("a", 1, 3), ("c", 3, 5), ("master", 5, 6))],
        "resolved_launch_settings": {"prima_graphs": True}}
    feasible = [(plan, selected)]
    assert candidate_profile_edges(manifest, feasible, segmented=True) == []
    plan["prima_graph_counts"] = {"master": 3, "a": 1, "c": 1}
    assert candidate_profile_edges(manifest, feasible, segmented=True) == []
    plan["prima_graph_counts"]["master"] = 4
    assert [(edge.source, edge.target) for edge in candidate_profile_edges(
        manifest, feasible, segmented=True)] == [("a", "c")]


@pytest.mark.parametrize("constraint", ["priority", "required"])
def test_candidate_edges_respect_user_node_constraints(approved, constraint):
    body, members, _, manifest = approved
    body = body.model_copy(update={"node_priority": ["a", "b", "c"]} if constraint == "priority" else {"required_nodes": ["b"]})
    assert candidate_profile_edges(manifest, candidates(body, members, capacity_plan)) == []


@pytest.mark.parametrize("fault", ["unknown", "self", "duplicate", "local", "extra_field"])
def test_manifest_refuses_widened_or_ambiguous_profile_edges(approved, fault):
    manifest = approved[3].model_dump()
    edge = {"source": "a", "target": "c"}
    if fault == "unknown":
        edge["target"] = "foreign"
    elif fault == "self":
        edge["target"] = "a"
    elif fault == "local":
        manifest["network_id"] = None
    elif fault == "extra_field":
        edge["port"] = 50052
    manifest["profile_edges"] = [edge] * (2 if fault == "duplicate" else 1)
    with pytest.raises(ValueError):
        ExecutionManifest.model_validate(manifest)


def test_unprepared_or_changed_candidate_does_not_get_measurement_grant(approved):
    body, members, _, manifest = approved
    changed = {**members, "c": {**members["c"], "rpc_bind": "192.168.234.99"}}
    assert candidate_profile_edges(manifest, [(capacity_plan(body, changed), changed)]) == []
    foreign = {**members, "d": {"role": "worker", "rpc_bind": "192.168.234.5"}}
    assert candidate_profile_edges(manifest, [(capacity_plan(body, foreign), foreign)]) == []


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("link_version", [1, 2])
async def test_four_agent_preparation_covers_removed_worker_link_or_refuses_old_agent(tmp_path, monkeypatch, link_version):
    from managed_network_lab import NativeAgentLab
    from test_prima_kernel_profile import report
    from kllm.modules.primacpp.interactors import distributed_planning, launch

    lab = NativeAgentLab(tmp_path, monkeypatch, names=("master", "worker-a", "worker-b", "worker-c"))
    model = tmp_path / "fixture.gguf"
    model.write_bytes(b"complete controlled model bytes")
    payload = report("cpu")
    payload.update(threads=2, total_layers=6, layers=[{**payload["layers"][i % 2], "layer": i} for i in range(6)])
    script = tmp_path / "profile.py"
    script.write_text("import json,os\nassert os.environ['KLLM_PRIMA_PROFILE_BACKEND']=='cpu'\n"
        + "print('KLLM_PRIMA_PROFILE='+json.dumps(" + repr(payload) + "))\n")
    tool = tmp_path / ("profile.cmd" if os.name == "nt" else "profile")
    tool.write_text(f'@"{sys.executable}" "{script}" %*\n' if os.name == "nt"
        else f'#!{sys.executable}\nexec(open({str(script)!r}).read())\n')
    if os.name != "nt":
        tool.chmod(0o700)
    runtime = {"engine": "primacpp", "runtime_id": "a" * 64, "backend": "cpu", "platform": platform.system(),
        "commit": "1" * 40, "recipe_digest": "2" * 64, "build_id": "fixture", "artifacts": {"fixture": "3" * 64},
        "profile_tool": str(tool), "llama_server": str(tmp_path / "fixture-server"),
        "capabilities": {"features": ["execution_manifest_v1", "typed_kernel_profile_v2",
            "logical_context_sequences_v1", "embedding_role_v1", "compact_swa_v1", "decode_metadata_v2"],
            "backends": ["cpu"]}}
    controls = []
    try:
        for node in lab.nodes:
            for member in node.state.members.values():
                member.update(backend="cpu", prima_control_version=1, prima_inventory_version=1,
                    prima_asset_lease_version=1, prima_profile_version=2, prima_link_profile_version=link_version,
                    prima_profile_context_version=1, prima_resources_version=1)
            registry = SimpleNamespace(root=node.home / "runtimes", lock=threading.RLock(), capture=lambda _: runtime,
                _kill=lambda child: child.terminate(), close=lambda: None)
            control = PrimaControl(node.service, registry, resolve_model=lambda _: model, execute_plan=capacity_plan)
            controls.append(control)
            node.service.runtimes = RuntimeControlService(node.service, registry, lambda: "cpu")
            node.service.runtimes._prima = control
            node.service.worker_rpc = WorkerRpcService(lambda node=node: node.state, lambda: None, registry=registry)
        monkeypatch.setattr(launch, "help_flags", lambda _: {"--parallel", "--batch-size", "--ubatch-size",
            "--threads-batch", "--cache-type-k", "--cache-type-v", "--alias", "--seed", "--temp",
            "--top-p", "--top-k", "--min-p", "--presence-penalty", "--repeat-penalty", "--frequency-penalty", "--predict"})
        await lab.start()
        started = await lab.setup()
        await lab.nodes[0].service.wait_operation(started["operation_id"])
        for node in lab.nodes[1:]:
            child = next(child for child in lab.children if child.pid == node.state.process_pid)
            child.terminate()
            child.wait(5)
            node.state.process_pid = None
        body = RunBody(model=model.name, network_id=started["network_id"], context=512, threads=2,
            cache_type_k="f16", cache_type_v="f16", flash_attention="off", checkpoints=0, cache_ram=0,
            parallel_slots=1, threads_batch=2)
        control = controls[0]
        preview = await asyncio.to_thread(control.plan, runtime["runtime_id"], body)
        job = await asyncio.to_thread(control.start_preparation, runtime["runtime_id"], body,
            preview["plan_digest"], str(uuid4()), include_profile=True)
        await lab.wait(lambda: control.job(job["job_id"])["phase"] in {"prepared", "failed"}, seconds=25)
        prepared = control.job(job["job_id"])
        if link_version == 1:
            assert prepared["phase"] == "failed" and "upgrade" in prepared["error"]
            assert all(not worker.jobs and not worker.profiles.jobs for worker in controls[1:])
            return
        assert prepared["phase"] == "prepared", prepared.get("error")
        manifest = ExecutionManifest.model_validate(prepared["manifest"])
        assert manifest.profile_edges == [ProfileEdge(source="worker-a", target="worker-c")]
        pairs = {(r["source_node_id"], r["target_node_id"]) for r in prepared["links"]}
        assert pairs == profile_pairs(manifest) and ("worker-a", "worker-c") in pairs
        assert all(r["scope"]["contract_digest"] == manifest.digest for r in prepared["links"])
        assert lab.nodes[1].service.store.peer("worker-c") is None
        assert all(not worker.profiles.processes and not worker.resources.busy for worker in controls)
        monkeypatch.setattr(distributed_planning, "read_model_layout", lambda _: {
            "architecture": "llama", "confidence": "metadata", "metadata": {"llama.embedding_length": 1024},
            "layers": [4096]*6, "input_bytes": 4096, "output_bytes": 4096})
        measured = await asyncio.to_thread(control.plan, runtime["runtime_id"], body)
        # Prepared profiles and links cover every bounded worker subset, so the
        # full feasible enumeration (seven for four members) receives estimates.
        assert measured["candidate_count"] == 7
        assert measured["profiling"]["status"] == "profiled_estimate", measured["profiling"]
        assert len(measured["profiling"]["candidate_estimates"]) == 7
        # Dropping only the alternative edge must restore capacity-first selection.
        control.jobs[job["job_id"]]["links"] = [r for r in prepared["links"]
            if (r["source_node_id"], r["target_node_id"]) != ("worker-a", "worker-c")]
        incomplete = await asyncio.to_thread(control.plan, runtime["runtime_id"], body)
        assert incomplete["profiling"]["status"] == "unmeasured"
        assert incomplete["plan_digest"] == preview["plan_digest"]
    finally:
        for control in controls:
            await asyncio.to_thread(control.close)
            await asyncio.to_thread(control.profiles.close)
        await lab.close()
