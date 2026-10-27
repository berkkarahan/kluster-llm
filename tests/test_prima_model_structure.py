"""Runtime structure protocol, real child ownership, and profiler manifest regression."""

import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from kllm.modules.primacpp.entities import ProfileRequest
from kllm.modules.primacpp.interactors import generation_profile as gp
from kllm.modules.primacpp.interactors import model_structure as ms


def runtime():
    return {"runtime_id": "a" * 64, "llama_server": sys.executable,
            "capabilities": {"features": [ms.FEATURE]}}


def child_script(tmp_path, monkeypatch, text):
    script = tmp_path / "native_fixture.py"
    script.write_text(text)
    popen = subprocess.Popen
    monkeypatch.setattr(ms.subprocess, "Popen", lambda args, **kw: popen([args[0], str(script), *args[1:]], **kw))


@pytest.mark.parametrize("report", [
    {"schema_version": 1, "architecture": "dense", "stored_blocks": 32, "execution_layers": 32},
    {"schema_version": 1, "architecture": "moe", "stored_blocks": 24, "execution_layers": 24},
    {"schema_version": 1, "architecture": "auxiliary", "stored_blocks": 65, "execution_layers": 64},
])
def test_real_inspection_child_reports_runtime_structure(tmp_path, monkeypatch, report):
    child_script(tmp_path, monkeypatch, f"print({(ms.MARKER + json.dumps(report))!r})")
    owned = []
    result = ms.inspect_model(runtime(), tmp_path / "model", tmp_path,
                              check=lambda: None, track=owned.append, stop=lambda p: p.kill())
    assert result == report
    assert len(owned) == 1 and owned[0].poll() == 0


@pytest.mark.parametrize("text, message", [
    ("import sys; print('native hyperparameter failure'); sys.exit(7)", "exit=7.*native hyperparameter failure"),
    ("print('no report')", "exactly one"),
    (f"print({(ms.MARKER + '{}')!r})", "Invalid native"),
    (f"print({(ms.MARKER + '{}')!r}); print({(ms.MARKER + '{}')!r})", "exactly one"),
])
def test_inspection_errors_keep_native_diagnostics(tmp_path, monkeypatch, text, message):
    child_script(tmp_path, monkeypatch, text)
    with pytest.raises(ValueError, match=message):
        ms.inspect_model(runtime(), tmp_path / "model", tmp_path,
                         check=lambda: None, track=lambda p: None, stop=lambda p: p.kill())


def test_inspection_cancellation_reaps_owned_child(tmp_path, monkeypatch):
    child_script(tmp_path, monkeypatch, "import time; time.sleep(60)")
    owned = []
    def check():
        if owned:
            raise ValueError("cancelled")
    with pytest.raises(ValueError, match="cancelled"):
        ms.inspect_model(runtime(), tmp_path / "model", tmp_path,
                         check=check, track=owned.append, stop=lambda p: p.kill())
    assert owned[0].poll() is not None


def test_old_runtime_requires_upgrade_before_spawn(tmp_path):
    with pytest.raises(ValueError, match="current managed Prima"):
        ms.inspect_model({}, tmp_path / "model", tmp_path,
                         check=lambda: None, track=lambda p: pytest.fail("spawned"), stop=lambda p: None)


@pytest.mark.parametrize("expected,gpu,error", [(63, 32, "approved preparation"), (64, 65, "exceeds")])
def test_profile_refuses_incompatible_ownership_before_launch(tmp_path, monkeypatch, expected, gpu, error):
    monkeypatch.setattr(gp, "inspect_model", lambda *a, **kw: {"execution_layers": 64, "stored_blocks": 65})
    req = ProfileRequest(runtime_id="a" * 64, model="model", gpu_layers=gpu, request_id=str(uuid4()))
    with pytest.raises(ValueError, match=error):
        gp.profile_generation(runtime(), tmp_path / "model", req, [{}], tmp_path, expected_layers=expected,
                              check=lambda: None, track=lambda p: pytest.fail("spawned"), stop=lambda p: None)


def test_profile_manifest_uses_executable_count_without_family_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(gp, "inspect_model", lambda *a, **kw: {
        "architecture": "any_future_architecture", "execution_layers": 64, "stored_blocks": 65})
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    seen = []
    def capture(manifest, caps):
        seen.append(manifest)
        raise RuntimeError("captured")
    monkeypatch.setattr(gp, "require_execution_capabilities", capture)
    req = ProfileRequest(runtime_id="a" * 64, model="model", gpu_layers=64, request_id=str(uuid4()))
    with pytest.raises(RuntimeError, match="captured"):
        gp.profile_generation(runtime(), model, req, [{}], tmp_path, expected_layers=64,
                              check=lambda: None, track=lambda p: None, stop=lambda p: None)
    assert seen[0].total_layers == seen[0].ranks[0].layers[0].end == seen[0].segments[0].end == 64


@pytest.mark.parametrize("failure", ["inspection", "readiness"])
def test_failed_native_job_never_publishes_measurements(tmp_path, monkeypatch, failure):
    from kllm.modules.primacpp.interactors import preparation as prep
    from kllm.modules.runtimes.interactors.registry import RuntimeRegistry
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    if failure == "inspection":
        child_script(tmp_path, monkeypatch, "import sys; print('Unsupported architecture'); sys.exit(3)")
        message = "Unsupported architecture"
    else:
        report = ms.MARKER + json.dumps({"schema_version": 1, "architecture": "dense", "stored_blocks": 4, "execution_layers": 4})
        child_script(tmp_path, monkeypatch,
            f"import sys,time\nif '--inspect-model' in sys.argv:\n print({report!r})\nelse:\n time.sleep(.2)\n print('native load failure')\n sys.exit(3)\n")
        monkeypatch.setattr(gp, "require_execution_capabilities", lambda *a: None)
        monkeypatch.setattr(gp, "device_arguments", lambda *a, **kw: [])
        message = "native load failure"
    device = {"name": "Vulkan0", "device_identity": "1" * 32, "driver_identity": "driver", "physical_index": 0}
    monkeypatch.setattr(prep, "discover_vulkan_devices", lambda _: [device])
    monkeypatch.setattr(prep, "select_vulkan_devices", lambda *a: [device])
    state = SimpleNamespace(node_id="node", process_pid=None, members={"node": {"backend": "vulkan"}})
    current = {**runtime(), "engine": "primacpp", "backend": "vulkan"}
    jobs = prep.ProfileJobs(tmp_path / "jobs", lambda _: current, lambda _: model, lambda: state, RuntimeRegistry._kill)
    try:
        job = jobs.start(ProfileRequest(runtime_id="a" * 64, model="model", mode="generation",
                                        gpu_layers=4, request_id=str(uuid4())))
        deadline = time.monotonic() + 10
        while job["phase"] not in prep.TERMINAL:
            assert time.monotonic() < deadline
            time.sleep(0.01)
            job = jobs.job(job["job_id"])
        assert job["phase"] == "failed"
        assert message in job["error"]
        assert "exit=3" in job["error"]
        assert message in jobs.logs(job["job_id"])
        assert ("Pass 1/2 (CPU)" if failure == "readiness" else "Model inspection (vulkan)") in job["error"]
        assert not list(jobs.cache.root.glob("*.json"))
    finally:
        jobs.close()
    assert not jobs.processes


@pytest.mark.parametrize("cache,flash", [("f16", "off"), ("q8_0", "on")])
def test_both_generation_passes_use_rank_device_contract(tmp_path, monkeypatch, cache, flash):
    from contextlib import nullcontext
    from kllm.modules.primacpp.interactors import launch
    from kllm.modules.primacpp.interactors.execution import ExecutionManifest
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    monkeypatch.setattr(gp, "inspect_model", lambda *a, **kw: {"stored_blocks": 65, "execution_layers": 64})
    monkeypatch.setattr(gp, "require_execution_capabilities", lambda *a: None)
    monkeypatch.setattr(launch, "help_flags", lambda _: {"--device", "--strict-device-kv"})
    monkeypatch.setattr(gp, "verify_vulkan_execution", lambda *a, **kw: {"verified": True})
    commands = []
    class Process:
        pid = 123
        code = None
        def poll(self): return self.code
        def wait(self, **kw): return self.code
    def popen(args, **kw):
        commands.append(args)
        manifest = ExecutionManifest.model_validate_json(
            Path(args[args.index("--execution-manifest") + 1]).read_text())
        report = {"runtime_id": manifest.runtime_id, "manifest_digest": manifest.digest,
                  "rank": 0, "ngl": manifest.ranks[0].ngl, "manifest_mode": True}
        kw["stdout"].write("KLLM_PRIMA_RANK_CONFIGURED=" + json.dumps(report) + "\n")
        kw["stdout"].flush()
        return Process()
    monkeypatch.setattr(gp.subprocess, "Popen", popen)
    monkeypatch.setattr(gp.psutil, "Process", lambda _: SimpleNamespace(
        memory_info=lambda: SimpleNamespace(rss=100), cpu_times=lambda: SimpleNamespace(user=0, system=0)))
    calls = []
    class Client:
        def get(self, path): return SimpleNamespace(status_code=200)
        def stream(self, *a, **kw):
            calls.append(kw)
            return nullcontext(SimpleNamespace(status_code=200, iter_lines=lambda: iter([
                'data:' + json.dumps({"stop": True, "timings": {"prompt_n": 21, "predicted_n": 16,
                                                             "prompt_ms": 10, "predicted_per_token_ms": 2}})])))
    monkeypatch.setattr(gp.httpx, "Client", lambda **kw: nullcontext(Client()))
    current = runtime()
    current["capabilities"]["features"] += ["explicit_devices_v1", "strict_device_kv_v1"]
    req = ProfileRequest(runtime_id="a" * 64, model="model", gpu_layers=64, output_gpu=True,
        cache_type_k=cache, cache_type_v=cache, flash_attention=flash, request_id=str(uuid4()))
    device = {"name": "Vulkan2", "physical_index": 2, "device_identity": "1" * 32, "driver_identity": "driver"}
    result = gp.profile_generation(current, model, req, [device], tmp_path, expected_layers=64,
        check=lambda: None, track=lambda p: None, stop=lambda p: setattr(p, "code", 0))
    assert commands[0][commands[0].index("--device") + 1] == "none"
    assert commands[1][commands[1].index("--device") + 1] == "Vulkan0"
    assert "--strict-device-kv" not in commands[0] and "--strict-device-kv" in commands[1]
    assert result["layers"] == 64 and len(calls) == 8
    assert len(result["cpu"]["samples"]) == len(result["vulkan"]["samples"]) == 3
    for args in commands:
        assert args[args.index("--cache-type-k") + 1] == args[args.index("--cache-type-v") + 1] == cache
        assert ("--flash-attn" in args) == (flash == "on")
    assert result["settings"]["cache_type_k"] == cache and result["settings"]["flash_attention"] == flash


def graph_evidence():
    from test_prima_device_selection import execution_fixture
    rank, manifest, devices, reports = execution_fixture([1.0], layers=2)
    # Arbitrary fused/recurrent matrix names; no family whitelist in the verifier.
    weights = [f"blk.{i}.{name}.weight" for i in range(2) for name in ("fused_projection", "recurrent_out", "ffn_down")]
    for report in reports:
        report.update(graph_contract_version=1, graph_count=2, completed_graphs=2,
                      graph_weights=list(weights), executed_weights={"Vulkan0": list(weights)})
    return rank, manifest, devices, reports


def test_native_graph_evidence_handles_fused_and_recurrent_layers():
    from kllm.modules.primacpp.interactors.evidence import verify_vulkan_execution
    rank, manifest, devices, reports = graph_evidence()
    assert verify_vulkan_execution(reports, manifest, rank, devices, require_graph_contract=True)["status"] == "passed"


@pytest.mark.parametrize("fault", ["partial_graph", "missing_work", "cpu_fallback", "missing_layer", "missing_phase", "missing_contract"])
def test_native_graph_evidence_does_not_relax_execution_proof(fault):
    from kllm.modules.primacpp.interactors.evidence import verify_vulkan_execution
    rank, manifest, devices, reports = graph_evidence()
    if fault == "partial_graph":
        reports[0]["completed_graphs"] = 1
    if fault == "missing_work":
        reports[0]["executed_weights"]["Vulkan0"].pop()
    if fault == "cpu_fallback":
        reports[0]["executed_weights"]["CPU"] = [reports[0]["executed_weights"]["Vulkan0"].pop()]
    if fault == "missing_layer":
        reports[0]["graph_weights"] = [w for w in reports[0]["graph_weights"] if w.startswith("blk.0.")]
    if fault == "missing_phase":
        reports.pop()
    if fault == "missing_contract":
        reports[0].pop("graph_contract_version")
    with pytest.raises(ValueError):
        verify_vulkan_execution(reports, manifest, rank, devices, require_graph_contract=True)
