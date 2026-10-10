"""Prima identities, exact placement, preparation and qualification boundaries."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from kllm.modules.primacpp.entities import DeviceProfile, ProfileRequest, PrimaTopology
from kllm.modules.primacpp.interactors.benchmark import Measurement, Workload, evaluate
from kllm.modules.primacpp.interactors.build import managed_prima_build, parse_device_report
from kllm.modules.primacpp.interactors.placement import (
    topology_from_plan,
    layer_windows,
    rank_launch,
    network_edges,
)
from kllm.modules.primacpp.interactors.preparation import (
    ProfileJobs,
    profiling_backend,
    gpu_profile_gflops,
)
from kllm.modules.primacpp.interactors.profiles import (
    ProfileCache,
    profile_key,
    choose_profiled_plan,
)
from kllm.modules.primacpp.interactors.recipe import recipe, recipe_digest, require_qualified
from kllm.modules.primacpp.interactors.staging import ModelStager
from kllm.modules.runtimes.entities import SourceRequest, RuntimeVersion, runtime_id
from kllm.modules.runtimes.interactors.source import resolve
from kllm.modules.runtimes.interactors.registry import RuntimeRegistry, file_digest
from kllm.modules.runtimes.interactors.runtime_control import RuntimeControlService
from kllm.modules.runtimes.interactors.selection import (
    selected_binary,
    selected_environment,
    use_runtime,
)
from kllm.modules.runtimes.router_http import build_router
from kllm.shared.state import ClusterState
from kllm.shared import native


def prima_version():
    definition = recipe()
    return resolve(
        SourceRequest(
            engine="primacpp",
            repository=definition["source_repository"],
            ref=definition["source_commit"],
            ref_type="commit",
        )
    )


def test_llama_identity_and_signed_payload_are_unchanged():
    repo, commit = "https://github.com/ggml-org/llama.cpp.git", "a" * 40
    identity = hashlib.sha256((repo + "\n" + commit).encode()).hexdigest()
    value = RuntimeVersion(repository=repo, commit=commit, ref="stable", runtime_id=identity)
    assert value.engine == "llamacpp"
    assert runtime_id(repo, commit) == identity
    assert "engine" not in value.model_dump()
    assert "recipe_digest" not in value.model_dump()
    assert RuntimeVersion.model_validate(value.model_dump()) == value


def test_prima_identity_includes_recipe_and_rejects_unpinned_source():
    value = prima_version()
    assert value.recipe_digest == recipe_digest()
    assert value.runtime_id != runtime_id(value.repository, value.commit)
    assert value.runtime_id != runtime_id(value.repository, value.commit, "primacpp", "f" * 64)
    assert value.model_dump()["engine"] == "primacpp"
    with pytest.raises(ValueError, match="pinned"):
        resolve(SourceRequest(engine="primacpp", ref="a" * 40, ref_type="commit"))


def test_prima_upstream_provenance_cannot_be_selected_or_built(tmp_path):
    definition = recipe()
    source = SourceRequest(
        engine="primacpp", repository=definition["upstream_repository"],
        ref=definition["upstream_commit"], ref_type="commit",
    )
    with pytest.raises(ValueError, match="pinned source"):
        resolve(source)
    destination = tmp_path / "build"
    with pytest.raises(ValueError, match="pinned source"):
        managed_prima_build(
            None, source.repository, source.ref, destination, "vulkan", recipe_digest()
        )
    assert not destination.exists()


def test_prima_stale_recipe_fails_before_preparation(tmp_path):
    definition = recipe()
    destination = tmp_path / "build"
    with pytest.raises(ValueError, match="recipe changed"):
        managed_prima_build(
            None, definition["source_repository"], definition["source_commit"],
            destination, "vulkan", "0" * 64,
        )
    assert not destination.exists()


def test_prima_stale_saved_build_is_retained_but_requires_rebuild(tmp_path):
    definition = recipe()
    old_digest = "0" * 64
    old_version = RuntimeVersion(
        engine="primacpp", repository=definition["upstream_repository"],
        ref=definition["upstream_commit"], ref_type="commit",
        commit=definition["upstream_commit"], recipe_digest=old_digest,
        runtime_id=runtime_id(
            definition["upstream_repository"], definition["upstream_commit"],
            "primacpp", old_digest,
        ),
    )
    registry = RuntimeRegistry(tmp_path, lambda: tmp_path)
    try:
        registry.add(old_version)
        registry.data["versions"][old_version.runtime_id]["build"] = {
            **old_version.model_dump(), "transport": "prima-ring-tcp",
            "artifact_dir": str(registry.root / "previous-build"),
        }
        registry._save()
    finally:
        registry.close()
    registry = RuntimeRegistry(tmp_path, lambda: tmp_path)
    try:
        assert registry.snapshot()["versions"][0]["version"] == old_version.model_dump()
        with pytest.raises(ValueError, match="resolve and rebuild"):
            registry.capture(old_version.runtime_id)
        assert registry.data["versions"][old_version.runtime_id]["build"] is not None
    finally:
        registry.close()


def test_vulkan_is_an_independent_mandatory_qualification_gate(monkeypatch):
    gates = {key: True for key in recipe()["qualification"]}
    gates["vulkan_gpu_inference"] = False
    monkeypatch.setattr(
        "kllm.modules.primacpp.interactors.recipe.recipe", lambda: {"qualification": gates}
    )
    with pytest.raises(ValueError, match="vulkan_gpu_inference"):
        require_qualified({"engine": "primacpp"})


def test_prima_device_report_requires_consistent_backend_and_ring_contract():
    install = SimpleNamespace(parse_list_devices_backends=lambda output: ["metal", "vulkan"])
    report = {
        "schema_version": 1,
        "ring_protocol_version": 1,
        "backends": ["Metal", "Vulkan"],
        "devices": [{"backend": "Vulkan", "name": "Vulkan0", "description": "Apple M1 Pro"}],
        "features": ["layer_window", "tensor_split", "ngl", "ring_tcp", "execution_manifest_v1"],
    }
    output = "Metal0: Apple GPU\nVulkan0: Apple M1 Pro\nKLLM_PRIMA_CAPABILITIES=" + json.dumps(report)
    result = parse_device_report(install, output)
    assert result["backends"] == ["metal", "vulkan"]
    assert result["devices"][0]["name"] == "Vulkan0"

    mismatched = output.replace('"Vulkan"', '"CUDA"', 1)
    with pytest.raises(ValueError, match="disagree"):
        parse_device_report(install, mismatched)

    report["ring_protocol_version"] = 2
    with pytest.raises(ValueError, match="execution contract"):
        parse_device_report(install, "KLLM_PRIMA_CAPABILITIES=" + json.dumps(report))


@pytest.mark.parametrize("value", [None, 0.0, -1.0, float("nan"), float("inf"), True])
def test_vulkan_profile_cannot_substitute_metal_cuda_or_cpu(value):
    metrics = {
        "metal_q4k_gflops": 100.0,
        "cuda_q4k_gflops": 200.0,
        "cpu_q4k_gflops": 50.0,
        "vulkan_q4k_gflops": value,
    }
    with pytest.raises(ValueError, match="vulkan GPU profiling is unavailable"):
        gpu_profile_gflops(metrics, "vulkan")


def test_selected_vulkan_profile_does_not_use_faster_metal_measurement():
    backend = profiling_backend({"backend": "metal+vulkan"}, {"actual_backend": "vulkan"})
    assert backend == "vulkan"
    assert (
        gpu_profile_gflops({"metal_q4k_gflops": 100.0, "vulkan_q4k_gflops": 10.0}, backend) == 10.0
    )
    assert gpu_profile_gflops({"metal_q4k_gflops": 100.0}, "cpu") is None
    with pytest.raises(ValueError, match="Select an available"):
        profiling_backend({"backend": "metal+vulkan"}, {"backend": "unknown"})


def plan():
    return {
        "estimated_layers": 8,
        "split_mode": "layer",
        "fit_status": "fits",
        "cpu_layers": 0,
        "device_allocations": [
            {
                "node_id": "worker",
                "layer_start": 0,
                "layer_end": 5,
                "layers": 5,
                "device": "RPC[10.10.10.3:50052]/0",
                "rpc_index": 0,
                "local": False,
                "memory_kind": "gpu",
            },
            {
                "node_id": "master",
                "layer_start": 5,
                "layer_end": 8,
                "layers": 3,
                "device": "Metal0",
                "local": True,
                "memory_kind": "gpu",
            },
        ],
    }


def members():
    return {
        "master": {"role": "master", "rpc_bind": "10.10.10.2"},
        "worker": {"role": "worker", "rpc_bind": "10.10.10.3", "devices": [{"device": "CUDA0"}]},
        "unused": {"role": "worker", "rpc_bind": "10.10.10.4"},
    }


def topology():
    return topology_from_plan(plan(), members(), "a" * 64)


def test_topology_preserves_roles_allocations_and_local_device_names():
    value = topology()
    assert [r.node_id for r in value.ranks] == ["master", "worker"]
    assert [r.role for r in value.ranks] == ["master", "worker"]
    assert layer_windows(value) == [3, 5]
    launch = rank_launch(value, "worker")
    assert launch["ngl"] == 5 and launch["devices"] == ["CUDA0"]
    assert launch["extra_args"][launch["extra_args"].index("-ngl") + 1] == "5"
    assert launch["env"] == {"KLLM_PRIMA_BIND": "10.10.10.3"}
    assert launch["tensor_split"] == "1"
    assert launch["extra_args"][launch["extra_args"].index("-ts") + 1] == "1"
    assert "3,5" in launch["extra_args"]
    assert {e["port"] for e in network_edges(value)} == {9000, 9001, 10001}


def test_cpu_worker_and_zero_layer_master_are_preserved():
    source = plan()
    source["device_allocations"] = [
        dict(source["device_allocations"][0], layer_end=8, layers=8, memory_kind="ram")
    ]
    value = topology_from_plan(source, members(), "a" * 64)
    assert layer_windows(value) == [0, 8]
    assert all(r.ngl == 0 for r in value.ranks)


def test_vulkan_rank_launch_pins_the_planned_device():
    inventory = members()
    inventory["worker"]["devices"] = [{"device": "Vulkan2"}]
    source = plan()
    source["device_allocations"][0]["device"] = "RPC[10.10.10.3:50052]/0"
    value = topology_from_plan(source, inventory, "a" * 64)
    launch = rank_launch(value, "worker")
    assert launch["env"]["GGML_VK_VISIBLE_DEVICES"] == "2"
    assert launch["ngl"] == 5


@pytest.mark.parametrize("fault", ["gap", "overlap", "public", "ring", "ports", "master"])
def test_topology_rejects_invalid_authority_network_and_layer_coverage(fault):
    value = topology().model_dump()
    if fault == "gap":
        value["ranks"][0]["layers"][0]["start"] = 6
    if fault == "overlap":
        value["ranks"][0]["layers"][0]["start"] = 4
    if fault == "public":
        value["ranks"][1]["address"] = "8.8.8.8"
    if fault == "ring":
        value["ranks"][1]["next_node_id"] = "worker"
    if fault == "ports":
        value["signal_port"] = value["data_port"]
    if fault == "master":
        value["master_node_id"] = "worker"
    with pytest.raises(ValueError):
        PrimaTopology.model_validate(value)


def test_master_cpu_prefix_and_gpu_suffix_are_not_silently_reordered():
    source = plan()
    source["cpu_layers"] = 2
    source["device_allocations"][0].update(layer_start=2, layers=3)
    value = topology_from_plan(source, members(), "a" * 64)
    with pytest.raises(ValueError, match="window order"):
        layer_windows(value)


@pytest.mark.parametrize("change", [{"split_mode": "tensor"}, {"expert_offloaded": True}])
def test_incompatible_placement_is_not_approximated(change):
    with pytest.raises(ValueError, match="compatibility"):
        topology_from_plan({**plan(), **change}, members(), "a" * 64)


def test_artifact_verification_and_selection_gate(tmp_path):
    version = prima_version()
    registry = RuntimeRegistry(tmp_path, lambda: tmp_path)
    try:
        registry.add(version)
        source = registry.snapshot()["engine_sources"][0]
        assert source["repository"] == version.repository
        assert source["ref"] == version.commit
        directory = registry.root / version.runtime_id / "build/bin"
        directory.mkdir(parents=True)
        for name in ("llama-server", "llama-cli", "profile-tool"):
            (directory / name).write_text(name)
        runtime_lib = directory / "lib" / "libvulkan.1.dylib"
        runtime_lib.parent.mkdir()
        runtime_lib.write_text("loader")
        manifest = {
            **version.model_dump(),
            "artifact_dir": str(directory),
            "build_id": "test",
            "transport": "prima-ring-tcp",
            "llama_server": str(directory / "llama-server"),
            "llama_cli": str(directory / "llama-cli"),
            "profile_tool": str(directory / "profile-tool"),
            "artifacts": {
                p.relative_to(directory).as_posix(): file_digest(p)
                for p in directory.rglob("*") if p.is_file()
            },
        }
        registry.data["versions"][version.runtime_id]["build"] = manifest
        assert registry.capture(version.runtime_id)["engine"] == "primacpp"
        with pytest.raises(ValueError, match="resolve and rebuild"):
            registry.verify({**manifest, "recipe_digest": "0" * 64})
        with pytest.raises(ValueError, match="not qualified"):
            registry.select(version.runtime_id)
        assert registry.snapshot()["versions"][0]["inference_supported"] is False
        (directory / "llama-cli").write_text("tampered")
        with pytest.raises(ValueError, match="artifacts changed"):
            registry.capture(version.runtime_id)
        (directory / "llama-cli").write_text("llama-cli")
        runtime_lib.write_text("tampered")
        with pytest.raises(ValueError, match="artifacts changed"):
            registry.capture(version.runtime_id)
    finally:
        registry.close()


def test_native_launch_gate_precedes_process_creation(monkeypatch):
    monkeypatch.setattr(
        native, "require_native", lambda: pytest.fail("Must not reach native spawn")
    )
    with use_runtime({"engine": "primacpp"}):
        with pytest.raises(ValueError, match="not qualified"):
            native.start_master()
        with pytest.raises(ValueError, match="ring workers"):
            selected_binary("rpc-server")
    require_qualified({})


def test_selected_runtime_environment_is_validated_and_available():
    runtime = {"engine": "primacpp", "runtime_env": {"VK_ICD_FILENAMES": "/runtime/icd.json"}}
    with use_runtime(runtime):
        assert selected_environment() == {"VK_ICD_FILENAMES": "/runtime/icd.json"}
    with use_runtime({"engine": "primacpp", "runtime_env": {"VK_ICD_FILENAMES": 3}}):
        with pytest.raises(ValueError, match="invalid process environment"):
            selected_environment()


def test_resume_staging_and_integrity(tmp_path):
    model = b"model" * 100
    digest = hashlib.sha256(model).hexdigest()
    stage = ModelStager(tmp_path, disk_free=lambda _: 1024**3)
    offsets = []

    def interrupted(offset):
        offsets.append(offset)
        yield model[:100]
        raise ValueError("connection lost")

    with pytest.raises(ValueError, match="connection lost"):
        stage.prepare(digest, len(model), interrupted, check=lambda: None)
    result = stage.prepare(
        digest,
        len(model),
        lambda offset: offsets.append(offset) or [model[offset:]],
        check=lambda: None,
    )
    assert offsets == [0, 100] and result.read_bytes() == model
    assert not result.with_suffix(".part").exists()
    with pytest.raises(ValueError, match="digest mismatch"):
        stage.prepare("f" * 64, 3, lambda offset: [b"bad"], check=lambda: None)
    with pytest.raises(ValueError, match="SHA-256"):
        stage.offset("../../escape")


def test_staging_checks_space_and_cancellation(tmp_path):
    stage = ModelStager(tmp_path, disk_free=lambda _: 1)
    with pytest.raises(ValueError, match="disk space"):
        stage.prepare("a" * 64, 10, lambda _: [], check=lambda: None)

    def cancelled():
        raise ValueError("authority expired")

    with pytest.raises(ValueError, match="authority expired"):
        stage.prepare("a" * 64, 10, lambda _: [], check=cancelled)


def profile(node, cpu_ms=10.0, gpu_ms=1.0):
    return DeviceProfile(
        runtime_id="a" * 64,
        model_sha256="b" * 64,
        node_id=node,
        hardware_digest="c" * 64,
        settings_digest="d" * 64,
        backend="cpu",
        measured_at=100.0,
        cpu_layer_ms=cpu_ms,
        gpu_layer_ms=gpu_ms,
        memory_available_bytes=1024,
    )


def test_profile_cache_is_read_only_on_misses_and_expires(tmp_path):
    cache = ProfileCache(tmp_path / "profiles", max_entries=1, max_age_s=60)
    value = profile("master")
    identity = {
        k: v
        for k, v in value.model_dump().items()
        if k
        in (
            "runtime_id",
            "model_sha256",
            "node_id",
            "hardware_digest",
            "settings_digest",
            "backend",
        )
    }
    assert cache.get(identity, now=101) is None and not cache.root.exists()
    assert cache.put(value) == profile_key(value)
    assert cache.get(identity, now=101) == value
    assert cache.get(identity, now=200) is None
    cache.put(profile("worker"))
    assert cache.get(identity, now=101) is None


def test_profiles_compare_only_complete_feasible_candidates():
    value = topology()
    measurements = {
        "now": 101.0,
        "model_sha256": "b" * 64,
        "hardware": {"master": "c" * 64, "worker": "c" * 64},
        "settings_digest": "d" * 64,
        "link_ms": {("master", "worker"): 1.0, ("worker", "master"): 1.0},
    }
    selected, status = choose_profiled_plan([value], {}, **measurements)
    assert selected == value and status["status"] == "unmeasured"
    selected, status = choose_profiled_plan(
        [value], {node: profile(node) for node in measurements["hardware"]}, **measurements
    )
    assert status["predicted_decode_ms"] == 10.0
    assert status["status"] == "profiled_estimate"


def test_benchmark_gate_requires_improvement_and_no_material_regression():
    identity = {
        key: "controlled"
        for key in (
            "model_sha256",
            "runtime_commits",
            "hardware",
            "backend",
            "context",
            "precision",
            "sampling",
            "concurrency",
            "prompt_sha256",
            "output_tokens",
        )
    }
    before = Measurement(ttft_ms=100.0, tokens_s=10.0, elapsed_ms=1000.0, peak_memory_bytes=1000)
    after = Measurement(ttft_ms=102.0, tokens_s=12.0, elapsed_ms=900.0, peak_memory_bytes=1000)
    value = Workload(
        identity=identity,
        target_distributed=True,
        memory_budget_bytes=2000,
        llama=[before] * 5,
        prima=[after] * 5,
        quality_passed=True,
        oom_failures=0,
    )
    assert evaluate([value])["passed"]
    assert not evaluate([value.model_copy(update={"target_distributed": False})])["passed"]
    assert not evaluate([value.model_copy(update={"memory_budget_bytes": 500})])["passed"]
    value.prima = [after.model_copy(update={"ttft_ms": 110.0})] * 5
    assert not evaluate([value])["passed"]


@pytest.mark.integration
@pytest.mark.skipif(sys.platform == "win32", reason="Controlled executable uses a Unix shebang")
@pytest.mark.parametrize("backend", ["cpu", "vulkan"])
def test_real_profile_process_and_owned_logs(tmp_path, backend):
    tool = tmp_path / "profile-tool"
    metrics = {
        "schema_version": 1,
        "cpu_q4k_gflops": 1.0,
        "layer_q4k_flops": 1000000.0,
        "memory_available_gib": 1.0,
        "metal_q4k_gflops": 100.0 if backend == "vulkan" else 0.0,
        "cuda_q4k_gflops": 0.0,
    }
    tool.write_text(
        f"#!{sys.executable}\nprint('KLLM_PRIMA_PROFILE=' + {json.dumps(json.dumps(metrics))})\n"
    )
    tool.chmod(0o755)
    model = tmp_path / "model.gguf"
    model.write_bytes(b"test")
    state = SimpleNamespace(
        process_pid=None, node_id="worker", members={"worker": {"backend": "cpu"}}
    )
    runtime = {"engine": "primacpp", "profile_tool": str(tool), "backend": backend}
    jobs = ProfileJobs(
        tmp_path / "jobs", lambda _: runtime, lambda _: model, lambda: state, RuntimeRegistry._kill
    )
    try:
        request = ProfileRequest(runtime_id="a" * 64, model="test", request_id=str(uuid4()))
        job = jobs.start(request)
        until = time.monotonic() + 5
        while (
            jobs.job(job["job_id"])["phase"] not in {"prepared", "failed"}
            and time.monotonic() < until
        ):
            time.sleep(0.01)
        result = jobs.job(job["job_id"])
        if backend == "vulkan":
            assert result["phase"] == "failed", result
            assert "vulkan GPU profiling is unavailable" in result["error"]
            assert not jobs.cache.root.exists()
        else:
            assert result["phase"] == "prepared", result
            assert result["profile"]["cpu_layer_ms"] == 1.0
        assert "KLLM_PRIMA_PROFILE=" in jobs.logs(job["job_id"])
        assert jobs.start(request)["job_id"] == job["job_id"]
        state.process_pid = 123
        with pytest.raises(ValueError, match="active run"):
            jobs.start(request.model_copy(update={"request_id": str(uuid4())}))
    finally:
        jobs.close()


def test_profile_http_requires_local_authenticated_mutation(tmp_path):
    registry = RuntimeRegistry(tmp_path, lambda: tmp_path)
    app = FastAPI()
    app.include_router(
        build_router(
            lambda: registry,
            lambda: None,
            lambda: SimpleNamespace(role="master"),
            token_verifier=lambda value: value == "valid",
        )
    )
    try:
        with TestClient(app, client=("192.168.1.9", 12)) as client:
            assert client.post("/v2/node/prima-profiles", json={}).status_code == 401
            assert (
                client.post(
                    "/v2/node/prima-profiles", json={}, headers={"Authorization": "valid"}
                ).status_code
                == 403
            )
    finally:
        registry.close()


def test_preparation_http_rejects_malformed_identity_before_control(tmp_path):
    registry = RuntimeRegistry(tmp_path, lambda: tmp_path)
    app = FastAPI()
    app.include_router(
        build_router(
            lambda: registry,
            lambda: pytest.fail("Invalid preparation reached control"),
            lambda: SimpleNamespace(role="master"),
            token_verifier=lambda value: value == "valid",
        )
    )
    payload = {
        "runtime_id": "a" * 64,
        "plan_digest": "b" * 64,
        "request_id": str(uuid4()),
        "settings": {"model": "model.gguf"},
    }
    try:
        with TestClient(app, client=("127.0.0.1", 12)) as client:
            endpoint = "/v2/cluster/prima-preparations"
            headers = {"Authorization": "valid"}
            for bad in (
                {**payload, "runtime_id": []},
                {**payload, "plan_digest": "stale"},
                {**payload, "request_id": "not-a-uuid"},
                {**payload, "include_profile": "true"},
                {**payload, "settings": []},
                {**payload, "assessment": True},
            ):
                assert client.post(endpoint, json=bad, headers=headers).status_code == 422
            assert client.post(endpoint, content="{", headers=headers).status_code == 422
    finally:
        registry.close()


def test_unqualified_cluster_target_is_refused_without_changing_state(tmp_path):
    registry = RuntimeRegistry(tmp_path, lambda: tmp_path)
    state = ClusterState(role="master", node_id="master")
    authority = SimpleNamespace(cluster_binding_id=uuid4())
    service = SimpleNamespace(
        state=lambda: state,
        channels=SimpleNamespace(master_authority=lambda: authority, sessions={}),
        rpc_runs=None,
        directory_view=lambda: {},
    )
    manager = RuntimeControlService(service, registry, lambda: "cpu")
    app = FastAPI()
    app.include_router(
        build_router(
            lambda: registry,
            lambda: manager,
            lambda: state,
            token_verifier=lambda value: value == "valid",
        )
    )
    try:
        version = prima_version()
        registry.add(version)
        with TestClient(app, client=("127.0.0.1", 12)) as client:
            response = client.put(
                "/v2/cluster/runtime-target",
                headers={"Authorization": "valid"},
                json={
                    "runtime_id": version.runtime_id,
                    "expected_revision": 0,
                    "request_id": str(uuid4()),
                },
            )
        assert response.status_code == 409
        assert "not qualified" in response.json()["detail"]["message"]
        assert registry.target() is None
        assert registry.data["running"] is None
        assert not registry.data["jobs"]
    finally:
        registry.close()
