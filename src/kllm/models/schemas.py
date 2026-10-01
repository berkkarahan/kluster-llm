"""Structured model catalog and operation response schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelCard(BaseModel):
    """Public information for one locally available or loaded model."""

    model_config = ConfigDict(extra="allow", frozen=True)

    id: str
    object: Literal["model"] = "model"
    owned_by: str = "kllm"
    filename: str | None = None
    loaded: bool = False


class ModelCatalog(BaseModel):
    """OpenAI-shaped listing of local model files."""

    model_config = ConfigDict(frozen=True)

    object: Literal["list"] = "list"
    data: list[ModelCard] = Field(default_factory=list)
    loaded: str | None = None


class ModelSearchHit(BaseModel):
    """One validated local or Hugging Face GGUF search result."""

    model_config = ConfigDict(frozen=True)

    repo_id: str
    filename: str
    path: str | None = None
    revision: str | None = None
    quant: str | None = None
    size_bytes: int
    gated: bool
    source: str
    fit: Literal["fits", "overflow", "unknown"] = "unknown"


class ModelSearchResults(BaseModel):
    """Structured result returned by the model search workflow."""

    model_config = ConfigDict(frozen=True)

    query: str
    total_vram_bytes: int | None
    results: tuple[ModelSearchHit, ...] = ()
    warnings: tuple[str, ...] = ()


class DownloadAccepted(BaseModel):
    """Acknowledgement for an accepted asynchronous model download."""

    model_config = ConfigDict(frozen=True)

    ok: Literal[True] = True
    accepted: Literal[True] = True
    source: str
    kind: Literal["download"] = "download"
    node_id: str
    state: Literal["running"] = "running"
    operation_id: str
    started_at: str
    updated_at: str
    finished_at: str | None = None


class ModelDeletion(BaseModel):
    """Result of deleting a model and its metadata sidecars."""

    model_config = ConfigDict(frozen=True)

    ok: Literal[True] = True
    name: str
    path: str
    results: list[Any] = Field(default_factory=list)
