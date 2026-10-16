"""Validated execution contracts at the Prima runtime boundary."""

from __future__ import annotations

import ipaddress
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class LayerRange(Record):
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.start >= self.end:
            raise ValueError("Layer ranges must be nonempty")
        return self


class RankAllocation(Record):
    node_id: str = Field(min_length=1, max_length=128)
    role: Literal["master", "worker"]
    rank: int = Field(ge=0, lt=32)
    address: str
    next_node_id: str
    layers: list[LayerRange] = Field(default_factory=list)
    ngl: int = Field(ge=0)
    devices: list[str] = Field(default_factory=list)
    tensor_split: list[float] = Field(default_factory=list)

    @field_validator("address")
    @classmethod
    def private_address(cls, value: str) -> str:
        address = ipaddress.ip_address(value)
        if (
            address.is_unspecified
            or address.is_multicast
            or not (address.is_private or address.is_loopback)
        ):
            raise ValueError("Prima requires a private LAN or loopback address")
        return str(address)

    @model_validator(mode="after")
    def offload(self):
        layers = sum(r.end - r.start for r in self.layers)
        if self.ngl > layers:
            raise ValueError("GPU offload exceeds this rank's transformer layer allocation")
        if len(self.devices) != len(self.tensor_split) or len(set(self.devices)) != len(
            self.devices
        ):
            raise ValueError("Device tensor proportions must match unique selected devices")
        if any(v <= 0 for v in self.tensor_split):
            raise ValueError("Selected GPU tensor proportions must be positive")
        if self.ngl and not self.devices:
            raise ValueError("GPU offload requires selected local devices")
        return self


class PrimaTopology(Record):
    schema_version: Literal[1] = 1
    runtime_id: str = Field(pattern="^[0-9a-f]{64}$")
    master_node_id: str
    total_layers: int = Field(gt=0)
    data_port: int = Field(default=9000, ge=1024, le=65504)
    signal_port: int = Field(default=10000, ge=1024, le=65504)
    ranks: list[RankAllocation] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def complete(self):
        size = len(self.ranks)
        if [r.rank for r in self.ranks] != list(range(size)):
            raise ValueError("Prima ranks must be ordered and contiguous from rank zero")
        if len({r.node_id for r in self.ranks}) != size:
            raise ValueError("Prima participants must have unique node IDs")
        if self.ranks[0].node_id != self.master_node_id or self.ranks[0].role != "master":
            raise ValueError("Kluster master must be Prima rank zero")
        if any(r.role != "worker" for r in self.ranks[1:]):
            raise ValueError("Prima must have exactly one master")
        if size > 1 and len({r.address for r in self.ranks}) != size:
            raise ValueError("Distributed ranks require distinct private node addresses")
        if size > 1 and any(ipaddress.ip_address(r.address).is_loopback for r in self.ranks):
            raise ValueError("Distributed ranks cannot use loopback addresses")
        for i, rank in enumerate(self.ranks):
            if rank.next_node_id != self.ranks[(i + 1) % size].node_id:
                raise ValueError("Prima ring must visit each selected participant exactly once")
        ranges = sorted((r.start, r.end) for rank in self.ranks for r in rank.layers)
        end = 0
        for start, next_end in ranges:
            if start != end:
                raise ValueError("Prima layer coverage has a gap or overlap")
            end = next_end
        if end != self.total_layers:
            raise ValueError("Prima placement must cover every transformer layer")
        data = set(range(self.data_port, self.data_port + size))
        signal = set(range(self.signal_port, self.signal_port + size))
        if data & signal:
            raise ValueError("Prima data and signal port ranges overlap")
        return self


class RunManifest(PrimaTopology):
    run_id: str
    model_sha256: str = Field(pattern="^[0-9a-f]{64}$")

    @field_validator("run_id")
    @classmethod
    def run_uuid(cls, value: str) -> str:
        return str(UUID(value))


class DeviceProfile(Record):
    schema_version: Literal[1] = 1
    runtime_id: str = Field(pattern="^[0-9a-f]{64}$")
    model_sha256: str = Field(pattern="^[0-9a-f]{64}$")
    node_id: str = Field(min_length=1)
    hardware_digest: str = Field(pattern="^[0-9a-f]{64}$")
    settings_digest: str = Field(pattern="^[0-9a-f]{64}$")
    backend: str
    measured_at: float = Field(gt=0)
    cpu_layer_ms: float = Field(gt=0)
    gpu_layer_ms: float | None = Field(default=None, gt=0)
    disk_read_bytes_s: float | None = Field(default=None, gt=0)
    memory_available_bytes: int = Field(ge=0)
    measurement_kind: Literal["kernel_estimate", "end_to_end"] = "kernel_estimate"
    device_identity: str = ""
    driver_identity: str = ""
    prefill_ms: float | None = Field(default=None, gt=0)
    decode_ms: float | None = Field(default=None, gt=0)
    transfer_bytes_s: float | None = Field(default=None, gt=0)
    peak_memory_bytes: int | None = Field(default=None, ge=0)


class ProfileRequest(Record):
    runtime_id: str = Field(pattern="^[0-9a-f]{64}$")
    model: str = Field(min_length=1, max_length=256)
    context: int = Field(default=4096, ge=8)
    threads: int = Field(default=8, ge=1)
    request_id: str = Field(pattern="^[0-9a-f-]{36}$")


class PreparationRequest(Record):
    runtime_id: str = Field(pattern="^[0-9a-f]{64}$")
    plan_digest: str = Field(pattern="^[0-9a-f]{64}$")
    request_id: str
    settings: dict[str, Any]
    include_profile: bool = False

    @field_validator("request_id")
    @classmethod
    def request_uuid(cls, value: str) -> str:
        return str(UUID(value))
