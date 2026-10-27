from __future__ import annotations

import ipaddress
import json
import sys

import pytest

from kllm.modules.cluster.interactors import discovery
from kllm.modules.cluster.interactors.discovery import DiscoveredMaster, browse_masters, join_key_id


def test_join_key_id_stable_and_truncated():
    a = join_key_id("secret-key")
    b = join_key_id("secret-key")
    assert a == b
    assert len(a) == 16
    assert a != join_key_id("other-key")
    assert all(c in "0123456789abcdef" for c in a)


def test_browse_masters_key_filter_unit():
    """Filter predicate used by browse_masters when key is provided."""
    kid = join_key_id("abc")
    found = [
        DiscoveredMaster(name="m1", host="1.1.1.1", port=18765, key_id=kid),
        DiscoveredMaster(name="m2", host="2.2.2.2", port=18765, key_id="deadbeefdeadbeef"),
        DiscoveredMaster(name="m3", host="3.3.3.3", port=18765, key_id=None),
    ]
    want = join_key_id("abc")
    filtered = [m for m in found if m.key_id == want]
    assert filtered == [found[0]]


def test_browse_masters_falls_back_to_helpers(monkeypatch):
    """When in-process mDNS is empty (LAN-blocked), use helper browse results."""
    kid = join_key_id("secret")
    monkeypatch.setattr(
        "kllm.modules.cluster.interactors.discovery._browse_inprocess",
        lambda timeout=2.0, key=None: [],
    )
    monkeypatch.setattr(
        "kllm.modules.cluster.interactors.discovery._browse_via_native",
        lambda timeout, key, trace=None: None,
    )
    monkeypatch.setattr(
        "kllm.modules.cluster.interactors.discovery._browse_via_python",
        lambda timeout, key, trace=None: [
            DiscoveredMaster(name="m", host="192.168.68.63", port=18765, key_id=kid),
        ],
    )
    monkeypatch.setattr(
        "kllm.modules.cluster.interactors.discovery_http._browse_via_http",
        lambda timeout, key, port=18765: (_ for _ in ()).throw(
            AssertionError("http should not run")
        ),
    )
    found = browse_masters(timeout=0.1, key="secret")
    assert len(found) == 1
    assert found[0].host == "192.168.68.63"


def test_browse_masters_uses_native_before_python(monkeypatch):
    monkeypatch.setattr(
        "kllm.modules.cluster.interactors.discovery._browse_inprocess",
        lambda timeout=2.0, key=None: [],
    )
    monkeypatch.setattr(
        "kllm.modules.cluster.interactors.discovery._browse_via_native",
        lambda timeout, key, trace=None: [
            DiscoveredMaster(name="m", host="10.10.10.2", port=18765, key_id="abcd"),
        ],
    )
    monkeypatch.setattr(
        "kllm.modules.cluster.interactors.discovery._browse_via_python",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("python should not run")),
    )
    found = browse_masters(timeout=0.1, key=None)
    assert found[0].host == "10.10.10.2"


def test_helper_probe_module_resolves_under_its_interpreter(monkeypatch):
    """The helper must run as ``python -m ...mdns_probe``, not a generated script."""
    monkeypatch.setattr(discovery, "_mdns_helper_candidates", lambda: [sys.executable])
    result = discovery._browse_via_python(0.05, None)
    assert result is not None  # browse completed, even when it found nothing


def test_interpreter_gate_accepts_apple_python_39():
    assert discovery._interpreter_supported((3, 9))
    assert discovery._interpreter_supported((3, 13))
    assert not discovery._interpreter_supported((3, 8))
    assert not discovery._interpreter_supported(None)


def test_browse_masters_skips_helpers_when_inprocess_finds(monkeypatch):
    calls = {"helpers": 0}
    monkeypatch.setattr(
        "kllm.modules.cluster.interactors.discovery._browse_inprocess",
        lambda timeout=2.0, key=None: [
            DiscoveredMaster(name="m", host="10.0.0.1", port=18765, key_id="abcd"),
        ],
    )

    def boom(*_args, **_kwargs):
        calls["helpers"] += 1
        raise AssertionError("helpers should not run")

    monkeypatch.setattr("kllm.modules.cluster.interactors.discovery._browse_via_native", boom)
    monkeypatch.setattr("kllm.modules.cluster.interactors.discovery._browse_via_python", boom)
    monkeypatch.setattr(
        "kllm.modules.cluster.interactors.discovery_http._browse_via_http",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("http should not run")),
    )
    found = browse_masters(timeout=0.1, key=None)
    assert found[0].host == "10.0.0.1"
    assert calls["helpers"] == 0


def test_browse_masters_falls_back_to_http(monkeypatch):
    """When mDNS and helpers are empty, HTTP /24 probe finds the master."""
    kid = join_key_id("secret")
    monkeypatch.setattr("kllm.modules.cluster.interactors.discovery._browse_inprocess", lambda timeout=2.0, key=None: [])
    monkeypatch.setattr("kllm.modules.cluster.interactors.discovery._browse_via_native", lambda timeout, key, trace=None: [])
    monkeypatch.setattr("kllm.modules.cluster.interactors.discovery._browse_via_python", lambda timeout, key, trace=None: [])
    monkeypatch.setattr(
        "kllm.modules.cluster.interactors.discovery_http._browse_via_http",
        lambda timeout, key, port=18765: [
            DiscoveredMaster(name="kllm-9aaafd02", host="192.168.68.100", port=port, key_id=kid),
        ],
    )
    found = browse_masters(timeout=0.1, key="secret")
    assert len(found) == 1
    assert found[0].host == "192.168.68.100"
    assert found[0].key_id == kid


def test_probe_http_master_matches_kid(monkeypatch):
    from kllm.modules.cluster.interactors.discovery_http import _probe_http_master

    kid = join_key_id("secret")
    payload = json.dumps({"ok": True, "role": "master", "node_id": "abc", "kid": kid}).encode()

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return payload

    monkeypatch.setattr("kllm.modules.cluster.interactors.discovery_http.urllib.request.urlopen", lambda *a, **k: _Resp())
    hit = _probe_http_master("192.168.68.100", 18765, kid, 0.2)
    assert hit is not None
    assert hit.host == "192.168.68.100"
    assert hit.key_id == kid

    miss = _probe_http_master("192.168.68.100", 18765, join_key_id("other"), 0.2)
    assert miss is None


def test_http_probe_hosts_covers_full_netmask(monkeypatch):
    """/22 LAN is probed in full, not truncated to the caller's /24."""
    from kllm.shared import netsec

    monkeypatch.setattr(
        netsec,
        "_list_ipv4_networks",
        lambda: [ipaddress.ip_network("192.168.68.0/22")],
    )
    hosts = discovery._http_probe_hosts(18765)
    assert "127.0.0.1" in hosts
    assert "192.168.68.64" in hosts
    assert "192.168.69.5" in hosts  # neighbouring /24 inside the /22
    assert "192.168.71.254" in hosts
    assert "192.168.72.1" not in hosts


def test_http_probe_hosts_caps_scan(monkeypatch):
    from kllm.shared import netsec

    monkeypatch.setattr(
        netsec,
        "_list_ipv4_networks",
        lambda: [ipaddress.ip_network("10.0.0.0/8")],
    )
    hosts = discovery._http_probe_hosts(18765)
    assert len(hosts) <= discovery.MAX_PROBE_HOSTS


def test_http_probe_hosts_falls_back_to_24(monkeypatch):
    """No interface masks available -> historical /24-per-IP behaviour."""
    from kllm.shared import netsec

    monkeypatch.setattr(netsec, "_list_ipv4_networks", lambda: [])
    monkeypatch.setattr(netsec, "_list_ipv4_addresses", lambda: ["192.168.68.57"])
    hosts = discovery._http_probe_hosts(18765)
    assert "127.0.0.1" in hosts
    assert "192.168.68.64" in hosts
    assert "192.168.69.1" not in hosts


def test_browse_trace_records_layers(monkeypatch):
    kid = join_key_id("secret")
    monkeypatch.setattr(discovery, "_browse_inprocess", lambda timeout=2.0, key=None: [])
    monkeypatch.setattr(discovery, "_browse_via_native", lambda timeout, key, trace=None: None)
    monkeypatch.setattr(
        discovery,
        "_browse_via_python",
        lambda timeout, key, trace=None: [
            DiscoveredMaster(name="m", host="10.10.10.2", port=18765, key_id=kid)
        ],
    )
    trace = discovery.BrowseTrace()
    found = browse_masters(timeout=0.1, key="secret", trace=trace)
    assert found
    assert trace.path == "python"
    assert trace.masters == 1
    assert "inprocess" in trace.summary()


def test_probe_command_parses_helper_json(monkeypatch):
    from types import SimpleNamespace

    payload = json.dumps(
        {
            "ok": True,
            "masters": [{"name": "m", "host": "10.0.0.5", "port": 18765, "key_id": "abcd"}],
            "reason": None,
            "detail": None,
        }
    )
    monkeypatch.setattr(
        discovery.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=payload, stderr=""),
    )
    found, attempt = discovery._run_probe_command(
        ["helper"], layer="native", target="helper", timeout=0.1
    )
    assert attempt.ok
    assert found is not None and found[0].host == "10.0.0.5"


def test_probe_command_reports_failure_reason(monkeypatch):
    from types import SimpleNamespace

    payload = json.dumps({"ok": False, "masters": [], "reason": "MDNS_BLOCKED", "detail": "denied"})
    monkeypatch.setattr(
        discovery.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout=payload, stderr=""),
    )
    found, attempt = discovery._run_probe_command(
        ["helper"], layer="native", target="helper", timeout=0.1
    )
    assert found is None
    assert attempt.reason == "MDNS_BLOCKED"
    assert attempt.detail == "denied"


def test_mdns_probe_decodes_compressed_names():
    from kllm.modules.cluster.interactors.mdns_probe import _read_name

    data = b"\x05local\x00" + b"\x05_kllm\xc0\x00"
    name, next_offset = _read_name(data, 0)
    assert name == "local."
    assert next_offset == 7
    name, next_offset = _read_name(data, 7)
    assert name == "_kllm.local."
    assert next_offset == 15


def test_mdns_probe_rejects_pointer_loops():
    from kllm.modules.cluster.interactors.mdns_probe import _read_name

    with pytest.raises(ValueError):
        _read_name(b"\xc0\x00", 0)


def test_mdns_probe_cli_speaks_json_protocol():
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", discovery.PROBE_MODULE, "--timeout", "0.05"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert isinstance(payload["masters"], list)
