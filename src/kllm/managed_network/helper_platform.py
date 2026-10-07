"""Existing ownership policy with privileged syscalls delegated over local IPC."""

from __future__ import annotations

from dataclasses import replace
import threading
from typing import Any
from uuid import uuid4

from kllm import native
from .helper import HelperClient
from .models import NodeAssignment
from .platform.base import AdapterSnapshot, PlatformAdapter, PlatformRefusal, Prepared
from .speed import MIN_DEDICATED_LINK_SPEED_MBPS


class ConflictHandle:
    def __init__(self, platform: HelperPlatform, network: str, handle: str):
        self.platform, self.network, self.handle = platform, network, handle

    def status(self):
        return tuple(
            self.platform.client.request(
                "conflict_status", network=self.network, handle=self.handle
            )
        )

    def close(self):
        self.platform.client.request("conflict_close", network=self.network, handle=self.handle)


class HelperPlatform(PlatformAdapter):
    def __init__(self, underlying: PlatformAdapter, client: HelperClient, grant: Any):
        self.underlying, self.client, self.grant = underlying, client, grant
        self.runtime = self
        self.context = threading.local()

    def inventory(self):
        grant = self.grant()
        ready = bool(grant and self.client.status(grant).get("ready", False))
        return [replace(snapshot, permission=ready) for snapshot in self.underlying.inventory()]

    def inspect(self, adapter_id: str) -> AdapterSnapshot:
        snapshot = self.underlying.inspect(adapter_id)
        grant = self.grant()
        return replace(snapshot, permission=bool(grant and self.client.status(grant).get("ready")))

    def eligible(self, snapshot: AdapterSnapshot) -> None:
        if not snapshot.permission:
            raise PlatformRefusal("ADMIN_REQUIRED", "Enable administrator access on this computer")
        state = snapshot.state
        if not state.physical_ethernet or not state.connected:
            raise PlatformRefusal("INVALID_ADAPTER", "Connect a dedicated Ethernet adapter")
        if state.speed_mbps is None or state.speed_mbps <= 0:
            raise PlatformRefusal(
                "UNKNOWN_LINK_SPEED", "The adapter’s connection speed could not be verified"
            )
        if state.speed_mbps < MIN_DEDICATED_LINK_SPEED_MBPS:
            raise PlatformRefusal(
                "LINK_TOO_SLOW", "The Ethernet connection must negotiate at least 1 Gbps (1000 Mbps)"
            )
        if state.dhcp is None:
            raise PlatformRefusal("UNKNOWN_DHCP", "The adapter’s configuration could not be read")
        if not native.managed_network_available():
            raise PlatformRefusal(
                "UPGRADE_REQUIRED", "Update Kluster to enable native network support"
            )

    def authorize(self, command: Any) -> None:
        self.context.network = str(command.network_id)
        snapshot = (
            self.inspect(command.body["assignment"]["adapter_id"]).public()
            if command.phase == "prepare"
            else {}
        )
        self.client.request(
            "authorize", network=self.context.network, wire=command._wire, snapshot=snapshot
        )

    def before_add(self, snapshot: AdapterSnapshot, prepared: Prepared) -> AdapterSnapshot:
        self.client.request("configure", network=str(prepared.assignment.network_id))
        current = self.inspect(snapshot.adapter_id)
        if current.state.dhcp is not False:
            raise PlatformRefusal(
                "UNKNOWN_DHCP", "The dedicated adapter could not confirm that DHCP was disabled"
            )
        return current

    def restore_configuration(self, network_id: str) -> None:
        self.client.request("restore", network=network_id)

    def add_exact(self, snapshot: AdapterSnapshot, assignment: NodeAssignment) -> None:
        self.client.request("add", network=str(assignment.network_id))

    def delete_exact(self, snapshot: AdapterSnapshot, assignment: NodeAssignment) -> None:
        self.client.request("delete", network=str(assignment.network_id))

    def check_source_route(self, snapshot: AdapterSnapshot, source: str, peer: str) -> None:
        self.underlying.check_source_route(snapshot, source, peer)

    def address_usable(self, snapshot: AdapterSnapshot, assignment: NodeAssignment) -> bool:
        return self.underlying.address_usable(snapshot, assignment)

    def conflict_preflight(self, snapshot: AdapterSnapshot, address: str) -> None:
        import os

        if os.name == "nt":
            self.underlying.conflict_preflight(snapshot, address)
        elif self.client.request("probe", network=self.context.network)["conflict"]:
            raise PlatformRefusal("ADDRESS_CONFLICT", "Another computer is using this address")

    def start_address_observer(self, interface_index: int, epoch: str):
        network = self.context.network
        return network, self.client.request("observe_start", network=network, epoch=epoch)["handle"]

    def address_observer_events(self, handle):
        return self.client.request("observe_events", network=handle[0], handle=handle[1])

    def stop_address_observer(self, handle):
        self.client.request("observe_close", network=handle[0], handle=handle[1])

    def start_ipv4_conflict_observer(self, interface: str, mac: str, address: str):
        network = self.context.network
        result = self.client.request("conflict_start", network=network, epoch=str(uuid4()))
        return ConflictHandle(self, network, result["handle"])
