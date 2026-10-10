"""Preferred-subnet reuse: 10.10.10.0/24 first, existing static addresses preserved."""

from __future__ import annotations

import ipaddress
from types import SimpleNamespace
from uuid import uuid4

import pytest

from kllm.modules.managed_network.interactors.service import ManagedNetworkService
from kllm.modules.managed_network.interactors.allocation import ParticipantSelection, assign, propose, reusable_addresses
from kllm.modules.managed_network.interactors.guided_setup import GuidedRequest
from kllm.shared.platform.base import AdapterSnapshot, PlatformRefusal
from kllm.shared.platform.inspection import InterfaceState

PREFERRED = ipaddress.IPv4Network("10.10.10.0/24")
ORDER = ["10.10.10.0/24", "192.168.234.0/24", "172.29.234.0/24", "10.234.234.0/24"]


def ranges(dhcp=False, addresses=(), routes=()):
    return {"dhcp": dhcp, "addresses": list(addresses), "routes": list(routes)}


def snapshot(name, dhcp=False, addresses=(), routes=()):
    return AdapterSnapshot(
        name,
        name,
        2,
        "a" * 64,
        "02:00:00:00:00:01",
        InterfaceState(
            name,
            True,
            10000,
            dhcp,
            tuple(ipaddress.IPv4Interface(value) for value in addresses),
            tuple(ipaddress.IPv4Network(value) for value in routes),
        ),
        permission=True,
    )


def service(tmp_path, role="master", node_id="master"):
    state = SimpleNamespace(
        role=role,
        node_id=node_id,
        join_key="isolated",
        members={"master": {"host": "192.168.68.1"}, "worker": {"host": "192.168.68.2"}},
        master_host=None if role == "master" else "192.168.68.1",
    )
    return ManagedNetworkService(
        tmp_path / node_id, lambda: state, lambda: "http://192.168.68.1:18765"
    )


SETUP = {"capability": "managed-network-v2", "administrator_enabled": True, "error_code": None}


def test_propose_prefers_ten_and_reuses_an_exact_static_address():
    clean = ranges()
    assert propose([(clean, []), (clean, [])], 2) == ORDER
    reusable = ranges(
        addresses=["10.10.10.5/24"], routes=["10.10.10.0/24", "10.10.10.5/32", "10.10.10.255/32"]
    )
    assert reusable_addresses(reusable, PREFERRED) == ["10.10.10.5"]
    assert propose([(reusable, []), (clean, [])], 2)[0] == "10.10.10.0/24"
    assert reusable_addresses(ranges(dhcp=True, addresses=["10.10.10.5/24"]), PREFERRED) == []
    assert reusable_addresses(ranges(addresses=["10.10.10.5/16"]), PREFERRED) == []
    assert reusable_addresses(ranges(addresses=["10.10.10.0/24"]), PREFERRED) == []
    assert reusable_addresses(ranges(addresses=["10.10.10.255/24"]), PREFERRED) == []


def test_system_wide_route_tables_do_not_defeat_reuse():
    # Every platform reports the whole route table per adapter, so the chosen
    # adapter's connected route reappears under the same computer's other
    # adapters. That alone must not disqualify reuse.
    reusable = ranges(addresses=["10.10.10.5/24"], routes=["10.10.10.0/24", "10.10.10.5/32"])
    management = ranges(
        addresses=["192.168.68.1/24"], routes=["192.168.68.0/24", "10.10.10.0/24", "10.10.10.5/32"]
    )
    assert propose([(reusable, [management])], 2)[0] == "10.10.10.0/24"
    # A genuine in-subnet address on another interface still blocks reuse.
    conflicting = ranges(addresses=["10.10.10.9/24"])
    assert propose([(reusable, [conflicting])], 2) == ORDER[1:]


@pytest.mark.parametrize(
    "inventory",
    [
        ranges(dhcp=True, addresses=["10.10.10.5/24"]),
        ranges(dhcp=None, addresses=["10.10.10.5/24"]),
        ranges(addresses=["10.10.10.5/16"]),
        ranges(addresses=["10.10.10.0/24"]),
        ranges(addresses=["10.10.10.255/24"]),
        ranges(addresses=["10.10.10.5/24", "10.10.10.6/25"]),
        ranges(routes=["10.10.10.0/24"]),
        ranges(addresses=["10.10.10.5/24"], routes=["10.10.10.0/24", "10.10.10.128/25"]),
    ],
)
def test_propose_falls_back_when_ten_is_unusable(inventory):
    assert propose([(inventory, [])], 2) == ORDER[1:]


@pytest.mark.parametrize(
    "other",
    [
        ranges(dhcp=False, addresses=["10.10.10.9/24"]),
        ranges(dhcp=False, routes=["10.10.10.0/24"]),
    ],
)
def test_propose_treats_other_visible_adapters_strictly(other):
    assert propose([(ranges(), [other])], 2) == ORDER[1:]


@pytest.mark.asyncio
async def test_guided_reuses_ten_despite_system_wide_management_routes(tmp_path, monkeypatch):
    master = service(tmp_path)
    ten = snapshot(
        "eth-ten",
        dhcp=False,
        addresses=["10.10.10.5/24"],
        routes=["10.10.10.0/24", "10.10.10.5/32", "10.10.10.255/32"],
    ).public()
    management = snapshot(
        "eth-mgmt",
        dhcp=True,
        addresses=["192.168.68.1/24"],
        routes=["192.168.68.0/24", "10.10.10.0/24"],
    ).public()
    worker = snapshot("eth-worker").public()

    async def inventories():
        return {
            "master": {"adapters": [ten, management], "setup": SETUP},
            "worker": {"adapters": [worker], "setup": SETUP},
        }

    monkeypatch.setattr(master, "inventories", inventories)
    monkeypatch.setattr(master, "_management_hosts", lambda: ["192.168.68.1", "192.168.68.2"])
    accepted = []

    async def begin(request):
        accepted.append(request)
        return {"operation_id": str(uuid4())}

    monkeypatch.setattr(master, "begin_start", begin)
    try:
        assessment = await master.guided.assess()
        assert all(computer["ready"] for computer in assessment["computers"])
        await master.guided.start(
            GuidedRequest(
                request_id=uuid4(),
                nodes=["master", "worker"],
                assessment_revision=assessment["assessment_revision"],
                subnet="10.10.10.0/24",
            )
        )
        assert accepted[0].subnet == "10.10.10.0/24"
        await master.guided.start(
            GuidedRequest(
                request_id=uuid4(),
                nodes=["master", "worker"],
                assessment_revision=assessment["assessment_revision"],
            )
        )
        assert accepted[1].subnet == "10.10.10.0/24"
    finally:
        master.store.close()


@pytest.mark.asyncio
async def test_guided_explicit_ten_refuses_overlap_without_reuse(tmp_path, monkeypatch):
    master = service(tmp_path)
    ten = snapshot(
        "eth-ten", dhcp=True, addresses=["10.10.10.5/24"], routes=["10.10.10.0/24"]
    ).public()
    worker = snapshot("eth-worker").public()

    async def inventories():
        return {
            "master": {"adapters": [ten], "setup": SETUP},
            "worker": {"adapters": [worker], "setup": SETUP},
        }

    monkeypatch.setattr(master, "inventories", inventories)
    monkeypatch.setattr(master, "_management_hosts", lambda: ["192.168.68.1", "192.168.68.2"])
    try:
        assessment = await master.guided.assess()
        assert all(computer["ready"] for computer in assessment["computers"])
        with pytest.raises(PlatformRefusal, match="private address range"):
            await master.guided.start(
                GuidedRequest(
                    request_id=uuid4(),
                    nodes=["master", "worker"],
                    assessment_revision=assessment["assessment_revision"],
                    subnet="10.10.10.0/24",
                )
            )
    finally:
        master.store.close()


@pytest.mark.asyncio
async def test_assessment_preselects_the_single_reusable_adapter(tmp_path, monkeypatch):
    master = service(tmp_path)
    reusable = snapshot(
        "eth-ten", dhcp=False, addresses=["10.10.10.7/24"], routes=["10.10.10.0/24"]
    ).public()
    clean = snapshot("eth-clean").public()
    worker = snapshot("eth-worker").public()

    async def inventories():
        return {
            "master": {"adapters": [clean, reusable], "setup": SETUP},
            "worker": {"adapters": [worker], "setup": SETUP},
        }

    monkeypatch.setattr(master, "inventories", inventories)
    monkeypatch.setattr(master, "_management_hosts", lambda: [])
    try:
        assessment = await master.guided.assess()
        computer = next(c for c in assessment["computers"] if c["node_id"] == "master")
        assert (computer["selected_adapter"], computer["ready"], computer["error_code"]) == (
            "eth-ten",
            True,
            None,
        )
        clean["addresses"] = ["10.10.10.8/24"]
        assessment = await master.guided.assess()
        computer = next(c for c in assessment["computers"] if c["node_id"] == "master")
        assert (computer["selected_adapter"], computer["ready"]) == ("", False)
        assert computer["message"] == "Choose which dedicated Ethernet adapter to use."
    finally:
        master.store.close()


def test_assign_reuses_exact_ten_addresses_and_allocates_free_hosts():
    inventories = {
        "master": snapshot("master-eth", addresses=["10.10.10.3/24"]).public(),
        "worker": snapshot("worker-eth", addresses=["10.10.10.7/24"]).public(),
        "extra": snapshot("extra-eth").public(),
    }
    selections = [
        ParticipantSelection(
            node_id=node,
            adapter_id=value["adapter_id"],
            inventory_revision=value["inventory_revision"],
        )
        for node, value in inventories.items()
    ]
    assignments = {
        assignment.node_id: assignment
        for assignment in assign(uuid4(), "10.10.10.0/24", "master", selections, inventories)
    }
    assert {node: assignment.address for node, assignment in assignments.items()} == {
        "master": "10.10.10.3",
        "worker": "10.10.10.7",
        "extra": "10.10.10.1",
    }
    assert all(assignment.prefix_length == 24 for assignment in assignments.values())
