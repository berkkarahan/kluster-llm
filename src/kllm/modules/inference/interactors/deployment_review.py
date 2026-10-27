"""Optimistic deployment review checks; never part of a manifest or digest."""
from typing import Any

from fastapi import HTTPException

from kllm.modules.inference.entities import DeploymentReview

DEPLOYMENT_REVIEW_VERSION = 1


def require_review(
    review: DeploymentReview | None,
    runtime: dict[str, Any] | None,
    plan: dict[str, Any] | None = None,
) -> None:
    if review is None:
        return
    runtime = runtime or {}
    if (
        runtime.get("engine") != "primacpp"
        or runtime.get("runtime_id") != review.runtime_id
        or (plan is not None and plan.get("plan_digest") != review.plan_digest)
    ):
        raise HTTPException(409, detail={
            "code": "DEPLOYMENT_REVIEW_CHANGED",
            "message": "Deployment changed; inspect and review the deployment again before Run.",
        })
