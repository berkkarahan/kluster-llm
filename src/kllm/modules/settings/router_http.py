"""HTTP handlers for local operator and data-directory settings."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from fastapi import HTTPException

from kllm.shared.schemas import ConfigBody, FeatureFlagBody, HfTokenBody
from kllm.shared.state import ClusterState


class ConfigGateway(Protocol):
    def load_config(self) -> dict[str, Any]: ...

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def feature_flags(self) -> dict[str, bool]: ...

    def set_feature_flag(self, name: str, enabled: bool) -> bool: ...


class SettingsHandlers:
    """Map local settings dependencies and validation errors to HTTP responses."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        *,
        ensure_api_token: Callable[[], str],
        hf_token_status: Callable[[], dict[str, Any]],
        save_hf_token: Callable[[str], dict[str, Any]],
        config: ConfigGateway,
        list_gpus: Callable[[], dict[str, Any]],
        home_dir: Callable[[], Any],
        models_dir: Callable[[], Any],
        logs_dir: Callable[[], Any],
    ) -> None:
        self._state = state
        self._ensure_api_token = ensure_api_token
        self._hf_token_status = hf_token_status
        self._save_hf_token = save_hf_token
        self._config = config
        self._list_gpus = list_gpus
        self._home_dir = home_dir
        self._models_dir = models_dir
        self._logs_dir = logs_dir

    def api_token(self) -> dict[str, str]:
        state = self._state()
        return {
            "api_token": self._ensure_api_token(),
            "base_url": f"http://127.0.0.1:{state.control_port}",
        }

    def hf_token(self) -> dict[str, Any]:
        return self._hf_token_status()

    def save_hf_token(self, body: HfTokenBody) -> dict[str, Any]:
        return self._save_hf_token(body.token)

    def get_config(self) -> dict[str, Any]:
        return self._config.load_config()

    def get_gpus(self) -> dict[str, Any]:
        return self._list_gpus()

    def save_config(self, body: ConfigBody) -> dict[str, Any]:
        payload: dict[str, Any] = {"device": body.device}
        if "gpu_ids" in body.model_fields_set:
            payload["gpu_ids"] = body.gpu_ids
        if body.vram_percent is not None:
            payload["vram_percent"] = body.vram_percent
        if body.ram_percent is not None:
            payload["ram_percent"] = body.ram_percent
        if "accel_backend" in body.model_fields_set:
            payload["accel_backend"] = body.accel_backend
        if "split_mode" in body.model_fields_set:
            payload["split_mode"] = body.split_mode
        try:
            return self._config.save_config(payload)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    def home(self) -> dict[str, str]:
        return {
            "home": str(self._home_dir()),
            "models": str(self._models_dir()),
            "logs": str(self._logs_dir()),
        }

    def get_feature_flags(self) -> dict[str, Any]:
        return {"flags": self._config.feature_flags()}

    def save_feature_flag(self, body: FeatureFlagBody) -> dict[str, Any]:
        try:
            self._config.set_feature_flag(body.name, body.enabled)
            return {"flags": self._config.feature_flags()}
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

LEGACY_V1_CREDENTIALS: tuple[tuple[str, str, str], ...] = (
    ('/v1/api-token', 'GET', 'get_api_token'),
    ('/v1/hf-token', 'GET', 'get_hf_token'),
    ('/v1/hf-token', 'PUT', 'put_hf_token'),
)

LEGACY_V1_CONFIG: tuple[tuple[str, str, str], ...] = (
    ('/v1/config', 'GET', 'get_config'),
    ('/v1/gpus', 'GET', 'get_gpus'),
    ('/v1/config', 'PUT', 'put_config'),
    ('/v1/home', 'GET', 'get_home'),
    ('/v1/flags', 'GET', 'get_feature_flags'),
    ('/v1/flags', 'PUT', 'put_feature_flag'),
)

CONTROL_V2_NODE: tuple[tuple[str, str, str], ...] = (
    ('/node/devices', 'GET', 'get_gpus'),
    ('/node/config', 'GET', 'get_config'),
    ('/node/config', 'PUT', 'put_config'),
    ('/node/home', 'GET', 'get_home'),
    ('/node/flags', 'GET', 'get_feature_flags'),
    ('/node/flags', 'PUT', 'put_feature_flag'),
)

CONTROL_V2_CREDENTIALS: tuple[tuple[str, str, str], ...] = (
    ('/credentials/api-token', 'GET', 'get_api_token'),
    ('/credentials/huggingface', 'GET', 'get_hf_token'),
    ('/credentials/huggingface', 'PUT', 'put_hf_token'),
)

