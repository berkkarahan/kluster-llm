"""Observed participant identities never stand in for inference qualification."""

import copy
import json

import pytest

from kllm.modules.primacpp.interactors import participants, qualification
from kllm.modules.primacpp.interactors.execution import ExecutionManifest
from kllm.modules.primacpp.interactors.participants import ParticipantEvidence, observe_participant, require_participant
from kllm.modules.runtimes.entities import body_digest


def runtime():
    return {"engine": "primacpp", "runtime_id": "a" * 64, "commit": "b" * 40,
        "recipe_digest": "c" * 64, "build_id": "immutable-build", "platform": "Windows", "backend": "vulkan",
        "artifacts": {"server.exe": "d" * 64}, "capabilities": {"backends": ["Vulkan", "CPU"]}}


def host():
    return {"os": "Windows-test-build", "cpu": "test CPU", "physical_cores": 8,
            "logical_cores": 16, "ram_bytes": 64 * 1024**3}


def device():
    return {"backend": "Vulkan", "name": "Vulkan0", "physical_index": 3, "device_identity": "e" * 32,
            "driver_identity": "123", "memory_bytes": 16 * 1024**3, "free_bytes": 8 * 1024**3}


@pytest.fixture
def observed(monkeypatch):
    from test_prima_lifecycle import manifest

    value = manifest()
    value.ranks[1].ngl = 8
    value.ranks[1].devices = ["Vulkan0"]
    value.ranks[1].tensor_split = [1.0]
    value.backends["worker"] = "vulkan"
    monkeypatch.setattr(participants.platform, "system", lambda: "Windows")
    monkeypatch.setattr(participants, "execution_host", host)
    monkeypatch.setattr(participants, "discover_vulkan_devices", lambda _: [device()])
    evidence = {r.node_id: observe_participant(runtime(), r.node_id, r, value.backends[r.node_id])
                for r in value.ranks}
    return ExecutionManifest.model_validate(value.model_copy(update={"participant_evidence": evidence}).model_dump())


def test_distributed_context_contains_every_observed_host_and_device(observed):
    context = qualification.manifest_context(observed, runtime(), devices=[])
    assert [p["platform"] for p in context["participants"]] == ["Windows", "Windows"]
    assert context["participants"][0]["backend"] == "cpu"
    assert context["devices"][0]["device_identity"] == device()["device_identity"]
    scopes = context["execution"]["participants"]
    assert len(scopes) == 2 and all(p["host"] == host() for p in scopes)
    assert all("node_id" not in p and "allocation_digest" not in p for p in scopes)
    assert context["execution"]["host"] == host()
    assert not qualification.qualification_status(runtime(), context=context)["inference_supported"]


@pytest.mark.parametrize("change", [{"platform": "Linux"}, {"build_id": "different-build"},
    {"artifacts_digest": "f" * 64}, {"host": {**host(), "ram_bytes": 1024}}])
def test_changed_worker_hardware_or_build_cannot_reuse_approval(observed, change):
    current = ParticipantEvidence.model_validate({**observed.participant_evidence["worker"].model_dump(), **change})
    with pytest.raises(ValueError, match="hardware/build changed"):
        require_participant(observed, "worker", current)


def test_observation_ignores_free_capacity_and_refuses_backend_substitution(monkeypatch, observed):
    original = observe_participant(runtime(), "worker", observed.ranks[1], "vulkan")
    monkeypatch.setattr(participants, "discover_vulkan_devices", lambda _: [{**device(), "free_bytes": 1}])
    assert observe_participant(runtime(), "worker", observed.ranks[1], "vulkan").digest == original.digest
    with pytest.raises(ValueError, match="allocation"):
        observe_participant(runtime(), "worker", observed.ranks[1], "cpu")
    with pytest.raises(ValueError, match="own prepared runtime/rank"):
        observe_participant(runtime(), "other", observed.ranks[1], "vulkan")
    with pytest.raises(ValueError, match="unavailable"):
        observe_participant({**runtime(), "capabilities": {"backends": ["CPU"]}}, "worker", observed.ranks[1], "vulkan")


@pytest.mark.parametrize("field,changed", [("node_id", "other"), ("runtime_id", "f" * 64),
    ("allocation_digest", "f" * 64), ("source_commit", "f" * 40), ("recipe_digest", "f" * 64)])
def test_manifest_rejects_wrong_signed_participant_identity(observed, field, changed):
    raw = observed.model_dump()
    raw["participant_evidence"]["worker"][field] = changed
    with pytest.raises(ValueError, match="Participant|participant"):
        ExecutionManifest.model_validate(raw)


def test_empty_evidence_preserves_legacy_canonical_manifest_digest():
    from test_prima_lifecycle import manifest

    old = manifest().model_dump()
    assert "participant_evidence" not in old
    parsed = ExecutionManifest.model_validate_json(json.dumps(old))
    assert parsed.digest == body_digest(old)
    assert "participant_evidence" not in json.loads(parsed.model_dump_json())


def test_gpu_driver_capacity_and_duplicate_selection_are_strict(observed):
    raw = observed.participant_evidence["worker"].model_dump()
    for change in ({"physical_index": True}, {"memory_bytes": 0}, {"driver_identity": ""}):
        changed = copy.deepcopy(raw)
        changed["devices"][0].update(change)
        with pytest.raises(ValueError):
            ParticipantEvidence.model_validate(changed)
    raw["devices"] *= 2
    with pytest.raises(ValueError, match="device selection"):
        ParticipantEvidence.model_validate(raw)


def test_receipt_bound_distributed_policy_accepts_only_the_exact_observed_scope(monkeypatch, observed):
    from kllm.modules.primacpp.interactors.recipe import recipe, recipe_digest

    source = recipe()["source_commit"]
    digest = recipe_digest()
    raw = observed.model_dump()
    for evidence in raw["participant_evidence"].values():
        evidence.update(source_commit=source, recipe_digest=digest)
    value = ExecutionManifest.model_validate(raw)
    captured = {**runtime(), "commit": source, "recipe_digest": digest}
    context = qualification.manifest_context(value, captured, devices=[])
    approved = {"schema_version": 1, "scopes": {"windows-vulkan-distributed": {
        "source_commit": source, "recipe_digest": digest,
        "gates": {gate: True for gate in qualification.COMMON_GATES | {"hardware_performance_gate"}},
        "models": [{"sha256": value.assets[0].sha256}], "features": context["features"],
        "placements": ["layer"], "devices": [device()], "receipts": ["isolated-policy-fixture"],
        "execution_scopes": [{"execution": copy.deepcopy(context["execution"]),
            "features": context["features"], "receipts": ["isolated-policy-fixture"]}]}}}
    monkeypatch.setattr(qualification, "policy", lambda: approved)
    assert qualification.qualification_status(captured, context=context)["inference_supported"]
    changed = copy.deepcopy(context)
    changed["execution"]["participants"][1]["host"]["cpu"] = "unmeasured CPU"
    assert "execution_settings_placement_not_qualified" in qualification.qualification_status(
        captured, context=changed)["qualification_errors"]
    changed = copy.deepcopy(context)
    changed["execution"]["participants"] = []
    assert "participant_hardware_evidence_missing" in qualification.qualification_status(
        captured, context=changed)["qualification_errors"]
