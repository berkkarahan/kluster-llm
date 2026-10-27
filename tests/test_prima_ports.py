"""Manifest/private-listener trust boundaries; no host firewall mutation."""

import json
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from kllm.modules.managed_network.entities import NodeAssignment
from kllm.modules.managed_network.interactors.helper_platform import HelperPlatform
from kllm.modules.managed_network.interactors.identity import SignedRuntimeCommand
from kllm.modules.managed_network.interactors.prima_ports import PrimaPortContract, port_contract
from kllm.modules.primacpp.entities import LayerRange, PrimaTopology, RankAllocation
from kllm.modules.primacpp.interactors.control import PrimaControl
from kllm.modules.primacpp.interactors.execution import Asset, ExecutionManifest, Segment
from kllm.modules.primacpp.interactors.placement import network_edges


@pytest.fixture
def prepared(tmp_path):
    network = str(uuid4())
    names = ["master", "worker1", "worker2"]
    ranks = [
        RankAllocation(
            node_id=node,
            role="master" if i == 0 else "worker",
            rank=i,
            address=f"192.168.234.{i + 1}",
            next_node_id=names[(i + 1) % 3],
            layers=[] if i == 0 else [LayerRange(start=(i - 1) * 4, end=i * 4)],
            ngl=0,
        )
        for i, node in enumerate(names)
    ]
    topology = PrimaTopology(
        runtime_id="a" * 64, master_node_id="master", total_layers=8, ranks=ranks
    )
    manifest = ExecutionManifest(
        runtime_id=topology.runtime_id,
        run_id=str(uuid4()),
        plan_digest="b" * 64,
        total_layers=8,
        ranks=ranks,
        settings={"context": 512, "port": 8080},
        network_id=network,
        inventory_digest="c" * 64,
        assets=[Asset(name="model.gguf", kind="model", sha256="d" * 64, size=1)],
        segments=[
            Segment(start=w.start, end=w.end, node_id=r.node_id) for r in ranks for w in r.layers
        ],
        backends={node: "cpu" for node in names},
        transfer_edges=network_edges(topology),
    )
    assignments = [
        NodeAssignment(
            network_id=network,
            node_id=r.node_id,
            adapter_id="4",
            os_interface="test",
            identity_fingerprint=str(i + 1) * 64,
            address=r.address,
            prefix_length=24,
            assignment_revision=7,
        )
        for i, r in enumerate(ranks)
    ]
    path = tmp_path / "execution.json"
    path.write_text(manifest.model_dump_json())
    return manifest, assignments, path


def command(manifest, contract):
    return SignedRuntimeCommand.model_validate(
        {
            "cluster_binding_id": str(uuid4()),
            "master_node_id": "master",
            "master_key_id": "e" * 64,
            "master_term": 1,
            "master_session_id": str(uuid4()),
            "version": 1,
            "type": "rpc_command",
            "channel_id": str(uuid4()),
            "sender_node_id": "master",
            "target_node_id": "worker1",
            "sequence": 1,
            "request_id": str(uuid4()),
            "run_id": manifest.run_id,
            "action": "prima_admit_ports",
            "ttl_ms": 60000,
            "body": {"contract": contract.model_dump(mode="json")},
        }
    )


def test_exact_inbound_rules_deduplicate_worker_replies_without_http_or_rpc(prepared):
    manifest, assignments, _ = prepared
    head = port_contract(manifest, "master", assignments)
    assert head.model_dump(mode="json") == {
        "network_id": manifest.network_id,
        "manifest_digest": manifest.digest,
        "address": "192.168.234.1",
        "assignment_revision": 7,
        "rules": [{"port": manifest.data_port, "peers": ["192.168.234.2", "192.168.234.3"]}],
    }
    worker = port_contract(manifest, "worker1", assignments)
    assert worker.model_dump()["rules"] == [
        {"port": manifest.data_port + 1, "peers": ["192.168.234.1"]},
        {"port": manifest.signal_port + 1, "peers": ["192.168.234.1"]},
    ]
    assert not {8080, 50052}.intersection(r.port for r in worker.rules + head.rules)


@pytest.mark.parametrize(
    "change", ["edge", "target", "address", "network", "missing", "duplicate", "subnet"]
)
def test_changed_private_topology_never_produces_an_admission_contract(prepared, change):
    manifest, assignments, _ = prepared
    if change == "edge":
        manifest.transfer_edges[0]["port"] += 1
    elif change == "target":
        manifest.transfer_edges[0]["target"] = "unknown"
    elif change == "address":
        assignments[0].address = "192.168.234.99"
    elif change == "network":
        assignments[0].network_id = uuid4()
    elif change == "missing":
        assignments.pop()
    elif change == "duplicate":
        assignments.append(assignments[0])
    else:
        assignments[0].prefix_length = 30
    with pytest.raises(ValueError):
        port_contract(manifest, "master", assignments)


@pytest.mark.parametrize(
    "change",
    [
        "wildcard",
        "public",
        "loopback",
        "reserved",
        "cidr",
        "program",
        "bool-port",
        "duplicate-port",
    ],
)
def test_port_payload_is_bounded_and_has_no_shell_or_broad_access(prepared, change):
    manifest, assignments, _ = prepared
    value = port_contract(manifest, "master", assignments).model_dump(mode="json")
    peers = {
        "wildcard": "0.0.0.0",
        "public": "8.8.8.8",
        "loopback": "127.0.0.1",
        "reserved": "240.0.0.1",
        "cidr": "192.168.234.2/24",
    }
    if change in peers:
        value["rules"][0]["peers"] = [peers[change]]
    elif change == "program":
        value["program"] = "powershell.exe"
    elif change == "bool-port":
        value["rules"][0]["port"] = True
    else:
        value["rules"].append(deepcopy(value["rules"][0]))
    with pytest.raises(ValueError):
        PrimaPortContract.model_validate(value)


def test_helper_gateway_forwards_the_signed_bytes_and_exact_owner(prepared):
    manifest, assignments, _ = prepared
    contract = port_contract(manifest, "worker1", assignments)
    calls = []
    client = SimpleNamespace(
        request=lambda action, **values: calls.append((action, values)) or {"phase": "admitted"}
    )
    platform = HelperPlatform(SimpleNamespace(), client, lambda: None)
    signed = command(manifest, contract)
    signed._wire = "complete-signed-envelope"
    platform.apply_prima_ports(signed)
    assert calls == [
        (
            "prima_ports_apply",
            {"network": manifest.network_id, "run_id": manifest.run_id, "wire": signed._wire},
        )
    ]
    signed._wire = ""
    with pytest.raises(ValueError, match="signed"):
        platform.apply_prima_ports(signed)
    assert len(calls) == 1
    platform.release_prima_ports(manifest.network_id, manifest.run_id)
    assert calls[-1] == (
        "prima_ports_release",
        {"network": manifest.network_id, "run_id": manifest.run_id},
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"prima_ports_version": 1},
        {"prima_ports_watchdog_version": 0},
        {"prima_ports_watchdog_version": True},
        {"prima_ports_version": 2.0},
        {"prima_ports_owned_runs": ["owned"]},
        {"ready": False},
        {},
    ],
)
def test_helper_capability_requires_supported_watchdog_and_no_owned_rules(changes):
    import os

    status = {
        "ready": True,
        "prima_ports_version": 2,
        "prima_ports_watchdog_version": 1,
        "prima_ports_owned_runs": [],
        **changes,
    }
    client = SimpleNamespace(status=lambda _: status)
    platform = HelperPlatform(SimpleNamespace(), client, lambda: {"node_id": "fixture"})
    assert platform.prima_ports_available() is (not changes and os.name == "nt")


def test_signed_helper_renewal_preserves_original_frame(prepared):
    manifest, assignments, _ = prepared
    value = command(manifest, port_contract(manifest, "worker1", assignments))
    value.action = "prima_renew_ports"
    value._wire = "original-signed-renewal"
    calls = []
    client = SimpleNamespace(request=lambda action, **body: calls.append((action, body)) or {})
    platform = HelperPlatform(SimpleNamespace(), client, lambda: None)
    platform.renew_prima_ports(value)
    assert calls == [
        (
            "prima_ports_renew",
            {
                "network": manifest.network_id,
                "run_id": manifest.run_id,
                "wire": "original-signed-renewal",
            },
        )
    ]


def receiver(prepared, monkeypatch):
    from kllm.modules.primacpp.interactors.port_runs import PortSupervisor

    manifest, assignments, path = prepared
    events = []
    contract = port_contract(manifest, "worker1", assignments)
    platform = SimpleNamespace(
        apply_prima_ports=lambda c: (
            events.append("apply") or {"phase": "admitted", "manifest_digest": manifest.digest}
        ),
        release_prima_ports=lambda *args: events.append("release") or {"phase": "released"},
    )
    service = SimpleNamespace(
        state=lambda: SimpleNamespace(node_id="worker1", process_pid=None),
        prima_admission=SimpleNamespace(validate_worker=lambda m: events.append("private")),
        executor=SimpleNamespace(
            platform=platform,
            preparation={manifest.network_id: SimpleNamespace(participants=assignments)},
        ),
    )
    control = SimpleNamespace(
        service=service,
        rank=SimpleNamespace(owner=None),
        cached_model=lambda _: path,
        _approved_preparation=lambda *args: events.append("approved"),
        check_participant=lambda *args: events.append("hardware"),
        registry=SimpleNamespace(capture=lambda _: {}),
    )
    control.ports = PortSupervisor(
        path.parent / "ports.json", platform=lambda: platform, rank=lambda: control.rank
    )
    monkeypatch.setattr(
        "kllm.modules.primacpp.interactors.recipe.require_qualified",
        lambda _: events.append("qualified"),
    )
    return control, command(manifest, contract), events


@pytest.mark.asyncio
async def test_signed_worker_admission_validates_preparation_hardware_and_contract(
    prepared, monkeypatch
):
    control, signed, events = receiver(prepared, monkeypatch)
    result = await PrimaControl.execute(control, signed, time.monotonic() + 5, lambda: None)
    assert result == {"phase": "admitted", "manifest_digest": prepared[0].digest}
    assert events == ["approved", "private", "hardware", "qualified", "apply"]


@pytest.mark.asyncio
async def test_changed_contract_refuses_before_privileged_mutation(prepared, monkeypatch):
    control, signed, events = receiver(prepared, monkeypatch)
    signed.body["contract"]["rules"][0]["port"] += 1
    with pytest.raises(ValueError, match="listeners differ"):
        await PrimaControl.execute(control, signed, time.monotonic() + 5, lambda: None)
    assert "apply" not in events


@pytest.mark.asyncio
async def test_inexact_admission_rolls_back_owned_ports(prepared, monkeypatch):
    control, signed, events = receiver(prepared, monkeypatch)

    def bad_ack(_):
        events.append("apply")
        return {"phase": "admitted", "manifest_digest": "f" * 64}

    control.service.executor.platform.apply_prima_ports = bad_ack
    with pytest.raises(ValueError, match="acknowledge"):
        await PrimaControl.execute(control, signed, time.monotonic() + 5, lambda: None)
    assert events[-2:] == ["apply", "release"]


@pytest.mark.asyncio
async def test_authority_revoked_after_host_apply_cleans_up_before_acknowledgement(
    prepared, monkeypatch
):
    control, signed, events = receiver(prepared, monkeypatch)

    def authorize():
        if "apply" in events:
            raise ValueError("authority revoked")

    with pytest.raises(ValueError, match="authority revoked"):
        await PrimaControl.execute(control, signed, time.monotonic() + 5, authorize)
    assert events[-2:] == ["apply", "release"]


def test_native_contract_vector_matches_python_signed_protocol():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from kllm.modules.managed_network.interactors.identity import LocalIdentity, encode

    wire = (Path(__file__).parent / "fixtures" / "prima-ports-command.json").read_text().strip()
    public = (
        Ed25519PrivateKey.from_private_bytes(bytes([7]) * 32)
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    value = LocalIdentity.verify(wire, encode(public))
    signed = SignedRuntimeCommand.model_validate(value)
    contract = PrimaPortContract.model_validate(signed.body["contract"])
    assert signed.action == "prima_admit_ports" and contract.assignment_revision == 1
    assert json.loads(wire)["payload"] == json.dumps(
        signed.model_dump(mode="json"), separators=(",", ":")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [None, "idle", "failed", "digest", "port", "authority"])
async def test_native_renewal_requires_live_rank_exact_contract_and_authority(
    prepared, monkeypatch, changed
):
    control, signed, events = receiver(prepared, monkeypatch)
    await PrimaControl.execute(control, signed, time.monotonic() + 5, lambda: None)
    manifest = prepared[0]
    platform = control.service.executor.platform
    platform.prima_ports_status = lambda *_: {
        "phase": "admitted",
        "manifest_digest": manifest.digest,
    }
    platform.renew_prima_ports = lambda _: (
        events.append("native-renew") or {"phase": "admitted", "manifest_digest": manifest.digest}
    )
    control._check_worker_network = lambda _: events.append("network-renew")
    control.rank.status = lambda *_args, **_kwargs: {
        "phase": changed if changed in ("idle", "failed") else "ready"
    }
    signed.action = "prima_renew_ports"
    if changed == "digest":
        signed.body["contract"]["manifest_digest"] = "f" * 64
    elif changed == "port":
        signed.body["contract"]["rules"][0]["port"] += 1
    elif changed == "authority":
        signed.master_session_id = uuid4()
    if changed is None:
        result = await PrimaControl.execute(control, signed, time.monotonic() + 5, lambda: None)
        assert result == {"phase": "admitted", "manifest_digest": manifest.digest}
        assert events[-1] == "native-renew"
    else:
        with pytest.raises(ValueError):
            await PrimaControl.execute(control, signed, time.monotonic() + 5, lambda: None)
        assert "native-renew" not in events


@pytest.mark.asyncio
async def test_listener_cleanup_requires_the_owned_rank_to_stop_first(prepared, monkeypatch):
    control, signed, events = receiver(prepared, monkeypatch)
    signed.action = "prima_release_ports"
    signed.body = {"network_id": prepared[0].network_id}
    control.rank.owner = {"run_id": prepared[0].run_id}
    with pytest.raises(ValueError, match="Stop the owned"):
        await PrimaControl.execute(control, signed, time.monotonic() + 5, lambda: None)
    assert not events
    control.rank.owner = None
    assert await PrimaControl.execute(control, signed, time.monotonic() + 5, lambda: None) == {
        "phase": "released"
    }
    assert events == ["release"]
