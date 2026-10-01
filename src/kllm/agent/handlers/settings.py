"""HTTP handlers for local operator and data-directory settings."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from fastapi import HTTPException

from kllm.agent.schemas import ConfigBody, HfTokenBody
from kllm.state import ClusterState


class ConfigGateway(Protocol):
    def load_config(self) -> dict[str, Any]: ...

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]: ...


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
