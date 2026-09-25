from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from kllm import gateway
from kllm.placement import memory_costs, plan_devices, MIB
from kllm.run_planning import build_master_launch, resolve_launch_settings
from kllm.run_settings import RunBody


def settings(**kwargs):
    return resolve_launch_settings(RunBody(model="test.gguf", **kwargs))


def test_installed_device_names_include_unnumbered_metal():
    from kllm.accel import parse_devices
    devices = parse_devices("Available devices:\n  Metal: Apple M5 Pro (49152 MiB, 40000 MiB free)\n  Vulkan0: AMD GPU (16304 MiB, 15000 MiB free)\n")
    assert [d["device"] for d in devices] == ["Metal", "Vulkan0"]
    assert devices[0]["backend"] == "metal"


@pytest.mark.asyncio
async def test_cancellation_closes_upstream():
    entered = asyncio.Event()
    class Stream(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b"never"
        async def aclose(self):
            self.closed = True
    stream = Stream()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client:
        app = FastAPI()
        app.state.inference_client = client
        response = await gateway._proxy(Request({"type": "http", "app": app}), 8080, "/v1/chat/completions", {"stream": True})
        task = asyncio.create_task(anext(response.body_iterator))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed


def layout(layers=4, weight=100 * MIB):
    return dict(architecture="llama", metadata={"llama.attention.head_count_kv": 1},
                layers=[weight] * layers, output_bytes=weight, input_bytes=10 * MIB,
                confidence="metadata")


def node(nid, memory, *, master=False, backend="vulkan", gpus=1, unified=False):
    return dict(node_id=nid, is_master=master, backend=backend,
                total_ram_bytes=8*1024*MIB, available_ram_bytes=8*1024*MIB,
                unified_memory=unified, rpc_endpoint=f"{nid}:50052", latency_ms=0 if master else 2,
                devices=[dict(index=i, device=f"{backend.title()}{i}", backend=backend,
                              vram_bytes=memory, free_bytes=memory) for i in range(gpus)])


def plan(nodes, model=None, **kw):
    return plan_devices(nodes, 500*MIB, model or layout(), settings(), context=512,
                        headroom=1, device="gpu", vram_weight=.6, latency_weight=.4, **kw)


def test_fitting_local_device_avoids_rpc_and_offloads_output():
    result = plan([node("remote", 4000*MIB, backend="metal"), node("master", 2000*MIB, master=True)])
    assert result["device_order"] == ["Vulkan0"]
    assert result["rpc_endpoints"] == []
    assert result["routing_mode"] == "local"
    assert result["ngl"] == 5
    assert result["device_allocations"][0]["output_layer"]
    assert result["cpu_layers"] == 0


def test_multi_local_gpus_prefer_local_over_rpc():
    """Two master GPUs that together fit must not pull in a remote worker."""
    master = node("master", 510 * MIB, master=True, gpus=2)
    remote = node("remote", 4000 * MIB, backend="metal")
    result = plan([remote, master])
    assert result["routing_mode"] == "local"
    assert result["rpc_endpoints"] == []
    assert result["fit_status"] != "overflow"
    assert all(a["local"] for a in result["device_allocations"])


def test_single_remote_device_when_master_cannot_fit():
    """One large remote GPU holds the model → single-RPC, not multi-node pool."""
    master = node("master", 50 * MIB, master=True)
    # Enough RAM for inputs/overhead, not enough to CPU-spill the whole model.
    master["available_ram_bytes"] = 400 * MIB
    master["total_ram_bytes"] = 400 * MIB
    big = node("big", 4000 * MIB, backend="metal")
    small = node("small", 400 * MIB, backend="cuda")
    result = plan([big, small, master])
    assert result["routing_mode"] == "single_remote"
    assert result["rpc_endpoints"] == ["big:50052"]
    assert len(result["device_allocations"]) == 1
    assert result["device_allocations"][0]["node_id"] == "big"


def test_pool_proportional_avoids_even_split_oom():
    """Heterogeneous pool: each device stays within budget (no even-split OOM)."""
    from kllm.tensor_split import proportional_weights, tensor_split_proportions

    assert tensor_split_proportions([24, 8]) == [24.0, 8.0]
    master = node("master", 800 * MIB, master=True)
    master["available_ram_bytes"] = 400 * MIB
    master["total_ram_bytes"] = 400 * MIB
    weak = node("weak", 800 * MIB, backend="cuda")
    result = plan([weak, master], model=layout(layers=6, weight=100 * MIB))
    assert result["routing_mode"] == "pool"
    assert result["fit_status"] != "overflow"
    assert set(result["rpc_endpoints"]) == {"weak:50052"}
    assert {a["node_id"] for a in result["device_allocations"]} == {"master", "weak"}
    for a in result["device_allocations"]:
        assert a["estimated_bytes"] <= a["budget_bytes"]
    weights = proportional_weights(
        [{"budget_bytes": 24 * 1024 * MIB}, {"budget_bytes": 8 * 1024 * MIB}]
    )
    assert weights == [24.0, 8.0]


def test_heterogeneous_multiple_devices_match_runtime_boundaries():
    # Limit master RAM so local+CPU alone cannot fit; remotes are required.
    master = node("master", 510 * MIB, master=True)
    master["available_ram_bytes"] = 300 * MIB
    master["total_ram_bytes"] = 300 * MIB
    result = plan([node("remote", 510 * MIB, backend="metal", gpus=2), master])
    assert result["fit_status"] != "overflow"
    assert result["routing_mode"] == "pool"
    allocations = result["device_allocations"]
    assert len(allocations) == 3
    assert set(result["device_order"]) == {"RPC0", "RPC1", "Vulkan0"}
    assert sum(a["layers"] for a in allocations) == result["ngl"]
    splits = result["tensor_split"]
    cumulative = []
    for value in splits:
        cumulative.append(value + (cumulative[-1] if cumulative else 0))
    for layer in range(result["cpu_layers"], 5):
        fraction = (layer - result["cpu_layers"]) / result["ngl"]
        device = next(i for i, boundary in enumerate(cumulative) if fraction < boundary / cumulative[-1])
        assert allocations[device]["layer_start"] <= layer < allocations[device]["layer_end"]


def test_cpu_spill_is_master_prefix_and_excluded_from_device_split():
    result = plan([node("master", 510*MIB, master=True)])
    assert result["cpu_layers"] == 3
    assert result["ngl"] == 2
    assert result["device_allocations"][0]["layer_start"] == 3
    assert result["ram_alloc_bytes"] > 300*MIB


def test_unified_memory_cannot_be_added_again_as_cpu_capacity():
    n = node("master", 510*MIB, master=True, unified=True, backend="metal")
    n["total_ram_bytes"] = n["available_ram_bytes"] = 510*MIB
    result = plan([n])
    assert result["fit_status"] == "overflow"
    assert result["budget_bytes"] == 510*MIB


def test_unrelated_usage_not_reclaimed():
    n = node("master", 4000*MIB, master=True)
    n["available_ram_bytes"] = 0
    assert plan([n])["fit_status"] == "overflow"
    n["reclaimable_ram_bytes"] = 500*MIB
    assert plan([n])["fit_status"] != "overflow"


def test_qwen_hybrid_kv_and_state_memory():
    m = layout()
    m["architecture"] = "qwen35moe"
    m["metadata"] = {f"qwen35moe.{k}": v for k,v in {
        "embedding_length": 3072, "attention.head_count": 32, "attention.head_count_kv": 2,
        "attention.key_length": 256, "attention.value_length": 256,
        "ssm.inner_size": 8192, "ssm.state_size": 128, "ssm.group_count": 16,
        "ssm.time_step_rank": 64, "ssm.conv_kernel": 4, "full_attention_interval": 4}.items()}
    costs = memory_costs(m, 500*MIB, 1024, settings(checkpoints=0, cache_type_k="q4_0", cache_type_v="q4_0"))
    assert costs["kv_bytes"] == 1024 * 2 * 512 * 18 // 32
    assert costs["state_bytes"] == 3 * 4 * (128*8192 + 3*(8192+2*16*128))
    two = memory_costs(m, 500*MIB, 1024, settings(checkpoints=0, parallel_slots=2, cache_type_k="q4_0", cache_type_v="q4_0"))
    assert two["kv_bytes"] == costs["kv_bytes"]
    assert two["state_bytes"] == 2 * costs["state_bytes"]


def test_explicit_launch_settings_and_overlapping_args():
    body = RunBody(model="test.gguf", context=99840, cache_type_k="q4_0", cache_type_v="q4_0",
                   batch_size=512, micro_batch=512, enable_thinking=True,
                   extra_args=["--flash-attn=on", "--parallel", "2"])
    resolved = resolve_launch_settings(body)
    launch = build_master_launch(body, {"ngl": 5, "tensor_split_csv": "5", "resolved_launch_settings": resolved},
                                 model_path=Path("missing.gguf"), rpc_servers=[], infer_port=8080, repo_root=None, gpu_env={}, extra_args=[])
    assert launch["micro_batch"] == 512
    assert launch["parallel"] == "2"
    assert launch["context"] == 99840
    assert launch["cache_type_k"] == "q4_0"
    assert launch["extra_args"].count("--flash-attn") == 1
    assert "--parallel" not in launch["extra_args"]
    assert settings(batch_size=64)["micro_batch"] == 64
    assert settings()["checkpoints"] == 32
    assert settings()["cache_ram"] == 8192
    assert settings(extra_args=["-cram", "1024"])["cache_ram"] == 1024


@pytest.mark.parametrize("flag", ["--device", "-ngl", "--tensor-split", "--split-mode", "--override-tensor"])
def test_managed_placement_cannot_be_overridden(flag):
    with pytest.raises(HTTPException):
        settings(extra_args=[flag, "1"])
