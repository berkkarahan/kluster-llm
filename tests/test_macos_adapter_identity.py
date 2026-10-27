"""macOS adapter identity when the OS redacts unprivileged ifconfig MACs."""

from __future__ import annotations

from kllm.shared.platform import macos as macos_module
from kllm.shared.platform.base import PlatformRefusal
from kllm.shared.platform.macos import MacOSAdapter

SERVICE_ORDER = "(1) AX88179A\n(Hardware Port: AX88179A, Device: en4)"
REAL_MAC = "f8:e4:3b:7d:ed:9b"
REDACTED = "02:00:00:00:00:00"


def ifconfig(mac: str) -> str:
    return (
        "en4: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
        f"\tether {mac}\n"
        "\tinet 10.10.10.2 netmask 0xffffff00 broadcast 10.10.10.255\n"
        "\tstatus: active\n"
    )


def base_outputs():
    return {
        ("networksetup", "-listnetworkserviceorder"): SERVICE_ORDER,
        ("networksetup", "-getinfo", "AX88179A"): (
            "Manual Configuration\nIP address: 10.10.10.2\nRouter: (null)"
        ),
        ("networksetup", "-getmedia", "AX88179A"): (
            "Current: autoselect\nActive: 1000baseT <full-duplex>"
        ),
        ("networksetup", "-getdnsservers", "AX88179A"): (
            "There aren't any DNS Servers set on AX88179A."
        ),
        ("netstat", "-rn", "-f", "inet"): (
            "Routing tables\nInternet:\nDestination Gateway Flags Netif\n"
            "10.10.10.0/24 link#13 UCS en4\n"
        ),
    }


class FakeRunner:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, args):
        key = tuple(args)
        self.calls.append(key)
        value = self.mapping.get(key)
        if value is None:
            raise AssertionError(f"unexpected command: {args}")
        return value() if callable(value) else value


def adapter(monkeypatch, runner):
    monkeypatch.setattr(macos_module, "run_fixed", runner)
    monkeypatch.setattr(macos_module.socket, "if_nametoindex", lambda name: 13, raising=False)
    return MacOSAdapter()


def test_networksetup_address_wins_over_redacted_ifconfig(monkeypatch):
    outputs = {
        **base_outputs(),
        ("networksetup", "-getmacaddress", "en4"): f"Ethernet Address: {REAL_MAC} (Device: en4)",
        ("ifconfig", "en4"): ifconfig(REDACTED),
    }
    runner = FakeRunner(outputs)
    snapshot = adapter(monkeypatch, runner).inspect("en4")
    assert snapshot.mac == REAL_MAC
    assert snapshot.interface_index == 13
    assert snapshot.state.speed_mbps == 1000
    assert snapshot.facts["service"] == "AX88179A"


def test_redacted_networksetup_address_falls_back_to_ifconfig(monkeypatch):
    outputs = {
        **base_outputs(),
        ("networksetup", "-getmacaddress", "en4"): f"Ethernet Address: {REDACTED} (Device: en4)",
        ("ifconfig", "en4"): ifconfig(REAL_MAC),
    }
    runner = FakeRunner(outputs)
    snapshot = adapter(monkeypatch, runner).inspect("en4")
    assert snapshot.mac == REAL_MAC


def test_unavailable_networksetup_falls_back_to_ifconfig(monkeypatch):
    def refuse():
        raise PlatformRefusal("ADMIN_REQUIRED", "networksetup unavailable")

    outputs = {
        **base_outputs(),
        ("networksetup", "-getmacaddress", "en4"): refuse,
        ("ifconfig", "en4"): ifconfig(REAL_MAC),
    }
    runner = FakeRunner(outputs)
    snapshot = adapter(monkeypatch, runner).inspect("en4")
    assert snapshot.mac == REAL_MAC
