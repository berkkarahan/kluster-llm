"""Immutable result models for device placement."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any, Literal

from kllm.exceptions import ValidationError


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return {_thaw(item) for item in value}
    return value


@dataclass(frozen=True)
class PlacementSettings:
    """Validated, immutable launch inputs consumed by the placement planner."""

    parallel_slots: int = 1
    batch_size: int = 512
    micro_batch: int = 128
    context: int = 4096
    cache_type_k: str = "q8_0"
    cache_type_v: str = "q8_0"
    flash_attention: str = "auto"
    checkpoints: int | str = 32
    split_mode: str = "layer"
    kv_unified: bool = False
    swa_full: bool = False
    speculative: str = "auto"
    draft_max: int = 3
    draft_cache_type_k: str = "f16"
    draft_cache_type_v: str = "f16"
    draft_size_bytes: int = 0
    draft_layout: ModelLayout | None = None
    vision_runtime: bool = False
    estimate_warnings: tuple[str, ...] = ()
    auxiliary_weights_bytes: int = 0
    auxiliary_bytes: int = 0
    requested_batch_size: int | None = None
    requested_micro_batch: int | None = None
    device_count: int = 0
    moe_offload: int | str = "auto"

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> PlacementSettings:
        """Adapt the existing request mapping into the allocator's typed input."""
        warnings = values.get("estimate_warnings", ())
        if not isinstance(warnings, (list, tuple)):
            warnings = ()
        draft_layout = values.get("_draft_layout")
        return cls(
            parallel_slots=int(values.get("parallel_slots", 1)),
            batch_size=int(values.get("batch_size", 512)),
            micro_batch=int(values.get("micro_batch", 128)),
            context=int(values.get("context", 4096)),
            cache_type_k=str(values.get("cache_type_k", "q8_0")),
            cache_type_v=str(values.get("cache_type_v", "q8_0")),
            flash_attention=str(values.get("flash_attention", "auto")),
            checkpoints=values.get("checkpoints", 32),
            split_mode=str(values.get("split_mode", "layer")),
            kv_unified=bool(values.get("kv_unified", False)),
            swa_full=bool(values.get("swa_full", False)),
            speculative=str(values.get("speculative", "auto")),
            draft_max=int(values.get("draft_max", 3)),
            draft_cache_type_k=str(values.get("draft_cache_type_k", "f16")),
            draft_cache_type_v=str(values.get("draft_cache_type_v", "f16")),
            draft_size_bytes=int(values.get("draft_size_bytes", 0)),
            draft_layout=ModelLayout.from_mapping(draft_layout)
            if isinstance(draft_layout, Mapping)
            else None,
            vision_runtime=bool(values.get("vision_runtime", False)),
            estimate_warnings=tuple(str(item) for item in warnings),
            auxiliary_weights_bytes=int(values.get("auxiliary_weights_bytes", 0)),
            auxiliary_bytes=int(values.get("auxiliary_bytes", 0)),
            requested_batch_size=(
                int(values["requested_batch_size"])
                if values.get("requested_batch_size") is not None
                else None
            ),
            requested_micro_batch=(
                int(values["requested_micro_batch"])
                if values.get("requested_micro_batch") is not None
                else None
            ),
            device_count=int(values.get("device_count", 0)),
            moe_offload=(
                values["moe_offload"]
                if values.get("moe_offload") == "auto"
                else int(values.get("moe_offload", "auto"))
            ),
        )

    def to_mapping(self, *, device_count: int | None = None) -> dict[str, Any]:
        """Serialize settings for legacy API and launch payload boundaries."""
        values: dict[str, Any] = {
            "parallel_slots": self.parallel_slots,
            "batch_size": self.batch_size,
            "micro_batch": self.micro_batch,
            "context": self.context,
            "cache_type_k": self.cache_type_k,
            "cache_type_v": self.cache_type_v,
            "flash_attention": self.flash_attention,
            "checkpoints": self.checkpoints,
            "split_mode": self.split_mode,
            "kv_unified": self.kv_unified,
            "swa_full": self.swa_full,
            "speculative": self.speculative,
            "draft_max": self.draft_max,
            "draft_cache_type_k": self.draft_cache_type_k,
            "draft_cache_type_v": self.draft_cache_type_v,
            "draft_size_bytes": self.draft_size_bytes,
            "_draft_layout": (
                self.draft_layout.to_mapping() if self.draft_layout is not None else None
            ),
            "vision_runtime": self.vision_runtime,
            "estimate_warnings": list(self.estimate_warnings),
            "auxiliary_weights_bytes": self.auxiliary_weights_bytes,
            "auxiliary_bytes": self.auxiliary_bytes,
            "device_count": self.device_count if device_count is None else device_count,
            "moe_offload": self.moe_offload,
        }
        if self.requested_batch_size is not None:
            values["requested_batch_size"] = self.requested_batch_size
        if self.requested_micro_batch is not None:
            values["requested_micro_batch"] = self.requested_micro_batch
        return values


@dataclass(frozen=True)
class MemoryEstimate:
    """Structured model memory requirements produced by the estimator."""

    costs: tuple[int, ...]
    input_bytes: int
    kv_bytes: int
    state_bytes: int
    checkpoint_bytes: int
    overhead: int
    weights_bytes: int
    auxiliary_weights_bytes: int
    auxiliary_runtime_bytes: int
    compute_bytes: int
    confidence: str
    warnings: tuple[str, ...]
    complete: bool
    architecture: str
    expert_costs: tuple[int, ...] = ()
    expert_bytes: int = 0


@dataclass(frozen=True)
class TensorLayout:
    """Tensor span metadata needed for layer and output memory pricing."""

    name: str
    dimensions: tuple[int, ...]
    dtype: int
    offset: int
    size_bytes: int


@dataclass(frozen=True)
class ModelLayout:
    """Immutable GGUF metadata relevant to placement and memory estimation."""

    architecture: str
    metadata: Mapping[str, Any]
    layers: tuple[int, ...]
    output_bytes: int
    input_bytes: int
    tensors: tuple[TensorLayout, ...]
    confidence: Literal["metadata", "approximate"]
    expert_layers: tuple[int, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> ModelLayout | None:
        """Normalize the GGUF reader payload at the planning boundary."""
        if value is None:
            return None
        architecture = value.get("architecture")
        metadata = value.get("metadata")
        layers = value.get("layers")
        raw_tensors = value.get("tensors", ())
        if not isinstance(architecture, str) or not isinstance(metadata, Mapping):
            raise ValidationError("model layout requires architecture and metadata")
        if not isinstance(layers, (list, tuple)) or not isinstance(raw_tensors, (list, tuple)):
            raise ValidationError("model layout layers and tensors must be sequences")
        tensors: list[TensorLayout] = []
        for raw_tensor in raw_tensors:
            if not isinstance(raw_tensor, Mapping):
                raise ValidationError("model layout contains an invalid tensor entry")
            dimensions = raw_tensor.get("dimensions")
            name = raw_tensor.get("name")
            if not isinstance(name, str) or not isinstance(dimensions, (list, tuple)):
                raise ValidationError("tensor dimensions must be a sequence")
            try:
                tensor = TensorLayout(
                    name=name,
                    dimensions=tuple(int(dimension) for dimension in dimensions),
                    dtype=int(raw_tensor["dtype"]),
                    offset=int(raw_tensor["offset"]),
                    size_bytes=int(raw_tensor["size_bytes"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError("model layout contains invalid tensor metadata") from exc
            if min(tensor.dtype, tensor.offset, tensor.size_bytes, *tensor.dimensions) < 0:
                raise ValidationError("tensor dimensions and byte spans must be non-negative")
            tensors.append(tensor)
        confidence = value.get("confidence", "approximate")
        if confidence not in ("metadata", "approximate"):
            raise ValidationError("model layout confidence must be metadata or approximate")
        try:
            normalized_layers = tuple(int(weight) for weight in layers)
            output_bytes = int(value.get("output_bytes", 0))
            input_bytes = int(value.get("input_bytes", 0))
        except (TypeError, ValueError) as exc:
            raise ValidationError("model layout contains invalid byte estimates") from exc
        if min(output_bytes, input_bytes, *normalized_layers) < 0:
            raise ValidationError("model layout byte estimates must be non-negative")
        raw_expert = value.get("expert_layers", ())
        if raw_expert is None:
            raw_expert = ()
        if not isinstance(raw_expert, (list, tuple)):
            raise ValidationError("model layout expert_layers must be a sequence")
        try:
            normalized_expert = tuple(int(weight) for weight in raw_expert)
        except (TypeError, ValueError) as exc:
            raise ValidationError("model layout expert_layers contains invalid byte estimates") from exc
        if normalized_expert and len(normalized_expert) != len(normalized_layers):
            raise ValidationError("model layout expert_layers must match layer count")
        if any(weight < 0 or weight > total for weight, total in zip(normalized_expert, normalized_layers)):
            raise ValidationError("model layout expert bytes must be non-negative and within layer totals")
        return cls(
            architecture=architecture,
            metadata=_freeze(metadata),
            layers=normalized_layers,
            output_bytes=output_bytes,
            input_bytes=input_bytes,
            tensors=tuple(tensors),
            confidence=confidence,
            expert_layers=normalized_expert,
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialize back to the GGUF-reader shape at adapter boundaries."""
        return {
            "architecture": self.architecture,
            "metadata": _thaw(self.metadata),
            "layers": list(self.layers),
            "expert_layers": list(self.expert_layers),
            "output_bytes": self.output_bytes,
            "input_bytes": self.input_bytes,
            "tensors": [
                {
                    "name": tensor.name,
                    "dimensions": tensor.dimensions,
                    "dtype": tensor.dtype,
                    "offset": tensor.offset,
                    "size_bytes": tensor.size_bytes,
                }
                for tensor in self.tensors
            ],
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class PlacementDeviceInput:
    """One discovered accelerator as required by the allocation policy."""

    device: str | None
    backend: str | None
    vram_bytes: int
    free_bytes: int
    reclaimable_bytes: int
    index: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> PlacementDeviceInput:
        total = value.get("vram_bytes") or 0
        return cls(
            index=int(value.get("index", 0)),
            device=str(value["device"]) if value.get("device") else None,
            backend=str(value["backend"]) if value.get("backend") else None,
            vram_bytes=int(total),
            free_bytes=int(value.get("free_bytes", total) or 0),
            reclaimable_bytes=int(value.get("reclaimable_bytes") or 0),
        )


@dataclass(frozen=True)
class RpcDeviceInput:
    """A device in the exact order exposed by a running worker RPC server."""

    index: int
    device: str
    backend: str
    memory_kind: Literal["gpu", "ram"]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> RpcDeviceInput:
        index, device = value.get("index"), value.get("device")
        kind, backend = value.get("memory_kind"), value.get("backend")
        if type(index) is not int or index < 0 or not isinstance(device, str) or not device:
            raise ValidationError("RPC inventory requires a device name and nonnegative index")
        if kind not in ("gpu", "ram") or not isinstance(backend, str) or not backend:
            raise ValidationError("RPC inventory requires backend and gpu|ram memory_kind")
        if (kind == "ram") != (backend == "cpu"):
            raise ValidationError("RPC CPU devices must use ram memory_kind")
        return cls(index, device, backend, kind)

    @classmethod
    def parse_inventory(cls, value: Any) -> tuple[RpcDeviceInput, ...]:
        if not isinstance(value, (list, tuple)) or any(not isinstance(d, Mapping) for d in value):
            raise ValidationError("RPC inventory must be a list of devices")
        devices = tuple(cls.from_mapping(d) for d in value)
        if [d.index for d in devices] != list(range(len(devices))) or len({d.device for d in devices}) != len(devices):
            raise ValidationError("RPC inventory must have unique devices in runtime order")
        return devices


@dataclass(frozen=True)
class PlacementNodeInput:
    """Node inventory facts used to budget host RAM and accelerators."""

    node_id: str
    is_master: bool
    backend: str
    total_ram_bytes: int
    available_ram_bytes: int
    reclaimable_ram_bytes: int
    unified_memory: bool
    rpc_endpoint: str | None
    latency_ms: float
    devices: tuple[PlacementDeviceInput, ...]
    rpc_devices: tuple[RpcDeviceInput, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> PlacementNodeInput:
        node_id = value.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            raise ValidationError("placement node is missing node_id")
        raw_devices = value.get("devices")
        if raw_devices is None:
            raw_devices = ()
        if not isinstance(raw_devices, (list, tuple)):
            raise ValidationError(f"{node_id}: device inventory must be a list")
        if any(not isinstance(device, Mapping) for device in raw_devices):
            raise ValidationError(f"{node_id}: device inventory contains an invalid entry")
        devices = tuple(PlacementDeviceInput.from_mapping(device) for device in raw_devices)
        rpc_devices = RpcDeviceInput.parse_inventory(value.get("rpc_devices") or ())
        return cls(
            node_id=node_id,
            is_master=bool(value.get("is_master")),
            backend=str(value.get("backend") or "unknown"),
            total_ram_bytes=int(value.get("total_ram_bytes") or 0),
            available_ram_bytes=int(value.get("available_ram_bytes") or 0),
            reclaimable_ram_bytes=int(value.get("reclaimable_ram_bytes") or 0),
            unified_memory=bool(value.get("unified_memory")),
            rpc_endpoint=(
                str(value["rpc_endpoint"]) if value.get("rpc_endpoint") is not None else None
            ),
            latency_ms=float(value.get("latency_ms") or 0),
            devices=devices,
            rpc_devices=rpc_devices,
        )


@dataclass(frozen=True)
class NodeByteValue:
    """Byte measurement associated with one cluster node."""

    node_id: str
    value: int


@dataclass(frozen=True)
class DeviceAllocation:
    """Contiguous model-layer assignment to one local or RPC device."""

    node_id: str
    device: str
    backend: str
    local: bool
    budget_bytes: int
    latency_ms: float
    rpc_endpoint: str | None
    rpc_index: int
    rpc_device_count: int
    unified_memory: bool
    score: float
    layer_start: int
    layer_end: int
    layers: int
    output_layer: bool
    estimated_bytes: int
    memory_kind: Literal["gpu", "ram"] = "gpu"


@dataclass(frozen=True)
class PlacementDeviceCandidate:
    """Budget and routing facts for one accelerator considered by placement."""

    node_id: str
    device: str
    backend: str
    local: bool
    budget_bytes: int
    latency_ms: float
    rpc_endpoint: str | None
    rpc_index: int
    rpc_device_count: int
    unified_memory: bool
    score: float = 0.0
    memory_kind: Literal["gpu", "ram"] = "gpu"

    def allocate(
        self, *, layer_start: int, layer_end: int, estimated_bytes: int, total_layers: int
    ) -> DeviceAllocation:
        """Create the immutable layer allocation for this candidate."""
        return DeviceAllocation(
            node_id=self.node_id,
            device=self.device,
            backend=self.backend,
            local=self.local,
            budget_bytes=self.budget_bytes,
            latency_ms=self.latency_ms,
            rpc_endpoint=self.rpc_endpoint,
            rpc_index=self.rpc_index,
            rpc_device_count=self.rpc_device_count,
            unified_memory=self.unified_memory,
            score=self.score,
            layer_start=layer_start,
            layer_end=layer_end,
            layers=layer_end - layer_start,
            output_layer=layer_end == total_layers,
            estimated_bytes=estimated_bytes,
            memory_kind=self.memory_kind,
        )


@dataclass(frozen=True)
class NodeDeviceAssignment:
    """Effective device mode selected for a cluster node."""

    node_id: str
    effective: Literal["gpu", "cpu"]
    fallback: bool


@dataclass(frozen=True)
class DevicePlacementPlan:
    """Structured placement result, serialized only by its caller at the API edge."""

    node_ids: tuple[str, ...]
    node_alloc_bytes: tuple[int, ...]
    distributable_bytes: int
    node_workload_bytes: tuple[NodeByteValue, ...]
    node_gpu_budget_bytes: tuple[NodeByteValue, ...]
    node_target_bytes: tuple[NodeByteValue, ...] | None
    budgets_bytes: tuple[int, ...]
    tensor_split: tuple[float, ...]
    tensor_split_csv: str
    device_allocations: tuple[DeviceAllocation, ...]
    device_order: tuple[str, ...]
    rpc_endpoints: tuple[str, ...]
    routing_mode: Literal["manual", "local", "single_remote", "pool"]
    cpu_layers: int
    cpu_alloc_bytes: int
    ngl: int
    estimated_layers: int
    model_size_bytes: int
    context_effective: int
    kv_estimate_bytes: int
    recurrent_state_bytes: int
    checkpoint_estimate_bytes: int
    weights_estimate_bytes: int
    auxiliary_weights_bytes: int
    compute_estimate_bytes: int
    auxiliary_runtime_bytes: int
    execution_overhead_bytes: int
    estimate_complete: bool
    estimate_warnings: tuple[str, ...]
    estimated_bytes: int
    total_budget_bytes: int
    budget_bytes: int
    estimate_confidence: str
    architecture: str
    split_mode: str
    device_requested: str
    device_effective: str
    node_devices: tuple[NodeDeviceAssignment, ...]
    node_scores: tuple[float, ...]
    node_latency_ms: tuple[float, ...]
    vram_alloc_bytes: int
    ram_alloc_bytes: int
    fit_status: Literal["overflow", "tight", "fits"]
    hint: str
    node_ram_budget_bytes: tuple[NodeByteValue, ...] = ()
    node_ram_alloc_bytes: tuple[NodeByteValue, ...] = ()
    node_vram_alloc_bytes: tuple[NodeByteValue, ...] = ()
    required_nodes: tuple[str, ...] = ()
    node_priority: tuple[str, ...] = ()
    expert_offloaded: bool = False
    expert_strategy: str = "none"
    expert_offload_count: int | None = None
    tensor_overrides: tuple[str, ...] = ()
    n_cpu_moe: int | None = None

    def to_payload(self) -> dict[str, object]:
        """Convert this domain result to the existing JSON response contract."""
        return {
            "required_nodes": list(self.required_nodes),
            "node_priority": list(self.node_priority),
            "node_ram_budget_bytes": {row.node_id: row.value for row in self.node_ram_budget_bytes},
            "node_ram_alloc_bytes": {row.node_id: row.value for row in self.node_ram_alloc_bytes},
            "node_vram_alloc_bytes": {row.node_id: row.value for row in self.node_vram_alloc_bytes},
            "node_ids": list(self.node_ids),
            "node_alloc_bytes": list(self.node_alloc_bytes),
            "distributable_bytes": self.distributable_bytes,
            "node_workload_bytes": {row.node_id: row.value for row in self.node_workload_bytes},
            "node_gpu_budget_bytes": {
                row.node_id: row.value for row in self.node_gpu_budget_bytes
            },
            "node_target_bytes": (
                {row.node_id: row.value for row in self.node_target_bytes}
                if self.node_target_bytes is not None
                else None
            ),
            "budgets_bytes": list(self.budgets_bytes),
            "tensor_split": list(self.tensor_split),
            "tensor_split_csv": self.tensor_split_csv,
            "device_allocations": [asdict(row) for row in self.device_allocations],
            "device_order": list(self.device_order),
            "rpc_endpoints": list(self.rpc_endpoints),
            "routing_mode": self.routing_mode,
            "cpu_layers": self.cpu_layers,
            "cpu_alloc_bytes": self.cpu_alloc_bytes,
            "ngl": self.ngl,
            "estimated_layers": self.estimated_layers,
            "model_size_bytes": self.model_size_bytes,
            "context_effective": self.context_effective,
            "kv_estimate_bytes": self.kv_estimate_bytes,
            "recurrent_state_bytes": self.recurrent_state_bytes,
            "checkpoint_estimate_bytes": self.checkpoint_estimate_bytes,
            "weights_estimate_bytes": self.weights_estimate_bytes,
            "auxiliary_weights_bytes": self.auxiliary_weights_bytes,
            "compute_estimate_bytes": self.compute_estimate_bytes,
            "auxiliary_runtime_bytes": self.auxiliary_runtime_bytes,
            "execution_overhead_bytes": self.execution_overhead_bytes,
            "estimate_complete": self.estimate_complete,
            "estimate_warnings": list(self.estimate_warnings),
            "estimated_bytes": self.estimated_bytes,
            "total_budget_bytes": self.total_budget_bytes,
            "budget_bytes": self.budget_bytes,
            "estimate_confidence": self.estimate_confidence,
            "architecture": self.architecture,
            "split_mode": self.split_mode,
            "device_requested": self.device_requested,
            "device_effective": self.device_effective,
            "node_devices": [asdict(row) for row in self.node_devices],
            "node_scores": list(self.node_scores),
            "node_latency_ms": list(self.node_latency_ms),
            "vram_alloc_bytes": self.vram_alloc_bytes,
            "ram_alloc_bytes": self.ram_alloc_bytes,
            "fit_status": self.fit_status,
            "hint": self.hint,
            "expert_offloaded": self.expert_offloaded,
            "expert_strategy": self.expert_strategy,
            "expert_offload_count": self.expert_offload_count,
            "tensor_overrides": list(self.tensor_overrides),
            "n_cpu_moe": self.n_cpu_moe,
        }
