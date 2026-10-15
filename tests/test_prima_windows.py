"""Windows scope, physical-device selection and local performance boundaries."""

import copy
import json
import threading
from types import SimpleNamespace

import pytest

from kllm.modules.primacpp.interactors import devices, qualification
from kllm.modules.primacpp.interactors.benchmark import Measurement, Workload, evaluate
from kllm.modules.primacpp.interactors.recipe import recipe, recipe_digest, require_qualified


def test_idle_prima_cleanup_preserves_other_engine_running_state():
    from kllm.modules.primacpp.interactors.runs import LocalPrimaRuns
    control = SimpleNamespace(rank=SimpleNamespace(owner=None), registry=SimpleNamespace())
    runs = LocalPrimaRuns(control)
    assert runs.stop() == {"phase": "idle"}
    assert runs.cancelled.is_set()


def test_prima_cleanup_stops_only_owned_rank_and_clears_registry():
    from kllm.modules.primacpp.interactors.runs import LocalPrimaRuns
    stopped, running = [], []
    control = SimpleNamespace(rank=SimpleNamespace(owner={"run_id": "owned"}, stop=stopped.append),
                              registry=SimpleNamespace(clear_running=lambda **kw: running.append(kw)))
    runs = LocalPrimaRuns(control)
    runs.manifest = object()
    runs.stop()
    assert stopped == ["owned"]
    assert running == [{"engine": "primacpp", "run_id": "owned"}]
    assert runs.manifest is None


def test_stop_during_prima_validation_cannot_be_cleared_by_later_start(monkeypatch):
    from kllm.modules.primacpp.interactors.runs import LocalPrimaRuns
    state = SimpleNamespace(role="master", process_pid=None, members={})
    service = SimpleNamespace(state=lambda: state, channels=SimpleNamespace(master_authority=lambda: "authority"))
    runs = LocalPrimaRuns(SimpleNamespace(service=service, closed=False,
                                         rank=SimpleNamespace(owner=None), registry=SimpleNamespace()))
    validating, release, errors = threading.Event(), threading.Event(), []

    def validation(*_):
        validating.set()
        assert release.wait(3)
        runs._check()
        pytest.fail("A cancelled validation reached native launch")

    monkeypatch.setattr(runs, "_launch", validation)
    def start():
        try:
            runs.launch(None, None, {}, {})
        except ValueError as error:
            errors.append(str(error))

    start_thread = threading.Thread(target=start)
    start_thread.start()
    assert validating.wait(3)
    stop_thread = threading.Thread(target=runs.stop)
    stop_thread.start()
    assert runs.cancelled.wait(3)
    release.set()
    start_thread.join(3)
    stop_thread.join(3)
    assert not start_thread.is_alive() and not stop_thread.is_alive()
    assert errors == ["Prima run cancelled"]


def test_monitor_retries_failed_cleanup_without_losing_owned_rank():
    from kllm.modules.primacpp.interactors.runs import LocalPrimaRuns
    saved, running, attempts = [], [], []
    state = SimpleNamespace(process_pid=123, process_role="master", active_config={"engine": "primacpp", "run_id": "owned"},
                            apply_error=None, save=lambda: saved.append(True))
    rank = SimpleNamespace(owner={"run_id": "owned"})
    def renew(*_):
        raise ValueError("Rank exited")
    def stop(_):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("Process identity is temporarily unavailable")
        rank.owner = None
    rank.renew, rank.stop = renew, stop
    runs = LocalPrimaRuns(SimpleNamespace(rank=rank, registry=SimpleNamespace(clear_running=lambda **kw: running.append(kw)),
                                         service=SimpleNamespace(state=lambda: state)))
    runs.manifest = SimpleNamespace(run_id="owned")
    runs.monitor_once()
    assert runs.cancelled.is_set() and runs.manifest is not None and rank.owner is not None
    assert state.process_pid == 123 and "identity is temporarily unavailable" in state.apply_error
    assert not running
    runs.monitor_once()
    assert len(attempts) == 2 and rank.owner is None and runs.manifest is None
    assert state.process_pid is None and state.active_config is None
    assert running == [{"engine": "primacpp", "run_id": "owned"}]


def test_prima_fences_inventory_and_master_authority():
    from kllm.modules.primacpp.interactors.runs import LocalPrimaRuns
    from kllm.modules.primacpp.interactors.execution import inventory_identity
    state = SimpleNamespace(role="master", members={})
    authority = ["original"]
    control = SimpleNamespace(closed=False, service=SimpleNamespace(state=lambda: state,
        channels=SimpleNamespace(master_authority=lambda: authority[0])))
    runs = LocalPrimaRuns(control)
    runs.authority = "original"
    runs.inventory_digest = inventory_identity({})
    runs._check()
    state.members = {"other": {"role": "worker"}}
    with pytest.raises(ValueError, match="inventory changed"):
        runs._check()
    state.members = {}
    authority[0] = "new"
    with pytest.raises(ValueError, match="authority changed"):
        runs._check()


def test_prima_run_rolls_back_owned_child_when_final_persistence_fails(monkeypatch, tmp_path):
    from kllm.modules.inference import router_http
    from kllm.modules.inference.entities import RunBody
    from kllm.modules.runtimes.interactors.selection import use_runtime
    actions = []
    state = SimpleNamespace(role="master", node_id="master", members={}, active_config=None,
                            process_pid=None, process_role=None, save=lambda: actions.append("save"))
    def finished(*_):
        state.process_pid = 42
        raise OSError("persistence failed")
    deps = SimpleNamespace(state=state, runtime_context=None, resolve_settings=None, preflight=None,
        resolve_model=lambda _: tmp_path / "model.gguf", ensure_master_member=lambda: None,
        merge_extra=lambda _: None, validate_vision=lambda _: None, network_members=None,
        execute_plan=lambda *_: {"fit_status": "fits"},
        prima_run=lambda *_: {"launch": {"pid": 42}, "run_id": "owned"},
        prima_stop=lambda: actions.append("stop-owned"), finish_master_launch=finished,
        persist_last_settings=lambda _: None, remember=lambda _: None)
    monkeypatch.setattr(router_http, "require_qualified", lambda _: None)
    monkeypatch.setattr(router_http, "effective_configuration", lambda *_: {})
    with use_runtime({"engine": "primacpp"}):
        with pytest.raises(OSError, match="persistence failed"):
            router_http.InferenceHandlers(deps).run(RunBody(model="model.gguf"))
    assert actions == ["stop-owned", "save"]
    assert state.process_pid is state.process_role is state.active_config is None


def test_prima_launch_translation_refuses_unsupported_cache_and_unresolved_attention(monkeypatch):
    from kllm.modules.primacpp.interactors import launch
    monkeypatch.setattr(launch, "help_flags", lambda _: {"--parallel", "--cache-type-k"})
    assert launch.rank_arguments(None, {"parallel_slots": 2, "cache_type_k": "f16"}) == [
        "--parallel", "2", "--cache-type-k", "f16"]
    with pytest.raises(ValueError, match="checkpoints"):
        launch.rank_arguments(None, {"checkpoints": 32})
    with pytest.raises(ValueError, match="explicit Flash Attention"):
        launch.rank_arguments(None, {"flash_attention": "auto"})


def test_prima_auto_defaults_resolve_to_native_support_without_dropping_explicit_values():
    from kllm.modules.inference.entities import RunBody
    from kllm.modules.inference.interactors.run_settings_resolution import resolve_launch_settings
    from kllm.modules.runtimes.interactors.selection import use_runtime
    with use_runtime({"engine": "primacpp"}):
        automatic = resolve_launch_settings(RunBody(model="m.gguf", cache_type_k="f16", cache_type_v="f16"))
        explicit = resolve_launch_settings(RunBody(model="m.gguf", cache_type_k="f16", cache_type_v="f16", checkpoints=32, cache_ram=8192))
    assert automatic.checkpoints == automatic.cache_ram == 0
    assert automatic.flash_attention == "off"
    assert explicit.checkpoints == 32 and explicit.cache_ram == 8192
    with use_runtime({"engine": "llamacpp"}):
        baseline = resolve_launch_settings(RunBody(model="m.gguf"))
    assert baseline.checkpoints == 32 and baseline.cache_ram == 8192


def test_prima_worker_launch_omits_server_alias_but_keeps_decoder_settings(monkeypatch):
    from kllm.modules.primacpp.interactors import launch
    cli_flags = {"--parallel", "--cache-type-k", "--cache-type-v"}
    monkeypatch.setattr(launch, "help_flags", lambda _: cli_flags)
    settings = {"alias": "served-model", "parallel_slots": 2,
                "cache_type_k": "f16", "cache_type_v": "f16"}
    assert launch.rank_arguments(None, settings, rank=1) == [
        "--parallel", "2", "--cache-type-k", "f16", "--cache-type-v", "f16"]
    assert settings["alias"] == "served-model"
    with pytest.raises(ValueError, match="alias"):
        launch.rank_arguments(None, settings, rank=0)
    cli_flags.add("--alias")
    assert launch.rank_arguments(None, settings, rank=0)[-2:] == ["--alias", "served-model"]


def test_failed_master_authority_lookup_releases_prima_start_reservation():
    from kllm.modules.primacpp.interactors.runs import LocalPrimaRuns
    def missing():
        raise ValueError("authority unavailable")
    state = SimpleNamespace(process_pid=None)
    control = SimpleNamespace(rank=SimpleNamespace(owner=None), registry=SimpleNamespace(),
        service=SimpleNamespace(state=lambda: state, channels=SimpleNamespace(master_authority=missing)))
    runs = LocalPrimaRuns(control)
    with pytest.raises(ValueError, match="authority unavailable"):
        runs.launch(None, None, {}, {})
    assert not runs.starting


def test_generation_profile_is_not_extrapolated_to_unmeasured_placements():
    from kllm.modules.primacpp.entities import DeviceProfile, LayerRange, RankAllocation, PrimaTopology
    from kllm.modules.primacpp.interactors.profiles import predicted_decode_ms
    rank = RankAllocation(node_id="master", role="master", rank=0, address="127.0.0.1", next_node_id="master",
        layers=[LayerRange(start=0, end=4)], ngl=2, devices=["Vulkan0"], tensor_split=[1.0])
    topology = PrimaTopology(runtime_id="a" * 64, master_node_id="master", total_layers=4, ranks=[rank])
    profile = DeviceProfile(runtime_id="a" * 64, node_id="master", model_sha256="b" * 64,
        hardware_digest="c" * 64, settings_digest="d" * 64, backend="vulkan", measured_at=100.0,
        cpu_layer_ms=4.0, memory_available_bytes=1000, measurement_kind="end_to_end", decode_ms=8.0,
        total_layers=4, gpu_layers=2)
    context = {"model_sha256": "b" * 64, "hardware": {"master": "c" * 64},
               "settings_digest": "d" * 64, "link_ms": {}, "now": 100.0}
    assert predicted_decode_ms(topology, {"master": profile}, **context) == 8.0
    topology = topology.model_copy(update={"ranks": [rank.model_copy(update={"ngl": 3})]})
    assert predicted_decode_ms(topology, {"master": profile}, **context) is None


def test_profile_refuses_prima_start_reservation_before_persisting(tmp_path):
    from kllm.modules.primacpp.entities import ProfileRequest
    from kllm.modules.primacpp.interactors.preparation import ProfileJobs
    from uuid import uuid4
    state = SimpleNamespace(process_pid=None)
    jobs = ProfileJobs(tmp_path, lambda _: {"engine": "primacpp"}, lambda _: tmp_path / "model.gguf",
                       lambda: state, lambda _: None, busy=lambda: True)
    try:
        with pytest.raises(ValueError, match="inference is starting"):
            jobs.start(ProfileRequest(runtime_id="a" * 64, model="model.gguf", request_id=str(uuid4())))
        assert not jobs.jobs
    finally:
        jobs.close()


def test_http_and_control_share_one_profiler_cache_and_start_reservation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from kllm.modules.primacpp.entities import ProfileRequest
    from kllm.modules.primacpp.interactors.preparation import get_profile_jobs
    from uuid import uuid4
    registry = SimpleNamespace(root=tmp_path, lock=threading.RLock(),
        capture=lambda _: {"engine": "primacpp"}, _kill=lambda _: None)
    def state():
        return SimpleNamespace(process_pid=None)
    with ThreadPoolExecutor(max_workers=4) as pool:
        services = list(pool.map(lambda _: get_profile_jobs(registry, state, lambda _: tmp_path / "local.gguf"), range(16)))
    service = services[0]
    try:
        assert all(s is service for s in services)
        control_service = get_profile_jobs(registry, state, lambda _: tmp_path / "prepared.gguf", busy=lambda: True)
        assert control_service is service and control_service.cache.root == tmp_path / "prima-profiles" / "cache"
        assert service.resolve_model("digest") == tmp_path / "prepared.gguf"
        with pytest.raises(ValueError, match="inference is starting"):
            services[-1].start(ProfileRequest(runtime_id="a" * 64, model="local.gguf", request_id=str(uuid4())))
        assert not service.jobs
    finally:
        service.close()


def test_plan_uses_matching_cached_generation_and_refuses_unmeasured_candidates(tmp_path, monkeypatch):
    import time
    from kllm.modules.inference.entities import RunBody
    from kllm.modules.primacpp.entities import DeviceProfile, LayerRange, RankAllocation, PrimaTopology
    from kllm.modules.primacpp.interactors import measured_planning
    from kllm.modules.primacpp.interactors.profiles import ProfileCache, generation_settings, generation_hardware
    from kllm.modules.runtimes.entities import body_digest
    from kllm.modules.runtimes.interactors.registry import file_digest
    model = tmp_path / "model.gguf"
    model.write_bytes(b"before")
    member = {"backend": "vulkan", "total_ram_bytes": 10000}
    members = {"master": member}
    rank = RankAllocation(node_id="master", role="master", rank=0, address="127.0.0.1", next_node_id="master",
        layers=[LayerRange(start=0, end=4)], ngl=2, devices=["Vulkan0"], tensor_split=[1.0])
    topology = PrimaTopology(runtime_id="a" * 64, master_node_id="master", total_layers=4, ranks=[rank])
    body = RunBody(model=model.name, cache_type_k="f16", cache_type_v="f16", flash_attention="off", threads=8)
    settings = {"threads": 8, "cache_type_k": "f16", "cache_type_v": "f16", "flash_attention": "off",
        "parallel_slots": 1, "batch_size": 512, "micro_batch": 128, "checkpoints": 0, "cache_ram": 0}
    plan = {"resolved_launch_settings": settings, "topology": topology}
    monkeypatch.setattr(measured_planning, "topology_from_plan", lambda plan, *_: plan["topology"])
    monkeypatch.setattr(measured_planning, "discover_vulkan_devices", lambda _: [device()])
    cache = ProfileCache(tmp_path / "cache")
    cached = DeviceProfile(runtime_id="a" * 64, model_sha256=file_digest(model), node_id="master",
        hardware_digest=body_digest(generation_hardware(member, [device()])),
        settings_digest=body_digest(generation_settings(body.context, 8, 2, [device()])), backend="vulkan",
        measured_at=time.time(), cpu_layer_ms=1.0, memory_available_bytes=1000, measurement_kind="end_to_end",
        total_layers=4, gpu_layers=2, decode_ms=8.0, prefill_ms=10.0, peak_memory_bytes=1,
        device_identity=device()["device_identity"], driver_identity=device()["driver_identity"])
    cache.put(cached)
    feasible = [(plan, members)]
    scored = measured_planning.measured_local_candidates(feasible, body, {"runtime_id": "a" * 64}, model, cache)
    assert scored[1]["predicted_decode_ms"] == 8.0
    assert "reason" not in scored[1]
    # A single-node placement profiles against its own rank only; other cluster
    # members without profiles must not force a fallback.
    multi = {"master": member, "worker-cpu": {"backend": "cpu", "total_ram_bytes": 8000}}
    multi_scored = measured_planning.measured_local_candidates([(plan, multi)], body, {"runtime_id": "a" * 64}, model, cache)
    assert multi_scored[1]["predicted_decode_ms"] == 8.0
    mismatched_threads = body.model_copy(update={"threads_batch": 4})
    refused = measured_planning.measured_local_candidates(feasible, mismatched_threads,
        {"runtime_id": "a" * 64}, model, cache)[1]
    assert refused["status"] == "unmeasured" and "matching batch threads" in refused["reason"]
    matching_threads = body.model_copy(update={"threads_batch": 8})
    assert measured_planning.measured_local_candidates(feasible, matching_threads,
        {"runtime_id": "a" * 64}, model, cache)[1]["predicted_decode_ms"] == 8.0
    output_gpu = {**plan, "topology": topology.model_copy(update={"ranks": [rank.model_copy(update={"output_gpu": True})]})}
    assert measured_planning.measured_local_candidates([(output_gpu, members)], body, {"runtime_id": "a" * 64}, model, cache)[1]["status"] == "unmeasured"
    gpu_profile = cached.model_copy(update={"output_gpu": True, "decode_ms": 7.0,
        "settings_digest": body_digest(generation_settings(body.context, 8, 2, [device()], True))})
    cache.put(gpu_profile)
    assert measured_planning.measured_local_candidates([(output_gpu, members)], body, {"runtime_id": "a" * 64}, model, cache)[1]["predicted_decode_ms"] == 7.0
    different = {**plan, "topology": topology.model_copy(update={"ranks": [rank.model_copy(update={"ngl": 3})]})}
    unknown = measured_planning.measured_local_candidates([*feasible, (different, members)], body, {"runtime_id": "a" * 64}, model, cache)
    assert unknown[0] == feasible[0] and unknown[1]["status"] == "unmeasured"
    assert "No cached end-to-end profile" in unknown[1]["reason"]
    model.write_bytes(b"afterr")
    invalid = measured_planning.measured_local_candidates(feasible, body, {"runtime_id": "a" * 64}, model, cache)
    assert invalid[1]["status"] == "unmeasured"
    assert "No cached end-to-end profile" in invalid[1]["reason"]


def test_serving_qualification_rejects_unqualified_request_time_features(monkeypatch):
    active = {"engine": "primacpp",
        "runtime": {**runtime(), "capabilities": {"features": []}},
        "qualification_context": context()}
    monkeypatch.setattr(qualification, "qualification_status", lambda _, **kwargs: {
        "inference_supported": True, "scope": "windows-vulkan-local",
        "qualified_features": ["chat", "streaming", "structured_output", "thinking_toggle", "thinking:off",
                               "tools:forced"]})
    qualification.require_api_features({"messages": [{"role": "user", "content": "hello"}]}, active, "chat")
    named = {"tools": [{"type": "function"}], "tool_choice": {"type": "function"}}
    qualification.require_api_features(named, active, "chat")
    with pytest.raises(ValueError, match="streaming_tools"):
        qualification.require_api_features({**named, "stream": True}, active, "chat")
    with pytest.raises(ValueError, match="tools:auto"):
        qualification.require_api_features({**named, "tool_choice": "auto"}, active, "chat")
    with pytest.raises(ValueError, match="responses"):
        qualification.require_api_features({}, active, "responses")
    with pytest.raises(ValueError, match="vision"):
        qualification.require_api_features({"messages": [{"content": [{"type": "image_url"}]}]}, active, "chat")
    qualification.require_api_features({"chat_template_kwargs": {"enable_thinking": False}}, active, "chat")
    with pytest.raises(ValueError, match="thinking:on"):
        qualification.require_api_features({"chat_template_kwargs": {"enable_thinking": True}}, active, "chat")
    with pytest.raises(ValueError, match="token_logprobs"):
        qualification.require_api_features({"logprobs": True}, active, "chat")
    qualification.require_api_features({**named, "stream": True}, {"engine": "llamacpp"}, "chat")


def device(index=3, identity="a" * 32, name="Vulkan0"):
    return {"backend": "Vulkan", "name": name, "description": "AMD GPU",
            "physical_index": index, "device_identity": identity, "driver_identity": "123"}


def test_selection_uses_physical_index_and_visible_order():
    inventory = [device(), device(7, "b" * 32, "Vulkan1")]
    selected = devices.select_vulkan_devices(inventory, ["b" * 32, "a" * 32])
    assert devices.vulkan_environment(selected) == {"GGML_VK_VISIBLE_DEVICES": "7,3"}
    assert devices.remapped_vulkan_names(selected) == ["Vulkan0", "Vulkan1"]
    assert [d["physical_index"] for d in inventory] == [3, 7]
    with pytest.raises(ValueError, match="explicit"):
        devices.select_vulkan_devices(inventory)
    with pytest.raises(ValueError, match="ambiguous"):
        devices.select_vulkan_devices(inventory, ["AMD GPU"])
    with pytest.raises(ValueError, match="more than once"):
        devices.select_vulkan_devices(inventory, ["Vulkan0", "a" * 32])


def test_legacy_ordinal_is_not_physical_identity():
    with pytest.raises(ValueError, match="Upgrade"):
        devices.select_vulkan_devices([{"name": "Vulkan0", "description": "AMD GPU"}])
    with pytest.raises(ValueError, match="substitution"):
        devices.select_vulkan_devices([])


def test_generation_hardware_ignores_allocation_counters_but_keeps_device_identity():
    from kllm.modules.primacpp.interactors.profiles import generation_hardware
    selected = {**device(), "memory_bytes": 1000, "free_bytes": 900}
    member = {"devices": [selected], "backend": "vulkan", "total_ram_bytes": 10000}
    changed = {**selected, "free_bytes": 100}
    assert generation_hardware(member, [selected]) == generation_hardware({**member, "devices": [changed]}, [changed])
    assert generation_hardware(member, [selected]) != generation_hardware(member, [{**selected, "driver_identity": "456"}])
    assert selected["free_bytes"] == 900


def test_discovery_refreshes_inventory_and_does_not_mutate_environment(monkeypatch):
    monkeypatch.setenv("GGML_VK_VISIBLE_DEVICES", "9")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="KLLM_PRIMA_CAPABILITIES=" + json.dumps({"devices": [device()]}))

    monkeypatch.setattr(devices.subprocess, "run", run)
    assert devices.discover_vulkan_devices({"llama_server": "verified.exe"}) == [device()]
    assert "GGML_VK_VISIBLE_DEVICES" not in calls[0][1]["env"]
    assert devices.os.environ["GGML_VK_VISIBLE_DEVICES"] == "9"


def qualified_policy():
    return {"schema_version": 1, "scopes": {"windows-vulkan-local": {
        "platform": "Windows", "backend": "vulkan", "topology": "local",
        "source_commit": recipe()["source_commit"], "recipe_digest": recipe_digest(),
        "gates": {key: True for key in qualification.COMMON_GATES | {"local_performance_gate"}},
        "models": [{"sha256": "c" * 64}], "features": ["chat", "streaming", "template:default"], "placements": ["layer"],
        "devices": [device()], "receipts": ["controlled-test-only"],
        "execution_scopes": [{"execution": context()["execution"],
            "features": ["chat", "streaming", "template:default"], "receipts": ["controlled-test-only"]}],
    }}}


def runtime():
    return {"engine": "primacpp", "platform": "Windows", "backend": "vulkan",
            "commit": recipe()["source_commit"], "recipe_digest": recipe_digest()}


def context():
    return qualification.manifest_context(local_manifest(), runtime(), devices=[device()])


def local_manifest():
    from kllm.modules.primacpp.entities import RankAllocation, LayerRange
    from kllm.modules.primacpp.interactors.execution import ExecutionManifest, Asset, Segment
    return ExecutionManifest(runtime_id="a" * 64, run_id="00000000-0000-0000-0000-000000000001",
        plan_digest="b" * 64, total_layers=4, inventory_digest="b" * 64,
        ranks=[RankAllocation(node_id="master", role="master", rank=0, address="127.0.0.1",
            next_node_id="master", layers=[LayerRange(start=0, end=4)], ngl=4,
            devices=["Vulkan0"], tensor_split=[1.0], output_gpu=True)],
        assets=[Asset(name="model.gguf", kind="model", sha256="c" * 64, size=64)],
        settings={"context": 4096, "threads": 8, "threads_batch": 8, "batch_size": 512,
                  "micro_batch": 128, "parallel_slots": 1, "cache_type_k": "f16",
                  "cache_type_v": "f16", "flash_attention": "off", "mmap_mlock": "mmap"},
        segments=[Segment(start=0, end=4, node_id="master")], backends={"master": "vulkan"})


@pytest.mark.parametrize("setting,value", [
    ("context", 8192), ("threads", 4), ("threads_batch", 16), ("batch_size", 2048),
    ("micro_batch", 512), ("parallel_slots", 2), ("cache_type_k", "q8_0"),
    ("cache_type_v", "q8_0"), ("flash_attention", "on"), ("mmap_mlock", "none"),
])
def test_qualification_refuses_unmeasured_native_settings(monkeypatch, setting, value):
    monkeypatch.setattr(qualification, "policy", qualified_policy)
    manifest = local_manifest()
    manifest.settings[setting] = value
    with pytest.raises(ValueError, match="execution_settings_placement_not_qualified"):
        require_qualified(runtime(), context=qualification.manifest_context(manifest, runtime(), devices=[device()]))


@pytest.mark.parametrize("change", [{"ngl": 3}, {"output_gpu": False}, {"tensor_split": [0.5]}])
def test_qualification_refuses_unmeasured_native_offload(monkeypatch, change):
    monkeypatch.setattr(qualification, "policy", qualified_policy)
    manifest = local_manifest()
    manifest.ranks[0] = manifest.ranks[0].model_copy(update=change)
    with pytest.raises(ValueError, match="execution_settings_placement_not_qualified"):
        require_qualified(runtime(), context=qualification.manifest_context(manifest, runtime(), devices=[device()]))


def test_serving_rechecks_its_exact_scope_after_policy_revocation(monkeypatch):
    approved = qualified_policy()
    monkeypatch.setattr(qualification, "policy", lambda: approved)
    active = {"engine": "primacpp", "runtime": runtime(), "qualification_context": context()}
    qualification.require_api_features({}, active, "chat")
    scopes = approved["scopes"]["windows-vulkan-local"]["execution_scopes"]
    scopes[0]["execution"]["settings"]["batch_size"] = 2048
    assert qualification.qualification_status(runtime())["inference_supported"]
    with pytest.raises(ValueError, match="no longer qualified"):
        qualification.require_api_features({}, active, "chat")
    with pytest.raises(ValueError, match="no qualified execution context"):
        qualification.require_api_features({}, {"engine": "primacpp", "runtime": runtime()}, "chat")


def test_api_features_cannot_leak_from_another_execution_scope(monkeypatch):
    approved = qualified_policy()
    record = approved["scopes"]["windows-vulkan-local"]
    record["features"].append("token_logprobs")
    another = copy.deepcopy(record["execution_scopes"][0])
    another["execution"]["settings"]["batch_size"] = 2048
    another["features"].append("token_logprobs")
    record["execution_scopes"].append(another)
    monkeypatch.setattr(qualification, "policy", lambda: approved)
    with pytest.raises(ValueError, match="token_logprobs"):
        qualification.require_api_features({"logprobs": True},
            {"engine": "primacpp", "runtime": runtime(), "qualification_context": context()}, "chat")


def test_qualification_requires_execution_receipts_and_detached_context(monkeypatch):
    approved = qualified_policy()
    monkeypatch.setattr(qualification, "policy", lambda: approved)
    manifest = local_manifest()
    bound = qualification.manifest_context(manifest, runtime(), devices=[device()])
    manifest.ranks[0].tensor_split[0] = 0.5
    manifest.settings["batch_size"] = 2048
    require_qualified(runtime(), context=bound)
    approved["scopes"]["windows-vulkan-local"]["execution_scopes"][0]["receipts"] = []
    assert not qualification.qualification_status(runtime())["inference_supported"]
    with pytest.raises(ValueError, match="execution_settings_placement_not_qualified"):
        require_qualified(runtime(), context=bound)


@pytest.mark.parametrize("field,value", [("os", "untested Windows build"), ("cpu", "untested CPU"),
                                        ("physical_cores", 1), ("ram_bytes", 1024)])
def test_qualification_cannot_move_to_unmeasured_host(monkeypatch, field, value):
    monkeypatch.setattr(qualification, "policy", qualified_policy)
    bound = context()
    bound["execution"]["host"][field] = value
    with pytest.raises(ValueError, match="execution_settings_placement_not_qualified"):
        require_qualified(runtime(), context=bound)


def test_qualification_binds_companions_and_device_capacity(monkeypatch):
    from kllm.modules.primacpp.interactors.execution import Asset
    monkeypatch.setattr(qualification, "policy", qualified_policy)
    manifest = local_manifest()
    manifest.assets.append(Asset(name="model-00002-of-00002.gguf", kind="shard", sha256="d" * 64, size=32))
    with pytest.raises(ValueError, match="execution_settings_placement_not_qualified"):
        require_qualified(runtime(), context=qualification.manifest_context(manifest, runtime(), devices=[device()]))
    with pytest.raises(ValueError, match="execution_settings_placement_not_qualified"):
        require_qualified(runtime(), context=qualification.manifest_context(local_manifest(), runtime(),
            devices=[{**device(), "memory_bytes": 1024}]))


def test_new_run_identity_does_not_extend_or_invalidate_measured_scope(monkeypatch):
    monkeypatch.setattr(qualification, "policy", qualified_policy)
    manifest = local_manifest()
    manifest.run_id = "00000000-0000-0000-0000-000000000002"
    manifest.data_port, manifest.signal_port = 11000, 12000
    manifest.settings.update(alias="another HTTP alias", assessment_port=13000, remember=True)
    bound = qualification.manifest_context(manifest, runtime(), devices=[device()])
    require_qualified(runtime(), context=bound)


def test_baseline_placement_verifier_rejects_unmatched_layers_and_output():
    from prima_placement_assessment import baseline_matrix_placement
    rows = [
        "node # 10 (   MUL_MAT): attn_q-0 [CPU 1.wgt0]: blk.0.attn_q.weight [CPU]",
        "node # 20 (   MUL_MAT): attn_q-1 [Vulka 1.wgt0]: blk.1.attn_q.weight [Vulka]",
        "node # 30 (   MUL_MAT): result_output [CPU 1.wgt0]: token_embd.weight [CPU]",
    ]
    assert baseline_matrix_placement("\n".join(rows), 2, 1, False)["output"] == ["cpu"]
    for changed in (rows[1:], [*rows[:2], rows[2].replace("[CPU", "[Vulka")],
                    [rows[0].replace("[CPU", "[Vulka"), *rows[1:]]):
        with pytest.raises(ValueError, match="differs from the requested"):
            baseline_matrix_placement("\n".join(changed), 2, 1, False)


def test_local_qualification_cannot_authorize_other_execution(monkeypatch):
    monkeypatch.setattr(qualification, "policy", qualified_policy)
    require_qualified(runtime(), context=context())
    for field, value, error in [
        ("topology", "distributed", "platform_backend_not_qualified"),
        ("model_sha256", "d" * 64, "model_not_qualified"),
        ("features", ["tools"], "features_not_qualified"),
        ("split_mode", "row", "placement_not_qualified"),
        ("devices", [device(identity="b" * 32)], "device_driver_not_qualified"),
        ("participants", context()["participants"] * 2, "requires_one_node"),
    ]:
        changed = {**context(), field: value}
        with pytest.raises(ValueError, match=error):
            require_qualified(runtime(), context=changed)
    with pytest.raises(ValueError, match="platform_backend_not_qualified"):
        require_qualified({**runtime(), "platform": "Linux"}, context=context())


def test_manifests_and_stale_receipts_cannot_qualify_runtime(monkeypatch):
    approved = qualified_policy()
    monkeypatch.setattr(qualification, "policy", lambda: approved)
    with pytest.raises(ValueError, match="qualification_identity_mismatch"):
        require_qualified({**runtime(), "commit": "f" * 40}, context=context())
    approved["scopes"]["windows-vulkan-local"]["gates"]["vulkan_gpu_inference"] = False
    with pytest.raises(ValueError, match="vulkan_gpu_inference"):
        require_qualified({**runtime(), "qualification_errors": [], "inference_supported": True})


def test_qualification_policy_changes_do_not_change_build_identity(monkeypatch):
    before = recipe_digest()
    monkeypatch.setattr(qualification, "policy", qualified_policy)
    assert qualification.qualification_status(runtime())["inference_supported"]
    assert recipe_digest() == before
    broken = copy.deepcopy(qualified_policy())
    del broken["scopes"]["windows-vulkan-local"]["gates"]["api_parity"]
    monkeypatch.setattr(qualification, "policy", lambda: broken)
    assert not qualification.qualification_status(runtime())["inference_supported"]


def test_local_performance_does_not_close_distributed_improvement_gate():
    identity = {key: "controlled" for key in (
        "model_sha256", "runtime_commits", "hardware", "backend", "context", "precision",
        "sampling", "concurrency", "prompt_sha256", "output_tokens")}
    measured = Measurement(ttft_ms=100, tokens_s=10, elapsed_ms=1000, peak_memory_bytes=1000)
    workload = Workload(identity=identity, target_distributed=False, memory_budget_bytes=2000,
                        llama=[measured] * 5, prima=[measured] * 5, quality_passed=True, oom_failures=0)
    assert evaluate([workload], scope="local")["passed"]
    assert not evaluate([workload])["passed"]
    with pytest.raises(ValueError, match="physical distributed"):
        evaluate([workload.model_copy(update={"target_distributed": True})], scope="local")


def test_gpu_evidence_requires_completed_work_on_every_assigned_layer():
    from kllm.modules.primacpp.entities import LayerRange, RankAllocation
    from kllm.modules.primacpp.interactors.execution import ExecutionManifest, Segment, Asset
    from kllm.modules.primacpp.interactors.evidence import verify_vulkan_execution
    from uuid import uuid4

    rank = RankAllocation(node_id="master", role="master", rank=0, address="127.0.0.1",
                          next_node_id="master", layers=[LayerRange(start=0, end=2)], ngl=1,
                          devices=["Vulkan0"], tensor_split=[1.0])
    manifest = ExecutionManifest(runtime_id="a" * 64, run_id=str(uuid4()), plan_digest="b" * 64,
                                 total_layers=2, ranks=[rank], assets=[Asset(name="m.gguf", kind="model", sha256="c" * 64, size=1)],
                                 settings={}, inventory_digest="d" * 64, segments=[Segment(start=0, end=2, node_id="master")],
                                 backends={"master": "vulkan"})
    weights = [f"blk.1.{suffix}.weight" for suffix in ("attn_q", "attn_k", "attn_v", "attn_output", "ffn_gate", "ffn_up", "ffn_down")]
    reports = [{"schema_version": 1, "runtime_id": manifest.runtime_id, "manifest_digest": manifest.digest,
                "rank": 0, "completed": True, "phase": phase, "tokens": tokens,
                "devices": [device()], "executed_weights": {"Vulkan0": list(weights)}}
               for phase, tokens in (("prefill", 12), ("decode", 1))]
    assert verify_vulkan_execution(reports, manifest, rank, [device()])["status"] == "passed"
    output_rank = rank.model_copy(update={"output_gpu": True})
    output_manifest = manifest.model_copy(update={"ranks": [output_rank]})
    output_reports = copy.deepcopy(reports)
    for report in output_reports:
        report["manifest_digest"] = output_manifest.digest
        report["executed_weights"]["Vulkan0"].append("output.weight")
    assert verify_vulkan_execution(output_reports, output_manifest, output_rank, [device()])["status"] == "passed"
    output_reports[0]["executed_weights"]["Vulkan0"].remove("output.weight")
    with pytest.raises(ValueError, match="output projection"):
        verify_vulkan_execution(output_reports, output_manifest, output_rank, [device()])
    output_reports[0]["executed_weights"]["CPU"] = ["output.weight"]
    with pytest.raises(ValueError, match="output projection executed on another backend"):
        verify_vulkan_execution(output_reports, output_manifest, output_rank, [device()])
    with pytest.raises(ValueError, match="decode"):
        verify_vulkan_execution(reports[:1], manifest, rank, [device()])
    changed = copy.deepcopy(reports)
    changed[0]["executed_weights"] = {"CPU": weights}
    with pytest.raises(ValueError, match="another backend"):
        verify_vulkan_execution(changed, manifest, rank, [device()])
    changed = copy.deepcopy(reports)
    changed[0]["completed"] = False
    with pytest.raises(ValueError, match="manifest"):
        verify_vulkan_execution(changed, manifest, rank, [device()])
    changed = copy.deepcopy(reports)
    changed[0]["devices"][0]["driver_identity"] = "new-driver"
    with pytest.raises(ValueError, match="physical device"):
        verify_vulkan_execution(changed, manifest, rank, [device()])


def test_topology_separates_synthetic_output_from_transformer_layers():
    from kllm.modules.primacpp.interactors.placement import topology_from_plan
    members = {"master": {"role": "master", "rpc_bind": "127.0.0.1"}}
    row = {"node_id": "master", "device": "Vulkan0", "layer_start": 0, "layer_end": 5,
           "layers": 5, "memory_kind": "gpu", "local": True}
    source = {"estimated_layers": 4, "device_allocations": [row], "cpu_layers": 0}
    topology = topology_from_plan(source, members, "a" * 64)
    rank = topology.ranks[0]
    assert rank.ngl == 4 and rank.layers[0].end == 4 and rank.output_gpu
    cpu = topology_from_plan({"estimated_layers": 4, "cpu_layers": 5, "device_effective": "cpu"}, members, "a" * 64)
    assert cpu.ranks[0].ngl == 0 and cpu.ranks[0].layers[0].end == 4 and not cpu.ranks[0].output_gpu
    with pytest.raises(ValueError, match="multiple owners"):
        topology_from_plan({**source, "device_allocations": [row, row]}, members, "a" * 64)
    with pytest.raises(ValueError, match="rank-zero master"):
        topology_from_plan({**source, "device_allocations": [{**row, "node_id": "worker"}]},
                           {**members, "worker": {"role": "worker"}}, "a" * 64)
