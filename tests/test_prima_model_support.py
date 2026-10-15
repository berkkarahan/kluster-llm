"""Prima model-family support: runtime capability parsing, plan and pre-spawn refusal."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from kllm.modules.primacpp.entities import LayerRange, RankAllocation
from kllm.modules.primacpp.interactors.build import parse_device_report
from kllm.modules.primacpp.interactors.execution import (
    Asset,
    ExecutionManifest,
    Segment,
)
from kllm.modules.primacpp.interactors.lifecycle import RankSupervisor
from kllm.modules.primacpp.interactors.model_support import (
    UNKNOWN_SUPPORT_DETAIL_CODE,
    UNSUPPORTED_DETAIL_CODE,
    architecture_support,
    model_architecture,
    require_model_support,
)
from kllm.modules.primacpp.interactors.runs import _exit_before_readiness, rank_log_tail

# Capabilities a current managed Prima runtime advertises; admission needs them
# before the model-family check is reached.
BASELINE_FEATURES = [
    "execution_manifest_v1",
    "logical_context_sequences_v1",
    "embedding_role_v1",
    "decode_metadata_v2",
    "compact_swa_v1",
    "explicit_devices_v1",
]


def _string(buf: bytearray, value: str) -> None:
    data = value.encode("utf-8")
    buf += struct.pack("<Q", len(data)) + data


def gguf_bytes(*, arch: str) -> bytes:
    """Smallest GGUF whose declared architecture Kluster can read."""
    part = bytearray()
    _string(part, "general.architecture")
    part += struct.pack("<I", 8)  # STRING
    _string(part, arch)
    return struct.pack("<4sIQQ", b"GGUF", 3, 0, 1) + bytes(part)


def _gguf(path: Path, *, arch: str) -> Path:
    path.write_bytes(gguf_bytes(arch=arch))
    return path


def _manifest() -> ExecutionManifest:
    return ExecutionManifest(
        runtime_id="a" * 64,
        run_id=str(uuid4()),
        plan_digest="b" * 64,
        total_layers=8,
        ranks=[
            RankAllocation(
                node_id="master", role="master", rank=0, address="127.0.0.1",
                next_node_id="worker", layers=[], ngl=0,
            ),
            RankAllocation(
                node_id="worker", role="worker", rank=1, address="127.0.0.2",
                next_node_id="master", layers=[LayerRange(start=0, end=8)], ngl=0,
            ),
        ],
        assets=[Asset(name="model.gguf", kind="model", sha256="c" * 64, size=1)],
        settings={"context": 512, "swa_full": False, "parallel_slots": 4,
                  "prima_kv_sequences": 5},
        inventory_digest="d" * 64,
        segments=[Segment(start=0, end=8, node_id="worker")],
        backends={"master": "cpu", "worker": "cpu"},
    )


def _report(architectures=None, features=None) -> str:
    capabilities = {
        "schema_version": 1,
        "ring_protocol_version": 1,
        "backends": ["CPU"],
        "devices": [{"backend": "CPU", "name": "CPU", "description": "host"}],
        "features": features if features is not None
        else ["layer_window", "tensor_split", "ngl", "ring_tcp", "execution_manifest_v1"],
    }
    if architectures is not None:
        capabilities["architectures"] = architectures
    return "CPU: host\nKLLM_PRIMA_CAPABILITIES=" + json.dumps(capabilities)


# --- the support rule -------------------------------------------------------


def test_architecture_support_judges_only_what_the_runtime_advertises():
    capabilities = {"architectures": ["llama", "qwen35"]}
    assert architecture_support("qwen35", capabilities) == {
        "supported": True, "error": None, "warning": None}

    refused = architecture_support("kolibri1", capabilities)
    assert refused["supported"] is False
    assert refused["error"].startswith(UNSUPPORTED_DETAIL_CODE)
    assert "kolibri1" in refused["error"] and "qwen35" in refused["error"]

    # A runtime that does not advertise its families cannot be judged: warn, never refuse.
    unknown = architecture_support("qwen35", {})
    assert unknown["supported"] is None and unknown["error"] is None
    assert unknown["warning"].startswith(UNKNOWN_SUPPORT_DETAIL_CODE)

    undeclared = architecture_support(None, capabilities)
    assert undeclared["supported"] is None and undeclared["error"] is None
    assert undeclared["warning"].startswith(UNKNOWN_SUPPORT_DETAIL_CODE)


def test_model_architecture_reads_the_gguf_and_tolerates_unreadable_files(tmp_path):
    assert model_architecture(_gguf(tmp_path / "saluki.gguf", arch="qwen35")) == "qwen35"
    assert model_architecture(_gguf(tmp_path / "kolibri.gguf", arch="kolibri1")) == "kolibri1"
    broken = tmp_path / "broken.gguf"
    broken.write_bytes(b"not a gguf")
    assert model_architecture(broken) is None
    assert model_architecture(tmp_path / "absent.gguf") is None


def test_require_model_support_raises_the_detail_code_and_returns_warnings(tmp_path):
    model = _gguf(tmp_path / "kolibri.gguf", arch="kolibri1")
    with pytest.raises(ValueError, match=UNSUPPORTED_DETAIL_CODE):
        require_model_support(model, {"architectures": ["llama"]})
    verdict = require_model_support(model, {})
    assert verdict["warning"].startswith(UNKNOWN_SUPPORT_DETAIL_CODE)


# --- capability report validation -------------------------------------------


def test_device_report_validates_the_advertised_architectures():
    install = SimpleNamespace(parse_list_devices_backends=lambda output: ["cpu"])
    reported = parse_device_report(install, _report(["qwen35", "llama", "qwen35"]))
    assert reported["architectures"] == ["llama", "qwen35"]

    unprefixed = parse_device_report(install, _report())
    assert "architectures" not in unprefixed

    for malformed in ([1, 2], ["qwen35", ""], "qwen35", {"qwen35": True}):
        with pytest.raises(ValueError, match="model architecture list"):
            parse_device_report(install, _report(malformed))
    report = json.loads(_report().split("KLLM_PRIMA_CAPABILITIES=", 1)[1])
    report["architectures"] = None
    with pytest.raises(ValueError, match="model architecture list"):
        parse_device_report(install, "KLLM_PRIMA_CAPABILITIES=" + json.dumps(report))


def test_device_report_rejects_malformed_feature_entries():
    install = SimpleNamespace(parse_list_devices_backends=lambda output: ["cpu"])
    with pytest.raises(ValueError, match="feature entry"):
        parse_device_report(
            install,
            _report(features=["layer_window", "tensor_split", "ngl", "ring_tcp",
                              "execution_manifest_v1", 7]),
        )

    for nested in ({"qwen35_segmented_v1": True}, ["qwen35_segmented_v1"]):
        with pytest.raises(ValueError, match="feature entry"):
            parse_device_report(install, _report(features=["layer_window", "tensor_split", "ngl", "ring_tcp",
                                                         "execution_manifest_v1", nested]))
    with pytest.raises(ValueError, match="feature entry"):
        parse_device_report(
            install,
            _report(features=["layer_window", "tensor_split", "ngl", "ring_tcp",
                              "execution_manifest_v1", ""]),
        )


# --- pre-spawn refusal ------------------------------------------------------


def test_rank_start_refuses_an_unsupported_family_before_spawning(tmp_path):
    value = _manifest()
    runtime = {
        "capabilities": {
            "features": list(BASELINE_FEATURES),
            "backends": ["cpu"],
            "architectures": ["llama", "qwen35"],
        }
    }
    spawned = []
    supervisor = RankSupervisor(
        tmp_path, "worker", capture=lambda _: runtime,
        spawn=lambda *_: spawned.append(True) or {"pid": 123, "start_token": "owned"},
        stop=lambda _: True, alive=lambda _: False, identity=lambda _: "owned",
    )
    refused = _gguf(tmp_path / "kolibri.gguf", arch="kolibri1")
    with pytest.raises(ValueError, match=UNSUPPORTED_DETAIL_CODE):
        supervisor.start(value, refused, lambda: None, assessment=True)
    assert spawned == []
    assert not (tmp_path / value.run_id).exists()

    supported = _gguf(tmp_path / "saluki.gguf", arch="qwen35")
    assert supervisor.start(value, supported, lambda: None, assessment=True)["pid"] == 123
    assert spawned == [True]

    # An older pin that does not advertise families still starts.
    supervisor.stop(value.run_id)
    runtime["capabilities"].pop("architectures")
    assert supervisor.start(value, refused, lambda: None, assessment=True)["pid"] == 123


# --- launch diagnostics -----------------------------------------------------


def test_launch_failure_reports_the_rank_log_tail(tmp_path):
    root = tmp_path / "runs"
    tail = root / "run-1" / "rank.log"
    tail.parent.mkdir(parents=True)
    tail.write_text("loading model\naborted: unsupported tensor\n", encoding="utf-8")
    assert rank_log_tail(tail) == "loading model\naborted: unsupported tensor"

    message = _exit_before_readiness(root, "run-1")
    assert message.startswith("Prima exited before model readiness")
    assert "unsupported tensor" in message

    assert _exit_before_readiness(root, "missing-run") == "Prima exited before model readiness"
    assert rank_log_tail(tmp_path / "nope.log") == ""
