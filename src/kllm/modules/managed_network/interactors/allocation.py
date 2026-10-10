"""Deterministic proposals and unique usable assignments; proposals need preparation."""

from __future__ import annotations

import ipaddress
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from kllm.modules.managed_network.entities import (
    Digest,
    Identifier,
    NodeAssignment,
    Port,
    Record,
    Revision,
    private_subnet,
)
from kllm.shared.platform.base import PlatformRefusal

CANDIDATES = ("10.10.10.0/24", "192.168.234.0/24", "172.29.234.0/24", "10.234.234.0/24")


class ParticipantSelection(Record):
    node_id: Identifier
    adapter_id: Identifier
    inventory_revision: Digest


class AdapterSelection(Record):
    adapter_id: Identifier
    expected_inventory_revision: Digest


class StartRequest(Record):
    request_id: UUID
    subnet: str | None = None
    participants: list[ParticipantSelection] = Field(min_length=2, max_length=256)
    bridge_port: Port = 50052
    diagnostic_port: Port = 18767

    @field_validator("subnet")
    @classmethod
    def subnet_valid(cls, value: str | None) -> str | None:
        return private_subnet(value) if value is not None else None

    @model_validator(mode="after")
    def unique(self) -> StartRequest:
        if len({p.node_id for p in self.participants}) != len(self.participants):
            raise ValueError("duplicate node selection")
        if (
            self.subnet
            and len(self.participants) > ipaddress.IPv4Network(self.subnet).num_addresses - 2
        ):
            raise ValueError("subnet has insufficient usable addresses")
        if self.bridge_port == self.diagnostic_port:
            raise ValueError("RPC bridge and diagnostics require distinct ports")
        return self


class OperationRequest(Record):
    request_id: UUID
    expected_revision: Revision
    action: str = Field(pattern=r"^(add|retry|stop)$")
    participants: list[ParticipantSelection] = Field(default_factory=list, max_length=256)


class CancelRequest(Record):
    request_id: UUID
    expected_operation_revision: Revision


def reusable_addresses(inventory: dict, subnet: ipaddress.IPv4Network) -> list[str]:
    """Usable static DHCP-disabled addresses already living in this exact subnet."""
    if inventory.get("dhcp") is not False:
        return []
    return [
        str(parsed.ip)
        for value in inventory.get("addresses", [])
        if (parsed := ipaddress.IPv4Interface(value)).network == subnet
        and parsed.network.prefixlen == subnet.prefixlen
        and parsed.ip not in (subnet.network_address, subnet.broadcast_address)
    ]


def _implied_routes(
    subnet: ipaddress.IPv4Network, reusable: list[str]
) -> tuple[ipaddress.IPv4Network, ...]:
    """Routes produced by reusing an adapter's exact static in-subnet addresses."""
    return (
        ()
        if not reusable
        else (
            subnet,
            ipaddress.IPv4Network(f"{subnet.broadcast_address}/32"),
            *(ipaddress.IPv4Network(f"{value}/32") for value in reusable),
        )
    )


def node_acceptable(
    chosen: dict, subnet: ipaddress.IPv4Network, others: list[dict] | None = None
) -> bool:
    """One computer's adapters: reuse its exact static addresses; other interfaces must not overlap.

    Route tables are system-wide on every platform, so the chosen adapter's connected
    routes may also appear under this computer's other adapters; a genuine in-subnet
    address on another interface always blocks reuse.
    """
    reusable = reusable_addresses(chosen, subnet)
    implied = _implied_routes(subnet, reusable)
    for inventory in (chosen, *(others or [])):
        for value in inventory.get("addresses", []):
            observed = ipaddress.IPv4Interface(value)
            if observed.network.overlaps(subnet) and (
                inventory is not chosen or str(observed.ip) not in reusable
            ):
                return False
        for value in inventory.get("routes", []):
            route = ipaddress.IPv4Network(value)
            if route.prefixlen and route.overlaps(subnet) and route not in implied:
                return False
    return True


def acceptable(
    nodes: list[tuple[dict, list[dict]]], subnet: ipaddress.IPv4Network, count: int
) -> bool:
    """Every selected computer accepts the subnet and it holds the requested participants."""
    return count <= subnet.num_addresses - 2 and all(
        node_acceptable(chosen, subnet, others) for chosen, others in nodes
    )


def propose(nodes: list[tuple[dict, list[dict]]], count: int) -> list[str]:
    """Acceptable candidates in preference order; each node is (chosen adapter, other adapters)."""
    result = []
    for candidate in CANDIDATES:
        subnet = ipaddress.IPv4Network(candidate)
        if acceptable(nodes, subnet, count):
            result.append(candidate)
    return result


def assign(
    network_id: UUID,
    subnet_text: str,
    master_id: str,
    selections: list[ParticipantSelection],
    inventories: dict[str, dict],
    existing: list[NodeAssignment] | None = None,
) -> list[NodeAssignment]:
    subnet = ipaddress.IPv4Network(private_subnet(subnet_text))
    if sum(p.node_id == master_id for p in selections) != 1 and not existing:
        raise PlatformRefusal("INVALID_ADAPTER", "Select the local master exactly once")
    old = {a.node_id: a for a in existing or []}
    if len(old) + len([p for p in selections if p.node_id not in old]) > subnet.num_addresses - 2:
        raise PlatformRefusal("SUBNET_CONFLICT", "Subnet has insufficient usable addresses")
    used = {a.address for a in old.values()}
    occupied: dict[str, set[str]] = {}
    compatible: dict[str, list[str]] = {}
    for node_id, observed_inventory in inventories.items():
        for value in observed_inventory.get("addresses", []):
            observed_address = ipaddress.IPv4Interface(value)
            if observed_address.ip in subnet:
                occupied.setdefault(str(observed_address.ip), set()).add(node_id)
                if observed_address.network == subnet and observed_address.ip not in (
                    subnet.network_address,
                    subnet.broadcast_address,
                ):
                    compatible.setdefault(node_id, []).append(str(observed_address.ip))
    if any(len(owners) > 1 for owners in occupied.values()):
        raise PlatformRefusal(
            "ADDRESS_CONFLICT", "Selected adapters already have duplicate addresses in this subnet"
        )
    hosts = iter(subnet.hosts())
    result = list(old.values())
    for selection in sorted(selections, key=lambda p: (p.node_id != master_id, p.node_id)):
        if selection.node_id in old:
            if selection.adapter_id != old[selection.node_id].adapter_id:
                raise PlatformRefusal(
                    "INVALID_ADAPTER",
                    "Existing assignments cannot be silently moved to another adapter",
                )
            continue
        inventory = inventories.get(selection.node_id)
        if (
            inventory is None
            or inventory["adapter_id"] != selection.adapter_id
            or inventory["inventory_revision"] != selection.inventory_revision
        ):
            raise PlatformRefusal("STALE_REVISION", "Refresh the selected participant inventory")
        address = next(
            (
                value
                for value in sorted(
                    compatible.get(selection.node_id, []), key=ipaddress.IPv4Address
                )
                if value not in used
            ),
            None,
        )
        if address is None:
            address = next(
                (
                    str(host)
                    for host in hosts
                    if str(host) not in used and str(host) not in occupied
                ),
                None,
            )
        if address is None:
            raise PlatformRefusal("SUBNET_CONFLICT", "No distinct usable address is available")
        used.add(address)
        result.append(
            NodeAssignment(
                network_id=network_id,
                node_id=selection.node_id,
                adapter_id=selection.adapter_id,
                os_interface=inventory["os_interface"],
                identity_fingerprint=inventory["identity_fingerprint"],
                address=address,
                prefix_length=subnet.prefixlen,
                state="preparing",
                link_speed_mbps=inventory["link_speed_mbps"],
                dhcp_state="disabled"
                if inventory["dhcp"] is False
                else "enabled"
                if inventory["dhcp"] is True
                else "unknown",
                permission_state="granted" if inventory["permission"] else "denied",
            )
        )
    return result
