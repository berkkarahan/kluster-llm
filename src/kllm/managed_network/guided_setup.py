"""Guided setup policy. Assessment is read-only; mutations reuse signed phases."""

from __future__ import annotations

import asyncio
import ipaddress
import json
from typing import Any, TYPE_CHECKING, cast
from uuid import UUID

from pydantic import Field, field_validator

from .allocation import CANDIDATES, OperationRequest, ParticipantSelection, StartRequest, acceptable, propose, reusable_addresses
from .helper import HelperClient
from .models import Digest, Identifier, Record, Revision, private_subnet
from .platform.base import PlatformRefusal
from .speed import MIN_DEDICATED_LINK_SPEED_MBPS

if TYPE_CHECKING:
    from kllm.agent.services.managed_network import ManagedNetworkService

ADMIN_NOTICE = (
    "Administrator access is required on each computer to configure its dedicated network adapter. "
    "Kluster will save the original settings and restore its changes when the network is stopped. "
    "After you enable access once on each computer, this cluster’s master can set up the private network."
)
MESSAGES = {
    "UNPAIRED_IDENTITY": "This computer needs a Kluster update or has not connected to the master yet.",
    "UNSUPPORTED_CAPABILITY": "Update Kluster on this computer to enable guided setup.",
    "TIMEOUT": "This computer is not responding. Reconnect it and refresh.",
    "RPC_PROTOCOL_UNSUPPORTED": "Update the networking runtime on this computer before setup.",
    "RPC_BUSY": "Stop the current inference run before testing this computer’s private connection.",
    "ADMIN_REQUIRED": "Open Kluster on this computer and enable administrator access.",
    "HELPER_REFUSED": "This computer could not apply the network settings. Open Advanced details to review the reason.",
    "OWNERSHIP_CONFLICT": "Network settings changed outside Kluster. Review them on this computer before retrying.",
    "CLEANUP_PENDING": "Some network changes still need to be restored. Reconnect this computer or review recovery.",
    "SUBNET_CONFLICT": "A private address range could not be selected safely. Review Advanced details.",
    "STALE_REVISION": "The network changed during setup. Refresh and try again.",
    "INFERENCE_ACTIVE": "Stop the inference run using this network before changing it.",
    "INVALID_ADAPTER": "A computer could not prepare its adapter. Review its status and try again.",
}


class GuidedRequest(Record):
    request_id: UUID
    nodes: list[Identifier] = Field(min_length=2, max_length=256)
    adapters: dict[Identifier, Identifier] = Field(default_factory=dict)
    assessment_revision: Digest
    subnet: str | None = None
    network_id: UUID | None = None
    expected_revision: Revision | None = None
    action: str = Field(default="start", pattern=r"^(start|add|retry)$")

    _subnet = field_validator("subnet")(
        lambda value: private_subnet(value) if value is not None else value
    )


class EnableRequest(Record):
    acknowledged: bool


class RecoveryRequest(Record):
    decision: str = Field(pattern=r"^(keep_current|restore_owned)$")
    snapshot_digest: Digest


class GuidedSetup:
    def __init__(self, service: ManagedNetworkService):
        self.service = service
        self.helper = HelperClient(service.identity.node_id)
        self.enabling = asyncio.Lock()
        self.requests = asyncio.Lock()

    def grant(self) -> dict[str, Any] | None:
        service, state = self.service, self.service.state()
        if state.role == "master":
            binding = service._authority().cluster_binding_id
            public = service.identity.public_key
        elif state.role == "worker":
            peer = next((p for p in service.store.peers() if p.expected_role == "master"), None)
            if peer is None or peer.cluster_binding_id is None:
                return None
            binding, public = peer.cluster_binding_id, peer.public_key
        else:
            return None
        return {
            "account": self.helper.account,
            "node_id": service.identity.node_id,
            "master_public_key": public,
            "cluster_binding_id": str(binding),
        }

    def local_status(self) -> dict[str, Any]:
        grant = self.grant()
        status = (
            self.helper.status(grant)
            if grant
            else {"ready": False, "reason": "Join and connect to the intended cluster first"}
        )
        code = None
        reason = status.get("reason", "")
        if not status.get("ready"):
            code = "ADMIN_REQUIRED"
        elif status.get("cleanup_pending"):
            code, reason = "CLEANUP_PENDING", MESSAGES["CLEANUP_PENDING"]
        return {
            "capability": "managed-network-v2",
            "administrator_enabled": bool(status.get("ready")),
            "error_code": code,
            "message": reason,
            "cleanup_pending": status.get("cleanup_pending", []),
        }

    async def enable(self, acknowledged: bool) -> dict[str, Any]:
        if not acknowledged:
            raise PlatformRefusal(
                "ADMIN_REQUIRED", "Read the administrator access notice before continuing"
            )
        if self.enabling.locked():
            raise PlatformRefusal(
                "OPERATION_IN_PROGRESS", "An administrator prompt is already open on this computer"
            )
        async with self.enabling:
            if self.service.state().role == "worker":
                await self.service.bootstrap.connect()
            grant = self.grant()
            repo = self.service.executor.repository()
            if grant is None or repo is None:
                raise PlatformRefusal(
                    "UNSUPPORTED_CAPABILITY", "Join the intended cluster and update Kluster first"
                )
            if any(n.observed_state != "stopped" for n in self.service.store.networks()):
                raise PlatformRefusal(
                    "OPERATION_IN_PROGRESS",
                    "Stop the private network before changing administrator authorization",
                )
            result = await self.helper.enable(repo, grant)
            from .helper_platform import HelperPlatform

            if not isinstance(self.service.executor.platform, HelperPlatform):
                self.service.executor.platform = HelperPlatform(
                    self.service.executor.platform, self.helper, self.grant
                )
            return result

    async def resolve(self, network_id: UUID, request: RecoveryRequest) -> dict[str, Any]:
        async with self.service.lock:
            store = self.service.store
            if any(run.state != "released" for run in store.runs(network_id)) or any(
                lease.state != "released" for lease in store.leases(network_id)
            ):
                raise PlatformRefusal("INFERENCE_ACTIVE", MESSAGES["INFERENCE_ACTIVE"])
            result = await asyncio.to_thread(
                self.helper.request,
                "resolve",
                network=str(network_id),
                decision=request.decision,
                snapshot_digest=request.snapshot_digest,
            )
            for journal in store.journals():
                if journal.network_id == network_id:
                    journal.phase = "removed_observed"
                    store.journal(journal)
            return cast(dict[str, Any], result)

    @staticmethod
    def adapter_reason(adapter: dict[str, Any], management: set[str]) -> str:
        if not adapter.get("physical_ethernet"):
            return "This is not a physical Ethernet adapter."
        if not adapter.get("connected"):
            return "Connect an Ethernet cable to this adapter."
        speed = adapter.get("link_speed_mbps")
        if speed is None or speed <= 0:
            return "The connection speed could not be verified."
        if speed < MIN_DEDICATED_LINK_SPEED_MBPS:
            return (
                f"This connection is {speed} Mbps; at least 1000 Mbps (1 Gbps) is required."
            )
        if adapter.get("facts", {}).get("default_route"):
            return "This adapter carries the computer’s internet connection."
        if any(
            str(ipaddress.IPv4Interface(a).ip) in management for a in adapter.get("addresses", [])
        ):
            return "This adapter carries the cluster’s management connection."
        if adapter.get("dhcp") is None:
            return "The adapter’s configuration could not be read."
        return ""

    async def assess(self) -> dict[str, Any]:
        service = self.service
        inventories = await service.inventories()
        management = set(await asyncio.to_thread(service._management_hosts))
        computers = []
        for node_id, member in service.state().members.items():
            inventory = inventories.get(node_id, {})
            setup = inventory.get("setup", {})
            adapters = [
                {**a, "reason": self.adapter_reason(a, management)}
                for a in inventory.get("adapters", [])
            ]
            candidates = [a for a in adapters if not a["reason"]]
            error_code = inventory.get("error_code") or setup.get("error_code")
            if inventory and not error_code and setup.get("capability") != "managed-network-v2":
                error_code = "UNSUPPORTED_CAPABILITY"
            message = MESSAGES.get(error_code, setup.get("message", "")) if error_code else ""
            if not candidates:
                physical = [a for a in adapters if a.get("physical_ethernet")]
                if physical:
                    message = physical[0]["reason"]
                elif not message:
                    message = "Connect a dedicated physical Ethernet adapter to this computer."
                error_code = error_code or "NO_ADAPTER"
            selected = candidates[0]["adapter_id"] if len(candidates) == 1 else ""
            if len(candidates) > 1 and not error_code:
                preferred = [a for a in candidates if reusable_addresses(a, ipaddress.IPv4Network(CANDIDATES[0]))]
                if len(preferred) == 1:
                    selected = preferred[0]["adapter_id"]
                else:
                    message = "Choose which dedicated Ethernet adapter to use."
            computers.append(
                {
                    "node_id": node_id,
                    "name": member.get("hostname") or member.get("host") or node_id,
                    "host": member.get("host"),
                    "master": node_id == service.identity.node_id,
                    "administrator_enabled": setup.get("administrator_enabled", False),
                    "adapters": adapters,
                    "selected_adapter": selected,
                    "ready": not error_code and bool(selected),
                    "error_code": error_code,
                    "message": message or "Ready",
                }
            )
        from .store import digest

        revision = digest({"nodes": inventories, "management": sorted(management)})
        return {
            "administrator_notice": ADMIN_NOTICE,
            "master_node_id": service.identity.node_id,
            "computers": computers,
            "assessment_revision": revision,
            "networks": service.summaries(),
        }

    async def start(self, request: GuidedRequest) -> dict[str, Any]:
        from .store import digest, StoreConflict

        async with self.requests:
            binding = self.service._authority().cluster_binding_id
            key = f"guided:{binding}:{request.request_id}"
            body_digest = digest(request.model_dump(mode="json"))
            saved = self.service.store.metadata(key)
            if saved:
                record = json.loads(saved)
                if record["digest"] != body_digest:
                    raise StoreConflict("request ID reused with changed guided body")
                if record["action"] == "start":
                    return await self.service.begin_start(
                        StartRequest.model_validate(record["request"])
                    )
                return await self.service.begin_operation(
                    UUID(record["network_id"]), OperationRequest.model_validate(record["request"])
                )
            return await self._start(request, key, body_digest)

    async def _start(self, request: GuidedRequest, key: str, body_digest: str) -> dict[str, Any]:
        if (
            len(set(request.nodes)) != len(request.nodes)
            or self.service.identity.node_id not in request.nodes
        ):
            raise PlatformRefusal(
                "INVALID_ADAPTER", "Include this master and each selected computer only once"
            )
        assessment = await self.assess()
        if request.assessment_revision != assessment["assessment_revision"]:
            raise PlatformRefusal("STALE_REVISION", MESSAGES["STALE_REVISION"])
        computers = {c["node_id"]: c for c in assessment["computers"]}
        selections, groups = [], []
        for node_id in request.nodes:
            computer = computers.get(node_id)
            if computer is None:
                raise PlatformRefusal(
                    "INVALID_ADAPTER", "Select only computers currently joined to this cluster"
                )
            if computer["error_code"]:
                raise PlatformRefusal(
                    computer["error_code"], f"{computer['name']}: {computer['message']}"
                )
            adapter_id = request.adapters.get(node_id) or computer["selected_adapter"]
            adapter = next(
                (
                    a
                    for a in computer["adapters"]
                    if a["adapter_id"] == adapter_id and not a["reason"]
                ),
                None,
            )
            if adapter is None:
                raise PlatformRefusal(
                    "INVALID_ADAPTER", f"{computer['name']}: choose a dedicated adapter"
                )
            selections.append(
                ParticipantSelection(
                    node_id=node_id,
                    adapter_id=adapter_id,
                    inventory_revision=adapter["inventory_revision"],
                )
            )
            groups.append((adapter, [a for a in computer["adapters"] if a is not adapter]))
        if request.action == "start":
            proposals = propose(groups, len(request.nodes))
            subnet = request.subnet or (proposals[0] if proposals else None)
            if subnet is None or not acceptable(groups, ipaddress.IPv4Network(subnet), len(request.nodes)):
                raise PlatformRefusal("SUBNET_CONFLICT", MESSAGES["SUBNET_CONFLICT"])
            if request.network_id is not None:
                raise PlatformRefusal(
                    "INVALID_INPUT", "A new network does not take an existing network ID"
                )
            resolved = StartRequest(
                request_id=request.request_id, subnet=subnet, participants=selections
            )
            self.service.store.set_metadata(
                key,
                json.dumps(
                    {
                        "digest": body_digest,
                        "action": "start",
                        "request": resolved.model_dump(mode="json"),
                    }
                ),
            )
            return await self.service.begin_start(resolved)
        if request.network_id is None or request.expected_revision is None:
            raise PlatformRefusal(
                "INVALID_INPUT", "Refresh the existing network before changing it"
            )
        network = self.service.store.network(request.network_id)
        if network is None:
            raise KeyError("unknown network")
        if request.subnet and request.subnet != network.subnet:
            raise PlatformRefusal(
                "SUBNET_CONFLICT", "An existing network keeps its private address range"
            )
        existing = {a.node_id: a for a in self.service.store.assignments(request.network_id)}
        if not set(existing).issubset(request.nodes) or any(
            p.node_id in existing and p.adapter_id != existing[p.node_id].adapter_id
            for p in selections
        ):
            raise PlatformRefusal(
                "INVALID_ADAPTER", "Keep every existing computer on its assigned adapter"
            )
        resolved_operation = OperationRequest(
            request_id=request.request_id,
            expected_revision=request.expected_revision,
            action=request.action,
            participants=[p for p in selections if p.node_id not in existing]
            if request.action == "add"
            else [],
        )
        self.service.store.set_metadata(
            key,
            json.dumps(
                {
                    "digest": body_digest,
                    "action": request.action,
                    "network_id": str(request.network_id),
                    "request": resolved_operation.model_dump(mode="json"),
                }
            ),
        )
        return await self.service.begin_operation(request.network_id, resolved_operation)
