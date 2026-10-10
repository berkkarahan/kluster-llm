"""Updates HTTP endpoints for the agent composition root."""

from __future__ import annotations

from typing import Any, cast
from fastapi import (
    Depends,
    Header,
)
from kllm.shared.schemas import UpdateProgressBody

from kllm.modules.http_deps import require_key

def _host():
    from kllm.agent import runtime as host

    return host

def cluster_update_endpoint(
    x_kllm_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Master: fan out update to workers. Worker: accept and spawn detached updater."""
    return cast(dict[str, Any], _host()._cluster_update_handlers().request(x_kllm_key))

def cluster_update_progress(
    body: UpdateProgressBody,
    _: None = Depends(require_key),
) -> dict[str, Any]:
    """Worker updater → master: report pull/rebuild/rejoin phase."""
    return cast(dict[str, Any], _host()._cluster_update_handlers().progress(body))