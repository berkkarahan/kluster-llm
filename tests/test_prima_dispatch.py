"""Signed distributed dispatch with real fixture children and HTTP readiness.

Model/native execution and adapter mutation are controlled here. Physical cluster
generation, GPU placement and performance acceptance are recorded separately.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
import platform
from types import SimpleNamespace
from uuid import uuid4

import psutil
import pytest

from kllm.modules.inference.entities import RunBody
from kllm.modules.inference.interactors.worker_rpc import WorkerRpcService
from kllm.modules.inference.router_http import InferenceHandlers
from kllm.modules.primacpp.interactors.control import PrimaControl
from kllm.modules.primacpp.interactors.lifecycle import RankSupervisor
from kllm.modules.runtimes.interactors.runtime_control import RuntimeControlService
from kllm.modules.runtimes.interactors.selection import use_runtime


CHILD = r"""
import hashlib,http.server,json,sys,time
from pathlib import Path
manifest=json.loads(Path(sys.argv[1]).read_text());rank=manifest['ranks'][int(sys.argv[2])]
assert hashlib.sha256(Path(sys.argv[3]).read_bytes()).hexdigest()==next(a['sha256'] for a in manifest['assets'] if a['kind']=='model')
report={'schema_version':1,'runtime_id':manifest['runtime_id'],'manifest_digest':sys.argv[4],
 'rank':rank['rank'],'ngl':rank['ngl'],'layers':sum(w['end']-w['start'] for w in rank['layers']),
 'manifest_mode':True,'phase':'ready','readiness_stage':'server_request_loop' if rank['rank']==0 else 'worker_decode_loop'}
print('KLLM_PRIMA_RANK_READY='+json.dumps(report),flush=True)
if rank['rank']==0:
 class Handler(http.server.BaseHTTPRequestHandler):
  def do_GET(self):
   self.send_response(200 if Path(sys.argv[5]).exists() else 503);self.end_headers();self.wfile.write(b'{}')
  def log_message(self,*args):pass
 http.server.HTTPServer(('127.0.0.1',manifest['settings']['assessment_port']),Handler).serve_forever()
else:time.sleep(60)
"""


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("include_profile", [False, True])
async def test_prepared_three_rank_run_uses_signed_start_barrier_stop_and_admission(tmp_path, monkeypatch, include_profile):
    from managed_network_lab import NativeAgentLab, available_port
    from kllm.modules.inference import router_http
    from kllm.modules.primacpp.interactors import distributed_runs, launch, recipe
    from prima_port_fixture import FixturePorts

    lab = NativeAgentLab(tmp_path, monkeypatch)
    model = tmp_path / "prima-model.gguf"
    model.write_bytes(b"controlled complete model asset")
    ready = tmp_path / "master-http-ready"
    ready.touch()
    runtime = {"engine": "primacpp", "runtime_id": "a" * 64, "backend": "cpu", "platform": platform.system(),
        "commit": "1" * 40, "recipe_digest": "2" * 64, "build_id": "fixture",
        "artifacts": {"fixture-server.exe": "3" * 64},
        "llama_server": str(tmp_path / "fixture-server.exe"), "llama_cli": str(tmp_path / "fixture-cli.exe"),
        "capabilities": {"features": ["execution_manifest_v1", "rank_readiness_v1",
            "logical_context_sequences_v1", "embedding_role_v1", "compact_swa_v1", "decode_metadata_v2"],
            "backends": ["cpu"]}}
    if include_profile:
        from test_prima_kernel_profile import report
        payload = report("cpu")
        payload["threads"] = 2
        payload["total_layers"] = 8
        payload["layers"] = [{**payload["layers"][i % 2], "layer": i} for i in range(8)]
        script = tmp_path / "profile.py"
        script.write_text("import json,os\nassert os.environ['KLLM_PRIMA_PROFILE_BACKEND']=='cpu'\n"
                          + "print('KLLM_PRIMA_PROFILE='+json.dumps(" + repr(payload) + "))\n")
        if os.name == "nt":
            tool = tmp_path / "profile.cmd"
            tool.write_text(f'@"{sys.executable}" "{script}" %*\n')
        else:
            tool = tmp_path / "profile"
            tool.write_text(f'#!{sys.executable}\nexec(open({str(script)!r}).read())\n')
            tool.chmod(0o700)
        runtime["profile_tool"] = str(tool)
        runtime["capabilities"]["features"].append("typed_kernel_profile_v2")
    controls, children, running, actions = [], [], [], []
    port_events, gateways = [], []
    def plan(_body, members):
        workers = sorted(n for n, m in members.items() if m["role"] == "worker")
        return {"fit_status": "fit", "estimated_layers": len(workers) * 4, "cpu_layers": 0,
            "device_effective": "cpu",
            "resolved_launch_settings": {"parallel_slots": 1, "prima_kv_sequences": 2},
            "device_allocations": [
                {"node_id": node, "layer_start": i * 4, "layer_end": (i + 1) * 4,
                 "layers": 4, "memory_kind": "ram"} for i, node in enumerate(workers)]}
    def spawn(_runtime, rank, cached, manifest_path, log, settings):
        manifest = json.loads(manifest_path.read_text())
        digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        actions.append((rank.node_id, "spawn", manifest["run_id"]))
        assert len(port_events) >= 3 and all(g.rules for g in gateways)
        with log.open("w") as output:
            child = subprocess.Popen([sys.executable, "-c", CHILD, str(manifest_path), str(rank.rank),
                str(cached), digest, str(ready)], stdout=output, stderr=subprocess.STDOUT)
        children.append(child)
        lab.children.append(child)
        return {"pid": child.pid, "start_token": str(psutil.Process(child.pid).create_time())}
    def stop(pid):
        child = next(c for c in children if c.pid == pid)
        child.terminate()
        child.wait(5)
        return True
    try:
        for node in lab.nodes:
            node.state.infer_port = available_port("127.0.0.1")
            node.state.active_config = None
            node.state.apply_error = None
            for member in node.state.members.values():
                member.update(backend="cpu", prima_control_version=1, prima_inventory_version=1,
                    prima_asset_lease_version=1, prima_ports_version=2, prima_profile_version=2, prima_link_profile_version=1,
                    prima_profile_context_version=1, prima_resources_version=1)
            gateway = FixturePorts(lab.nodes[0].service.identity.public_key, node.name, port_events)
            gateway.install(node.service.executor.platform, monkeypatch)
            gateways.append(gateway)
            registry = SimpleNamespace(root=node.home / "runtimes", lock=threading.RLock(),
                capture=lambda _: runtime, running=lambda value: running.append(value),
                claim_running=lambda value: running.append(value),
                clear_running=lambda **_: running.append(None),
                target=lambda: None, data={"running": None}, close=lambda: None, _kill=lambda _: None)
            control = PrimaControl(node.service, registry, resolve_model=lambda _: model, execute_plan=plan)
            control.rank = RankSupervisor(control.root / "rank", node.name, capture=registry.capture,
                spawn=spawn, stop=stop, alive=lambda pid: any(c.pid == pid and c.poll() is None for c in children),
                identity=lambda pid: str(psutil.Process(pid).create_time()))
            controls.append(control)
            node.service.runtimes = RuntimeControlService(node.service, registry, lambda: "cpu")
            node.service.runtimes._prima = control
            node.service.worker_rpc = WorkerRpcService(lambda node=node: node.state, lambda: None,
                registry=registry)
        await lab.start()
        started = await lab.setup()
        master = lab.nodes[0]
        await master.service.wait_operation(started["operation_id"])
        assert master.service.network_view(started["network_id"])["observed_state"] == "ready"
        # End only the lab's explicit legacy fixture children before preparation.
        for node in lab.nodes[1:]:
            child = next(c for c in lab.children if c.pid == node.state.process_pid)
            child.terminate()
            child.wait(5)
            node.state.process_pid = None
        body = RunBody(model=model.name, network_id=started["network_id"], context=512, threads=2,
            cache_type_k="f16", cache_type_v="f16", flash_attention="off", checkpoints=0, cache_ram=0)
        control = controls[0]
        preview = await asyncio.to_thread(control.plan, runtime["runtime_id"], body)
        # The source-controlled production gate still blocks these fixture runtimes.
        with use_runtime(runtime):
            with pytest.raises(ValueError, match="qualif"):
                await asyncio.to_thread(control.runs.launch, body, model, preview,
                    await asyncio.to_thread(master.service.prima_admission.members, body.network_id))
        assert not children
        # Explicit fixture composition grants no published hardware qualification.
        monkeypatch.setattr(distributed_runs, "require_qualified", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(recipe, "require_qualified", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(router_http, "require_qualified", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(router_http, "effective_configuration", lambda *_: {})
        monkeypatch.setattr("kllm.modules.primacpp.interactors.devices.discover_vulkan_devices", lambda _: [])
        monkeypatch.setattr(launch, "help_flags", lambda _: {"--parallel", "--batch-size", "--ubatch-size",
            "--threads-batch", "--cache-type-k", "--cache-type-v", "--alias", "--seed", "--temp",
            "--top-p", "--top-k", "--min-p", "--presence-penalty", "--repeat-penalty", "--frequency-penalty",
            "--predict"})
        job = await asyncio.to_thread(control.start_preparation, runtime["runtime_id"], body,
            preview["plan_digest"], str(uuid4()), include_profile=include_profile)
        await lab.wait(lambda: control.job(job["job_id"])["phase"] in {"prepared", "failed"})
        prepared = control.job(job["job_id"])
        assert prepared["phase"] == "prepared", prepared.get("error") or prepared
        assert prepared["manifest"]["settings"]["assessment_port"] == master.state.infer_port
        assert len(prepared["nodes"]) == 3
        if include_profile:
            for node, value in prepared["nodes"].items():
                assert value["profile"]["control_owner"]["operation_id"] == job["job_id"]
                assert value["profile"]["profile"]["node_id"] == node
                assert value["profile"]["profile"]["kernel_profile_version"] == 2
            assert len(list(control.profiles.cache.root.glob("*.json"))) == 3
            worker = lab.nodes[1].name
            profile_job = prepared["nodes"][worker]["profile"]["job_id"]
            for action in ("prima_status", "prima_cancel", "prima_profile_context"):
                with pytest.raises(Exception, match="signed preparation"):
                    await asyncio.to_thread(control._call, worker, str(uuid4()), action,
                                            {"profile_job_id": profile_job})
            retained = await asyncio.to_thread(control._call, worker, job["job_id"], "prima_status",
                                               {"profile_job_id": profile_job})
            assert retained["phase"] == "prepared"
            context = await asyncio.to_thread(control._call, worker, job["job_id"], "prima_profile_context",
                                              {"profile_job_id": profile_job})
            assert context["manifest_digest"] == prepared["manifest_digest"]
            assert context["participant_evidence"]["node_id"] == worker
            assert context["storage_identity"] == retained["profile"]["storage_identity"]
            pairs = {(e["source"], e["target"]) for e in prepared["manifest"]["transfer_edges"]}
            assert {(e["source_node_id"], e["target_node_id"]) for e in prepared["links"]} == pairs
            assert (lab.nodes[1].name, lab.nodes[2].name) in pairs
            assert all(e["bandwidth_bytes_s"] > 0 and len(e["sample_seconds"]) == 3 for e in prepared["links"])
            assert lab.nodes[1].service.store.peer(lab.nodes[2].name) is None
            receipt = next(e for e in prepared["links"] if e["target_node_id"] == worker)
            link_request = {"preparation_job_id": prepared["nodes"][worker]["job_id"],
                            "scope": receipt["scope"], "mode": "receive"}
            with pytest.raises(Exception, match="signed preparation"):
                await asyncio.to_thread(control._call, worker, str(uuid4()), "prima_link_profile", link_request)
            worker_control = controls[1]
            approved = worker_control.jobs[link_request["preparation_job_id"]]
            expires = approved["expires_at"]
            approved["expires_at"] = 0
            with pytest.raises(Exception, match="current complete signed preparation"):
                await asyncio.to_thread(control._call, worker, job["job_id"], "prima_link_profile", link_request)
            approved["expires_at"] = expires
        assert set(prepared["manifest"]["participant_evidence"]) == {n.name for n in lab.nodes}
        assert all(e["platform"] == platform.system() and e["kind"] == "observed_hardware"
                   for e in prepared["manifest"]["participant_evidence"].values())
        def finish(path, plan, launched, persist, remember):
            master.state.process_pid, master.state.process_role = launched["pid"], "master"
            persist()
            remember()
        handler = InferenceHandlers(SimpleNamespace(state=master.state,
            runtime_context=lambda: use_runtime(runtime), resolve_settings=None, resolve_model=lambda _: model,
            preflight=None, ensure_master_member=lambda: None, merge_extra=lambda _: None,
            validate_vision=lambda _: None, execute_plan=plan,
            network_run=lambda _: None, network_members=master.service.prima_admission.members,
            prima_plan=lambda b, p, m: control.plan(runtime["runtime_id"], b, members=m, model=p),
            prima_run=control.runs.launch, prima_stop=control.runs.stop, finish_master_launch=finish,
            persist_last_settings=lambda _: None, remember=lambda _: None))
        # Every node must reserve resources before any rank can spawn. A busy
        # worker preserves its foreign claim while partial listeners drain.
        foreign = controls[1].resources.acquire(("primacpp", "foreign-profile", "other-authority", "other-manifest"), "profile")
        try:
            with pytest.raises(Exception, match="resources.*busy"):
                await asyncio.to_thread(handler.run, body)
            assert not children and not any(g.rules for g in gateways)
            assert controls[1].resources.busy
            assert not controls[0].resources.busy and not controls[2].resources.busy
            assert master.service.store.runs(body.network_id)[-1].state == "released"
        finally:
            foreign.release()
        port_events.clear()
        result = await asyncio.to_thread(handler.run, body)
        assert result["engine"] == "primacpp" and result["run_id"] == prepared["manifest"]["run_id"]
        assert control.runs.manifest.run_id == result["run_id"]
        assert [node for node, _, _ in actions] == [lab.nodes[1].name, lab.nodes[2].name, "master"]
        assert len(children) == 3 and all(c.poll() is None for c in children)
        assert master.service.store.runs(body.network_id)[-1].state == "active"
        assert [event[:2] for event in port_events] == [(n.name, "admit") for n in lab.nodes]
        assert all(c.ports.owner["phase"] == "admitted" for c in controls)
        assert all(g.renewals >= 1 for g in gateways)
        # The startup log limit must not become a serving-time trace-volume limit.
        log = control.rank.root / result["run_id"] / "rank.log"
        with log.open("ab") as output:
            output.write(b"trace\n" * (4 * 1024 * 1024 // 6 + 1))
        for _ in range(100):
            await asyncio.to_thread(control.runs.monitor_once)
            if all(g.renewals >= 2 for g in gateways):
                break
            await asyncio.sleep(0.02)  # Nonblocking monitor can yield to the background owner.
        assert control.runs.distributed.coordinator.phase == "serving"
        assert all(g.renewals >= 2 for g in gateways)
        worker = lab.nodes[1]
        prepared_network = worker.service.executor.prepared[body.network_id]
        prepared_network.ownership_conflict = True
        refused = await master.service.channels.sessions[worker.name].request_runtime(
            result["run_id"], "prima_renew", body={})
        assert refused["status"] == "failed" and "private assignment" in refused["message"]
        prepared_network.ownership_conflict = False
        for node in lab.nodes[1:]:
            refused = await master.service.channels.sessions[node.name].request_runtime(
                uuid4(), "start", body={"runtime_id": runtime["runtime_id"]})
            assert refused["status"] == "failed" and "Prima rank" in refused["message"]
        await asyncio.to_thread(control.runs.stop)
        assert all(c.poll() is not None for c in children)
        assert all(c.rank.owner is None for c in controls)
        assert master.service.store.runs(body.network_id)[-1].state == "released"
        assert all(c.ports.owner is None for c in controls) and not any(g.rules for g in gateways)
        assert master.state.process_pid is master.state.active_config is None
        assert control.runs.manifest is None

        # Losing an admission reply cannot start any rank or forget partial rules.
        gateways[1].fail_apply = gateways[1].fail_release = True
        with pytest.raises(Exception, match="cleanup pending"):
            await asyncio.to_thread(handler.run, body)
        assert len(children) == 3
        pending = master.service.store.runs(body.network_id)[-1]
        assert pending.state == "releasing" and pending.prima_port_nodes == [lab.nodes[1].name]
        assert controls[1].ports.owner["phase"] == "cleanup_pending" and gateways[1].rules
        assert controls[0].ports.owner is None and not gateways[0].rules
        gateways[1].fail_apply = gateways[1].fail_release = False
        await asyncio.to_thread(control.runs.stop)
        assert not any(g.rules for g in gateways) and all(c.ports.owner is None for c in controls)
        assert master.service.store.runs(body.network_id)[-1].state == "released"

        # Older wire capability is refused before any mutation or reservation.
        member = master.state.members[lab.nodes[1].name]
        for field, old, current in (("prima_ports_version", 1, 2), ("prima_resources_version", 0, 1)):
            member[field] = old
            before = len(master.service.store.runs(body.network_id))
            with pytest.raises(Exception, match="upgrade the agent"):
                await asyncio.to_thread(handler.run, body)
            assert len(master.service.store.runs(body.network_id)) == before and len(children) == 3
            member[field] = current

        # Preparation cannot be reused under changed inputs, an expired approval
        # or another authority, even while its cached bytes are still present.
        original = control.jobs[job["job_id"]].copy()
        for change in ({"command_owner": {"sender": "other", "authority_digest": "e" * 64}},
                {"expires_at": 0}, {"request_settings_digest": "f" * 64}, {"plan_digest": "f" * 64}):
            control.jobs[job["job_id"]] = {**original, **change}
            with pytest.raises(Exception, match="Prepare the exact Prima plan"):
                await asyncio.to_thread(handler.run, body)
            assert len(children) == 3
        control.jobs[job["job_id"]] = original

        # Fresh signed observations fence changed node-local builds before any spawn.
        runtime["artifacts"]["fixture-server.exe"] = "4" * 64
        with pytest.raises(Exception, match="hardware/build changed"):
            await asyncio.to_thread(handler.run, body)
        assert len(children) == 3
        runtime["artifacts"]["fixture-server.exe"] = "3" * 64

        # Stop while validation is blocked must not be cleared by coordinator.start.
        validating, proceed = threading.Event(), threading.Event()
        prepared_lookup = control.runs.distributed._prepared
        def delayed_lookup(*args):
            validating.set()
            assert proceed.wait(5)
            return prepared_lookup(*args)
        monkeypatch.setattr(control.runs.distributed, "_prepared", delayed_lookup)
        launched = asyncio.create_task(asyncio.to_thread(handler.run, body))
        await lab.wait(validating.is_set)
        stopping = asyncio.create_task(asyncio.to_thread(control.runs.stop))
        await lab.wait(control.runs.cancelled.is_set)
        proceed.set()
        with pytest.raises(Exception, match="cancel"):
            await launched
        await stopping
        assert len(children) == 3 and not control.runs.busy
        monkeypatch.setattr(control.runs.distributed, "_prepared", prepared_lookup)

        # Native acknowledgements alone cannot publish serving without HTTP health.
        ready.unlink()
        launched = asyncio.create_task(asyncio.to_thread(handler.run, body))
        await lab.wait(lambda: len(children) == 6)
        await lab.wait(lambda: (control.rank.root / prepared["manifest"]["run_id"] / "rank.log").stat().st_size > 0)
        assert control.runs.distributed.coordinator.phase == "starting"
        assert master.service.store.runs(body.network_id)[-1].state == "reserved"
        await asyncio.to_thread(control.runs.stop)
        with pytest.raises(Exception, match="cancel"):
            await launched
        assert all(c.poll() is not None for c in children)
        assert master.service.store.runs(body.network_id)[-1].state == "released"

        # A dead worker causes one complete owned restart after the grace sample.
        ready.touch()
        await asyncio.to_thread(handler.run, body)
        old_pid = master.state.process_pid
        dead = next(c for c in children if c.pid == controls[1].rank.owner["pid"])
        dead.terminate()
        dead.wait(5)
        await asyncio.to_thread(control.runs.monitor_once)
        assert control.runs.distributed.coordinator.phase == "degraded"
        await asyncio.to_thread(control.runs.monitor_once)
        assert control.runs.distributed.coordinator.phase == "serving", master.state.apply_error
        assert master.state.process_pid != old_pid
        assert master.state.active_config["process_pid"] == master.state.process_pid
        assert control.runs.distributed.coordinator.retries == 1
        assert master.service.store.runs(body.network_id)[-1].state == "active"
        await asyncio.to_thread(control.runs.stop)
        assert all(c.poll() is not None for c in children)
        assert all(r.state == "released" for r in master.service.store.runs(body.network_id))
    finally:
        try:
            if controls and controls[0].runs.busy:
                await asyncio.to_thread(controls[0].runs.stop)
        finally:
            await lab.close()
