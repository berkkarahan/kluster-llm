"""Engine settings use an independent atomic CAS document, never legacy writes."""
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import threading

import pytest

from test_plan_run_params import client as client, _model_file


def test_http_engine_settings_auth_composition_and_readback(client, monkeypatch):
    from types import SimpleNamespace
    from kllm.agent import runtime as host
    from kllm.shared.auth import ensure_api_token
    from kllm.modules.inference.interactors.run_settings_http import RunSettingsHandlers
    from kllm.modules.runtimes.interactors.selection import snapshot
    c, _, tmp = client
    _model_file(tmp)
    calls = []
    runtime = {"engine": "primacpp", "runtime_id": "a" * 64}
    monkeypatch.setattr(host, "_runtime_registry", lambda: SimpleNamespace(capture=lambda _: runtime))
    monkeypatch.setattr(RunSettingsHandlers, "preflight", lambda self, body, path: calls.append(snapshot()))
    url = "/v2/inference/engine-model-settings/primacpp/toy.gguf"
    payload = {"runtime_id": "a" * 64, "expected_revision": 0, "parameter_set": {"seed": 0}}
    assert c.get(url).status_code == 401
    assert c.put(url, json=payload).status_code == 401
    headers = {"authorization": f"Bearer {ensure_api_token()}"}
    initial = c.get(url, headers=headers)
    assert initial.status_code == 200, initial.text
    assert initial.json()["revision"] == 0
    saved = c.put(url, json=payload, headers=headers)
    assert saved.status_code == 200, saved.text
    assert c.get(url, headers=headers).json() == saved.json()
    assert calls == [runtime]
    assert c.put(url, json=payload, headers=headers).status_code == 409
    assert c.put(url, json={**payload, "expected_revision": False}, headers=headers).status_code == 422
    assert not (tmp / "kllm-home" / "model-settings.json").exists()
    assert (tmp / "kllm-home" / "engine-model-settings.json").exists()


def test_capabilities_capture_exact_runtime_and_unknown_catalog(client, monkeypatch):
    from types import SimpleNamespace
    from kllm.agent import runtime as host
    from kllm.modules.inference.interactors import engine_parameters
    from kllm.modules.runtimes.interactors.selection import snapshot
    c, _, _ = client
    runtime = {"runtime_id": "a" * 64, "engine": "primacpp"}
    captures, observed = [], []
    def capture(identity):
        captures.append(identity)
        return runtime
    def catalog():
        observed.append(snapshot())
        return {}
    monkeypatch.setattr(host, "_runtime_registry", lambda: SimpleNamespace(capture=capture))
    monkeypatch.setattr(engine_parameters, "engine_catalog", catalog)
    result = c.get("/v2/inference/capabilities", params={"runtime_id": "a" * 64})
    assert result.status_code == 200, result.text
    value = result.json()
    assert captures == ["a" * 64] and observed == [runtime]
    assert value["runtime_id"] == "a" * 64 and value["engine"] == "primacpp"
    assert value["deployment_review_version"] == 1
    assert not value["engine_available"]
    assert all(not descriptor["available"] for descriptor in value["parameters"] if descriptor["engine_flags"])
    assert snapshot() is None
    c.get("/v2/inference/capabilities")
    assert captures == ["a" * 64] and observed[-1] is None


def test_atomic_store_two_writers_and_legacy_bytes(tmp_path):
    from kllm.modules.inference.gateways.engine_settings import EngineSettingsStore, ModelSettingsChanged
    legacy = tmp_path / "model-settings.json"
    legacy.write_bytes(b'{"old": {"extra_args": ["--seed", "0"]}}\n')
    before = legacy.read_bytes()
    path = tmp_path / "engine-model-settings.json"
    stores = [EngineSettingsStore(path), EngineSettingsStore(path)]
    assert stores[0].get("primacpp", "toy.gguf") is None
    assert not path.exists()
    barrier = threading.Barrier(2)
    requested = {"seed": 0, "enable_thinking": False, "draft_model": None,
                 "extra_args": ["--seed", "0"]}
    def save(store):
        barrier.wait()
        try:
            return store.put("primacpp", "toy.gguf", 0, {"seed": 0}, requested)
        except ModelSettingsChanged:
            return "conflict"
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(save, stores))
    assert results.count("conflict") == 1
    document = stores[0].get("primacpp", "toy.gguf")
    assert document["revision"] == 1
    assert document["requested_parameter_set"] == requested
    assert document["parameter_set"] == {"seed": 0}
    assert document["updated_at"]
    assert stores[1].get("primacpp", "toy.gguf") == document
    assert legacy.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("contents", ["not json", "{}", '{"schema_version": 99, "settings": {}}'])
def test_corrupt_store_never_resets_or_overwrites(tmp_path, contents):
    from kllm.modules.inference.gateways.engine_settings import EngineSettingsStore
    path = tmp_path / "engine-model-settings.json"
    path.write_text(contents)
    store = EngineSettingsStore(path)
    with pytest.raises(ValueError, match="[Ss]ettings"):
        store.get("primacpp", "toy.gguf")
    with pytest.raises(ValueError, match="[Ss]ettings"):
        store.put("primacpp", "toy.gguf", 0, {}, {})
    assert path.read_text() == contents


def settings_handler(tmp_path, *, engine="primacpp"):
    from types import SimpleNamespace
    from kllm.modules.inference.gateways.engine_settings import EngineSettingsStore
    from kllm.modules.inference.interactors.engine_settings import EngineSettingsHandlers
    from kllm.modules.runtimes.interactors.selection import snapshot
    observations = []
    legacy = {"temperature": 0.8, "extra_args": ["--temp", "0.2"], "old_unknown": True}
    runtime = {"runtime_id": "a" * 64, "engine": engine}
    handler = EngineSettingsHandlers(
        EngineSettingsStore(tmp_path / "engine-model-settings.json"),
        SimpleNamespace(get_model_settings=lambda _: {"parameter_set": legacy}),
        lambda _: tmp_path / "toy.gguf",
        lambda identity: runtime,
        lambda body, path: observations.append((snapshot(), body, path)),
    )
    return handler, observations, legacy


def test_engine_settings_defaults_and_explicit_raw_projection(tmp_path):
    from kllm.modules.inference.entities import EngineModelSettingsBody, RunSettingsFields
    from kllm.modules.runtimes.interactors.selection import snapshot
    handler, observations, legacy = settings_handler(tmp_path)
    initial = handler.get("primacpp", "toy.gguf")
    assert initial["revision"] == 0 and initial["updated_at"] is None
    assert initial["legacy_parameter_set"] == legacy
    assert initial["parameter_set"]["cache_type_k"] == "f16"
    assert initial["parameter_set"]["cache_type_v"] == "f16"
    assert initial["parameter_set"]["flash_attention"] == "auto"
    assert set(initial["parameter_set"]) == set(RunSettingsFields(model="toy.gguf").snapshot_for_remember())
    raw = {"temperature": None, "enable_thinking": False,
           "extra_args": ["--seed=0", "--top-k", "0"], "remember": True,
           "advanced_open": True, "settings_source": "saved", "network_id": "ignored", "review": {}}
    saved = handler.save("primacpp", "toy.gguf", EngineModelSettingsBody(
        runtime_id="a" * 64, expected_revision=0, parameter_set=raw))
    assert saved["revision"] == 1
    assert saved["parameter_set"]["seed"] == 0
    assert saved["parameter_set"]["top_k"] == 0
    assert saved["parameter_set"]["extra_args"] == []
    assert saved["parameter_set"]["temperature"] is None
    assert saved["requested_parameter_set"] == {
        "temperature": None, "enable_thinking": False, "extra_args": ["--seed=0", "--top-k", "0"]}
    assert saved == handler.get("primacpp", "toy.gguf")
    assert observations[0][0] == {"runtime_id": "a" * 64, "engine": "primacpp"}
    assert snapshot() is None
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        handler.save("primacpp", "toy.gguf", EngineModelSettingsBody(
            runtime_id="a" * 64, expected_revision=0, parameter_set={}))
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "MODEL_SETTINGS_CHANGED"


@pytest.mark.parametrize("params", [
    {"temperature": 0.2, "extra_args": ["--temp", "0.8"]},
    {"extra_args": ["--seed", "0", "-s", "1"]},
    {"unknown_option": True}, {"model": "other.gguf"},
])
def test_settings_reject_invalid_modern_values_without_writes(tmp_path, params):
    from fastapi import HTTPException
    from kllm.modules.inference.entities import EngineModelSettingsBody
    handler, observations, _ = settings_handler(tmp_path)
    with pytest.raises(HTTPException) as error:
        handler.save("primacpp", "toy.gguf", EngineModelSettingsBody(
            runtime_id="a" * 64, expected_revision=0, parameter_set=params))
    assert error.value.status_code == 422
    assert not observations
    assert not (tmp_path / "engine-model-settings.json").exists()


@pytest.mark.parametrize("changes", [{"expected_revision": -1}, {"expected_revision": True},
    {"expected_revision": "0"}, {"runtime_id": "A" * 64}, {"extra": 1}])
def test_settings_write_envelope_is_strict(changes):
    from pydantic import ValidationError
    from kllm.modules.inference.entities import EngineModelSettingsBody
    with pytest.raises(ValidationError):
        EngineModelSettingsBody.model_validate({"runtime_id": "a" * 64,
            "expected_revision": 0, "parameter_set": {}, **changes})


def test_settings_reject_other_engine_or_captured_runtime(tmp_path):
    from fastapi import HTTPException
    from kllm.modules.inference.entities import EngineModelSettingsBody
    handler, observations, _ = settings_handler(tmp_path, engine="llamacpp")
    with pytest.raises(HTTPException) as error:
        handler.get("llamacpp", "toy.gguf")
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:
        handler.save("primacpp", "toy.gguf", EngineModelSettingsBody(
            runtime_id="a" * 64, expected_revision=0, parameter_set={}))
    assert error.value.status_code == 422
    assert not observations


def _process_writer(path, pipe, begin):
    from kllm.modules.inference.gateways.engine_settings import EngineSettingsStore, ModelSettingsChanged
    pipe.send("ready")
    if not begin.wait(10):
        return
    try:
        value = EngineSettingsStore(path).put("primacpp", "toy.gguf", 0, {"seed": 0}, {"seed": 0})
        pipe.send(value["revision"])
    except ModelSettingsChanged:
        pipe.send("conflict")
    finally:
        pipe.close()


@pytest.mark.integration
def test_cross_process_writers_recheck_revision_inside_file_lock(tmp_path):
    from kllm.modules.inference.gateways.engine_settings import EngineSettingsStore
    context = multiprocessing.get_context("spawn")
    begin = context.Event()
    path = tmp_path / "engine-model-settings.json"
    children, pipes = [], []
    try:
        for _ in range(2):
            parent, child = context.Pipe()
            process = context.Process(target=_process_writer, args=(path, child, begin))
            process.start()
            child.close()
            children.append(process)
            pipes.append(parent)
        for pipe in pipes:
            assert pipe.poll(10), "settings writer bootstrap timed out"
            assert pipe.recv() == "ready"
        begin.set()
        results = []
        for pipe in pipes:
            assert pipe.poll(10), "settings write timed out"
            results.append(pipe.recv())
        assert results.count(1) == 1 and results.count("conflict") == 1
        for process in children:
            process.join(10)
            assert process.exitcode == 0
        assert EngineSettingsStore(path).get("primacpp", "toy.gguf")["revision"] == 1
    finally:
        begin.set()
        for process in children:
            if process.is_alive():
                process.terminate()
            process.join(5)
            assert not process.is_alive()
            process.close()
        for pipe in pipes:
            pipe.close()


def test_real_preflight_rejects_unsupported_prima_setting_without_persistence(tmp_path):
    from kllm.modules.inference.entities import EngineModelSettingsBody
    from kllm.modules.inference.interactors.run_settings_http import RunSettingsHandlers
    from fastapi import HTTPException
    handler, _, _ = settings_handler(tmp_path)
    handler._preflight = RunSettingsHandlers(handler._legacy, handler._resolve_model, lambda: 8).preflight
    with pytest.raises(HTTPException) as error:
        handler.save("primacpp", "toy.gguf", EngineModelSettingsBody(
            runtime_id="a" * 64, expected_revision=0,
            parameter_set={"cache_type_v": "q8_0", "flash_attention": "off"}))
    assert error.value.status_code == 422
    assert not (tmp_path / "engine-model-settings.json").exists()


def test_corrupt_settings_are_actionable_http_refusals(client):
    from kllm.shared.auth import ensure_api_token
    c, _, tmp = client
    path = tmp / "kllm-home" / "engine-model-settings.json"
    path.write_text("not valid json")
    response = c.get("/v2/inference/engine-model-settings/primacpp/toy.gguf",
                     headers={"authorization": f"Bearer {ensure_api_token()}"})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["code"] == "MODEL_SETTINGS_INVALID"
    assert path.read_text() == "not valid json"
