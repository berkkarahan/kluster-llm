"""Exact native listener contracts for the privileged private-network helper."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from kllm.modules.managed_network.entities import Digest, NodeAssignment, Record, Revision
from kllm.modules.primacpp.entities import PrimaTopology
from kllm.modules.primacpp.interactors.placement import network_edges


class PrimaListener(Record):
    port: int = Field(ge=1024, le=65535)
    peers: list[str] = Field(min_length=1, max_length=32)

    @field_validator("peers")
    @classmethod
    def exact_peers(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Duplicate Prima listener peer")
        for value in values:
            address = IPv4Address(value)
            private = any(
                address in IPv4Network(network)
                for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            )
            if str(address) != value or not private:
                raise ValueError("Prima peers must be canonical private IPv4 addresses")
        return values


class PrimaPortContract(Record):
    network_id: UUID
    manifest_digest: Digest
    address: str
    assignment_revision: Revision
    rules: list[PrimaListener] = Field(min_length=1, max_length=2)

    @field_validator("address")
    @classmethod
    def exact_address(cls, value):
        PrimaListener.exact_peers([value])
        return value

    @model_validator(mode="after")
    def exact_listeners(self):
        if len({rule.port for rule in self.rules}) != len(self.rules):
            raise ValueError("Duplicate Prima listener port")
        if any(self.address in rule.peers for rule in self.rules):
            raise ValueError("Prima listener peers cannot include its own address")
        return self


def port_contract(manifest, node_id: str, assignments: list[NodeAssignment]) -> PrimaPortContract:
    """Derive inbound rules only; the master HTTP port is not a rank edge."""
    if not manifest.network_id or len(manifest.ranks) < 2:
        raise ValueError("Prima listener admission requires a distributed private manifest")
    topology = PrimaTopology(
        runtime_id=manifest.runtime_id,
        master_node_id=manifest.ranks[0].node_id,
        total_layers=manifest.total_layers,
        ranks=manifest.ranks,
        data_port=manifest.data_port,
        signal_port=manifest.signal_port,
    )
    if manifest.transfer_edges != network_edges(topology):
        raise ValueError("Prima listener edges differ from the approved rank topology")
    selected = {}
    for assignment in assignments:
        if assignment.node_id in selected or str(assignment.network_id) != manifest.network_id:
            raise ValueError("Prima private assignments are duplicated or from another network")
        selected[assignment.node_id] = assignment
    ranks = {rank.node_id: rank for rank in manifest.ranks}
    if node_id not in ranks or not set(ranks) <= selected.keys():
        raise ValueError("Prima listener participants lack private assignments")
    for node, rank in ranks.items():
        if selected[node].address != rank.address:
            raise ValueError("Prima rank address changed after preparation")
    assignment = selected[node_id]
    if not 8 <= assignment.prefix_length <= 30:
        raise ValueError("Prima private prefix is outside the supported range")
    subnet = IPv4Network(f"{assignment.address}/{assignment.prefix_length}", strict=False)
    listeners: dict[int, set[str]] = {}
    for edge in manifest.transfer_edges:
        if edge["target"] != node_id:
            continue
        peer = ranks[edge["source"]].address
        address = IPv4Address(peer)
        if address not in subnet or address in (subnet.network_address, subnet.broadcast_address):
            raise ValueError("Prima peer is outside the listener's private subnet")
        listeners.setdefault(edge["port"], set()).add(peer)
    return PrimaPortContract(
        network_id=manifest.network_id,
        manifest_digest=manifest.digest,
        address=assignment.address,
        assignment_revision=assignment.assignment_revision,
        rules=[
            PrimaListener(port=port, peers=sorted(peers))
            for port, peers in sorted(listeners.items())
        ],
    )
