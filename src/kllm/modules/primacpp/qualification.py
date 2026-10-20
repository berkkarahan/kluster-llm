"""Prima runtime qualification policy enforcement and validation.

This module provides zero-trust policy enforcement for the Prima managed
runtime, ensuring that unverified platform profiles without physical hardware
qualification evidence are strictly rejected in accordance with PRIMA-DELTA-BUG-002.
"""

from __future__ import annotations

import json
from typing import Any

from kllm.modules.primacpp.interactors.qualification import (
    COMMON_GATES,
    EXECUTION_SETTINGS,
    api_features,
    execution_host,
    execution_scope,
    manifest_context,
    policy,
    qualification_status,
    require_api_features,
)
from kllm.modules.primacpp.interactors.recipe import (
    qualification_errors,
    require_qualified,
)

__all__ = [
    "COMMON_GATES",
    "EXECUTION_SETTINGS",
    "api_features",
    "execution_host",
    "execution_scope",
    "manifest_context",
    "policy",
    "qualification_status",
    "qualification_errors",
    "require_api_features",
    "require_qualified",
    "validate_qualification_policy",
    "is_platform_qualified",
    "enforce_zero_trust_policy",
]


def validate_qualification_policy(definition: dict[str, Any] | None = None) -> list[str]:
    """Validate that the qualification policy strictly enforces zero-trust invariants.

    Ensures that non-Windows platforms (Linux, Darwin) do not ship with unrestricted
    execution permissions, pre-granted capability gates, or dummy receipts until
    physical hardware qualification passes.
    """
    definition = definition or policy()
    errors: list[str] = []

    if definition.get("schema_version") != 1:
        errors.append("invalid_schema_version")

    scopes = definition.get("scopes", {})
    for scope_name, scope in scopes.items():
        platform_name = scope.get("platform")
        if platform_name in ("Linux", "Darwin"):
            if scope.get("unrestricted") is not False:
                errors.append(f"{scope_name}: unrestricted_must_be_false")
            gates = scope.get("gates", {})
            for gate_name, gate_value in gates.items():
                if gate_value is not False:
                    errors.append(f"{scope_name}: gate_{gate_name}_must_be_false")
            if scope.get("receipts"):
                errors.append(f"{scope_name}: receipts_must_be_empty")
            if scope.get("devices"):
                errors.append(f"{scope_name}: devices_must_be_empty")
            if scope.get("models"):
                errors.append(f"{scope_name}: models_must_be_empty")
            if scope.get("execution_scopes"):
                errors.append(f"{scope_name}: execution_scopes_must_be_empty")

    return errors


def is_platform_qualified(platform_name: str, backend: str = "vulkan", topology: str = "local") -> bool:
    """Check whether a specific platform/backend/topology has verified qualification evidence."""
    runtime = {
        "engine": "primacpp",
        "platform": platform_name,
        "backend": backend,
    }
    status = qualification_status(runtime, context={"topology": topology})
    return status.get("inference_supported", False)


def enforce_zero_trust_policy(policy_data: dict[str, Any]) -> dict[str, Any]:
    """Sanitize a policy dictionary, resetting non-Windows platforms to zero-trust defaults."""
    sanitized = json.loads(json.dumps(policy_data))
    scopes = sanitized.get("scopes", {})
    for scope_name, scope in scopes.items():
        if scope.get("platform") in ("Linux", "Darwin"):
            scope["unrestricted"] = False
            scope["source_commit"] = None
            scope["recipe_digest"] = None
            gates = scope.get("gates", {})
            for gate_key in gates:
                gates[gate_key] = False
            scope["models"] = []
            scope["features"] = []
            scope["placements"] = []
            scope["devices"] = []
            scope["execution_scopes"] = []
            scope["receipts"] = []
    return sanitized
