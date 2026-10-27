"""Unit tests for Prima qualification policy and zero-trust gate enforcement.

Tests PRIMA-DELTA-BUG-002: Qualification Gate Bypass via Pre-Enabled Unrestricted Scopes.
Verifies that:
- Shipped qualification.json enforces zero-trust for all Linux and Darwin platform profiles.
- Unqualified Linux and Darwin runtimes are cleanly rejected.
- Unrestricted profiles are strictly forbidden in production without explicit developer overrides.
- Empty receipts cannot qualify a platform.
- Policy validation detects any regressions to permissive defaults.
"""

from __future__ import annotations


from kllm.modules.primacpp.qualification import (
    enforce_zero_trust_policy,
    is_platform_qualified,
    policy,
    qualification_errors,
    qualification_status,
    validate_qualification_policy,
)


def test_shipped_qualification_json_zero_trust_invariants():
    """Asserts that all Linux and Darwin entries in qualification.json have unrestricted == False,

    all capability gates False, and empty lists for receipts, devices, models, and features.
    """
    definition = policy()
    assert definition.get("schema_version") == 1, "Schema version must be 1"

    scopes = definition.get("scopes", {})
    non_windows_scopes = [
        "linux-vulkan-local",
        "linux-vulkan-distributed",
        "darwin-vulkan-local",
        "darwin-vulkan-distributed",
    ]

    for scope_key in non_windows_scopes:
        assert scope_key in scopes, f"Scope {scope_key} must exist in qualification.json"
        scope = scopes[scope_key]

        # Zero-trust core invariants
        assert scope.get("unrestricted") is False, (
            f"{scope_key} must enforce unrestricted: false"
        )
        assert scope.get("source_commit") is None, (
            f"{scope_key} must have source_commit: null until qualified"
        )
        assert scope.get("recipe_digest") is None, (
            f"{scope_key} must have recipe_digest: null until qualified"
        )

        # Capability gates must all be false
        gates = scope.get("gates", {})
        assert isinstance(gates, dict), f"{scope_key} gates must be a dictionary"
        for gate_name, gate_value in gates.items():
            assert gate_value is False, (
                f"{scope_key} gate '{gate_name}' must be false, got {gate_value}"
            )

        # Required capability gates explicitly verified
        expected_gates = [
            "vulkan_gpu_inference",
            "api_parity",
            "model_and_placement_parity",
            "signed_rank_lifecycle",
            "local_performance_gate",
            "hardware_performance_gate",
            "distributed_ring_transport",
            "vulkan_kv_cache_transfer",
        ]
        for expected_gate in expected_gates:
            assert expected_gate in gates, (
                f"{scope_key} missing expected gate '{expected_gate}'"
            )
            assert gates[expected_gate] is False, (
                f"{scope_key} gate '{expected_gate}' must be false"
            )

        # Receipts, devices, models, and features must be empty lists
        assert scope.get("receipts") == [], f"{scope_key} receipts must be empty list"
        assert scope.get("devices") == [], f"{scope_key} devices must be empty list"
        assert scope.get("models") == [], f"{scope_key} models must be empty list"
        assert scope.get("features") == [], f"{scope_key} features must be empty list"
        assert scope.get("placements") == [], f"{scope_key} placements must be empty list"
        assert scope.get("execution_scopes") == [], (
            f"{scope_key} execution_scopes must be empty list"
        )

    # Policy validator must report zero violations on shipped file
    policy_violations = validate_qualification_policy(definition)
    assert policy_violations == [], f"Shipped policy has violations: {policy_violations}"


def test_unqualified_linux_runtime_rejected():
    """Passes a simulated Linux runtime without receipts into qualification_errors() and

    qualification_status(); asserts it returns zero-trust rejections.
    """
    linux_runtime = {
        "engine": "primacpp",
        "platform": "Linux",
        "backend": "vulkan",
        "commit": "dummy_commit_sha",
        "recipe_digest": "dummy_recipe_digest",
    }

    status = qualification_status(linux_runtime, context={"topology": "local"})
    assert status.get("inference_supported") is False, (
        "Unqualified Linux runtime must not support inference"
    )
    assert status.get("scope") == "linux-vulkan-local"

    errors = status.get("qualification_errors", [])
    assert "qualification_evidence_missing" in errors, (
        "Must report missing qualification evidence"
    )
    assert "qualified_device_set_empty" in errors, (
        "Must report empty qualified device set"
    )
    assert "qualified_model_set_empty" in errors, (
        "Must report empty qualified model set"
    )
    assert "qualification_identity_mismatch" in errors, (
        "Must report qualification identity mismatch against unverified profile"
    )

    # Missing gates must be reported
    assert "vulkan_gpu_inference" in errors
    assert "api_parity" in errors
    assert "local_performance_gate" in errors

    # Check via qualification_errors() helper
    q_errors = qualification_errors(linux_runtime, context={"topology": "local"})
    assert "qualification_evidence_missing" in q_errors

    # Check helper is_platform_qualified
    assert is_platform_qualified("Linux", "vulkan", "local") is False
    assert is_platform_qualified("Linux", "vulkan", "distributed") is False


def test_unqualified_darwin_runtime_rejected():
    """Passes a simulated Darwin runtime into qualification_status(); asserts zero-trust rejection."""
    darwin_runtime = {
        "engine": "primacpp",
        "platform": "Darwin",
        "backend": "vulkan",
        "commit": "dummy_commit_sha",
        "recipe_digest": "dummy_recipe_digest",
    }

    status = qualification_status(darwin_runtime, context={"topology": "local"})
    assert status.get("inference_supported") is False, (
        "Unqualified Darwin runtime must not support inference"
    )
    assert status.get("scope") == "darwin-vulkan-local"

    errors = status.get("qualification_errors", [])
    assert "qualification_evidence_missing" in errors
    assert "qualified_device_set_empty" in errors
    assert "qualified_model_set_empty" in errors
    assert "vulkan_gpu_inference" in errors

    assert is_platform_qualified("Darwin", "vulkan", "local") is False
    assert is_platform_qualified("Darwin", "vulkan", "distributed") is False


def test_unrestricted_override_requires_explicit_context():
    """Asserts that setting 'unrestricted': true in a record without allow_unrestricted_override=True

    produces 'unrestricted_profile_forbidden_in_production'.
    """
    from kllm.modules.primacpp.interactors.qualification import _scope_errors

    permissive_record = {
        "platform": "Linux",
        "backend": "vulkan",
        "topology": "local",
        "unrestricted": True,
        "source_commit": None,
        "recipe_digest": None,
        "gates": {
            "vulkan_gpu_inference": True,
            "api_parity": True,
            "model_and_placement_parity": True,
            "signed_rank_lifecycle": True,
            "local_performance_gate": True,
        },
        "models": [{"name": "all", "sha256": "*"}],
        "devices": [{"device_identity": "dev1", "driver_identity": "drv1"}],
        "features": ["chat"],
        "placements": ["layer"],
        "receipts": ["test-receipt"],
    }
    dummy_runtime = {"commit": "c", "recipe_digest": "d"}

    # Attempt 1: In production mode (no context or context without override)
    errors, _ = _scope_errors(permissive_record, dummy_runtime, "local", context=None)
    assert "unrestricted_profile_forbidden_in_production" in errors, (
        "Must forbid unrestricted profile when context is None"
    )

    errors, _ = _scope_errors(
        permissive_record, dummy_runtime, "local", context={"topology": "local"}
    )
    assert "unrestricted_profile_forbidden_in_production" in errors, (
        "Must forbid unrestricted profile when allow_unrestricted_override is absent"
    )

    errors, _ = _scope_errors(
        permissive_record,
        dummy_runtime,
        "local",
        context={"allow_unrestricted_override": False},
    )
    assert "unrestricted_profile_forbidden_in_production" in errors, (
        "Must forbid unrestricted profile when allow_unrestricted_override is False"
    )

    # Attempt 2: Explicit developer override passed in context
    errors_override, _ = _scope_errors(
        permissive_record,
        dummy_runtime,
        "local",
        context={"allow_unrestricted_override": True},
    )
    assert "unrestricted_profile_forbidden_in_production" not in errors_override, (
        "Developer override must permit unrestricted profile for testing"
    )


def test_empty_receipts_rejected():
    """Verifies that even if gates and models were pre-set, empty receipts: [] triggers

    qualification_evidence_missing.
    """
    from kllm.modules.primacpp.interactors.qualification import _scope_errors

    record_no_receipts = {
        "platform": "Linux",
        "backend": "vulkan",
        "topology": "local",
        "unrestricted": False,
        "source_commit": "c",
        "recipe_digest": "d",
        "gates": {
            "vulkan_gpu_inference": True,
            "api_parity": True,
            "model_and_placement_parity": True,
            "signed_rank_lifecycle": True,
            "local_performance_gate": True,
        },
        "models": [{"name": "test", "sha256": "abc"}],
        "devices": [{"device_identity": "d", "driver_identity": "drv"}],
        "receipts": [],  # Empty receipts!
    }
    errors, _ = _scope_errors(
        record_no_receipts,
        {"commit": "c", "recipe_digest": "d"},
        "local",
        context={"topology": "local"},
    )
    assert "qualification_evidence_missing" in errors, (
        "Empty receipts must trigger qualification_evidence_missing"
    )


def test_enforce_zero_trust_policy_sanitizes_permissive_defaults():
    """Verifies that enforce_zero_trust_policy strips all permissive attributes from non-Windows scopes."""
    tampered_policy = {
        "schema_version": 1,
        "scopes": {
            "linux-vulkan-local": {
                "platform": "Linux",
                "backend": "vulkan",
                "topology": "local",
                "unrestricted": True,
                "gates": {"vulkan_gpu_inference": True, "api_parity": True},
                "receipts": ["bogus-receipt"],
                "models": [{"name": "all"}],
                "devices": [{"device": "gpu0"}],
            },
            "darwin-vulkan-local": {
                "platform": "Darwin",
                "backend": "vulkan",
                "topology": "local",
                "unrestricted": True,
                "gates": {"vulkan_gpu_inference": True},
                "receipts": ["bogus-receipt"],
            },
        },
    }

    # Pre-condition: policy has violations
    violations = validate_qualification_policy(tampered_policy)
    assert len(violations) > 0

    # Sanitize policy
    sanitized = enforce_zero_trust_policy(tampered_policy)
    assert validate_qualification_policy(sanitized) == []

    linux_scope = sanitized["scopes"]["linux-vulkan-local"]
    assert linux_scope["unrestricted"] is False
    assert linux_scope["receipts"] == []
    assert linux_scope["models"] == []
    assert linux_scope["devices"] == []
    assert all(v is False for v in linux_scope["gates"].values())


def test_non_prima_engine_not_subject_to_prima_policy():
    """Verifies that non-primacpp engine (llamacpp) returns empty qualification errors."""
    llamacpp_runtime = {"engine": "llamacpp", "platform": "Linux"}
    assert qualification_errors(llamacpp_runtime) == []

    status = qualification_status(llamacpp_runtime)
    assert status.get("inference_supported") is True
    assert status.get("qualification_errors") == []
