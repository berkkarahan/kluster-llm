"""Direct administrator composition, native-policy delegation and owned real pipes."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from kllm.modules.managed_network.interactors import direct_client, service as service_module
from kllm.modules.managed_network.interactors.direct_client import DirectClient
from kllm.modules.managed_network.interactors.direct_platform import DirectPortPlatform
from kllm.modules.managed_network.interactors.helper_platform import HelperPlatform
from kllm.modules.managed_network.interactors.service import ManagedNetworkService
from kllm.shared.platform.base import PlatformAdapter, PlatformRefusal


@contextmanager
def child(tmp_path):
    grant = {"account": "fixture", "node_id": "worker", "cluster_binding_id": str(uuid4()), "master_public_key": "fixture"}
    client = DirectClient(tmp_path, lambda: tmp_path, lambda: grant)
    client.home.mkdir(parents=True)
    client.config.write_text(json.dumps(grant))
    process = subprocess.Popen([sys.executable, str(Path(__file__).parent / "fixtures" / "prima-direct-child.py"),
        str(client.config)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    client.process, client.bound = process, grant
    try:
        yield client, process
    finally:
        client.close()
        assert process.poll() == 0
        assert not any(t.name == "kllm-helper-io-deadline" for t in threading.enumerate())
        process.stderr.close()


@pytest.mark.skipif(os.name != "nt", reason="Real synchronous Windows subprocess pipes")
@pytest.mark.parametrize("action", ["delay", "partial"])
def test_direct_pipe_correlates_late_admission_and_cleanup_replies(tmp_path, monkeypatch, action):
    with child(tmp_path) as (client, _):
        assert client.status(client.grant())["ready"] is True
        monkeypatch.setattr(direct_client, "HELPER_QUERY_TIMEOUT", 0.05)
        with pytest.raises(PlatformRefusal) as error:
            client.request(action)
        assert error.value.code == "TIMEOUT"
        # A late successful admission reply must not acknowledge this release.
        assert client.request("prima_ports_release", wire="complete-original-envelope") == {"phase": "released"}
        requests = [json.loads(line) for line in client.config.with_suffix(".requests").read_text().splitlines()]
        assert requests[-1]["wire"] == "complete-original-envelope"
        assert len({r["request_id"] for r in requests}) == len(requests)


@pytest.mark.skipif(os.name != "nt", reason="Real synchronous Windows subprocess pipes")
def test_direct_invalid_envelope_cannot_publish_ready(tmp_path):
    with child(tmp_path) as (client, _):
        with pytest.raises(PlatformRefusal) as error:
            client.request("malformed")
        assert error.value.code == "HELPER_REFUSED"


@pytest.mark.skipif(os.name != "nt", reason="Real synchronous Windows subprocess pipes")
@pytest.mark.parametrize("invalid", ["missing_runs", "missing_scopes", "null_runs", "mapping_scopes", "foreign_grant", "non_object"])
def test_grant_rotation_requires_explicit_matching_empty_ownership(tmp_path, monkeypatch, invalid):
    with child(tmp_path) as (client, process):
        original = client.grant()
        assert client.status(original)["ready"]
        next_grant = {**original, "cluster_binding_id": str(uuid4())}
        client.grant = lambda: next_grant
        status = {"prima_direct_version": 1, "grant": original,
                  "prima_ports_owned_runs": [], "prima_direct_active_scopes": []}
        if invalid == "missing_runs":
            del status["prima_ports_owned_runs"]
        elif invalid == "missing_scopes":
            del status["prima_direct_active_scopes"]
        elif invalid == "null_runs":
            status["prima_ports_owned_runs"] = None
        elif invalid == "mapping_scopes":
            status["prima_direct_active_scopes"] = {}
        elif invalid == "foreign_grant":
            status["grant"] = next_grant
        else:
            status = []
        client.config.with_suffix(".status").write_text(json.dumps(status))
        client.journal.write_text(json.dumps({"prima_ports": {}, "scopes": {}}))
        config_before, journal_before = client.config.read_bytes(), client.journal.read_bytes()
        monkeypatch.setattr(client, "_start", lambda: pytest.fail("Unverified ownership retired the native owner"))
        with pytest.raises(PlatformRefusal) as error:
            client.request("authorize", network=str(uuid4()), wire="unchanged-signed-envelope")
        assert error.value.code == "UPGRADE_REQUIRED"
        assert client.process is process and process.poll() is None
        assert client.config.read_bytes() == config_before and client.journal.read_bytes() == journal_before
        assert not list(client.home.glob("authorization-*.journal.json"))
        requests = [json.loads(line) for line in client.config.with_suffix(".requests").read_text().splitlines()]
        assert all(request["action"] == "status" for request in requests)


@pytest.mark.skipif(os.name != "nt", reason="Real synchronous Windows subprocess pipes")
@pytest.mark.parametrize("owned_key", ["prima_ports_owned_runs", "prima_direct_active_scopes"])
def test_grant_rotation_retains_an_owned_native_scope(tmp_path, owned_key):
    with child(tmp_path) as (client, process):
        original = client.grant()
        assert client.status(original)["ready"]
        client.grant = lambda: {**original, "cluster_binding_id": str(uuid4())}
        status = {"prima_direct_version": 1, "grant": original,
                  "prima_ports_owned_runs": [], "prima_direct_active_scopes": []}
        status[owned_key] = [str(uuid4())]
        client.config.with_suffix(".status").write_text(json.dumps(status))
        with pytest.raises(PlatformRefusal) as error:
            client.request("authorize", network=str(uuid4()), wire="original-wire")
        assert error.value.code == "CLEANUP_PENDING"
        assert client.process is process and process.poll() is None
        assert json.loads(client.config.read_text()) == original


@pytest.mark.skipif(os.name != "nt", reason="Real synchronous Windows subprocess pipes")
@pytest.mark.parametrize("compatible", [False, True])
def test_replacement_owner_is_probed_before_signed_authorization(tmp_path, monkeypatch, compatible):
    with child(tmp_path) as (client, old_process):
        original = client.grant()
        assert client.status(original)["ready"]
        expected = {**original, "cluster_binding_id": str(uuid4())}
        client.grant = lambda: expected
        client.journal.write_text(json.dumps({"prima_ports": {}, "scopes": {}}))

        def start_replacement():
            assert old_process.poll() == 0
            assert json.loads(client.config.read_text()) == expected
            if not compatible:
                client.config.with_suffix(".status").write_text(json.dumps({"prima_direct_version": 0}))
            client.process = subprocess.Popen([sys.executable,
                str(Path(__file__).parent / "fixtures" / "prima-direct-child.py"), str(client.config)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            client.bound, client.probed = expected, False

        monkeypatch.setattr(client, "_start", start_replacement)
        if compatible:
            assert client.request("authorize", network=str(uuid4()), wire="original-signed-wire") == {"ok": True}
        else:
            with pytest.raises(PlatformRefusal) as error:
                client.request("authorize", network=str(uuid4()), wire="original-signed-wire")
            assert error.value.code == "UPGRADE_REQUIRED"
        replacement = client.process
        client.close()
        assert replacement.poll() == 0
        replacement.stderr.close()
        requests = [json.loads(line) for line in client.config.with_suffix(".requests").read_text().splitlines()]
        assert requests[-1]["action"] == ("authorize" if compatible else "status")
        if compatible:
            assert requests[-2]["action"] == "status" and requests[-1]["wire"] == "original-signed-wire"
        else:
            assert not any(request["action"] == "authorize" for request in requests)
        assert len(list(client.home.glob("authorization-*.journal.json"))) == 1


def test_direct_refuses_start_without_current_administrator_privilege(tmp_path, monkeypatch):
    monkeypatch.setattr(direct_client, "is_admin", lambda: False)
    client = DirectClient(tmp_path, lambda: tmp_path, lambda: {"node_id": "worker"})
    status = client.status()
    assert status["ready"] is False and status["error_code"] == "ADMIN_REQUIRED"
    assert client.process is None and not client.config.exists()


def test_direct_manual_operations_never_call_helper_address_mutation():
    events = []
    underlying = SimpleNamespace(runtime="native", inventory=lambda: [], inspect=lambda _: "snapshot",
        eligible=lambda s: events.append(("eligible", s)), add_exact=lambda *a: events.append("manual-add"),
        delete_exact=lambda *a: events.append("manual-delete"), restore_configuration=lambda *_: events.append("manual-restore"))
    requests = []
    native = SimpleNamespace(request=lambda action, **values: requests.append((action, values)) or {"ok": True})
    platform = DirectPortPlatform(underlying, native, lambda: {})
    platform.eligible("snapshot")
    platform.add_exact(None, None)
    platform.authorize(SimpleNamespace(phase="cleanup", network_id=str(uuid4())))
    platform.delete_exact(None, None)
    platform.restore_configuration("network")
    assert platform.runtime == "native"
    assert events == [("eligible", "snapshot"), "manual-add", "manual-delete", "manual-restore"]
    assert {r[0] for r in requests} == {"direct_forget"}


def test_live_prima_blocks_manual_cleanup_before_any_actor_or_address_call():
    platform = DirectPortPlatform(SimpleNamespace(runtime="native"), SimpleNamespace(), lambda: {}, lambda: True)
    for call in (lambda: platform.authorize(SimpleNamespace(phase="cleanup", network_id="network")),
                 lambda: platform.delete_exact(None, None), lambda: platform.restore_configuration("network")):
        with pytest.raises(PlatformRefusal) as error:
            call()
        assert error.value.code == "INFERENCE_ACTIVE"


def test_direct_apply_observes_even_user_owned_address_after_manual_barrier(monkeypatch):
    events = []
    snapshot = object()
    monkeypatch.setattr(PlatformAdapter, "apply", lambda *_args: events.append("manual-barrier") or snapshot)
    native = SimpleNamespace(request=lambda action, **values: events.append(action) or {"ok": True})
    platform = DirectPortPlatform(SimpleNamespace(runtime="native"), native, lambda: {})
    prepared = SimpleNamespace(assignment=SimpleNamespace(network_id=uuid4()), user_owned=True)
    assert platform.apply(prepared, None, lambda: events.append("fence")) is snapshot
    assert events == ["manual-barrier", "fence", "direct_observe", "fence"]


@pytest.mark.skipif(os.name != "nt", reason="Windows default platform selection")
@pytest.mark.parametrize("saved_direct,old_ports,protected_ready,admin", [(False,False,False,True),(True,True,False,True),(False,True,False,True),(False,False,True,True),(True,True,True,False)])
def test_startup_never_switches_an_owned_port_ledger(tmp_path, monkeypatch, saved_direct, old_ports, protected_ready, admin):
    underlying = SimpleNamespace(runtime="native")
    monkeypatch.setattr(service_module, "platform_adapter", lambda: underlying)
    monkeypatch.setattr(service_module, "is_admin", lambda: admin)
    state = SimpleNamespace(node_id="master", role="master", join_key="fixture", members={}, master_host=None)
    service = ManagedNetworkService(tmp_path, lambda: state, lambda: "http://127.0.0.1")
    try:
        service.guided.helper.status = lambda *_: {"ready": protected_ready}
        service.runtimes = SimpleNamespace(registry=SimpleNamespace(root=tmp_path / "runtimes"))
        if old_ports:
            path = service.runtimes.registry.root / "prima-control" / "ports.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"persisted_owner": True}))
        if saved_direct:
            path = tmp_path / "prima-direct" / "authorization.json"
            path.parent.mkdir()
            path.write_text("{}")
        service._configure_direct_platform()
        direct = saved_direct or (not old_ports and not protected_ready)
        assert isinstance(service.executor.platform, DirectPortPlatform if direct else HelperPlatform)
        assert service.executor.platform.underlying is underlying
        assert not isinstance(service.executor.platform, HelperPlatform) if direct else True
    finally:
        service.store.close()


def test_shutdown_cannot_forget_orphan_native_cleanup(tmp_path):
    client = DirectClient(tmp_path, lambda: tmp_path, lambda: {})
    client.home.mkdir(parents=True)
    client.journal.write_text(json.dumps({"prima_ports": {"owned": {}}}))
    with pytest.raises(PlatformRefusal) as error:
        client.close()
    assert error.value.code == "CLEANUP_PENDING"


def test_direct_privilege_loss_is_not_hidden_by_an_available_protected_service():
    from kllm.modules.managed_network.interactors.guided_setup import GuidedSetup

    client = SimpleNamespace(status=lambda: {"ready": False, "error_code": "ADMIN_REQUIRED",
        "reason": "Restart the agent with administrator privileges for its remembered direct cleanup"})
    platform = DirectPortPlatform(SimpleNamespace(runtime="native"), client, lambda: {})
    guided = object.__new__(GuidedSetup)
    guided.service = SimpleNamespace(executor=SimpleNamespace(platform=platform))
    guided.grant = lambda: {"node_id": "worker"}
    guided.helper = SimpleNamespace(status=lambda *_: {"ready": True})
    status = guided.local_status()
    assert not status["administrator_enabled"] and status["error_code"] == "ADMIN_REQUIRED"
    assert "remembered direct cleanup" in status["message"]
