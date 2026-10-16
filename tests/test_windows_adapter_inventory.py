"""Windows adapter inventory batching and its per-adapter fallback."""

from __future__ import annotations

import json

from kllm.shared.platform import windows as windows_module
from kllm.shared.platform.base import PlatformRefusal
from kllm.shared.platform.windows import WindowsAdapter

ETHERNET = {
    "Index": 7,
    "Guid": "{3DF37FC4-B750-4BDE-8C16-785AA5010934}",
    "MAC": "34-5A-60-4E-4B-42",
    "Name": "Ethernet",
    "Status": "Up",
    "LinkSpeed": "1 Gbps",
    "Hardware": True,
    "Description": "Realtek PCIe 5GbE Family Controller",
    "Default": False,
    "HasIpif": True,
    "Dhcp": False,
    "Addresses": [{"IPAddress": "10.10.10.3", "PrefixLength": 24}],
    "AddressStates": [
        {
            "IPAddress": "10.10.10.3",
            "PrefixLength": 24,
            "AddressState": 4,
            "SkipAsSource": False,
            "InstanceID": None,
        }
    ],
    "Routes": ["10.10.10.0/24"],
}

WIFI = {
    "Index": 14,
    "Guid": "{977B1ADE-FC71-4B17-A343-42517C853BAB}",
    "MAC": "04-68-74-9F-26-33",
    "Name": "Wi-Fi",
    "Status": "Up",
    "LinkSpeed": "866 Mbps",
    "Hardware": True,
    "Description": "Qualcomm FastConnect 7800 Wi-Fi 7",
    "Default": True,
    "HasIpif": True,
    "Dhcp": True,
    "Addresses": [{"IPAddress": "192.168.68.52", "PrefixLength": 22}],
    "AddressStates": [],
    "Routes": ["10.10.10.0/24"],
}

# A virtual adapter the per-adapter path skipped: no IPv4 interface row.
NO_IPIF = {
    **ETHERNET,
    "Index": 30,
    "Guid": "{AAAAAAAA-0000-0000-0000-000000000000}",
    "MAC": "00-11-22-33-44-55",
    "Name": "vEthernet",
    "Description": "Hyper-V Virtual Ethernet Container Adapter",
    "HasIpif": False,
    "Addresses": [],
    "AddressStates": [],
    "Routes": [],
}


def test_batch_inventory_uses_one_call_and_reports_exact_fields(monkeypatch):
    calls: list[str] = []

    def fake(script: str, values: dict[str, str]) -> str:
        calls.append(script)
        return json.dumps([ETHERNET, WIFI, NO_IPIF])

    monkeypatch.setattr(windows_module, "powershell", fake)
    adapters = WindowsAdapter().inventory()
    assert len(calls) == 1
    assert [a.adapter_id for a in adapters] == ["7", "14"]

    ethernet, wifi = adapters
    assert ethernet.os_interface == "7" and ethernet.interface_index == 7
    assert ethernet.mac == "34:5a:60:4e:4b:42"
    assert ethernet.state.connected and ethernet.state.speed_mbps == 1000
    assert ethernet.state.dhcp is False and ethernet.state.physical_ethernet
    assert ethernet.state.default_route is False
    assert [str(a) for a in ethernet.state.addresses] == ["10.10.10.3/24"]
    assert [str(r) for r in ethernet.state.routes] == ["10.10.10.0/24"]
    assert ethernet.facts["guid"] == "{3DF37FC4-B750-4BDE-8C16-785AA5010934}"
    assert ethernet.facts["address_states"][0]["AddressState"] == 4

    # Wi-Fi keeps its distinct facts: no dedicated-Ethernet candidacy, default route.
    assert wifi.state.speed_mbps == 866 and wifi.state.default_route
    assert wifi.state.physical_ethernet is False
    assert wifi.public()["facts"]["default_route"] is True


def test_batch_inventory_skips_malformed_entries(monkeypatch):
    def fake(script: str, values: dict[str, str]) -> str:
        return json.dumps([{**WIFI, "Addresses": [{"PrefixLength": 24}]}, ETHERNET])

    monkeypatch.setattr(windows_module, "powershell", fake)
    adapters = WindowsAdapter().inventory()
    assert [a.adapter_id for a in adapters] == ["7"]


def test_inventory_falls_back_to_per_adapter_scripts(monkeypatch):
    state_payload = {
        "Name": "Ethernet",
        "Status": "Up",
        "LinkSpeed": "1 Gbps",
        "Hardware": True,
        "Description": "Realtek PCIe 5GbE Family Controller",
        "Dhcp": False,
        "Addresses": [{"IPAddress": "10.10.10.3", "PrefixLength": 24}],
        "Routes": ["10.10.10.0/24"],
    }
    extra_payload = {
        "Guid": "{3DF37FC4-B750-4BDE-8C16-785AA5010934}",
        "MAC": "34-5A-60-4E-4B-42",
        "Index": 7,
        "Addresses": [{"IPAddress": "10.10.10.3", "PrefixLength": 24, "AddressState": 4}],
    }
    calls: list[str] = []

    def fake(script: str, values: dict[str, str]) -> str:
        calls.append(script)
        if "$defaults=" in script:
            raise PlatformRefusal("TIMEOUT", "Platform command exceeded its deadline")
        if "ExpandProperty ifIndex" in script:
            return json.dumps([7])
        if "HardwareInterface" in script:
            return json.dumps(state_payload)
        if "MAC=$a.MacAddress;Index" in script:
            return json.dumps(extra_payload)
        raise AssertionError(f"unexpected script: {script[:80]}")

    monkeypatch.setattr(windows_module, "powershell", fake)
    adapters = WindowsAdapter().inventory()
    assert len(calls) == 4
    assert [a.adapter_id for a in adapters] == ["7"]
    assert adapters[0].mac == "34:5a:60:4e:4b:42"
    assert adapters[0].state.speed_mbps == 1000
