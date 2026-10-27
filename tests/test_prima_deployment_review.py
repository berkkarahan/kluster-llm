"""Deployment review is an ephemeral run precondition, never execution input."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from kllm.modules.inference.entities import PlanBody, RunBody
from kllm.modules.inference.router_http import InferenceHandlers
from kllm.modules.runtimes.interactors.selection import use_runtime
from test_prima_preparation_progress import control as control

RUNTIME = "a" * 64
DIGEST = "b" * 64
REVIEW = {"runtime_id": RUNTIME, "plan_digest": DIGEST}


def test_review_is_excluded_from_all_settings_serialization():
    plan = PlanBody(model="toy.gguf", settings_source="defaults")
    run = RunBody(model="toy.gguf", settings_source="defaults", review=REVIEW)
    assert run.model_dump() == plan.model_dump()
    assert run.snapshot_for_remember() == plan.snapshot_for_remember()
    assert 'review' not in run.model_dump_json()
    with pytest.raises(ValidationError):
        run.review.runtime_id = "c" * 64


@pytest.mark.parametrize("review", [
    {**REVIEW, "runtime_id": "A" * 64},
    {**REVIEW, "plan_digest": "b" * 63},
    {**REVIEW, "other": True},
])
def test_review_is_strict(review):
    with pytest.raises(ValidationError):
        RunBody(model="toy.gguf", review=review)


def test_changed_runtime_refuses_before_any_fallback():
    handler = InferenceHandlers(SimpleNamespace(runtime_context=None))
    with use_runtime({"engine": "llamacpp", "runtime_id": RUNTIME}):
        with pytest.raises(HTTPException) as error:
            handler.run(RunBody(model="toy.gguf", review=REVIEW))
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "DEPLOYMENT_REVIEW_CHANGED"


def test_authoritative_plan_checked_after_settings_rebuild_before_launch(tmp_path, monkeypatch):
    from kllm.modules.inference import router_http
    monkeypatch.setattr(router_http, "require_qualified", lambda _: None)
    calls = []
    runtime = {"engine": "primacpp", "runtime_id": RUNTIME}
    deps = SimpleNamespace(
        runtime_context=lambda: use_runtime(runtime),
        state=SimpleNamespace(role="master", members={}),
        resolve_settings=lambda body: RunBody.model_validate(body.model_dump()),
        resolve_model=lambda _: tmp_path / "toy.gguf", preflight=None,
        ensure_master_member=lambda: None, merge_extra=lambda _: None,
        validate_vision=lambda _: None,
        execute_plan=lambda *_: {"fit_status": "fit", "plan_digest": DIGEST},
        prima_plan=lambda *_: {"plan_digest": "c" * 64},
        prima_run=lambda *_: calls.append("launch"),
    )
    with pytest.raises(HTTPException) as error:
        InferenceHandlers(deps).run(RunBody(model="toy.gguf", review=REVIEW))
    assert error.value.detail["code"] == "DEPLOYMENT_REVIEW_CHANGED"
    assert calls == []


def test_runtime_preview_projects_effective_configuration_without_target_mutation(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kllm.modules.runtimes.router_http import build_router
    from kllm.modules.runtimes.interactors.selection import snapshot
    from kllm.modules.inference.interactors import effective_config
    observations = []
    runtime = {"runtime_id": RUNTIME, "engine": "primacpp"}
    registry = SimpleNamespace(capture=lambda identity: runtime, target="unchanged")
    path = tmp_path / "toy.gguf"
    path.write_bytes(b"controlled model")
    def plan(identity, body):
        observations.append((snapshot(), body.model_dump()))
        return {"plan_digest": DIGEST, "inference_supported": False, "qualification_errors": ["unqualified"]}
    prima = SimpleNamespace(plan=plan, resolve_model=lambda model: path)
    def effective(body, result, model):
        assert snapshot() == runtime
        assert model == path
        return {"requested": body.snapshot_for_remember(), "values": {"seed": body.seed}}
    monkeypatch.setattr(effective_config, "effective_configuration", effective)
    app = FastAPI()
    app.include_router(build_router(lambda: registry, lambda: SimpleNamespace(prima=prima),
        lambda: SimpleNamespace(role="master"), token_verifier=lambda _: True))
    body = PlanBody(model="toy.gguf", settings_source="defaults", seed=0)
    response = TestClient(app).post(f"/v2/node/runtimes/{RUNTIME}/prima-plan", json=body.model_dump())
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["effective_config"]["values"]["seed"] == 0
    assert value["deployment_review_version"] == 1
    assert value["plan_digest"] == DIGEST
    assert value["qualification_errors"] == ["unqualified"] and not value["inference_supported"]
    assert observations == [(runtime, body.model_dump())]
    assert registry.target == "unchanged" and snapshot() is None


def test_generic_prima_preview_advertises_review_version(tmp_path, monkeypatch):
    from kllm.modules.inference import router_http
    monkeypatch.setattr(router_http, "effective_configuration", lambda *_: {})
    deps = SimpleNamespace(runtime_context=lambda: use_runtime({"engine": "primacpp", "runtime_id": RUNTIME}),
        state=SimpleNamespace(role="master", members={}, node_id="master"),
        resolve_settings=None, resolve_model=lambda _: tmp_path / "toy.gguf", preflight=None,
        ensure_master_member=lambda: None, merge_extra=lambda _: None,
        execute_plan=lambda *_: {"fit_status": "fit"},
        prima_plan=lambda *_: {"plan_digest": DIGEST, "engine": "primacpp"})
    result = InferenceHandlers(deps).plan(PlanBody(model="toy.gguf", settings_source="defaults"))
    assert result["deployment_review_version"] == 1
    assert result["plan_digest"] == DIGEST


def test_review_does_not_change_real_plan_or_execution_manifest(tmp_path):
    from kllm.modules.primacpp.interactors.planner import preview, materialize
    from test_prima_lifecycle import BASELINE_FEATURES
    model = tmp_path / "toy.gguf"
    model.write_bytes(b"controlled model")
    runtime = {"runtime_id": RUNTIME, "engine": "primacpp", "backend": "cpu",
               "capabilities": {"features": BASELINE_FEATURES, "backends": ["cpu"]}}
    members = {"master": {"role": "master", "host": "127.0.0.1", "backend": "cpu"}}
    def plan(*_):
        return {"fit_status": "fit", "estimated_layers": 1, "cpu_layers": 1,
                "device_effective": "cpu", "device_allocations": [],
                "resolved_launch_settings": {"parallel_slots": 1, "prima_kv_sequences": 2}}
    body = PlanBody(model=model.name, settings_source="defaults", cache_type_k="f16", cache_type_v="f16", flash_attention="off")
    original = preview(body, runtime, members, model, plan)
    run = RunBody(**body.model_dump(), review={"runtime_id": RUNTIME, "plan_digest": original["plan_digest"]})
    reviewed = preview(run, runtime, members, model, plan)
    assert reviewed["plan_digest"] == original["plan_digest"]
    execution, _ = materialize(run, runtime, members, model, plan, expected_digest=original["plan_digest"])
    assert execution.settings == body.model_dump()
    assert "review" not in execution.settings


def test_cancel_public_projection_reports_local_work_until_settled(control):
    from concurrent.futures import Future
    from copy import deepcopy
    future = Future()
    control.jobs["pending"] = {"job_id": "pending", "phase": "preparing", "created_at": 1}
    control.preparation_futures["pending"] = future
    lease = control.resources.acquire(("primacpp", "pending", "owner", "manifest"), "prepare")
    control.preparation_reservations["pending"] = lease
    result = control.cancel("pending")
    assert result["phase"] == "cancelled" and result["local_work_pending"] is True
    before = deepcopy(control.jobs)
    assert control.job("pending")["local_work_pending"] is True
    assert control.latest_preparation()["local_work_pending"] is True
    assert control.jobs == before
    future.set_result(None)
    control._settle_preparation("pending")
    assert control.job("pending")["local_work_pending"] is False
    assert control.latest_preparation()["phase"] == "cancelled"
    assert "local_work_pending" not in control.jobs["pending"]


def test_prepared_projection_keeps_settling_phase(control):
    control.jobs["pending"] = {"job_id": "pending", "phase": "prepared"}
    lease = control.resources.acquire(("primacpp", "pending", "owner", "manifest"), "prepare")
    control.preparation_reservations["pending"] = lease
    assert control.job("pending")["phase"] == "settling"
    assert control.job("pending")["local_work_pending"] is True
    assert control.latest_preparation()["phase"] == "settling"
    assert control.jobs["pending"]["phase"] == "prepared"
    control._settle_preparation("pending")


@pytest.mark.parametrize("review", [None, REVIEW])
def test_matching_review_or_legacy_run_preserves_admission_and_qualification(tmp_path, monkeypatch, review):
    from kllm.modules.inference import router_http
    runtime = {"engine": "primacpp", "runtime_id": RUNTIME}
    checked, launched = [], []
    def qualify(value):
        checked.append(value)
        raise ValueError("unqualified runtime")
    monkeypatch.setattr(router_http, "require_qualified", qualify)
    body = RunBody(model="toy.gguf", review=review)
    deps = SimpleNamespace(runtime_context=lambda: use_runtime(runtime))
    with pytest.raises(HTTPException) as error:
        InferenceHandlers(deps).run(body)
    assert error.value.detail["code"] == "ENGINE_NOT_QUALIFIED"
    assert checked == [runtime]
    monkeypatch.setattr(router_http, "require_qualified", lambda _: None)
    monkeypatch.setattr(router_http, "effective_configuration", lambda *_: {})
    def admission(*args):
        launched.append(args)
        raise ValueError("admission refused")
    deps.state = SimpleNamespace(role="master", members={})
    deps.resolve_settings = lambda body: RunBody.model_validate(body.model_dump())
    deps.resolve_model = lambda _: tmp_path / "toy.gguf"
    deps.preflight = None
    deps.ensure_master_member = lambda: None
    deps.merge_extra = lambda _: None
    deps.validate_vision = lambda _: None
    deps.execute_plan = lambda *_: {"fit_status": "fit"}
    deps.prima_plan = lambda *_: {"plan_digest": DIGEST}
    deps.prima_run = admission
    with pytest.raises(HTTPException) as error:
        InferenceHandlers(deps).run(body)
    assert error.value.detail["code"] == "PRIMA_RUN_REFUSED"
    assert "admission refused" in error.value.detail["message"]
    assert len(launched) == 1


@pytest.mark.parametrize("runtime", [None, {"engine": "primacpp", "runtime_id": "c" * 64}])
def test_missing_or_changed_prima_identity_refuses_before_resolution(runtime):
    with use_runtime(runtime), pytest.raises(HTTPException) as error:
        InferenceHandlers(SimpleNamespace(runtime_context=None)).run(RunBody(model="toy.gguf", review=REVIEW))
    assert error.value.detail["code"] == "DEPLOYMENT_REVIEW_CHANGED"
