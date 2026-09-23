from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kllm import mpi_runtime


def test_reserve_private_port_rejects_public_and_unspecified_addresses():
    with pytest.raises(mpi_runtime.MpiRuntimeError, match="fixed private"):
        mpi_runtime.reserve_private_port("0.0.0.0")
    with pytest.raises(mpi_runtime.MpiRuntimeError, match="fixed private"):
        mpi_runtime.reserve_private_port("8.8.8.8")


def test_reserve_private_port_uses_the_fixed_range():
    with mpi_runtime.reserve_private_port("127.0.0.1") as reservation:
        port = int(reservation.address.rsplit(":", 1)[1])
        assert mpi_runtime.DATA_PORT_MIN <= port <= mpi_runtime.DATA_PORT_MAX


def test_provider_only_install_is_not_engine_capability(monkeypatch, tmp_path):
    flavor = tmp_path / "vendor" / "mpi" / "vulkan"
    (flavor / "bin").mkdir(parents=True)
    check = flavor / "bin" / ("kllm-mpi-provider-check.exe" if os.name == "nt" else "kllm-mpi-provider-check")
    check.write_bytes(b"probe")
    provider = {
        "upstream_revision": "rev",
        "local_patch_digest": "digest",
        "protocol_version": 1,
        "target_triple": "target",
        "features": ["derive"],
        "binary_sha256": "sha256:provider",
    }
    (flavor / "install.json").write_text(
        json.dumps({
            "schema_version": 1,
            "backend": "vulkan",
            "provider": {**provider, "self_test": "passed"},
            "engine": {"available": False, "blocker": "engine pending"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(mpi_runtime, "_provider_probe", lambda _: provider)
    report = mpi_runtime.probe_local_capabilities(
        [{"physical_id": "pci:01", "vendor_id": "0x10de"}], root=tmp_path
    )
    assert report["available"] is False
    assert report["provider"]["self_test"] == "passed"
    assert report["backends"]["vulkan"]["available"] is False
    assert report["backends"]["vulkan"]["reason"] == "engine pending"
    assert report["fingerprint"].startswith("sha256:")


def test_rank_supervisor_keeps_secret_out_of_argv(tmp_path):
    script = tmp_path / "rank.py"
    script.write_text(
        """import json, sys
spec = json.loads(sys.stdin.readline())
print(json.dumps({'kind':'kllm-mpi-rank-ready','rank':spec['rank'],
 'world_size':spec['world_size'],'manifest_identity':spec['manifest_identity']}), flush=True)
for line in sys.stdin:
    command=json.loads(line)
    if command['command']=='stop':
        print(json.dumps({'kind':'kllm-mpi-rank-stopped'}), flush=True)
        break
""",
        encoding="utf-8",
    )
    wrapper = tmp_path / ("rank.cmd" if os.name == "nt" else "rank")
    if os.name == "nt":
        wrapper.write_text(f'@"{os.sys.executable}" "{script}"\r\n', encoding="utf-8")
    else:
        wrapper.write_text(f"#!{os.sys.executable}\nexec {os.sys.executable} {script}\n", encoding="utf-8")
        wrapper.chmod(0o755)
    supervisor = mpi_runtime.MpiRankSupervisor(wrapper, startup_timeout=2)
    secret = "ab" * 32
    ready = supervisor.start({
        "rank": 0, "world_size": 1, "manifest_identity": "sha256:m",
        "job_secret": secret,
    })
    try:
        assert ready["rank"] == 0
        assert secret not in " ".join(supervisor.process.args)
        assert "job_secret" not in json.dumps(supervisor.status())
    finally:
        supervisor.stop()
    assert supervisor.status()["state"] == "stopped"


def test_rank_supervisor_reaps_rank_after_control_timeout(tmp_path):
    script = tmp_path / "stalled_rank.py"
    script.write_text(
        """import json, sys, time
spec = json.loads(sys.stdin.readline())
print(json.dumps({'kind':'kllm-mpi-rank-ready','rank':spec['rank'],
 'world_size':spec['world_size'],'manifest_identity':spec['manifest_identity']}), flush=True)
for line in sys.stdin:
    json.loads(line)
    time.sleep(30)
""",
        encoding="utf-8",
    )
    wrapper = tmp_path / ("stalled-rank.cmd" if os.name == "nt" else "stalled-rank")
    if os.name == "nt":
        wrapper.write_text(f'@"{os.sys.executable}" "{script}"\r\n', encoding="utf-8")
    else:
        wrapper.write_text(
            f"#!{os.sys.executable}\nexec {os.sys.executable} {script}\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    supervisor = mpi_runtime.MpiRankSupervisor(wrapper, startup_timeout=2)
    supervisor.start({"rank": 0, "world_size": 1, "manifest_identity": "sha256:m"})

    with pytest.raises(mpi_runtime.MpiRuntimeError, match="response timed out"):
        supervisor.command({"command": "health"}, timeout=0.1)

    assert supervisor.status()["state"] == "stopped"


def test_engine_capability_requires_live_binary_agreement(monkeypatch, tmp_path):
    flavor = tmp_path / "vendor" / "mpi" / "cuda"
    (flavor / "bin").mkdir(parents=True)
    for name in ("kllm-mpi-provider-check", "kllm-mpi-engine"):
        (flavor / "bin" / (name + (".exe" if os.name == "nt" else ""))).write_bytes(b"x")
    provider = {
        "upstream_revision": "rev", "local_patch_digest": "digest",
        "protocol_version": 1, "target_triple": "target",
        "features": ["derive"], "binary_sha256": "sha256:p",
    }
    engine = {
        "available": True,
        "binary": "bin/" + ("kllm-mpi-engine.exe" if os.name == "nt" else "kllm-mpi-engine"),
        "revision": "b11026-patch1",
        "mpi_compatibility_id": "compat1",
        "operation_profile": "dense_llama_text_f16kv_flash_tp_v1",
        "fingerprint": "sha256:e",
        "supported_architectures": ["llama"],
        "devices": [{"physical_id": "pci:01", "device": "CUDA0", "backend": "cuda"}],
    }
    (flavor / "install.json").write_text(json.dumps({
        "schema_version": 1, "backend": "cuda",
        "provider": {**provider, "self_test": "passed"}, "engine": engine,
    }), encoding="utf-8")
    monkeypatch.setattr(mpi_runtime, "_provider_probe", lambda _: provider)
    live = {**engine, "kind": "kllm-mpi-engine-capabilities", "backend": "cuda"}
    monkeypatch.setattr(mpi_runtime, "_engine_probe", lambda *args: live)
    report = mpi_runtime.probe_local_capabilities(
        [{"physical_id": "pci:01", "vendor_id": "0x10de"}], root=tmp_path
    )
    assert report["available"] is True
    assert report["backends"]["cuda"]["engine_revision"] == "b11026-patch1"
