"""Bind versioned control routes without changing paths or operation ids."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from fastapi import APIRouter

Endpoint = Callable[..., Any]
Route = tuple[str, str, str]


def bind_routes(
    router: APIRouter,
    routes: Sequence[Route],
    handlers: Mapping[str, Endpoint],
    *,
    version: str,
) -> None:
    """Attach handlers with the historical operation id formula."""
    for path, method, name in routes:
        endpoint = handlers.get(name)
        if endpoint is None:
            continue
        operation_id = (
            f"{version}_{name}_{method.lower()}_"
            f"{path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}"
        )
        if method == "WEBSOCKET":
            router.add_api_websocket_route(path, endpoint, name=operation_id)
        else:
            router.add_api_route(path, endpoint, methods=[method], operation_id=operation_id)
