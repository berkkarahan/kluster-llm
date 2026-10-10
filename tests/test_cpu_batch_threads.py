"""Prompt and RPC CPU threads scale past the generation cap on large CPUs."""

from __future__ import annotations

from pathlib import Path

from kllm.modules.inference.interactors.effective_config import effective_configuration
from kllm.modules.inference.interactors.run_launch import build_master_launch
from kllm.modules.inference.entities import cpu_batch_threads
from kllm.modules.inference.entities import RunBody
from test_plan_run_params import _fake_plan


def test_batch_threads_follow_physical_cores_without_shrinking_small_hosts(monkeypatch):
    monkeypatch.setattr("kllm.modules.inference.entities.available_threads", lambda: 32)

    def cores(*, logical: bool = True) -> int:
        return 32 if logical else 16

    monkeypatch.setattr("psutil.cpu_count", cores)
    assert cpu_batch_threads() == 16

    monkeypatch.setattr("kllm.modules.inference.entities.available_threads", lambda: 8)

    def small(*, logical: bool = True) -> int:
        return 8 if logical else 4

    monkeypatch.setattr("psutil.cpu_count", small)
    assert cpu_batch_threads() == 8


def test_unset_threads_batch_is_what_launch_and_the_snapshot_use(monkeypatch):
    monkeypatch.setattr("kllm.modules.inference.interactors.run_launch.cpu_batch_threads", lambda: 12)
    monkeypatch.setattr("kllm.modules.inference.interactors.effective_config.cpu_batch_threads", lambda: 12)
    body = RunBody(model="toy.gguf")
    plan = _fake_plan()
    launch = build_master_launch(
        body,
        plan,
        model_path=Path("/tmp/toy.gguf"),
        rpc_servers=[],
        infer_port=8080,
        repo_root=None,
        gpu_env={},
        extra_args=[],
    )
    args = launch["extra_args"]
    assert args[args.index("--threads-batch") + 1] == "12"
    snapshot = effective_configuration(body, plan, Path("/tmp/toy.gguf"))
    assert snapshot["values"]["threads_batch"] == 12


def test_explicit_threads_batch_is_not_replaced(monkeypatch):
    monkeypatch.setattr("kllm.modules.inference.interactors.run_launch.cpu_batch_threads", lambda: 12)
    body = RunBody(model="toy.gguf", threads_batch=3)
    launch = build_master_launch(
        body,
        _fake_plan(),
        model_path=Path("/tmp/toy.gguf"),
        rpc_servers=[],
        infer_port=8080,
        repo_root=None,
        gpu_env={},
        extra_args=[],
    )
    args = launch["extra_args"]
    assert args[args.index("--threads-batch") + 1] == "3"
    assert args.count("--threads-batch") == 1
