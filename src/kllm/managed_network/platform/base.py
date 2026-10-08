"""Exact temporary changes with eligibility, barriers and conservative ownership."""

from __future__ import annotations

import ctypes
import ipaddress
import os
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence
from uuid import uuid4

from kllm import native
from ..models import LocalMutationJournal, NodeAssignment
from ..speed import MIN_DEDICATED_LINK_SPEED_MBPS
from ..store import digest
from .inspection import InterfaceState, NetworkError


class PlatformRefusal(NetworkError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def run_fixed(args: Sequence[str], *, env: dict[str, str] | None = None) -> str:
    """No shell, <=5 seconds, <=64 KiB combined output, deterministic locale."""
    environment = os.environ.copy()
    environment.update({"LC_ALL": "C", "LANG": "C"})
    if env:
        environment.update(env)
    try:
        child = subprocess.Popen(
            list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment
        )
    except OSError:
        raise PlatformRefusal(
            "UNSUPPORTED_CAPABILITY", "Required platform command is unavailable"
        ) from None
    output = bytearray()
    oversized = threading.Event()

    def collect() -> None:
        assert child.stdout is not None
        while chunk := child.stdout.read(4096):
            if len(output) + len(chunk) > 65536:
                oversized.set()
                child.kill()
                break
            output.extend(chunk)

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=2)
        raise PlatformRefusal("TIMEOUT", "Platform command exceeded its deadline") from None
    finally:
        reader.join(timeout=2)
        if child.stdout:
            child.stdout.close()
    if oversized.is_set() or reader.is_alive():
        raise PlatformRefusal("UNSUPPORTED_CAPABILITY", "Platform output exceeded its bound")
    if child.returncode:
        raise PlatformRefusal(
            "UNSUPPORTED_CAPABILITY", "Platform command failed; inspect local configuration"
        )
    return output.decode("utf-8", errors="strict")


def is_admin() -> bool:
    if os.name == "nt":
        try:
            return bool(getattr(ctypes, "windll").shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False
    return os.geteuid() == 0


@dataclass(frozen=True)
class AdapterSnapshot:
    adapter_id: str
    os_interface: str
    interface_index: int
    fingerprint: str
    mac: str
    state: InterfaceState
    facts: dict[str, Any] = field(default_factory=dict)
    permission: bool | None = None

    def public(self) -> dict[str, Any]:
        data = {
            "adapter_id": self.adapter_id,
            "os_interface": self.os_interface,
            "interface_index": self.interface_index,
            "identity_fingerprint": self.fingerprint,
            "mac": self.mac,
            "connected": self.state.connected,
            "link_speed_mbps": self.state.speed_mbps,
            "dhcp": self.state.dhcp,
            "permission": is_admin() if self.permission is None else self.permission,
            "physical_ethernet": self.state.physical_ethernet,
            "addresses": [str(a) for a in self.state.addresses],
            "routes": [str(r) for r in self.state.routes],
            "facts": {
                **self.facts,
                **({"default_route": True} if self.state.default_route else {}),
            },
        }
        data["inventory_revision"] = digest(data)
        return data


@dataclass
class Prepared:
    assignment: NodeAssignment
    snapshot: AdapterSnapshot
    snapshot_digest: str
    observer: Any
    epoch: str
    high_water: int
    user_owned: bool
    deadline: float
    conflict_observer: Any = None
    owns_resource: bool = False
    ownership_conflict: bool = False


class PlatformAdapter(ABC):
    runtime: Any = native

    def authorize(self, command: Any) -> None:
        """Optional privileged boundary; manual adapters execute in the agent."""

    def before_add(self, snapshot: AdapterSnapshot, prepared: Prepared) -> AdapterSnapshot:
        return snapshot

    def restore_configuration(self, network_id: str) -> None:
        """Manual adapters never change DHCP configuration."""

    @abstractmethod
    def inventory(self) -> list[AdapterSnapshot]: ...

    @abstractmethod
    def inspect(self, adapter_id: str) -> AdapterSnapshot: ...

    @abstractmethod
    def add_exact(self, snapshot: AdapterSnapshot, assignment: NodeAssignment) -> None: ...

    @abstractmethod
    def delete_exact(self, snapshot: AdapterSnapshot, assignment: NodeAssignment) -> None: ...

    @abstractmethod
    def check_source_route(self, snapshot: AdapterSnapshot, source: str, peer: str) -> None: ...

    def conflict_preflight(self, snapshot: AdapterSnapshot, address: str) -> None:
        if self.runtime.probe_ipv4_conflict(snapshot.os_interface, snapshot.mac, address):
            raise PlatformRefusal(
                "ADDRESS_CONFLICT", "Another Ethernet peer claims the proposed address"
            )

    def eligible(self, snapshot: AdapterSnapshot) -> None:
        state = snapshot.state
        if not is_admin():
            raise PlatformRefusal(
                "INSUFFICIENT_PERMISSION", "Agent requires root/administrator privileges"
            )
        if not state.physical_ethernet or not state.connected:
            raise PlatformRefusal("INVALID_ADAPTER", "Select a connected physical Ethernet adapter")
        if state.speed_mbps is None or state.speed_mbps <= 0:
            raise PlatformRefusal("UNKNOWN_LINK_SPEED", "Negotiated speed is unknown")
        if state.speed_mbps < MIN_DEDICATED_LINK_SPEED_MBPS:
            raise PlatformRefusal(
                "LINK_TOO_SLOW", "Negotiated speed must be at least 1000 Mbps (1 Gbps)"
            )
        if state.dhcp is not False:
            raise PlatformRefusal("DHCP_ENABLED", "DHCP must be proved disabled before any change")
        if not native.managed_network_available():
            raise PlatformRefusal(
                "UNSUPPORTED_CAPABILITY", "Native observation and conflict probing are required"
            )

    def prepare(
        self, assignment: NodeAssignment, inventory_revision: str, management_hosts: list[str]
    ) -> Prepared:
        snapshot = self.inspect(assignment.adapter_id)
        self.eligible(snapshot)
        if (
            snapshot.public()["inventory_revision"] != inventory_revision
            or snapshot.fingerprint != assignment.identity_fingerprint
            or snapshot.os_interface != assignment.os_interface
        ):
            raise PlatformRefusal(
                "STALE_REVISION", "Adapter inventory changed; refresh the selection"
            )
        subnet = ipaddress.IPv4Network(
            f"{assignment.address}/{assignment.prefix_length}", strict=False
        )
        for host in management_hosts:
            try:
                address = ipaddress.IPv4Address(host)
            except ValueError:
                # Resolve management names before invoking platform preparation.
                raise PlatformRefusal(
                    "MANAGEMENT_ROUTE_CONFLICT",
                    "Management path must be resolved before preparation",
                ) from None
            if address in subnet:
                raise PlatformRefusal(
                    "MANAGEMENT_ROUTE_CONFLICT", "Dedicated subnet overlaps the management path"
                )
        existing = ipaddress.IPv4Interface(f"{assignment.address}/{assignment.prefix_length}")
        user_owned = existing in snapshot.state.addresses
        for address in snapshot.state.addresses:
            if address.ip == existing.ip and address != existing:
                raise PlatformRefusal(
                    "ADDRESS_CONFLICT", "Address already exists with another prefix"
                )
            if address.network.overlaps(subnet) and address != existing:
                raise PlatformRefusal(
                    "SUBNET_CONFLICT", "Selected adapter has an overlapping address"
                )
        for route in snapshot.state.routes:
            # Connected/local routes for an exact pre-existing address are preserved.
            compatible = user_owned and route in (
                subnet,
                ipaddress.IPv4Network(f"{assignment.address}/32"),
                ipaddress.IPv4Network(f"{subnet.broadcast_address}/32"),
            )
            if route.prefixlen and route.overlaps(subnet) and not compatible:
                raise PlatformRefusal("SUBNET_CONFLICT", "Subnet overlaps an existing route")
        epoch = str(uuid4())
        observer = self.runtime.start_address_observer(snapshot.interface_index, epoch)
        conflict_observer = None
        try:
            if os.name != "nt":
                conflict_observer = self.runtime.start_ipv4_conflict_observer(
                    snapshot.os_interface, snapshot.mac, assignment.address
                )
            before = self.runtime.address_observer_events(observer)
            current = self.inspect(assignment.adapter_id)
            after = self.runtime.address_observer_events(observer)
            if (
                before["health"] != "continuous"
                or after["health"] != "continuous"
                or after["events"]
                or current.public() != snapshot.public()
            ):
                raise PlatformRefusal(
                    "OWNERSHIP_CONFLICT", "Cannot establish a stable snapshot/event barrier"
                )
            if not user_owned:
                self.conflict_preflight(snapshot, assignment.address)
            final = self.runtime.address_observer_events(observer)
            if final["health"] != "continuous" or final["events"]:
                raise PlatformRefusal(
                    "OWNERSHIP_CONFLICT", "Adapter changed during conflict preparation"
                )
            return Prepared(
                assignment,
                snapshot,
                digest(snapshot.public()),
                observer,
                epoch,
                final["high_water"],
                user_owned,
                time.monotonic() + 20,
                conflict_observer,
            )
        except BaseException:
            self.runtime.stop_address_observer(observer)
            if conflict_observer is not None:
                conflict_observer.close()
            raise

    def apply(
        self, prepared: Prepared, journal: LocalMutationJournal | None, fence: Callable[[], None]
    ) -> AdapterSnapshot:
        """Caller must commit journal intent before entering this function."""
        fence()
        self.check_conflict(prepared)
        if time.monotonic() >= prepared.deadline:
            raise PlatformRefusal("TIMEOUT", "Prepared assignment expired")
        current = self.inspect(prepared.assignment.adapter_id)
        self.eligible(current)
        events = self.runtime.address_observer_events(prepared.observer)
        if (
            events["health"] != "continuous"
            or events["events"]
            or digest(current.public()) != prepared.snapshot_digest
        ):
            raise PlatformRefusal("OWNERSHIP_CONFLICT", "Prepared snapshot changed before apply")
        if prepared.user_owned:
            return current
        if (
            journal is None
            or journal.phase != "intent_written"
            or str(journal.observation_epoch) != prepared.epoch
            or journal.adapter_fingerprint != current.fingerprint
            or journal.exact_address_prefix
            != f"{prepared.assignment.address}/{prepared.assignment.prefix_length}"
            or journal.pre_snapshot != current.public()
        ):
            raise PlatformRefusal("OWNERSHIP_CONFLICT", "Durable exact mutation intent is required")
        fence()  # executor holds the authority/operation serialization boundary
        current = self.before_add(current, prepared)
        self.add_exact(current, prepared.assignment)
        deadline = min(prepared.deadline, time.monotonic() + 10)
        expected_adds = 0
        while time.monotonic() < deadline:
            snapshot = self.inspect(prepared.assignment.adapter_id)
            batch = self.runtime.address_observer_events(prepared.observer)
            prepared.high_water = batch["high_water"]
            if batch["health"] != "continuous" or snapshot.fingerprint != current.fingerprint:
                prepared.ownership_conflict = True
                raise PlatformRefusal("OWNERSHIP_CONFLICT", "Address observation lost continuity")
            for event in batch["events"]:
                if event["address"] == prepared.assignment.address:
                    if (
                        event["kind"] == "address_add"
                        and event["prefix_length"] == prepared.assignment.prefix_length
                    ):
                        expected_adds += 1
                        if expected_adds == 1:
                            prepared.owns_resource = True
                    elif (
                        event["kind"] == "address_change"
                        and event["prefix_length"] == prepared.assignment.prefix_length
                        and event["dad_state"] in (0, 1, 4)
                    ):
                        pass  # native Windows DAD lifecycle of the observed exact add
                    else:
                        prepared.ownership_conflict = True
                if (
                    event.get("dad_state") == 2
                    and event.get("address") == prepared.assignment.address
                ):
                    raise PlatformRefusal(
                        "ADDRESS_CONFLICT", "Native IPv4 duplicate-address detection failed"
                    )
            if expected_adds > 1 or prepared.ownership_conflict:
                prepared.ownership_conflict = True
                raise PlatformRefusal("OWNERSHIP_CONFLICT", "Exact address was externally replaced")
            if expected_adds == 1 and not prepared.ownership_conflict:
                prepared.owns_resource = True
            self.check_conflict(prepared)
            expected = ipaddress.IPv4Interface(journal.exact_address_prefix)
            if (
                expected_adds == 1
                and expected in snapshot.state.addresses
                and self.address_usable(snapshot, prepared.assignment)
            ):
                if set(snapshot.state.addresses) - {expected} != set(current.state.addresses):
                    prepared.ownership_conflict = True
                    raise PlatformRefusal(
                        "OWNERSHIP_CONFLICT", "Unrelated addresses changed during application"
                    )
                prepared.owns_resource = True
                return snapshot
            time.sleep(0.05)
        raise PlatformRefusal(
            "TIMEOUT", "Address did not become observed and usable before its deadline"
        )

    def close_prepared(self, prepared: Prepared) -> None:
        self.runtime.stop_address_observer(prepared.observer)
        if prepared.conflict_observer is not None:
            prepared.conflict_observer.close()

    def check_conflict(self, prepared: Prepared) -> None:
        if prepared.conflict_observer is not None:
            conflict, lost, closed = prepared.conflict_observer.status()
            if lost or closed:
                raise PlatformRefusal(
                    "UNSUPPORTED_CAPABILITY", "IPv4 conflict observation lost continuity"
                )
            if conflict:
                raise PlatformRefusal(
                    "ADDRESS_CONFLICT", "Another peer claims the dedicated address"
                )

    def address_usable(self, snapshot: AdapterSnapshot, assignment: NodeAssignment) -> bool:
        return True  # Linux/macOS eligibility also requires the preceding IPv4 ARP check.

    def observe(self, prepared: Prepared) -> AdapterSnapshot:
        snapshot = self.inspect(prepared.assignment.adapter_id)
        batch = self.runtime.address_observer_events(prepared.observer)
        prepared.high_water = batch["high_water"]
        if batch["health"] != "continuous" or snapshot.fingerprint != prepared.snapshot.fingerprint:
            prepared.ownership_conflict = True
        for event in batch["events"]:
            if event["address"] == prepared.assignment.address and (
                event["kind"] in ("address_add", "address_delete")
                or event["prefix_length"] != prepared.assignment.prefix_length
            ):
                prepared.ownership_conflict = True
        return snapshot

    def remove_owned(
        self, prepared: Prepared, journal: LocalMutationJournal, fence: Callable[[], None]
    ) -> bool:
        snapshot = self.observe(prepared)
        exact = ipaddress.IPv4Interface(journal.exact_address_prefix)
        if exact not in snapshot.state.addresses:
            return True  # Absence proves removal without a deletion command.
        if prepared.user_owned:
            return True  # Deliberately preserved user setting, no app mutation.
        if (
            not prepared.owns_resource
            or prepared.ownership_conflict
            or journal.observer_health != "continuous"
            or str(journal.observation_epoch) != prepared.epoch
            or snapshot.fingerprint != journal.adapter_fingerprint
        ):
            raise PlatformRefusal(
                "OWNERSHIP_CONFLICT",
                "Present address requires authenticated local ownership review",
            )
        fence()
        # Caller persists removal_intent under its current assignment/authority fence.
        if journal.phase != "removal_intent":
            raise PlatformRefusal("OWNERSHIP_CONFLICT", "Durable removal intent is required")
        self.delete_exact(snapshot, prepared.assignment)
        final = self.inspect(prepared.assignment.adapter_id)
        if exact in final.state.addresses:
            raise PlatformRefusal("CLEANUP_PENDING", "Exact address removal is not confirmed")
        return True
