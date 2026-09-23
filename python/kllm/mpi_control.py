"""Master-side orchestration for authenticated provider interoperability runs.

This is a validation harness for the production launch path. It never accepts
an executable, environment, data-plane address, or port. Each target agent
allocates those details from its own private configuration.
"""

from __future__ import annotations

import ipaddress
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx


class MpiControlError(RuntimeError):
    """A provider validation rank failed preparation, launch, or collectives."""


@dataclass(frozen=True)
class ProviderValidationTarget:
    node_id: str
    control_base: str
    backend: str
    physical_device_id: str

    def validate(self) -> None:
        parsed = urlsplit(self.control_base)
        if parsed.scheme != "http" or not parsed.hostname or parsed.path not in ("", "/"):
            raise MpiControlError(f"{self.node_id}: control base must be a plain HTTP origin")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise MpiControlError(f"{self.node_id}: control host must be a fixed IP address") from exc
        if address.is_unspecified or not (
            address.is_private or address.is_loopback or address.is_link_local
        ):
            raise MpiControlError(f"{self.node_id}: control host is not private")
        if self.backend not in ("vulkan", "metal", "cuda", "rocm"):
            raise MpiControlError(f"{self.node_id}: unsupported backend")
        if not self.node_id or not self.physical_device_id:
            raise MpiControlError("validation target identities must be nonempty")


def _post(
    target: ProviderValidationTarget,
    path: str,
    body: dict[str, Any],
    key: str,
    timeout: float,
) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.post(
                target.control_base.rstrip("/") + path,
                json=body,
                headers={"X-Kllm-Key": key},
            )
    except httpx.HTTPError as exc:
        raise MpiControlError(f"{target.node_id}: control request failed: {exc}") from exc
    if response.status_code != 200:
        raise MpiControlError(
            f"{target.node_id}: {path} returned {response.status_code}: {response.text[:500]}"
        )
    try:
        value = response.json()
    except ValueError as exc:
        raise MpiControlError(f"{target.node_id}: {path} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise MpiControlError(f"{target.node_id}: {path} returned a non-object")
    return value


def _parallel(
    targets: list[ProviderValidationTarget],
    operation: Callable[[ProviderValidationTarget], dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not targets:
        return {}
    results: dict[str, dict[str, Any]] = {}
    errors = []
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        futures = {pool.submit(operation, target): target for target in targets}
        for future in as_completed(futures):
            target = futures[future]
            try:
                results[target.node_id] = future.result()
            except Exception as exc:
                errors.append(str(exc))
    if errors:
        raise MpiControlError("; ".join(errors))
    return results


def validate_provider_world(
    targets: list[ProviderValidationTarget],
    *,
    join_key: str,
    plan_fingerprint: str,
    manifest_identity: str = "sha256:provider-interoperability-v1",
    forced_loss_rank: int | None = None,
) -> dict[str, Any]:
    """Launch, exercise, and clean one provider world through agent endpoints."""
    if not targets or len({target.node_id for target in targets}) != len(targets):
        raise MpiControlError("provider validation requires unique target nodes")
    if forced_loss_rank is not None and (
        len(targets) < 2 or forced_loss_rank < 0 or forced_loss_rank >= len(targets)
    ):
        raise MpiControlError("forced loss rank must identify a rank in a multi-rank world")
    for target in targets:
        target.validate()
    job_id = secrets.token_hex(16)
    job_secret = secrets.token_hex(32)
    prepared: dict[str, dict[str, Any]] = {}
    ordered = list(targets)
    try:
        for rank, target in enumerate(ordered):
            prepared[target.node_id] = _post(
                target,
                "/v1/mpi/prepare",
                {
                    "job_id": job_id,
                    "rank": rank,
                    "world_size": len(ordered),
                    "plan_fingerprint": plan_fingerprint,
                    "backend": target.backend,
                    "physical_device_id": target.physical_device_id,
                    "manifest_identity": manifest_identity,
                    "purpose": "provider_validation",
                },
                join_key,
                10,
            )
        compatibility = {
            (
                value["provider"]["upstream_revision"],
                value["provider"]["local_patch_digest"],
                value["provider"]["protocol_version"],
                tuple(sorted(value["provider"]["features"])),
            )
            for value in prepared.values()
        }
        if len(compatibility) != 1:
            raise MpiControlError("provider revisions, protocol, patch, or features differ")
        peers = [
            {
                "rank": rank,
                "address": prepared[target.node_id]["address"],
                "nonce": prepared[target.node_id]["nonce"],
            }
            for rank, target in enumerate(ordered)
        ]
        commits = _parallel(
            ordered,
            lambda target: _post(
                target,
                "/v1/mpi/commit",
                {
                    "job_id": job_id,
                    "prepare_token": prepared[target.node_id]["prepare_token"],
                    "job_secret": job_secret,
                    "peers": peers,
                },
                join_key,
                45,
            ),
        )
        collectives = _parallel(
            ordered,
            lambda target: _post(
                target,
                "/v1/mpi/rank/collectives",
                {"job_id": job_id, "phase": "provider_interoperability", "layer": -1},
                join_key,
                45,
            ),
        )
        signatures = {
            (
                tuple(value.get("allreduce") or []),
                tuple(value.get("allgatherv_counts") or []),
                value.get("broadcast_sha256"),
                value.get("allgatherv_sha256"),
                value.get("status"),
            )
            for value in collectives.values()
        }
        signature = next(iter(signatures)) if len(signatures) == 1 else None
        expected_sum = len(ordered) * (len(ordered) + 1) / 2
        if (
            signature is None
            or signature[-1] != "passed"
            or signature[0] != (expected_sum, float(len(ordered)))
            or signature[1] != tuple(range(1, len(ordered) + 1))
        ):
            raise MpiControlError("provider collective results differ across ranks")
        loss_result = None
        if forced_loss_rank is not None:
            failed_target = ordered[forced_loss_rank]
            _post(
                failed_target,
                "/v1/mpi/rank/fail",
                {"job_id": job_id},
                join_key,
                10,
            )
            try:
                _parallel(
                    ordered,
                    lambda target: _post(
                        target,
                        "/v1/mpi/rank/barrier",
                        {"job_id": job_id, "phase": "forced_rank_loss", "layer": -1},
                        join_key,
                        45,
                    ),
                )
            except MpiControlError as exc:
                # Each endpoint waits for its local provider failure and reaps
                # its owned rank before returning the expected error.
                loss_result = {
                    "rank": forced_loss_rank,
                    "node_id": failed_target.node_id,
                    "status": "propagated",
                    "error": str(exc),
                }
            else:
                raise MpiControlError("forced rank loss did not fail the communicator")
        return {
            "kind": "kllm-mpi-provider-validation",
            "job_id": job_id,
            "world_size": len(ordered),
            "node_ids": [target.node_id for target in ordered],
            "providers": {
                node_id: value["provider"] for node_id, value in prepared.items()
            },
            "rank_readiness": commits,
            "collectives": collectives,
            "forced_rank_loss": loss_result,
            "status": "passed",
        }
    finally:
        # No secret, nonce, or preparation token is retained or returned.
        def abort(target: ProviderValidationTarget) -> dict[str, Any]:
            return _post(
                target, "/v1/mpi/abort", {"job_id": job_id}, join_key, 10
            )

        unwinding = sys.exc_info()[0] is not None
        try:
            _parallel([target for target in ordered if target.node_id in prepared], abort)
        except MpiControlError:
            if not unwinding:
                raise


def validate_provider_recovery(
    targets: list[ProviderValidationTarget],
    *,
    join_key: str,
    plan_fingerprint: str,
    forced_loss_rank: int = 1,
) -> dict[str, Any]:
    """Prove rank-loss cleanup and then start a second clean provider world."""
    loss_run = validate_provider_world(
        targets,
        join_key=join_key,
        plan_fingerprint=plan_fingerprint,
        forced_loss_rank=forced_loss_rank,
    )
    clean_run = validate_provider_world(
        targets,
        join_key=join_key,
        plan_fingerprint=plan_fingerprint,
    )
    return {
        "kind": "kllm-mpi-provider-recovery-validation",
        "forced_loss_run": loss_run,
        "second_clean_run": clean_run,
        "status": "passed",
    }
