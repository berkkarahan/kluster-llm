"""Typed request and response schemas used by the agent API."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from kllm import netsec
from kllm.state import DEFAULT_CONTROL_PORT, DEFAULT_RPC_PORT


class ErrorDetail(BaseModel):
    """Machine-readable failure with an actionable message."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    """Stable error envelope returned by control protocol v2."""

    model_config = ConfigDict(frozen=True)

    error: ErrorDetail


class StartBody(BaseModel):
    key: str | None = None
    control_port: int = DEFAULT_CONTROL_PORT
    rpc_port: int = DEFAULT_RPC_PORT
    rpc_bind: str | None = None
    overlay: str = netsec.OVERLAY_NONE
    gpu_ids: list[int] | None = None


class JoinBody(BaseModel):
    key: str
    master_host: str | None = None
    master_port: int = DEFAULT_CONTROL_PORT
    rpc_port: int = DEFAULT_RPC_PORT
    rpc_bind: str | None = None
    overlay: str = netsec.OVERLAY_NONE
    gpu_ids: list[int] | None = None


class RegisterBody(BaseModel):
    node_id: str
    host: str
    control_port: int = DEFAULT_CONTROL_PORT
    rpc_port: int = DEFAULT_RPC_PORT
    total_ram_bytes: int = 0
    available_ram_bytes: int = 0
    vram_bytes: int | None = None
    backend: str = "unknown"
    actual_backend: str | None = None
    requested_backend: str | None = None
    devices: list[dict[str, Any]] = Field(default_factory=list)
    unified_memory: bool = False
    binary_version: str | None = None
    reclaimable_ram_bytes: int = 0
    lan_host: str | None = None
    wg_public_key: str | None = None
    wg_listen_port: int = netsec.WG_LISTEN_PORT
    gpu_ids: list[int] | None = None
    git_head: str | None = None
    llama_release: str | None = None
    process_pid: int | None = None
    process_role: str | None = None
    process_started_at: str | None = None
    process_start_token: str | None = None
    resource_session: dict[str, Any] | None = None


class UnregisterBody(BaseModel):
    """Worker identity submitted when a node leaves the cluster."""

    node_id: str = Field(default="", max_length=128)


class UpdateProgressBody(BaseModel):
    node_id: str | None = None
    phase: str
    error: str | None = None
    git_head: str | None = None
    llama_release: str | None = None
    rebuild: str | None = None
    host: str | None = None


class LogLineBody(BaseModel):
    event_id: uuid.UUID | None = None
    ts: float | None = Field(default=None, allow_inf_nan=False)
    source: str = "agent"
    line: str


class LogsIngestBody(BaseModel):
    node_id: str
    lines: list[LogLineBody] = Field(default_factory=list)


class ConfigBody(BaseModel):
    device: Literal["cpu", "gpu"] = Field(default="gpu")
    gpu_ids: list[int] | None = None
    vram_percent: int | None = Field(default=None, ge=10, le=100)
    ram_percent: int | None = Field(default=None, ge=10, le=100)
    accel_backend: str | None = None
    split_mode: Literal["layer", "row", "tensor"] | None = None


class DownloadBody(BaseModel):
    source: str
    name: str | None = None


class SearchBody(BaseModel):
    query: str


class HfTokenBody(BaseModel):
    token: str


class DeleteBody(BaseModel):
    name: str


class LlamaSetupBody(BaseModel):
    tag: str | None = None
    backend: str = "auto"
    force: bool = False


class TemplateCheckBody(BaseModel):
    text: str


class ModelSettingsBody(BaseModel):
    parameter_set: dict[str, Any] = Field(default_factory=dict)
