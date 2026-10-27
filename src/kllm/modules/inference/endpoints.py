"""Inference HTTP endpoints for the agent composition root."""

from __future__ import annotations

from kllm.shared import state_ref
import asyncio
import httpx
from kllm.shared import http_clients
from typing import Annotated, Any, cast
from fastapi import (
    Depends,
    Header,
    HTTPException,
    Request,
    Query,
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
    EngineModelSettingsBody,
    PlanBody,
    RunBody,
    LegacyPlanBody,
    LegacyRunBody,
)

from kllm.modules.http_deps import require_api_token

from collections.abc import Callable
from fastapi import APIRouter

_observed_inference_provider: Callable[..., Any] | None = None
_active_config_provider: Callable[[], Any] | None = None
_connection_url_provider: Callable[[str, int], str] | None = None
_connection_request_url_provider: Callable[[Request], str | None] | None = None
_master_lan_host_provider: Callable[[], str] | None = None
_inference_activity_provider: Callable[[], dict[str, Any]] | None = None
_run_settings_handlers_provider: Callable[[], Any] | None = None
_engine_settings_handlers_provider: Callable[[], Any] | None = None
_template_validation_service_provider: Callable[[], Any] | None = None
_inference_handlers_provider: Callable[[], Any] | None = None
_managed_network_service_provider: Callable[[], Any] | None = None
_managed_network_error_provider: Callable[[], Any] | None = None


def set_inference_providers(
    *,
    observed_inference: Callable[..., Any] | None = None,
    active_config: Callable[[], Any] | None = None,
    connection_url: Callable[[str, int], str] | None = None,
    connection_request_url: Callable[[Request], str | None] | None = None,
    master_lan_host: Callable[[], str] | None = None,
    inference_activity: Callable[[], dict[str, Any]] | None = None,
    run_settings_handlers: Callable[[], Any] | None = None,
    engine_settings_handlers: Callable[[], Any] | None = None,
    template_validation_service: Callable[[], Any] | None = None,
    inference_handlers: Callable[[], Any] | None = None,
    managed_network_service: Callable[[], Any] | None = None,
    managed_network_error: Callable[[], Any] | None = None,
) -> None:
    global _observed_inference_provider, _active_config_provider, _connection_url_provider
    global _connection_request_url_provider, _master_lan_host_provider, _inference_activity_provider
    global _run_settings_handlers_provider, _engine_settings_handlers_provider
    global _template_validation_service_provider, _inference_handlers_provider
    global _managed_network_service_provider, _managed_network_error_provider
    if observed_inference is not None:
        _observed_inference_provider = observed_inference
    if active_config is not None:
        _active_config_provider = active_config
    if connection_url is not None:
        _connection_url_provider = connection_url
    if connection_request_url is not None:
        _connection_request_url_provider = connection_request_url
    if master_lan_host is not None:
        _master_lan_host_provider = master_lan_host
    if inference_activity is not None:
        _inference_activity_provider = inference_activity
    if run_settings_handlers is not None:
        _run_settings_handlers_provider = run_settings_handlers
    if engine_settings_handlers is not None:
        _engine_settings_handlers_provider = engine_settings_handlers
    if template_validation_service is not None:
        _template_validation_service_provider = template_validation_service
    if inference_handlers is not None:
        _inference_handlers_provider = inference_handlers
    if managed_network_service is not None:
        _managed_network_service_provider = managed_network_service
    if managed_network_error is not None:
        _managed_network_error_provider = managed_network_error


async def _call_observed_inference(path: str, response: Any) -> Any:
    if _observed_inference_provider is not None:
        return await _observed_inference_provider(path, response)
    return await response


def _get_active_config() -> Any:
    if _active_config_provider is not None:
        return _active_config_provider()
    return None


def _get_connection_url(host: str, port: int) -> str:
    if _connection_url_provider is not None:
        return _connection_url_provider(host, port)
    return f"http://{host}:{port}"


def _get_connection_request_url(request: Request) -> str | None:
    if _connection_request_url_provider is not None:
        return _connection_request_url_provider(request)
    return None


def _get_master_lan_host() -> str:
    if _master_lan_host_provider is not None:
        return _master_lan_host_provider()
    return "127.0.0.1"


def _get_inference_activity() -> dict[str, Any]:
    if _inference_activity_provider is not None:
        return _inference_activity_provider()
    return {}


def _get_run_settings_handlers() -> Any:
    if _run_settings_handlers_provider is not None:
        return _run_settings_handlers_provider()
    raise RuntimeError("run settings handlers provider is not configured")


def _get_engine_settings_handlers() -> Any:
    if _engine_settings_handlers_provider is not None:
        return _engine_settings_handlers_provider()
    raise RuntimeError("engine settings handlers provider is not configured")


def _get_template_validation_service() -> Any:
    if _template_validation_service_provider is not None:
        return _template_validation_service_provider()
    raise RuntimeError("template validation service provider is not configured")


def _get_inference_handlers() -> Any:
    if _inference_handlers_provider is not None:
        return _inference_handlers_provider()
    raise RuntimeError("inference handlers provider is not configured")


def _get_managed_network_service() -> Any:
    if _managed_network_service_provider is not None:
        return _managed_network_service_provider()
    return None


def _get_managed_network_error() -> Any:
    if _managed_network_error_provider is not None:
        return _managed_network_error_provider()
    return None


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
    return await _call_observed_inference(
        "/v1/chat/completions",
        gateway.proxy_chat_completions(
            state_ref.current().infer_port, request, model_path=model_path, active_config=_get_active_config()
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
    return await _call_observed_inference(
        "/v1/messages",
        gateway.proxy_anthropic_messages(
            state_ref.current().infer_port, request, model_path=model_path, active_config=_get_active_config()
        ),
    )


async def chat_completion_control(request: Request, _: None = Depends(require_api_token)) -> Any:
    return await gateway.proxy_completion_control(
        state_ref.current().infer_port, request, active_config=_get_active_config()
    )


async def responses(request: Request, _: None = Depends(require_api_token)) -> Any:
    return await _call_observed_inference(
        "/v1/responses",
        gateway.proxy_responses(state_ref.current().infer_port, request, active_config=_get_active_config()),
    )


async def response_tokens(request: Request, _: None = Depends(require_api_token)) -> Any:
    return await _call_observed_inference(
        "/v1/responses/input_tokens",
        gateway.proxy_responses(
            state_ref.current().infer_port, request,
            count_tokens=True, active_config=_get_active_config(),
        ),
    )


async def inference_models(request: Request, _: None = Depends(require_api_token)) -> Any:
    return await gateway.served_models(state_ref.current().infer_port, request.app.state.inference_client)


async def embeddings(request: Request, _: None = Depends(require_api_token)) -> Any:
    return await _call_observed_inference(
        "/v1/embeddings",
        gateway.proxy_embeddings(state_ref.current().infer_port, request, active_config=_get_active_config()),
    )


async def raw_embeddings(request: Request, _: None = Depends(require_api_token)) -> Any:
    path = request.url.path
    return await _call_observed_inference(
        path,
        gateway.proxy_embeddings(state_ref.current().infer_port, request,
                                 active_config=_get_active_config(), path=path),
    )


async def rerank(request: Request, _: None = Depends(require_api_token)) -> Any:
    return await _call_observed_inference(
        "/v1/rerank",
        gateway.proxy_rerank(state_ref.current().infer_port, request, active_config=_get_active_config()),
    )


async def tokenize(request: Request, _: None = Depends(require_api_token)) -> Any:
    return await _call_observed_inference(
        "/tokenize",
        gateway.proxy_model_utility(state_ref.current().infer_port, request, "/tokenize",
                                    active_config=_get_active_config()),
    )


async def detokenize(request: Request, _: None = Depends(require_api_token)) -> Any:
    return await _call_observed_inference(
        "/detokenize",
        gateway.proxy_model_utility(state_ref.current().infer_port, request, "/detokenize",
                                    active_config=_get_active_config()),
    )


async def apply_template(request: Request, _: None = Depends(require_api_token)) -> Any:
    return await _call_observed_inference(
        "/apply-template",
        gateway.proxy_model_utility(state_ref.current().infer_port, request, "/apply-template",
                                    active_config=_get_active_config()),
    )


async def text_completions(request: Request, _: None = Depends(require_api_token)) -> Any:
    return await _call_observed_inference(
        "/v1/completions",
        gateway.proxy_text_completions(state_ref.current().infer_port, request,
                                       active_config=_get_active_config()),
    )


async def infill(request: Request, _: None = Depends(require_api_token)) -> Any:
    return await _call_observed_inference(
        "/infill",
        gateway.proxy_infill(state_ref.current().infer_port, request,
                             active_config=_get_active_config()),
    )


async def raw_completions(request: Request, _: None = Depends(require_api_token)) -> Any:
    path = request.url.path
    return await _call_observed_inference(
        path,
        gateway.proxy_raw_completion(state_ref.current().infer_port, request, path,
                                     active_config=_get_active_config()),
    )

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
            else _get_connection_url(state_ref.current().master_host, state_ref.current().master_port)
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
    activity = _get_inference_activity()
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
            "url": _get_connection_request_url(request)
            or _get_connection_url(_get_master_lan_host(), state_ref.current().control_port),
            "model": model,
            "api_token": ensure_api_token(),
        },
        headers={"Cache-Control": "no-store"},
    )


def get_model_settings(model: str) -> dict[str, Any]:
    return cast(dict[str, Any], _get_run_settings_handlers().get(model))


def run_capabilities(model: str | None = None,
                     runtime_id: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None) -> dict[str, Any]:
    return cast(dict[str, Any], _get_run_settings_handlers().capabilities(model, runtime_id))


def get_engine_model_settings(engine: str, model: str,
                              _: None = Depends(require_api_token)) -> dict[str, Any]:
    return _get_engine_settings_handlers().get(engine, model)


def put_engine_model_settings(engine: str, model: str, body: EngineModelSettingsBody,
                              _: None = Depends(require_api_token)) -> dict[str, Any]:
    return _get_engine_settings_handlers().save(engine, model, body)


def validate_chat_template(body: TemplateCheckBody) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], _get_template_validation_service().validate(body.text).as_payload())
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (ExternalServiceError, ResourceUnavailableError) as exc:
        raise HTTPException(503, str(exc)) from exc


def put_model_settings(model: str, body: ModelSettingsBody) -> dict[str, Any]:
    return cast(dict[str, Any], _get_run_settings_handlers().save(model, body))


def plan(body: PlanBody, preview: bool = False) -> dict[str, Any]:
    return cast(dict[str, Any], _get_inference_handlers().plan(body, preview))


def run(body: RunBody) -> dict[str, Any]:
    service = _get_managed_network_service()
    if service is None and _get_managed_network_error():
        raise HTTPException(503, "Recover signed node/runtime control before starting inference")
    result = (
        service.rpc_runs.launch(body)
        if service is not None and getattr(service, "rpc_runs", None) is not None
        else _get_inference_handlers().run(body)
    )
    publish_lifecycle(LifecycleEvent.RUN_STARTED, state_ref.current().model_name)
    publish_lifecycle(LifecycleEvent.INFERENCE_STARTED, state_ref.current().model_name)
    return cast(dict[str, Any], result)


def legacy_plan(body: LegacyPlanBody, preview: bool = False) -> dict[str, Any]:
    return cast(dict[str, Any], _get_inference_handlers().plan(body, preview))


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
    service = _get_managed_network_service()
    prima = getattr(getattr(service, "runtimes", None), "_prima", None) if service is not None else None
    if prima is not None:
        prima.runs.cancelled.set()
        prima.runs.stop()
    result = (
        service.rpc_runs.stop(lambda: _get_inference_handlers().stop())
        if service is not None and getattr(service, "rpc_runs", None) is not None
        else _get_inference_handlers().stop()
    )
    publish_lifecycle(LifecycleEvent.INFERENCE_STOPPED, state_ref.current().node_id or None)
    return cast(dict[str, Any], result)


def create_inference_router(
    run_settings_handlers: Any = None,
    engine_settings_handlers: Any = None,
    inference_handlers: Any = None,
    template_validation_service: Any = None,
    managed_network_service: Any = None,
) -> APIRouter:
    router = APIRouter()

    def r_settings() -> Any:
        if run_settings_handlers is not None:
            return run_settings_handlers() if callable(run_settings_handlers) else run_settings_handlers
        return _get_run_settings_handlers()

    def e_settings() -> Any:
        if engine_settings_handlers is not None:
            return engine_settings_handlers() if callable(engine_settings_handlers) else engine_settings_handlers
        return _get_engine_settings_handlers()

    def inf_h() -> Any:
        if inference_handlers is not None:
            return inference_handlers() if callable(inference_handlers) else inference_handlers
        return _get_inference_handlers()

    def tpl_val() -> Any:
        if template_validation_service is not None:
            return template_validation_service() if callable(template_validation_service) else template_validation_service
        return _get_template_validation_service()

    def net_svc() -> Any:
        if managed_network_service is not None:
            return managed_network_service() if callable(managed_network_service) else managed_network_service
        return _get_managed_network_service()

    @router.get("/inference/capabilities")
    @router.get("/v1/run-capabilities")
    def _caps(model: str | None = None, runtime_id: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None) -> dict[str, Any]:
        return cast(dict[str, Any], r_settings().capabilities(model, runtime_id))

    @router.get("/inference/model-settings/{model}")
    @router.get("/v1/model-settings/{model}")
    def _get_settings(model: str) -> dict[str, Any]:
        return cast(dict[str, Any], r_settings().get(model))

    @router.put("/inference/model-settings/{model}")
    @router.put("/v1/model-settings/{model}")
    def _put_settings(model: str, body: ModelSettingsBody) -> dict[str, Any]:
        return cast(dict[str, Any], r_settings().save(model, body))

    @router.get("/inference/engine-model-settings/{engine}/{model}")
    def _get_engine_settings(engine: str, model: str, _: None = Depends(require_api_token)) -> dict[str, Any]:
        return e_settings().get(engine, model)

    @router.put("/inference/engine-model-settings/{engine}/{model}")
    def _put_engine_settings(engine: str, model: str, body: EngineModelSettingsBody, _: None = Depends(require_api_token)) -> dict[str, Any]:
        return e_settings().save(engine, model, body)

    @router.post("/inference/plans")
    @router.post("/v1/plan")
    def _plan(body: PlanBody, preview: bool = False) -> dict[str, Any]:
        return cast(dict[str, Any], inf_h().plan(body, preview))

    @router.post("/inference/runs")
    @router.post("/v1/run")
    def _run(body: RunBody) -> dict[str, Any]:
        svc = net_svc()
        result = (
            svc.rpc_runs.launch(body)
            if svc is not None and getattr(svc, "rpc_runs", None) is not None
            else inf_h().run(body)
        )
        publish_lifecycle(LifecycleEvent.RUN_STARTED, state_ref.current().model_name)
        publish_lifecycle(LifecycleEvent.INFERENCE_STARTED, state_ref.current().model_name)
        return cast(dict[str, Any], result)

    @router.post("/inference/runs/stop")
    @router.post("/v1/inference/stop")
    def _stop() -> dict[str, Any]:
        svc = net_svc()
        prima = getattr(getattr(svc, "runtimes", None), "_prima", None) if svc is not None else None
        if prima is not None:
            prima.runs.cancelled.set()
            prima.runs.stop()
        result = (
            svc.rpc_runs.stop(lambda: inf_h().stop())
            if svc is not None and getattr(svc, "rpc_runs", None) is not None
            else inf_h().stop()
        )
        publish_lifecycle(LifecycleEvent.INFERENCE_STOPPED, state_ref.current().node_id or None)
        return cast(dict[str, Any], result)

    @router.post("/models/chat-template/validation")
    @router.post("/v1/chat-template/validate")
    def _validate_template(body: TemplateCheckBody) -> dict[str, Any]:
        try:
            return cast(dict[str, Any], tpl_val().validate(body.text).as_payload())
        except ValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except (ExternalServiceError, ResourceUnavailableError) as exc:
            raise HTTPException(503, str(exc)) from exc

    return router


create_router = create_inference_router
