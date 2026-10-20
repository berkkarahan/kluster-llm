"""Tests for PRIMA-DELTA-BUG-001: RankSupervisor readiness caching and large log handling."""

import json
from uuid import uuid4

from kllm.modules.primacpp.entities import LayerRange, RankAllocation
from kllm.modules.primacpp.interactors.execution import (
    Asset,
    ExecutionManifest,
    Segment,
)
from kllm.modules.primacpp.interactors.lifecycle import RankSupervisor

BASELINE_FEATURES = [
    "execution_manifest_v1",
    "logical_context_sequences_v1",
    "embedding_role_v1",
    "decode_metadata_v2",
    "compact_swa_v1",
    "explicit_devices_v1",
]


def make_manifest():
    return ExecutionManifest(
        runtime_id="a" * 64,
        run_id=str(uuid4()),
        plan_digest="b" * 64,
        total_layers=8,
        ranks=[
            RankAllocation(
                node_id="master",
                role="master",
                rank=0,
                address="127.0.0.1",
                next_node_id="worker",
                layers=[],
                ngl=0,
            ),
            RankAllocation(
                node_id="worker",
                role="worker",
                rank=1,
                address="127.0.0.2",
                next_node_id="master",
                layers=[LayerRange(start=0, end=8)],
                ngl=0,
            ),
        ],
        assets=[Asset(name="model.gguf", kind="model", sha256="c" * 64, size=1)],
        settings={"context": 512, "swa_full": False, "parallel_slots": 4, "prima_kv_sequences": 5},
        inventory_digest="d" * 64,
        segments=[Segment(start=0, end=8, node_id="worker")],
        backends={"master": "vulkan", "worker": "vulkan"},
    )


def test_readiness_report_cached_in_ready_phase(tmp_path):
    value = make_manifest()
    owner = {"sender": "master", "authority_digest": "a" * 64}
    identity = ["owned"]

    supervisor = RankSupervisor(
        tmp_path,
        "worker",
        capture=lambda _: {"capabilities": {"features": list(BASELINE_FEATURES)}},
        spawn=lambda *_: {"pid": 123, "start_token": "owned"},
        stop=lambda _: True,
        alive=lambda _: True,
        identity=lambda _: identity[0],
    )
    supervisor.start(value, tmp_path / "model", lambda: None, assessment=True, command_owner=owner)

    report = {
        "schema_version": 1,
        "runtime_id": value.runtime_id,
        "rank": 1,
        "ngl": 0,
        "layers": 8,
        "manifest_mode": True,
        "manifest_digest": value.digest,
        "phase": "ready",
        "readiness_stage": "worker_decode_loop",
    }

    # Call ready() to acknowledge readiness and cache the report
    res = supervisor.ready(value.run_id, report, lambda: None)
    assert res["phase"] == "ready"
    assert res["readiness_report"] == report

    # Delete or empty the rank.log file completely
    log = tmp_path / value.run_id / "rank.log"
    if log.exists():
        log.unlink()

    # readiness_report() should return the cached report directly without reading disk
    cached = supervisor.readiness_report(value.run_id, lambda: None, command_owner=owner)
    assert cached == report


def test_serving_log_exceeding_4mb_does_not_crash_readiness(tmp_path):
    value = make_manifest()
    owner = {"sender": "master", "authority_digest": "a" * 64}
    identity = ["owned"]

    supervisor = RankSupervisor(
        tmp_path,
        "worker",
        capture=lambda _: {"capabilities": {"features": list(BASELINE_FEATURES)}},
        spawn=lambda *_: {"pid": 123, "start_token": "owned"},
        stop=lambda _: True,
        alive=lambda _: True,
        identity=lambda _: identity[0],
    )
    supervisor.start(value, tmp_path / "model", lambda: None, assessment=True, command_owner=owner)

    report = {
        "schema_version": 1,
        "runtime_id": value.runtime_id,
        "rank": 1,
        "ngl": 0,
        "layers": 8,
        "manifest_mode": True,
        "manifest_digest": value.digest,
        "phase": "ready",
        "readiness_stage": "worker_decode_loop",
    }

    log = tmp_path / value.run_id / "rank.log"
    readiness_line = "KLLM_PRIMA_RANK_READY=" + json.dumps(report) + "\n"
    padding = "INFO: serving token completion batch generation chunk\n" * 80_000
    log.write_text(readiness_line + padding, encoding="utf-8")
    assert log.stat().st_size > 4 * 1024 * 1024  # > 4MB

    # Transition to ready phase
    supervisor.ready(value.run_id, report, lambda: None)

    # 1. Serving with cached report succeeds
    assert supervisor.readiness_report(value.run_id, lambda: None, command_owner=owner) == report

    # 2. Even if cache is evicted/missing, ready phase disk fallback does not crash on >4MB log
    del supervisor.owner["readiness_report"]
    assert supervisor.readiness_report(value.run_id, lambda: None, command_owner=owner) == report

