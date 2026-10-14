"""Observed participant hardware; signed transport supplies freshness and authority."""

from __future__ import annotations

import platform
from typing import Literal

from pydantic import Field, model_validator

from kllm.modules.primacpp.entities import Record, RankAllocation
from kllm.modules.primacpp.interactors.devices import discover_vulkan_devices, select_vulkan_devices
from kllm.modules.primacpp.interactors.qualification import execution_host
from kllm.modules.runtimes.entities import body_digest


class HostEvidence(Record):
    os: str = Field(min_length=1, max_length=256)
    cpu: str = Field(min_length=1, max_length=256)
    physical_cores: int = Field(gt=0)
    logical_cores: int = Field(gt=0)
    ram_bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def cores(self):
        if self.physical_cores > self.logical_cores:
            raise ValueError("Physical cores exceed logical cores")
        return self


class DeviceEvidence(Record):
    backend: Literal["vulkan"]
    name: str = Field(min_length=1, max_length=128)
    device_identity: str = Field(pattern="^[0-9a-f]{32}$")
    driver_identity: str = Field(min_length=1, max_length=128)
    physical_index: int = Field(ge=0)
    memory_bytes: int = Field(gt=0)


class ParticipantEvidence(Record):
    schema_version: Literal[1] = 1
    kind: Literal["observed_hardware"] = "observed_hardware"
    node_id: str = Field(min_length=1, max_length=128)
    runtime_id: str = Field(pattern="^[0-9a-f]{64}$")
    source_commit: str = Field(pattern="^[0-9a-f]{40}$")
    recipe_digest: str = Field(pattern="^[0-9a-f]{64}$")
    build_id: str = Field(min_length=1, max_length=256)
    artifacts_digest: str = Field(pattern="^[0-9a-f]{64}$")
    platform: Literal["Windows", "Linux", "Darwin"]
    build_backend: str = Field(min_length=1, max_length=64)
    backend: Literal["cpu", "vulkan"]
    allocation_digest: str = Field(pattern="^[0-9a-f]{64}$")
    host: HostEvidence
    devices: list[DeviceEvidence] = Field(max_length=32)

    @model_validator(mode="after")
    def selected(self):
        if (self.backend == "cpu" and self.devices or self.backend == "vulkan" and not self.devices
                or len({d.device_identity for d in self.devices}) != len(self.devices)
                or len({d.physical_index for d in self.devices}) != len(self.devices)):
            raise ValueError("Participant device selection disagrees with its execution backend")
        return self

    @property
    def digest(self):
        return body_digest(self.model_dump())


def observe_participant(runtime, node_id, rank: RankAllocation, backend: str):
    if runtime.get("engine") != "primacpp" or rank.node_id != node_id:
        raise ValueError("Prima inventory requires the node's own prepared runtime/rank")
    if (backend == "cpu" and (rank.ngl or rank.output_gpu or rank.devices)
            or backend == "vulkan" and (not (rank.ngl or rank.output_gpu) or not rank.devices)):
        raise ValueError("Participant backend disagrees with its requested GPU allocation")
    if backend not in ("cpu", "vulkan"):
        raise ValueError("Observed Prima hardware identity for this backend is not implemented")
    if runtime.get("platform") != platform.system():
        raise ValueError("Prepared Prima runtime platform differs from this host")
    devices = []
    if backend == "vulkan":
        if "vulkan" not in {str(b).lower() for b in runtime.get("capabilities", {}).get("backends", [])}:
            raise ValueError("Requested participant Vulkan backend is unavailable")
        selected = select_vulkan_devices(discover_vulkan_devices(runtime), rank.devices)
        devices = [DeviceEvidence(backend="vulkan", **{k: d[k] for k in (
            "name", "device_identity", "driver_identity", "physical_index", "memory_bytes")}) for d in selected]
    artifacts = runtime.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("Prepared Prima artifact identity is unavailable")
    return ParticipantEvidence(node_id=node_id, runtime_id=runtime["runtime_id"],
        source_commit=runtime["commit"], recipe_digest=runtime["recipe_digest"], build_id=runtime["build_id"],
        artifacts_digest=body_digest(artifacts), platform=platform.system(), build_backend=runtime["backend"],
        backend=backend, allocation_digest=body_digest(rank.model_dump()), host=execution_host(), devices=devices)


def require_participant(manifest, node_id, current: ParticipantEvidence):
    expected = manifest.participant_evidence.get(node_id)
    if expected is None or current.digest != expected.digest:
        raise ValueError(f"{node_id}: Prima participant hardware/build changed; prepare a new plan")
