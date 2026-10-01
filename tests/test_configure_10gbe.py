from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import ipaddress
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "configure_10gbe.py"
spec = importlib.util.spec_from_file_location("configure_10gbe", SCRIPT)
assert spec and spec.loader
net = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = net
spec.loader.exec_module(net)


def test_profile_maps_coordinator_and_worker_to_first_two_hosts():
    coordinator = net.make_profile("coordinator")
    worker = net.make_profile("worker")
    assert coordinator.local == ipaddress.ip_address("192.168.10.1")
    assert coordinator.peer == worker.local == ipaddress.ip_address("192.168.10.2")
    assert worker.peer == coordinator.local


@pytest.mark.parametrize("subnet", ["192.168.10.1/24", "8.8.8.0/24", "10.0.0.1/32", "2001:db8::/64"])
def test_profile_rejects_invalid_nonprivate_or_too_small_subnet(subnet):
    with pytest.raises(net.NetworkError):
        net.make_profile("coordinator", subnet)


def test_profile_rejects_unknown_role():
    with pytest.raises(net.NetworkError, match="role"):
        net.make_profile("workerish")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("10 Gbps", 10_000), ("10Gbase-T <full-duplex>", 10_000), ("2500 Mbps", 2500), ("10000", 10_000), ("unknown", None)],
)
def test_parse_link_speed(value, expected):
    assert net.parse_speed_mbps(value) == expected


def test_route_overlap_ignores_default_route():
    subnet = ipaddress.ip_network("192.168.10.0/24")
    assert not net.routes_overlap(subnet, (ipaddress.ip_network("0.0.0.0/0"),))
    assert not net.routes_overlap(subnet, (ipaddress.ip_network("172.16.0.0/16"),))


def state(**updates):
    values = dict(
        name="eth-test",
        connected=True,
        speed_mbps=10_000,
        dhcp=False,
        addresses=(),
        routes=(),
        physical_ethernet=True,
    )
    values.update(updates)
    return net.InterfaceState(**values)


def test_configure_applies_address_only_after_safe_preflight(monkeypatch):
    calls = []
    monkeypatch.setattr(net, "is_admin", lambda: True)
    monkeypatch.setattr(net, "inspect_interface", lambda _interface: state())
    monkeypatch.setattr(net, "_apply_address", lambda interface, profile: calls.append((interface, profile)))
    assert net.configure("eth-test", "coordinator") is True
    assert calls[0][0] == "eth-test"
    assert calls[0][1].local == ipaddress.ip_address("192.168.10.1")


@pytest.mark.parametrize(
    "bad_state",
    [
        state(connected=False),
        state(speed_mbps=1000),
        state(speed_mbps=None),
        state(dhcp=True),
        state(dhcp=None),
        state(physical_ethernet=False),
        state(routes=(ipaddress.ip_network("192.168.10.0/24"),)),
        state(addresses=(ipaddress.ip_interface("192.168.10.9/24"),)),
    ],
)
def test_configure_refuses_unsafe_interface_before_mutation(monkeypatch, bad_state):
    calls = []
    monkeypatch.setattr(net, "is_admin", lambda: True)
    monkeypatch.setattr(net, "inspect_interface", lambda _interface: bad_state)
    monkeypatch.setattr(net, "_apply_address", lambda *args: calls.append(args))
    with pytest.raises(net.NetworkError):
        net.configure("eth-test", "coordinator")
    assert calls == []


def test_configure_requires_interface_and_elevation(monkeypatch):
    monkeypatch.setattr(net, "is_admin", lambda: False)
    with pytest.raises(net.NetworkError, match="administrator/root"):
        net.configure("eth-test", "coordinator")
    with pytest.raises(net.NetworkError, match="--interface"):
        net.configure("", "coordinator")


def test_configure_rejects_unsupported_os_before_mutation(monkeypatch):
    calls = []
    monkeypatch.setattr(net, "is_admin", lambda: True)
    monkeypatch.setattr(net.platform, "system", lambda: "FreeBSD")
    monkeypatch.setattr(net, "_apply_address", lambda *args: calls.append(args))
    with pytest.raises(net.NetworkError, match="unsupported operating system"):
        net.configure("em0", "coordinator")
    assert calls == []


def test_configure_is_idempotent_for_existing_assignment(monkeypatch):
    calls = []
    monkeypatch.setattr(net, "is_admin", lambda: True)
    monkeypatch.setattr(
        net,
        "inspect_interface",
        lambda _interface: state(addresses=(ipaddress.ip_interface("192.168.10.1/24"),)),
    )
    monkeypatch.setattr(net, "_apply_address", lambda *args: calls.append(args))
    assert net.configure("eth-test", "coordinator") is False
    assert calls == []


def test_apply_address_uses_temporary_windows_store(monkeypatch):
    calls = []
    monkeypatch.setattr(net.platform, "system", lambda: "Windows")
    monkeypatch.setattr(net, "_powershell", lambda script, values: calls.append((script, values)) or "")
    profile = net.make_profile("coordinator")
    net._apply_address("12", profile)
    assert "-PolicyStore ActiveStore" in calls[0][0]
    assert "DefaultGateway" not in calls[0][0]
    assert "Firewall" not in calls[0][0]
    assert calls[0][1]["interface"] == "12"


def test_native_command_errors_keep_diagnostics_and_do_not_use_a_shell(monkeypatch):
    observed = []
    def fail(args, **kwargs):
        observed.append((args, kwargs))
        return net.subprocess.CompletedProcess(args, 1, stdout="", stderr="access denied")
    monkeypatch.setattr(net.subprocess, "run", fail)
    with pytest.raises(net.NetworkError, match="access denied"):
        net._run(["ip", "address", "add", "192.168.10.1/24", "dev", "eth0"])
    args, kwargs = observed[0]
    assert isinstance(args, list)
    assert kwargs.get("shell", False) is False


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        ("Darwin", ["ifconfig", "en3", "inet", "192.168.10.1", "netmask", "255.255.255.0", "alias"]),
        ("Linux", ["ip", "address", "add", "192.168.10.1/24", "dev", "eth-test"]),
    ],
)
def test_apply_address_is_interface_scoped(monkeypatch, system, expected):
    calls = []
    monkeypatch.setattr(net.platform, "system", lambda: system)
    monkeypatch.setattr(net, "_run", lambda args, **_kwargs: calls.append(list(args)) or "")
    interface = "en3" if system == "Darwin" else "eth-test"
    net._apply_address(interface, net.make_profile("coordinator"))
    assert calls == [expected]


def test_windows_inspection_reads_dhcp_link_speed_and_routes(monkeypatch):
    payload = {
        "Name": "Ethernet 10G",
        "Status": "Up",
        "LinkSpeed": "10 Gbps",
        "Hardware": True,
        "Description": "PCIe Ethernet Adapter",
        "Dhcp": False,
        "Addresses": [{"IPAddress": "192.168.10.1", "PrefixLength": 24}],
        "Routes": ["172.16.0.0/16"],
    }
    monkeypatch.setattr(net, "_powershell", lambda *_args: json.dumps(payload))
    inspected = net._inspect_windows("12")
    assert inspected.connected and inspected.physical_ethernet
    assert inspected.speed_mbps == 10_000 and inspected.dhcp is False
    assert inspected.addresses == (ipaddress.ip_interface("192.168.10.1/24"),)
    assert inspected.routes == (ipaddress.ip_network("172.16.0.0/16"),)


def test_macos_inspection_maps_service_and_parses_media(monkeypatch):
    outputs = {
        ("networksetup", "-listnetworkserviceorder"): "(1) 10Gb Ethernet\n(Hardware Port: Ethernet, Device: en3)",
        ("ifconfig", "en3"): "en3: flags=8863<UP>\n\tinet 192.168.10.1 netmask 0xffffff00\n\tstatus: active",
        ("networksetup", "-getinfo", "10Gb Ethernet"): "Manual Configuration\nIP address: 192.168.10.1",
        ("networksetup", "-getmedia", "10Gb Ethernet"): "Current: autoselect\nActive: 10Gbase-T <full-duplex>",
        ("netstat", "-rn", "-f", "inet"): "Routing tables\nInternet:\nDestination Gateway Flags Netif\n172.16.0.0/16 link#1 U en2",
    }
    monkeypatch.setattr(net, "_run", lambda args, **_kwargs: outputs[tuple(args)])
    inspected = net._inspect_macos("en3")
    assert inspected.connected and inspected.physical_ethernet
    assert inspected.speed_mbps == 10_000 and inspected.dhcp is False
    assert inspected.addresses == (ipaddress.ip_interface("192.168.10.1/24"),)


def test_linux_inspection_reads_speed_and_address(monkeypatch, tmp_path):
    iface = tmp_path / "eth-test"
    iface.mkdir()
    (iface / "operstate").write_text("up", encoding="ascii")
    (iface / "speed").write_text("10000", encoding="ascii")
    (iface / "device").mkdir()
    monkeypatch.setattr(net, "SYS_CLASS_NET", tmp_path)
    monkeypatch.setattr(net, "_linux_dhcp", lambda _interface: False)
    monkeypatch.setattr(
        net,
        "_run",
        lambda args, **_kwargs: (
            "2: eth-test inet 192.168.10.1/24 brd 192.168.10.255 scope global eth-test"
            if "address" in args
            else '[{"dst":"default"},{"dst":"172.16.0.0/16"}]'
        ),
    )
    inspected = net._inspect_linux("eth-test")
    assert inspected.connected and inspected.physical_ethernet
    assert inspected.speed_mbps == 10_000 and inspected.dhcp is False
    assert inspected.addresses == (ipaddress.ip_interface("192.168.10.1/24"),)
    assert inspected.routes == (ipaddress.ip_network("172.16.0.0/16"),)


@pytest.mark.parametrize(
    ("result", "expected"),
    [(True, "reachable"), (False, "unreachable"), (None, "unable-to-verify")],
)
def test_verify_reports_distinct_probe_outcomes(monkeypatch, result, expected):
    calls = []
    monkeypatch.setattr(net, "_ping", lambda interface, peer: calls.append((interface, peer)) or result)
    monkeypatch.setattr(net, "_apply_address", lambda *_args: pytest.fail("verification mutated network state"))
    assert net.verify("eth-test", "192.168.10.2") == expected
    assert calls == [("eth-test", "192.168.10.2")]


def test_verify_validates_peer_and_requires_interface(monkeypatch):
    with pytest.raises(net.NetworkError, match="peer"):
        net.verify("eth-test", "not-an-ip")
    with pytest.raises(net.NetworkError, match="--interface"):
        net.verify("", "192.168.10.2")


def test_verify_rejects_ipv6_peer():
    with pytest.raises(net.NetworkError, match="IPv4"):
        net.verify("eth-test", "2001:db8::1")


def test_verify_rejects_loopback_peer():
    with pytest.raises(net.NetworkError, match="usable unicast"):
        net.verify("eth-test", "127.0.0.1")


def test_linux_inspection_without_dhcp_detector_fails_closed(monkeypatch, tmp_path):
    iface = tmp_path / "eth-test"
    iface.mkdir()
    (iface / "operstate").write_text("up", encoding="ascii")
    (iface / "speed").write_text("10000", encoding="ascii")
    (iface / "device").mkdir()
    monkeypatch.setattr(net, "SYS_CLASS_NET", tmp_path)
    monkeypatch.setattr(net, "_linux_dhcp", lambda _interface: None)
    monkeypatch.setattr(net, "_run", lambda args, **_kwargs: "[]" if args[:3] == ["ip", "-j", "-4"] else "")
    # The missing routes and addresses return empty data; configure must still
    # refuse because DHCP state cannot be established.
    inspected = net._inspect_linux("eth-test")
    monkeypatch.setattr(net, "is_admin", lambda: True)
    monkeypatch.setattr(net, "inspect_interface", lambda _interface: inspected)
    with pytest.raises(net.NetworkError, match="DHCP"):
        net.configure("eth-test", "coordinator")


def test_windows_verify_distinguishes_missing_route(monkeypatch):
    calls = []
    monkeypatch.setattr(net.platform, "system", lambda: "Windows")
    monkeypatch.setattr(net, "_powershell", lambda *_args: "unreachable")
    monkeypatch.setattr(net.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    assert net._ping("12", "192.168.10.2") is False
    assert calls == []


def test_windows_verify_binds_ping_to_selected_interface_source(monkeypatch):
    seen = []
    monkeypatch.setattr(net.platform, "system", lambda: "Windows")
    monkeypatch.setattr(net, "_powershell", lambda *_args: "routed:192.168.10.1")
    monkeypatch.setattr(
        net.subprocess,
        "run",
        lambda args, **_kwargs: seen.append(args) or type("Result", (), {"returncode": 0})(),
    )
    assert net._ping("12", "192.168.10.2") is True
    assert seen == [["ping.exe", "-n", "1", "-w", "2000", "-S", "192.168.10.1", "192.168.10.2"]]


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        ("Darwin", ["ping", "-n", "-c", "1", "-W", "2000", "-S", "192.168.10.1", "192.168.10.2"]),
        ("Linux", ["ping", "-n", "-c", "1", "-W", "2", "-I", "eth-test", "192.168.10.2"]),
    ],
)
def test_peer_probe_is_bound_to_selected_interface(monkeypatch, system, expected):
    seen = []
    monkeypatch.setattr(net.platform, "system", lambda: system)
    monkeypatch.setattr(net, "_interface_local_address", lambda *_args: "192.168.10.1")
    monkeypatch.setattr(
        net.subprocess,
        "run",
        lambda args, **_kwargs: seen.append(args) or type("Result", (), {"returncode": 0})(),
    )
    interface = "en3" if system == "Darwin" else "eth-test"
    assert net._ping(interface, "192.168.10.2") is True
    assert seen == [expected]


def test_verify_rejects_unsupported_os(monkeypatch):
    monkeypatch.setattr(net.platform, "system", lambda: "FreeBSD")
    with pytest.raises(net.NetworkError, match="unsupported operating system"):
        net.verify("em0", "192.168.10.2")


def test_linux_dhcp_reads_active_connection_profile(monkeypatch):
    calls = []
    connection = "12345678-1234-1234-1234-123456789abc"
    monkeypatch.setattr(net.shutil, "which", lambda name: "/usr/bin/nmcli" if name == "nmcli" else None)
    def run(args, **kwargs):
        calls.append(args)
        return connection if "GENERAL.CON-UUID" in args else "manual"
    monkeypatch.setattr(net, "_run", run)
    assert net._linux_dhcp("eth0") is False
    assert calls[-1] == ["/usr/bin/nmcli", "-g", "ipv4.method", "connection", "show", "uuid", connection]
