"""Inference HTTP endpoints for the agent composition root."""

from __future__ import annotations

from kllm.shared import state_ref
import asyncio
import httpx
from kllm.shared import http_clients
from typing import Any, cast
from fastapi import (
    Depends,
    Header,
    HTTPException,
    Request,
)
from fastapi.responses import JSONResponse
from kllm.modules.inference.interactors import (
    gateway,
    load_progress,
)
from kllm.modules import models as model_ops
from kllm.shared.auth import (
    ensure_api_token,
    verify_api_token,
)
from kllm.modules.connect.interactors.connect import _served_model_id
from kllm.shared.exceptions import (
    ExternalServiceError,
    ResourceUnavailableError,
    ValidationError,
)
from kllm.shared.schemas import (
    ModelSettingsBody,
    LegacyModelSettingsBody,
    TemplateCheckBody,
)
from kllm.shared.lifecycle_bus import (
    LifecycleEvent,
    publish_lifecycle,
)
from kllm.modules.inference.entities import (
    PlanBody,
    RunBody,
    LegacyPlanBody,
    LegacyRunBody,
)

from kllm.modules.http_deps import require_api_token

def _host():
    from kllm.agent import runtime as host

    return host

def load_progress_endpoint() -> dict[str, Any]:
    """Active inference load state for the UI (phase machine + RSS fraction)."""
    return load_progress.sample(state_ref.current().infer_port)

async def chat_completions(
    request: Request,
    _: None = Depends(require_api_token),
) -> Any:
    model_path = None
    if state_ref.current().model_name:
        try:
            model_path = model_ops.resolve_local_model(state_ref.current().model_name)
        except FileNotFoundError:
            model_path = None
    return await _host()._observed_inference(
        "/v1/chat/completions",
        gateway.proxy_chat_completions(
            state_ref.current().infer_port, request, model_path=model_path, active_config=_host()._active_config()
        ),
    )

async def anthropic_messages(
    request: Request,
    _: None = Depends(require_api_token),
) -> Any:
    model_path = None
    if state_ref.current().model_name:
        try:
            model_path = model_ops.resolve_local_model(state_ref.current().model_name)
        except FileNotFoundError:
            model_path = None
    return await _host()._observed_inference(
        "/v1/messages",
        gateway.proxy_anthropic_messages(
            state_ref.current().infer_port, request, model_path=model_path, active_config=_host()._active_config()
        ),
    )

async def responses(request: Request, _: None = Depends(require_api_token)) -> Any:
    return await _host()._observed_inference(
        "/v1/responses",
        gateway.proxy_responses(state_ref.current().infer_port, request, active_config=_host()._active_config()),
    )

async def response_tokens(request: Request, _: None = Depends(require_api_token)) -> Any:
    return await _host()._observed_inference(
        "/v1/responses/input_tokens",
        gateway.proxy_responses(state_ref.current().infer_port, request, count_tokens=True),
    )

async def inference_models(request: Request, _: None = Depends(require_api_token)) -> Any:
    return await gateway.served_models(state_ref.current().infer_port, request.app.state.inference_client)

async def connection_snapshot(
    request: Request,
    authorization: str | None = Header(default=None),
    x_kllm_key: str | None = Header(default=None),
) -> JSONResponse:
    """Return a no-store Connect snapshot sourced from the serving master."""
    if state_ref.current().role == "worker":
        if not verify_api_token(authorization):
            raise HTTPException(401, "invalid or missing API token")
        if not state_ref.current().master_host:
            raise HTTPException(503, "worker has no reachable cluster master")
        base = (
            f"http://127.0.0.1:{int(cast(int, state_ref.current().cp_proxy_port))}"
            if state_ref.current().cp_proxy_port
            else _host()._connection_url(state_ref.current().master_host, state_ref.current().master_port)
        )

        def fetch_master() -> dict[str, Any]:
            response = http_clients.control_client().get(
                f"{base}/v2/inference/connection",
                headers={"X-Kllm-Key": state_ref.current().join_key or ""},
                timeout=5.0,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("role") != "master":
                raise ValueError("master returned an invalid Connect snapshot")
            return payload

        try:
            payload = await asyncio.to_thread(fetch_master)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise HTTPException(503, "worker pairing cannot authenticate with the master") from exc
            raise HTTPException(503, "master Connect details are unavailable") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(503, "master Connect details are unavailable") from exc
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    token_ok = verify_api_token(authorization)
    cluster_key_ok = bool(state_ref.current().role == "master" and state_ref.current().join_key and x_kllm_key == state_ref.current().join_key)
    if not token_ok and not cluster_key_ok:
        raise HTTPException(401, "invalid or missing API token")
    if state_ref.current().role != "master":
        raise HTTPException(503, "start this node as the cluster master first")
    activity = _host()._inference_activity()
    if not (state_ref.current().process_pid and state_ref.current().process_role == "master") or not activity.get("server_ready"):
        raise HTTPException(503, "master inference is not ready; start a model before copying a command")
    catalog = await gateway.served_models(state_ref.current().infer_port, request.app.state.inference_client)
    model = _served_model_id(catalog, state_ref.current().model_name)
    if not model:
        raise HTTPException(503, "master has no uniquely identified served model")
    return JSONResponse(
        {
            "role": "master",
            "node_id": state_ref.current().node_id,
            "url": _host()._connection_request_url(request)
            or _host()._connection_url(_host()._master_lan_host(), state_ref.current().control_port),
            "model": model,
            "api_token": ensure_api_token(),
        },
        headers={"Cache-Control": "no-store"},
    )

def get_model_settings(model: str) -> dict[str, Any]:
    return cast(dict[str, Any], _host()._run_settings_handlers().get(model))

def run_capabilities(model: str | None = None) -> dict[str, Any]:
    return cast(dict[str, Any], _host()._run_settings_handlers().capabilities(model))

def validate_chat_template(body: TemplateCheckBody) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], _host()._template_validation_service().validate(body.text).as_payload())
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (ExternalServiceError, ResourceUnavailableError) as exc:
        raise HTTPException(503, str(exc)) from exc

def put_model_settings(model: str, body: ModelSettingsBody) -> dict[str, Any]:
    """Persist per-model run settings (context, KV dtype, speculative, ...).

    Validated against ``RunSettingsFields`` so a malformed set is rejected
    with field-level errors instead of poisoning the store. The stored set is
    applied by the dashboard on model selection and merged into plan/run
    payloads — no cluster reload is required for it to take effect.
    """
    return cast(dict[str, Any], _host()._run_settings_handlers().save(model, body))

def plan(body: PlanBody, preview: bool = False) -> dict[str, Any]:
    return cast(dict[str, Any], _host()._inference_handlers().plan(body, preview))

def run(body: RunBody) -> dict[str, Any]:
    service = getattr(_host().app.state, "managed_network", None)
    if service is None and getattr(_host().app.state, "managed_network_error", None):
        raise HTTPException(503, "Recover signed node/runtime control before starting inference")
    result = (
        service.rpc_runs.launch(body)
        if service is not None and service.rpc_runs is not None
        else _host()._inference_handlers().run(body)
    )
    # A successful run is both accepted and serving; listeners can use either event.
    publish_lifecycle(LifecycleEvent.RUN_STARTED, state_ref.current().model_name)
    publish_lifecycle(LifecycleEvent.INFERENCE_STARTED, state_ref.current().model_name)
    return cast(dict[str, Any], result)

def legacy_plan(body: LegacyPlanBody, preview: bool = False) -> dict[str, Any]:
    return cast(dict[str, Any], _host()._inference_handlers().plan(body, preview))

def legacy_run(body: LegacyRunBody) -> dict[str, Any]:
    return run(body)

def legacy_put_model_settings(model: str, body: LegacyModelSettingsBody) -> dict[str, Any]:
    from kllm.modules.inference.entities import RunSettingsFields
    from kllm.modules.inference.interactors.parameter_catalog import canonicalize_parameters

    values = {
        key: value
        for key, value in body.parameter_set.items()
        if key in RunSettingsFields.model_fields
    }
    return put_model_settings(
        model, ModelSettingsBody(parameter_set=canonicalize_parameters(values, legacy=True))
    )

def stop_inference() -> dict[str, Any]:
    """Stop llama-server and drop the loaded model. The cluster stays up."""
    service = getattr(_host().app.state, "managed_network", None)
    result = (
        service.rpc_runs.stop(lambda: _host()._inference_handlers().stop())
        if service is not None and service.rpc_runs is not None
        else _host()._inference_handlers().stop()
    )
    publish_lifecycle(LifecycleEvent.INFERENCE_STOPPED, state_ref.current().node_id or None)
    return cast(dict[str, Any], result)