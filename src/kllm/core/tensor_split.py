"""VRAM-proportional tensor-split helpers and memory-pooling routing policy.

RPC is for pooling memory when a model exceeds single-device capacity — not for
compute scaling. GGUF stays on the master; workers contribute rpc-server budgets.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from kllm.core.placement_models import PlacementDeviceCandidate
from kllm.exceptions import ValidationError

RoutingMode = Literal["local", "single_remote", "pool"]

MIB = 1024**2


@dataclass(frozen=True)
class DeviceRoutingSelection:
    """Named result of applying the device routing policy."""

    mode: RoutingMode
    devices: tuple[PlacementDeviceCandidate, ...]


def tensor_split_proportions(budgets_mib: Sequence[int | float]) -> list[float]:
    """VRAM-proportional ``--tensor-split`` weights (e.g. ``24, 8``).

    llama.cpp normalizes the values; keep absolute MiB (or any positive scale)
    so heterogeneous GPUs receive layers in proportion to free memory.
    """
    if not budgets_mib or sum(budgets_mib) <= 0:
        raise ValidationError("need positive device budgets")
    if any(b < 0 for b in budgets_mib):
        raise ValidationError("device budgets must be non-negative")
    return [float(b) for b in budgets_mib]


def proportional_weights(devices: Sequence[PlacementDeviceCandidate]) -> list[float]:
    """GiB-scale weights from device ``budget_bytes`` for ``--tensor-split``.

    Falls back to MiB (then raw bytes) when every device is below 1 GiB so small
    unit-test budgets still produce positive proportions.
    """
    gib = 1024**3
    mib = 1024**2
    budgets = [max(0, d.budget_bytes) // gib for d in devices]
    if sum(budgets) <= 0:
        budgets = [max(0, d.budget_bytes) // mib for d in devices]
    if sum(budgets) <= 0:
        budgets = [max(0, d.budget_bytes) for d in devices]
    return tensor_split_proportions(budgets)


def layer_counts_from_weights(weights: list[float], n_layers: int) -> list[int]:
    """Map proportional weights to contiguous integer layer counts summing to ``n_layers``."""
    if n_layers < 0:
        raise ValidationError("n_layers must be non-negative")
    if n_layers == 0:
        return [0] * len(weights)
    if not weights or sum(weights) <= 0:
        raise ValidationError("need positive weights")
    total = sum(weights)
    raw = [n_layers * w / total for w in weights]
    counts = [int(x) for x in raw]
    # Distribute remainder to largest fractional parts (largest-remainder method).
    remainder = n_layers - sum(counts)
    order = sorted(
        range(len(weights)), key=lambda i: (raw[i] - counts[i], weights[i]), reverse=True
    )
    for i in order[:remainder]:
        counts[i] += 1
    return counts


def device_can_hold(device: PlacementDeviceCandidate, *, model_bytes: int, overhead: int) -> bool:
    """True if one accelerator's budget covers all layer costs plus overhead."""
    return device.budget_bytes >= model_bytes + overhead


def select_routing_devices(
    devices: Sequence[PlacementDeviceCandidate],
    *,
    model_bytes: int,
    overhead: int,
    cpu_base: int,
    cpu_budget: int,
    locals_fit: bool,
) -> DeviceRoutingSelection:
    """Choose candidate devices under the memory-pooling routing policy.

    ``locals_fit`` is True when contiguous DP over master-local devices alone
    can place the model (caller runs that check). Prefer:

    1. ``local`` — master-local only (no ``--rpc``)
    2. ``single_remote`` — one remote accelerator that alone holds the model
    3. ``pool`` — all devices, ordered by score then budget (heterogeneous-safe)
    """
    locals_only = [d for d in devices if d.local]
    remotes = [d for d in devices if not d.local]

    if cpu_base <= cpu_budget and locals_fit and locals_only:
        return DeviceRoutingSelection("local", tuple(locals_only))

    if cpu_base <= cpu_budget:
        # Prefer a single remote that can hold the full model over multi-node pooling.
        fitting_remote = next(
            (
                d
                for d in sorted(remotes, key=lambda x: -x.budget_bytes)
                if device_can_hold(d, model_bytes=model_bytes, overhead=overhead)
            ),
            None,
        )
        if fitting_remote is not None:
            return DeviceRoutingSelection("single_remote", (fitting_remote,))

    # Pool: keep score order (caller already sorted); break ties by larger budget first.
    pooled = sorted(devices, key=lambda d: (-d.score, -d.budget_bytes))
    return DeviceRoutingSelection("pool", tuple(pooled))
