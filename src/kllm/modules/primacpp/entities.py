"""Validated execution contracts at the Prima runtime boundary."""

from __future__ import annotations

import ipaddress
from typing import Annotated, Any, Literal
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
    output_gpu: bool = False


    @field_validator("address")
    @classmethod
    def private_address(cls, value: str) -> str:
        address = ipaddress.ip_address(value)
        if (
            address.is_unspecified
            or address.is_multicast
            or not (address.is_private or address.is_loopback)
        ):
            raise ValueError("Prima requires a private LAN, overlay, or loopback address")
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
        if self.output_gpu and (self.rank != 0 or self.role != "master" or len(self.devices) != 1):
            raise ValueError("Output GPU placement requires the rank-zero master's single selected device")
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


class StorageEvidence(Record):
    filesystem_device: str = Field(min_length=1, max_length=128)
    directory: str = Field(min_length=1, max_length=32768)


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
    gpu_layers: int | None = Field(default=None, ge=0)
    cpu_decode_ms: float | None = Field(default=None, gt=0)
    total_layers: int | None = Field(default=None, gt=0)
    output_gpu: bool = False

    kernel_profile_version: Literal[2] | None = None
    kernel_context: int | None = Field(default=None, ge=8)
    kernel_threads: int | None = Field(default=None, ge=1)
    cpu_layer_ms_by_index: list[Annotated[float, Field(gt=0)]] = Field(default_factory=list, max_length=4096)
    gpu_layer_ms_by_index: list[Annotated[float, Field(gt=0)]] = Field(default_factory=list, max_length=4096)
    weights_bytes_by_index: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list, max_length=4096)
    input_ms: float | None = Field(default=None, gt=0)
    output_cpu_ms: float | None = Field(default=None, gt=0)
    output_gpu_ms: float | None = Field(default=None, gt=0)
    gpu_kv_copy_ms: float | None = Field(default=None, gt=0)
    disk_random_read_bytes_s: float | None = Field(default=None, gt=0)
    disk_random_block_bytes: int | None = Field(default=None, ge=4096)
    gpu_transfer_bytes: int | None = Field(default=None, strict=True, gt=0, le=4194304)
    gpu_upload_ms: float | None = Field(default=None, gt=0)
    gpu_download_ms: float | None = Field(default=None, gt=0)
    gpu_logits_bytes: int | None = Field(default=None, strict=True, gt=0, le=4194304)
    gpu_logits_download_ms: float | None = Field(default=None, gt=0)
    storage_identity: StorageEvidence | None = None

    @model_validator(mode="after")
    def complete_kernel_costs(self):
        logits = (self.gpu_logits_bytes, self.gpu_logits_download_ms)
        if any(v is not None for v in logits) and (any(v is None for v in logits)
                or self.kernel_profile_version != 2 or self.backend == "cpu" or self.gpu_logits_bytes % 4
                or self.gpu_transfer_bytes is None):
            raise ValueError("Incomplete or invalid GPU logits transfer profile")
        handoff = (self.gpu_transfer_bytes, self.gpu_upload_ms, self.gpu_download_ms)
        if any(v is not None for v in handoff) and (any(v is None for v in handoff)
                or self.kernel_profile_version != 2 or self.backend == "cpu" or self.gpu_transfer_bytes % 4):
            raise ValueError("Incomplete or invalid GPU host transfer profile")
        if self.kernel_profile_version is not None:
            if (self.measurement_kind != "kernel_estimate" or not self.total_layers
                    or len(self.cpu_layer_ms_by_index) != self.total_layers
                    or len(self.weights_bytes_by_index) != self.total_layers
                    or self.kernel_context is None or self.kernel_threads is None
                    or self.input_ms is None or self.output_cpu_ms is None
                    or self.disk_read_bytes_s is None or self.disk_random_read_bytes_s is None
                    or self.disk_random_block_bytes is None):
                raise ValueError("Incomplete typed kernel profile")
            if self.backend == "cpu":
                if self.gpu_layer_ms_by_index or self.output_gpu_ms is not None or self.gpu_kv_copy_ms is not None:
                    raise ValueError("CPU profile cannot claim GPU costs")
            elif (len(self.gpu_layer_ms_by_index) != self.total_layers
                    or self.output_gpu_ms is None or self.gpu_kv_copy_ms is None):
                raise ValueError("Incomplete typed GPU profile")
        elif self.cpu_layer_ms_by_index or self.gpu_layer_ms_by_index or self.weights_bytes_by_index:
            raise ValueError("Layer cost vectors require typed kernel version")
        return self


class ProfileRequest(Record):
    runtime_id: str = Field(pattern="^[0-9a-f]{64}$")
    model: str = Field(min_length=1, max_length=256)
    context: int = Field(default=4096, ge=8)
    threads: int = Field(default=8, ge=1)
    devices: list[str] | None = None
    backend: Literal["cpu", "vulkan", "metal", "cuda", "rocm"] | None = None
    mode: Literal["kernel", "generation"] | None = None
    gpu_layers: int | None = Field(default=None, ge=0)
    output_gpu: bool = False
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
