"""Cross-platform Prima planning, qualification scope selection and build identity.

Everything here is fixture-based: no GPU, native runtime or second machine is involved.
The Linux and macOS scopes ship without hardware evidence, so these tests prove scope
selection and rejection behavior only. They do not validate Linux or macOS hardware.
"""

import copy

import pytest

from kllm.modules.primacpp.interactors import participants, qualification
from kllm.modules.primacpp.interactors.execution import ExecutionManifest, inventory_identity
from kllm.modules.primacpp.interactors.participants import ParticipantEvidence, require_shared_identity
from kllm.modules.primacpp.interactors.placement import topology_from_plan
from kllm.modules.primacpp.interactors.planner import participant_platforms
from kllm.modules.primacpp.interactors.platforms import normalize_platform, scope_platform
from kllm.modules.primacpp.interactors.recipe import recipe, recipe_digest
from kllm.modules.runtimes.entities import body_digest
# ruff: noqa: F811
from test_prima_participants import device, observed, runtime as participant_runtime  # noqa: F401  (fixtures)
from test_prima_windows import qualified_policy
from test_prima_windows import context as windows_context
from test_prima_windows import runtime as windows_runtime

RUNTIME_ID = "a" * 64


def member(role, address, platform=None, architecture=None, backend="vulkan"):
    value = {"role": role, "rpc_bind": address, "backend": backend, "actual_backend": backend,
             "devices": [{"device": "Vulkan0"}]}
    if platform:
        value["platform"] = platform
    if architecture:
        value["architecture"] = architecture
    return value


def gpu_row(node, start, end, *, local):
    return {"node_id": node, "device": "Vulkan0", "layer_start": start, "layer_end": end,
            "layers": end - start, "memory_kind": "gpu", "local": local, **({} if local else {"rpc_index": 0})}


# --- US1: mixed-OS planning ---------------------------------------------------------

def test_mixed_os_ring_is_planned_and_records_each_node_platform():
    members = {"m": member("master", "10.0.0.1", "Windows", "AMD64"),
               "w1": member("worker", "10.0.0.2", "Linux", "x86_64"),
               "w2": member("worker", "10.0.0.3", "macOS", "arm64")}  # input alias for Darwin
    plan = {"estimated_layers": 6, "cpu_layers": 0, "device_allocations": [
        gpu_row("m", 0, 2, local=True), gpu_row("w1", 2, 4, local=False), gpu_row("w2", 4, 6, local=False)]}
    topology = topology_from_plan(plan, members, RUNTIME_ID)
    assert [rank.node_id for rank in topology.ranks] == ["m", "w1", "w2"]
    assert participant_platforms(topology, members) == {
        "m": {"rank": 0, "platform": "Windows", "architecture": "AMD64", "backend": "vulkan"},
        "w1": {"rank": 1, "platform": "Linux", "architecture": "x86_64", "backend": "vulkan"},
        "w2": {"rank": 2, "platform": "Darwin", "architecture": "arm64", "backend": "vulkan"},
    }


@pytest.mark.parametrize("platform", ["Windows", "Linux", "Darwin"])
def test_coordinator_only_cpu_master_is_accepted_on_every_os(platform):
    members = {"m": member("master", "10.0.0.1", platform, backend="cpu"),
               "w": member("worker", "10.0.0.2", "Linux", "x86_64")}
    plan = {"estimated_layers": 4, "cpu_layers": 0, "device_allocations": [gpu_row("w", 0, 4, local=False)]}
    topology = topology_from_plan(plan, members, RUNTIME_ID)
    master = topology.ranks[0]
    assert master.layers == [] and master.ngl == 0 and not master.devices
    assert participant_platforms(topology, members)["m"]["platform"] == platform


def test_mixed_gpu_backends_are_rejected_naming_both_nodes_and_backends():
    members = {"m": member("master", "10.0.0.1", "Darwin", "arm64", backend="metal"),
               "w": member("worker", "10.0.0.2", "Linux", "x86_64", backend="vulkan")}
    plan = {"estimated_layers": 4, "cpu_layers": 0, "device_allocations": [
        gpu_row("m", 0, 2, local=True), gpu_row("w", 2, 4, local=False)]}
    with pytest.raises(ValueError, match=r"one shared compatible GPU backend.*m \(Darwin, metal\).*w \(Linux, vulkan\)"):
        topology_from_plan(plan, members, RUNTIME_ID)


def test_members_without_platform_still_plan_and_never_become_windows():
    members = {"m": member("master", "10.0.0.1"), "w": member("worker", "10.0.0.2")}
    plan = {"estimated_layers": 4, "cpu_layers": 0, "device_allocations": [
        gpu_row("m", 0, 2, local=True), gpu_row("w", 2, 4, local=False)]}
    topology = topology_from_plan(plan, members, RUNTIME_ID)
    assert all(v["platform"] is None for v in participant_platforms(topology, members).values())


def test_platform_normalization_never_assumes_windows():
    assert [normalize_platform(v) for v in ("Windows", "linux", "Darwin", "macOS", "MACOS")] == [
        "Windows", "Linux", "Darwin", "Darwin", "Darwin"]
    assert [normalize_platform(v) for v in (None, "", "FreeBSD", 7)] == [None] * 4
    assert scope_platform("macOS") == "darwin"


def test_platform_change_makes_a_plan_stale_without_changing_legacy_digests():
    legacy = {"m": member("master", "10.0.0.1")}
    assert inventory_identity(legacy) == inventory_identity({"m": {**legacy["m"], "platform": None, "architecture": None}})
    reported = {"m": member("master", "10.0.0.1", "Windows", "AMD64")}
    assert inventory_identity(reported) != inventory_identity(legacy)
    assert inventory_identity({"m": {**reported["m"], "platform": "Linux"}}) != inventory_identity(reported)
    assert inventory_identity({"m": {**reported["m"], "architecture": "arm64"}}) != inventory_identity(reported)


# --- US2: platform-aware qualification ---------------------------------------------

def runtime_for(platform, backend="vulkan"):
    return {"engine": "primacpp", "platform": platform, "backend": backend,
            "commit": recipe()["source_commit"], "recipe_digest": recipe_digest()}


@pytest.mark.parametrize("topology", ["local", "distributed"])
@pytest.mark.parametrize("platform,backend,prefix", [
    ("Windows", "vulkan", "windows"), ("Linux", "vulkan", "linux"), ("Darwin", "vulkan", "darwin"),
    ("Darwin", "metal+vulkan", "darwin"), ("macOS", "vulkan", "darwin")])
def test_scope_follows_the_nodes_own_platform_and_reports_missing_evidence(platform, backend, prefix, topology):
    status = qualification.qualification_status(runtime_for(platform, backend), context={"topology": topology})
    assert status["scope"] == f"{prefix}-vulkan-{topology}"
    assert status["inference_supported"] is False  # no evidence is recorded for any shipped scope
    errors = status["qualification_errors"]
    assert "qualification_evidence_missing" in errors and "platform_backend_not_qualified" not in errors


def test_shipped_policy_defines_every_platform_scope_without_any_evidence():
    scopes = qualification.policy()["scopes"]
    for prefix in ("windows", "linux", "darwin"):
        for topology in ("local", "distributed"):
            record = scopes[f"{prefix}-vulkan-{topology}"]
            assert record["topology"] == topology and record["backend"] == "vulkan"
            assert not any(record["gates"].values())
            assert not (record["receipts"] or record["models"] or record["devices"] or record["execution_scopes"])


@pytest.mark.parametrize("platform,backend", [("Windows", "cuda"), ("Linux", "metal+vulkan"), ("Darwin", "metal"),
                                              ("Linux", None)])
def test_non_vulkan_build_backends_are_unqualified(platform, backend):
    status = qualification.qualification_status(runtime_for(platform, backend))
    assert not status["inference_supported"]
    assert status["qualification_errors"] == ["platform_backend_not_qualified"]


@pytest.mark.parametrize("platform", [None, "", "FreeBSD"])
def test_unrecognized_platform_is_unqualified_with_an_explicit_reason(platform):
    status = qualification.qualification_status(runtime_for(platform))
    assert status["scope"] is None and not status["inference_supported"]
    assert status["qualification_errors"] == ["platform_backend_not_qualified", "platform_unrecognized"]


def test_windows_evidence_is_never_borrowed_by_another_platform(monkeypatch):
    monkeypatch.setattr(qualification, "policy", qualified_policy)
    assert qualification.qualification_status(windows_runtime(), context=windows_context())["inference_supported"]
    for platform in ("Linux", "Darwin"):
        status = qualification.qualification_status(runtime_for(platform), context=windows_context())
        assert not status["inference_supported"]
        assert status["scope"] == f"{platform.lower()}-vulkan-local"
        assert status["qualification_errors"] == ["platform_backend_not_qualified"]


def distributed_policy(observed_manifest, captured):
    context = qualification.manifest_context(observed_manifest, captured, devices=[])
    source, digest = recipe()["source_commit"], recipe_digest()
    record = {"source_commit": source, "recipe_digest": digest,
              "gates": {gate: True for gate in qualification.COMMON_GATES | {"hardware_performance_gate"}},
              "models": [{"sha256": observed_manifest.assets[0].sha256}], "features": context["features"],
              "placements": ["layer"], "devices": [device()], "receipts": ["isolated-policy-fixture"],
              "execution_scopes": [{"execution": copy.deepcopy(context["execution"]),
                                    "features": context["features"], "receipts": ["isolated-policy-fixture"]}]}
    return context, {"schema_version": 1, "scopes": {"windows-vulkan-distributed": record}}, record


@pytest.fixture
def distributed(monkeypatch, observed):
    source, digest = recipe()["source_commit"], recipe_digest()
    raw = observed.model_dump()
    for evidence in raw["participant_evidence"].values():
        evidence.update(source_commit=source, recipe_digest=digest)
    manifest = ExecutionManifest.model_validate(raw)
    captured = {**participant_runtime(), "commit": source, "recipe_digest": digest}
    context, approved, record = distributed_policy(manifest, captured)
    monkeypatch.setattr(qualification, "policy", lambda: approved)
    return captured, context, approved, record


def test_every_participant_platform_must_have_its_own_qualified_scope(distributed):
    captured, context, approved, record = distributed
    assert qualification.qualification_status(captured, context=context)["inference_supported"]
    mixed = copy.deepcopy(context)
    mixed["participants"][1]["platform"] = "Linux"
    # Windows is qualified, but the Linux participant has no scope in this policy: refuse.
    status = qualification.qualification_status(captured, context=mixed)
    assert not status["inference_supported"]
    assert status["qualification_errors"] == ["linux-vulkan-distributed: platform_backend_not_qualified"]
    # A defined-but-empty Linux scope is also unqualified, with its evidence gaps attributed to it.
    approved["scopes"]["linux-vulkan-distributed"] = {
        "gates": {}, "models": [], "devices": [], "receipts": [], "execution_scopes": []}
    status = qualification.qualification_status(captured, context=mixed)
    assert not status["inference_supported"]
    assert "linux-vulkan-distributed: qualification_evidence_missing" in status["qualification_errors"]
    # Only a fully evidenced Linux scope lets the mixed run qualify.
    approved["scopes"]["linux-vulkan-distributed"] = copy.deepcopy(record)
    assert qualification.qualification_status(captured, context=mixed)["inference_supported"]


def test_unrecognized_participant_platform_blocks_qualification(distributed):
    captured, context, _, _ = distributed
    changed = copy.deepcopy(context)
    changed["participants"][1]["platform"] = "FreeBSD"
    assert "participant_platform_backend_not_qualified" in qualification.qualification_status(
        captured, context=changed)["qualification_errors"]


# --- US3: cross-platform build identity --------------------------------------------

def test_evidence_records_architecture_and_omits_it_when_unset(observed):
    evidence = observed.participant_evidence["worker"]
    raw = evidence.model_dump()
    assert raw["architecture"] == participants.platform.machine()
    legacy = ParticipantEvidence.model_validate({k: v for k, v in raw.items() if k != "architecture"})
    assert "architecture" not in legacy.model_dump()
    assert legacy.digest == body_digest({k: v for k, v in raw.items() if k != "architecture"})
    assert legacy.digest != evidence.digest


def test_same_source_on_different_os_and_architecture_is_accepted(observed):
    raw = observed.model_dump()
    master, worker = (raw["participant_evidence"][r.node_id] for r in observed.ranks)
    master.update(platform="Darwin", architecture="arm64", build_id="mac-build", artifacts_digest="1" * 64)
    worker.update(platform="Linux", architecture="x86_64", build_id="linux-build", artifacts_digest="2" * 64)
    accepted = ExecutionManifest.model_validate(raw)
    assert {n: (e.platform, e.architecture) for n, e in accepted.participant_evidence.items()} == {
        observed.ranks[0].node_id: ("Darwin", "arm64"), observed.ranks[1].node_id: ("Linux", "x86_64")}


def test_differing_recipe_is_rejected_naming_the_node(observed):
    master, worker = (r.node_id for r in observed.ranks)
    raw = observed.model_dump()
    raw["participant_evidence"][worker]["recipe_digest"] = "f" * 64
    with pytest.raises(ValueError, match=rf"identities disagree: {worker} differ from master {master}"):
        ExecutionManifest.model_validate(raw)


@pytest.mark.parametrize("field,message", [("source_commit", "source commit"), ("recipe_digest", "recipe"),
                                           ("runtime_id", "runtime"), ("node_id", "node")])
def test_preparation_names_the_node_and_the_differing_identity(observed, field, message):
    node = observed.ranks[1].node_id
    runtime = {"commit": "b" * 40, "recipe_digest": "c" * 64}
    evidence = observed.participant_evidence[node]
    value = ParticipantEvidence.model_validate({**evidence.model_dump(), "source_commit": "b" * 40,
                                               "recipe_digest": "c" * 64})
    require_shared_identity(value, node, value.runtime_id, runtime)  # matching identity, any OS
    other = {"source_commit": "d" * 40, "recipe_digest": "d" * 64, "runtime_id": "d" * 64, "node_id": "x"}[field]
    changed = ParticipantEvidence.model_validate({**value.model_dump(), field: other})
    with pytest.raises(ValueError, match=rf"^{node}: signed participant evidence has a different {message}$"):
        require_shared_identity(changed, node, value.runtime_id, runtime)
