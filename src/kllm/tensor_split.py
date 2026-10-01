"""Compatibility import surface for tensor split helpers."""

from kllm.core.tensor_split import (
    MIB,
    DeviceRoutingSelection,
    RoutingMode,
    device_can_hold,
    layer_counts_from_weights,
    proportional_weights,
    select_routing_devices,
    tensor_split_proportions,
)

__all__ = [
    "MIB",
    "DeviceRoutingSelection",
    "RoutingMode",
    "device_can_hold",
    "layer_counts_from_weights",
    "proportional_weights",
    "select_routing_devices",
    "tensor_split_proportions",
]
