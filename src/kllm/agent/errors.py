"""Structured HTTP error rendering for the agent's public control API."""

import math
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from kllm.agent.schemas import ErrorDetail, ErrorResponse


def error_envelope(status_code: int, detail: Any) -> dict[str, object]:
    """Build the stable v2 error body for an HTTP failure."""
    codes = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        502: "upstream_error",
        503: "unavailable",
    }
    message = detail if isinstance(detail, str) else "The request could not be completed."
    error = ErrorDetail(
        code=codes.get(status_code, "internal_error"),
        message=message,
        details=None if isinstance(detail, str) else detail,
    )
    return ErrorResponse(error=error).model_dump(exclude_none=True)


async def plan_run_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Keep legacy v1 planning errors and the versioned v2 envelope compatible."""
    errors = exc.errors()
    for error in errors:
        value = error.get("input")
        if isinstance(value, float) and not math.isfinite(value):
            error["input"] = str(value)
    if request.url.path.startswith("/v2/"):
        return JSONResponse(
            status_code=422,
            content=error_envelope(422, jsonable_encoder(errors)),
        )
    if request.url.path in ("/v1/plan", "/v1/run"):
        return JSONResponse(status_code=400, content={"detail": jsonable_encoder(errors)})
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


async def http_error_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Render HTTP failures with the response shape selected by API version."""
    if request.url.path.startswith("/v2/"):
        content = error_envelope(exc.status_code, exc.detail)
    else:
        content = {"detail": exc.detail}
    return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)
