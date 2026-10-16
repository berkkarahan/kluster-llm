"""Isolated admission, filesystem, progress, and process-environment boundaries."""

from concurrent.futures import Future
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from kllm.modules.inference.entities import PlanBody
from kllm.modules.managed_network.entities import MasterAuthority
from kllm.modules.primacpp.interactors import control as control_module
from kllm.modules.primacpp.interactors.control import PrimaControl
from kllm.modules.primacpp.interactors.preparation_metrics import (
    LOCAL_CHUNK_ENV, diagnostics_scope, digest_file, local_chunk_bytes, measure,
)
from kllm.modules.primacpp.interactors.staging import ModelStager
from test_prima_lifecycle import BASELINE_FEATURES


@pytest.fixture
def control(tmp_path):
    model = tmp_path / "model with spaces.gguf"
    model.write_bytes(b"deterministic bytes" * 8192)
    authority = MasterAuthority(cluster_binding_id=uuid4(), master_node_id="master",
        master_key_id="1" * 64, master_term=1, master_session_id=uuid4())
    state = SimpleNamespace(role="master", node_id="master", process_pid=None,
        members={"master": {"role": "master", "host": "127.0.0.1", "backend": "cpu"}})
    service = SimpleNamespace(state=lambda: state,
        channels=SimpleNamespace(sessions={}, master_authority=lambda: authority))
    runtime = {"engine": "primacpp", "runtime_id": "a" * 64, "backend": "cpu",
        "capabilities": {"features": list(BASELINE_FEATURES), "backends": []}}
    registry = SimpleNamespace(root=tmp_path / "runtimes", capture=lambda _: runtime,
        _kill=lambda _: None, lock=threading.RLock())
    plan = {"estimated_layers": 1, "device_effective": "cpu", "cpu_layers": 1,
        "device_allocations": [], "fit_status": "fit",
        "resolved_launch_settings": {"parallel_slots": 1, "swa_full": False,
            "prima_kv_sequences": 2, "checkpoints": 0, "cache_ram": 0}}
    value = PrimaControl(service, registry, resolve_model=lambda _: model, execute_plan=lambda *_: plan)
    yield value
    value.close()


def wait_for(predicate):
    until = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < until
        time.sleep(.01)


@pytest.mark.parametrize("value,expected", [(None, 4), ("1", 1), ("4", 4), ("8", 8), ("64", 64)])
def test_chunk_environment(value, expected, monkeypatch):
    monkeypatch.delenv(LOCAL_CHUNK_ENV, raising=False)
    if value is not None:
        monkeypatch.setenv(LOCAL_CHUNK_ENV, value)
    assert local_chunk_bytes() == expected * 1024 * 1024


@pytest.mark.parametrize("value", ["", " ", "abc", "4.0", "0", "-1", "65", "1e1", "+4", " 4", "４", "9" * 10000])
def test_invalid_environment_refuses_before_work_or_reservation(control, value, monkeypatch):
    monkeypatch.setenv(LOCAL_CHUNK_ENV, value)
    def forbidden(*args, **kwargs):
        pytest.fail("invalid tuning reached expensive work")
    monkeypatch.setattr(control, "resolve_model", forbidden)
    monkeypatch.setattr(control, "_call", forbidden)
    monkeypatch.setattr(control.pool, "submit", forbidden)
    with pytest.raises(ValueError, match=LOCAL_CHUNK_ENV + ".*1 through 64"):
        control.start_preparation("a" * 64, PlanBody(model="model"), "b" * 64, str(uuid4()))
    assert not control.jobs and not control.resources.busy and not control.root.exists()


def test_admission_freezes_buffer_without_changing_plan_or_manifest(control, monkeypatch):
    body = PlanBody(model="model")
    plan = control.plan("a" * 64, body)
    entered, proceed = threading.Event(), threading.Event()
    original = control._prepare_owned
    observed = []
    original_chunks = control._file_chunks
    def chunks(path, offset, chunk_bytes, check):
        observed.append(chunk_bytes)
        yield from original_chunks(path, offset, chunk_bytes, check)
    def held(*args):
        entered.set()
        assert proceed.wait(5)
        return original(*args)
    monkeypatch.setattr(control, "_prepare_owned", held)
    monkeypatch.setattr(control, "_file_chunks", chunks)
    monkeypatch.setenv(LOCAL_CHUNK_ENV, "1")
    request_id = str(uuid4())
    try:
        job = control.start_preparation("a" * 64, body, plan["plan_digest"], request_id)
        assert entered.wait(2)
        monkeypatch.setenv(LOCAL_CHUNK_ENV, "8")
        assert control.plan("a" * 64, body)["plan_digest"] == plan["plan_digest"]
        proceed.set()
        wait_for(lambda: job["job_id"] not in control.preparation_reservations)
        result = control.job(job["job_id"])
        assert result["phase"] == "prepared", result
        assert observed and set(observed) == {1024 * 1024}
        assert result["local_chunk_bytes"] == 1024 * 1024
        assert "local_chunk_bytes" not in json.dumps(result["manifest"])
        assert "diagnostics" not in json.dumps(result["manifest"])
        assert result["progress"]["rate_bps"] is None
        stages = {s["stage"] for s in result["diagnostics"]["stages"]}
        assert {"source_hash", "asset_enumeration", "local_copy", "final_verify", "checkpoint"} <= stages
        # Idempotent status retrieval does not reconfigure an admitted job.
        monkeypatch.setenv(LOCAL_CHUNK_ENV, "invalid")
        assert control.start_preparation("a" * 64, body, plan["plan_digest"], request_id)["local_chunk_bytes"] == 1024 * 1024
    finally:
        proceed.set()


def test_bursts_and_windows_have_bounded_publications_and_correct_stage_rate(control, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(control_module.time, "monotonic", lambda: now[0])
    control.jobs["progress"] = {"phase": "preparing"}
    labels = []
    def report(done, **kwargs):
        control._update_job_progress("progress", phase="staging", label=lambda: labels.append(done) or "copy",
            bytes_done=done, bytes_total=1000000, rate_kind="local_copy", **kwargs)
    # 1000 callbacks/s over several windows, plus a burst at one instant.
    for tick in range(24001):
        now[0] = tick / 1000
        report(tick)
    for _ in range(5000):
        report(24000)
    result = control.job("progress")
    assert result["progress_bytes"]["done"] == 24000
    assert result["progress"]["rate_bps"] == 1000
    assert result["progress"]["eta_seconds"] >= 0
    assert len(labels) <= 97
    assert result["progress_statistics"]["callbacks"] == 29001
    assert len(control._job_samples["progress"].samples) <= 40
    # An idle stream drops old samples and eventually reports zero, never stale ETA.
    now[0] = 40
    report(24000)
    now[0] = 40.25
    report(24000)
    assert control.job("progress")["progress"]["rate_bps"] == 0
    assert control.job("progress")["progress"]["eta_seconds"] is None
    for changed in ({"stage": "final_verify"}, {"scope": ("other-rank",)}, {"scope": ("other-asset",)}):
        report(1, **changed)
        assert control.job("progress")["progress"]["rate_bps"] is None
    report(0)
    assert control.job("progress")["progress"]["rate_bps"] is None


def test_cancel_owns_terminal_progress_until_actual_cleanup(control):
    future = Future()
    control.jobs["progress"] = {"phase": "preparing"}
    control.preparation_futures["progress"] = future
    lease = control.resources.acquire(("primacpp", "progress", "owner", "manifest"), "prepare")
    control.preparation_reservations["progress"] = lease
    control._update_job_progress("progress", phase="staging", label="copy", bytes_done=10, bytes_total=20)
    result = control.cancel("progress")
    snapshot = deepcopy(result["progress"])
    assert result["phase"] == "cancelled" and control.resources.busy
    assert snapshot["bytes_done"] == 10 and snapshot["bytes_total"] == 20
    control._update_job_progress("progress", phase="prepared", label="late completion", fraction=1)
    control._update_job_progress("progress", phase="staging", label="late copy", bytes_done=20, bytes_total=20)
    assert control.job("progress")["progress"] == snapshot
    assert "progress" not in control._job_samples
    future.set_result(None)
    control._settle_preparation("progress")
    assert not control.resources.busy


def test_verification_has_own_rate_and_cannot_publish_complete_early(control, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(control_module.time, "monotonic", lambda: now[0])
    control.jobs["hash"] = {"phase": "preparing"}
    def report(done):
        control._update_job_progress("hash", phase="staging", stage="final_verify", label="Verifying",
            fraction=1, bytes_done=1000, bytes_total=1000,
            rate_done=done, rate_total=1000, rate_kind="hash")
    report(0)
    assert control.job("hash")["progress"]["eta_seconds"] is None
    now[0] = .5
    report(100)
    progress = control.job("hash")["progress"]
    assert progress["bytes_done"] == 1000 and progress["stage_bytes_done"] == 100
    assert progress["rate_bps"] == 200 and progress["eta_seconds"] == 4.5
    assert progress["fraction"] < 1 and progress["rate_kind"] == "hash"


@pytest.mark.parametrize("chunk_mib", [1, 4, 8, 64])
def test_real_staging_resume_cache_and_phase_bytes(tmp_path, chunk_mib):
    data = bytes(range(251)) * 17000
    path = tmp_path / "source with spaces"
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    stager = ModelStager(tmp_path / "cache")
    stager.root.mkdir()
    final, partial = stager._paths(digest)
    partial.write_bytes(data[:113])
    job, stages = {}, []
    def progress(stage, done, total):
        stages.append(stage) if not stages or stages[-1] != stage else None
        if stage == "final_verify":
            assert not final.exists()
    with diagnostics_scope(job, threading.RLock(), lambda: None, "local"):
        result = stager.prepare(digest, len(data),
            lambda offset: PrimaControl._file_chunks(path, offset, chunk_mib * 1024 * 1024),
            check=lambda: None, stage_progress=progress)
    assert result.read_bytes() == data
    records = {r["stage"]: r for r in job["diagnostics"]["stages"]}
    assert records["resume"]["counters"]["resume_bytes"] == 113
    assert records["local_copy"]["counters"]["copied_bytes"] == len(data) - 113
    assert records["final_verify"]["counters"]["hashed_bytes"] == len(data)
    assert records["publication"]["counters"]["publications"] == 1
    assert stages == ["resume", "local_copy", "final_verify", "publication"]
    warm = {}
    with diagnostics_scope(warm, threading.RLock(), lambda: None, "local"):
        stager.prepare(digest, len(data), lambda _: pytest.fail("cache hit copied bytes"), check=lambda: None)
    records = {r["stage"]: r for r in warm["diagnostics"]["stages"]}
    assert records["cache_hit"]["counters"]["cache_hit_bytes"] == len(data)
    assert records["cache_verify"]["counters"]["hashed_bytes"] == len(data)
    assert "local_copy" not in records


@pytest.mark.parametrize("cancel_stage", ["local_copy", "final_verify", "cache_verify"])
def test_cancellation_checks_during_copy_and_full_hash(tmp_path, cancel_stage):
    data = b"x" * (3 * 1024 * 1024 + 17)
    digest = hashlib.sha256(data).hexdigest()
    path = tmp_path / "source"
    path.write_bytes(data)
    stager = ModelStager(tmp_path / "cache")
    stager.root.mkdir()
    final, partial = stager._paths(digest)
    if cancel_stage == "cache_verify":
        final.write_bytes(data)
    cancelled = False
    def progress(stage, done, total):
        nonlocal cancelled
        if stage == cancel_stage and done >= 1024 * 1024:
            cancelled = True
    def check():
        if cancelled:
            raise ValueError("cancelled fixture")
    with pytest.raises(ValueError, match="cancelled fixture"):
        stager.prepare(digest, len(data), lambda offset: PrimaControl._file_chunks(path, offset, 1024 * 1024, check),
            check=check, stage_progress=progress)
    if cancel_stage != "cache_verify":
        assert not final.exists() and partial.exists()
    assert stager.prepare(digest, len(data), lambda offset: PrimaControl._file_chunks(path, offset),
        check=lambda: None).read_bytes() == data


def test_diagnostic_histories_are_bounded_and_sampled(tmp_path):
    job = {}
    with diagnostics_scope(job, threading.RLock(), lambda: None, "node"):
        for _ in range(1024):
            with measure("hot", sample=64) as counts:
                counts["written_bytes"] = 24
        for index in range(1000):
            with measure("asset", asset=str(index)):
                pass
    stages = job["diagnostics"]["stages"]
    assert len(stages) == 128
    hot = stages[0]
    assert hot["calls"] == 1024 and hot["timed_calls"] == 16
    assert sum(hot["latency_buckets"]) == 16
    assert hot["counters"]["written_bytes"] == 1024 * 24
    assert job["diagnostics"]["omitted_stage_calls"] == 873


def test_owned_source_hash_checks_cancellation(tmp_path):
    path = tmp_path / "source"
    path.write_bytes(b"x" * (2 * 1024 * 1024))
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ValueError("authority changed")
    job = {}
    with diagnostics_scope(job, threading.RLock(), check, "node"):
        with pytest.raises(ValueError, match="authority changed"):
            digest_file(path)
    assert job["diagnostics"]["stages"][0]["counters"]["hashed_bytes"] == 1024 * 1024


def test_cli_new_agent_environment_inheritance_and_existing_agent_attachment(tmp_path, monkeypatch):
    from kllm.modules.cluster.interactors import agent_runtime
    probe = tmp_path / "child-environment.json"
    children = []
    real_popen = subprocess.Popen
    def popen(command, **kwargs):
        assert "env" not in kwargs  # Actual CLI gateway uses normal OS inheritance.
        if "kllm.agent" in command:
            script = ("import os,json,time; from pathlib import Path; "
                f"Path({str(probe)!r}).write_text(json.dumps(os.environ.get({LOCAL_CHUNK_ENV!r}))); "
                "time.sleep(30)")
        else:
            script = "import sys; sys.stdin.buffer.read()"
        child = real_popen([sys.executable, "-c", script], **kwargs)
        children.append(child)
        return child
    monkeypatch.setenv(LOCAL_CHUNK_ENV, "8")
    monkeypatch.setattr(agent_runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(agent_runtime, "ensure_home", lambda: None)
    monkeypatch.setattr(agent_runtime, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(agent_runtime, "_agent_healthy", lambda _: None)
    monkeypatch.setattr(agent_runtime, "_stop_local", lambda *a, **k: None)
    monkeypatch.setattr(agent_runtime, "_validate_saved_state", lambda: None)
    monkeypatch.setattr(agent_runtime.runtime, "register_agent", lambda *a: None)
    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url):
            return SimpleNamespace(status_code=200)
    monkeypatch.setattr(agent_runtime.httpx, "Client", Client)
    try:
        agent_runtime._ensure_agent(19999)
        wait_for(probe.exists)
        assert json.loads(probe.read_text()) == "8"
        monkeypatch.setenv(LOCAL_CHUNK_ENV, "1")
        monkeypatch.setattr(agent_runtime, "_agent_healthy", lambda _: {
            "control_protocol": agent_runtime.kllm_config.CONTROL_PROTOCOL_VERSION, "pid": children[1].pid})
        monkeypatch.setattr(agent_runtime.runtime, "load", lambda: SimpleNamespace(agent_pid=children[1].pid, control_port=19999))
        agent_runtime._ensure_agent(19999)
        assert len(children) == 2 and json.loads(probe.read_text()) == "8"
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(5)


@pytest.mark.parametrize("cancel", [False, True])
def test_layout_copy_fallback_is_verified_and_cancellable(tmp_path, monkeypatch, cancel):
    from kllm.modules.primacpp.interactors.control import _layout_asset
    from kllm.modules.primacpp.interactors.execution import Asset
    data = b"layout" * 600000
    source = tmp_path / "cache"
    source.write_bytes(data)
    directory = tmp_path / "layout"
    directory.mkdir()
    asset = Asset(name="model.gguf", kind="model", size=len(data), sha256=hashlib.sha256(data).hexdigest())
    def no_link(*_):
        raise OSError("controlled cross-volume fallback")
    monkeypatch.setattr(Path, "hardlink_to", no_link)
    checks = 0
    def check():
        nonlocal checks
        checks += 1
        if cancel and checks >= 3:
            raise ValueError("cancelled layout")
    job = {}
    with diagnostics_scope(job, threading.RLock(), check, "node"):
        if cancel:
            with pytest.raises(ValueError, match="cancelled layout"):
                _layout_asset(asset, source, directory)
            assert not list(directory.iterdir())
        else:
            assert _layout_asset(asset, source, directory).read_bytes() == data
    assert source.read_bytes() == data
    records = {r["stage"]: r for r in job["diagnostics"]["stages"]}
    assert records["layout_copy"]["counters"]["copied_bytes"] == len(data)
    if not cancel:
        assert records["layout_verify"]["counters"]["hashed_bytes"] == len(data)


def test_real_profile_process_reports_rss_separately_from_progress(tmp_path, monkeypatch):
    from kllm.modules.primacpp.interactors import preparation
    from kllm.modules.primacpp.entities import ProfileRequest
    model = tmp_path / "model"
    model.write_bytes(b"test model")
    release = tmp_path / "release"
    metrics = {"schema_version": 1, "cpu_q4k_gflops": 1.0,
               "layer_q4k_flops": 1000000.0, "memory_available_gib": 1.0}
    script = ("import time; from pathlib import Path\n"
              "data = bytearray(2 * 1024 * 1024)\n"
              f"while not Path({str(release)!r}).exists(): time.sleep(.01)\n"
              f"print('KLLM_PRIMA_PROFILE=' + {json.dumps(metrics)!r}, flush=True)\n")
    real_popen = subprocess.Popen
    def popen(command, **kwargs):
        assert command[command.index("-t") + 1] == "8"
        return real_popen([sys.executable, "-c", script], **kwargs)
    monkeypatch.setattr(preparation.subprocess, "Popen", popen)
    state = SimpleNamespace(process_pid=None, node_id="local", members={"local": {"backend": "cpu"}})
    runtime = {"engine": "primacpp", "profile_tool": "controlled", "backend": "cpu"}
    def kill(process):
        process.terminate()
        process.wait(5)
    jobs = preparation.ProfileJobs(tmp_path / "profiles", lambda _: runtime, lambda _: model, lambda: state, kill)
    try:
        request = ProfileRequest(runtime_id="a" * 64, model="model", threads=8, request_id=str(uuid4()))
        job = jobs.start(request)
        identity = job["job_id"]
        wait_for(lambda: jobs.job(identity).get("process_observation", {}).get("rss_bytes", 0) > 0)
        observed = jobs.job(identity)
        assert observed["progress"]["bytes_done"] == observed["progress"]["bytes_total"] == 0
        assert observed["progress"]["fraction"] == .30
        release.touch()
        wait_for(lambda: identity not in jobs.futures)
        result = jobs.job(identity)
        assert result["phase"] == "prepared", result
        assert result["profile"]["cpu_layer_ms"] == 1.0
        stages = {r["stage"]: r for r in result["diagnostics"]["stages"]}
        assert stages["profile_preflight_hash"]["counters"]["hashed_bytes"] == model.stat().st_size
        assert stages["native_kernel_profiler"]["elapsed_seconds"] > 0
        assert stages["profile_cache_publication"]["counters"]["publications"] == 1
        jobs._update_progress(identity, phase="profiling", label="late callback", fraction=.5)
        assert jobs.job(identity)["progress"]["phase"] == "prepared"
    finally:
        release.touch()
        jobs.close()
