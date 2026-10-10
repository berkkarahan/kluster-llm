"""Runtimes HTTP endpoints for the agent composition root."""

from __future__ import annotations

from kllm.shared import state_ref
from typing import Any
from fastapi import Header
from kllm.shared.schemas import LlamaSetupBody
from kllm.modules.runtimes.interactors.runtime_api import setup_runtime

def _host():
    from kllm.agent import runtime as host

    return host

def llama_setup(
    body: LlamaSetupBody | None = None,
    x_kllm_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Extract the pinned llama.cpp pack from ~/.kllm/models into vendor/bin."""
    return setup_runtime(
        body,
        x_kllm_key,
        state=state_ref.current(),
        service=_host()._runtime_setup_service,
    )
