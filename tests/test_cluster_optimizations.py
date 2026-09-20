from __future__ import annotations

import asyncio
import hashlib
import json
import struct
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from kllm import gateway, models
from kllm.placement import memory_costs, plan_devices, MIB
from kllm.run_planning import build_master_launch, resolve_launch_settings
from kllm.run_settings import RunBody
from kllm.transfer_manifest import manifest_for, atomic_json


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
    assert result["ngl"] == 5
    assert result["device_allocations"][0]["output_layer"]
    assert result["cpu_layers"] == 0


def test_heterogeneous_multiple_devices_match_runtime_boundaries():
    result = plan([node("remote", 510*MIB, backend="metal", gpus=2), node("master", 510*MIB, master=True)])
    assert result["fit_status"] != "overflow"
    allocations = result["device_allocations"]
    assert len(allocations) == 3
    assert set(result["device_order"]) == {"RPC0", "RPC1", "Vulkan0"}
    assert sum(a["layers"] for a in allocations) == result["ngl"]
    splits = result["tensor_split"]
    cumulative = []
    for value in splits:
        cumulative.append(value + (cumulative[-1] if cumulative else 0))
    for layer in range(result["cpu_layers"], 5):
        fraction = (layer-result["cpu_layers"]) / result["ngl"]
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


@pytest.fixture
def transfer(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "models_dir", lambda: tmp_path)
    payload = b"abcdefghABCDEFGH12345678"
    source = tmp_path / "source.gguf"
    source.write_bytes(payload)
    manifest = manifest_for(source, piece_size=8)
    calls = []
    behavior = {}
    original_client = httpx.Client
    def handler(request):
        start, end, _ = models.parse_bytes_range(request.headers["range"], len(payload))
        calls.append((request.url.host, start))
        override = behavior.get("handler")
        if override:
            response = override(request, start, end)
            if response:
                return response
        return httpx.Response(206, content=payload[start:end], headers={"Content-Range": f"bytes {start}-{end-1}/{len(payload)}"})
    monkeypatch.setattr(models.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handler)))
    def pull(**kw):
        return models.pull_from_seed("dest.gguf", len(payload), manifest["sha256"], "seed", 18765,
                                     "key", manifest=manifest, connections=1, **kw)
    return SimpleNamespace(path=tmp_path, payload=payload, manifest=manifest, calls=calls, behavior=behavior, pull=pull)


def test_interrupted_transfer_resumes_only_verified_pieces(transfer):
    transfer.behavior["handler"] = lambda req, start, end: httpx.Response(503) if start == 8 else None
    with pytest.raises(httpx.HTTPStatusError):
        transfer.pull()
    assert not (transfer.path / "dest.gguf").exists()
    assert transfer.calls.count(("seed", 8)) == 4
    transfer.behavior.clear()
    transfer.calls.clear()
    transfer.pull()
    assert transfer.calls == [("seed", 8)]
    assert (transfer.path / "dest.gguf").read_bytes() == transfer.payload
    assert not (transfer.path / "dest.gguf.partial.resume.json").exists()


def test_corrupt_resume_piece_is_fetched_again(transfer):
    transfer.behavior["handler"] = lambda req, start, end: httpx.Response(503) if start == 8 else None
    with pytest.raises(httpx.HTTPStatusError):
        transfer.pull()
    with (transfer.path / "dest.gguf.partial").open("r+b") as f:
        f.write(b"CORRUPT!")
    transfer.calls.clear()
    transfer.behavior.clear()
    transfer.pull()
    assert transfer.calls == [("seed", 0), ("seed", 8)]


@pytest.mark.parametrize("failure", ["range", "length", "corrupt", "overflow", "unavailable"])
def test_piece_retry_switches_seed(transfer, failure):
    def fail(request, start, end):
        if request.url.host != "seed":
            return None
        headers = {"Content-Range": f"bytes {start}-{end-1}/24"}
        data = transfer.payload[start:end]
        if failure == "unavailable":
            raise httpx.ConnectError("offline", request=request)
        if failure == "range":
            headers["Content-Range"] = "bytes 0-7/999"
        if failure == "length":
            headers["Content-Length"] = "99"
        if failure == "corrupt":
            data = b"BADBYTES"
        if failure == "overflow":
            data += b"extra"
        return httpx.Response(206, content=data, headers=headers)
    transfer.behavior["handler"] = fail
    transfer.pull(sources=[{"host": "backup", "port": 18765}])
    assert (transfer.path / "dest.gguf").read_bytes() == transfer.payload
    assert ("backup", 0) in transfer.calls


def test_manifest_invalidates_identical_name_and_size(tmp_path):
    p = tmp_path / "model.gguf"
    p.write_bytes(b"aaaa")
    first = manifest_for(p)
    p.write_bytes(b"bbbb")
    second = manifest_for(p)
    assert first["sha256"] != second["sha256"]
    models.write_sha256_sidecar(p, second["sha256"])
    p.write_bytes(b"cccc")
    assert models.read_sha256_sidecar(p) is None


def test_gguf_tensor_spans_and_layer_count(tmp_path):
    from kllm.gguf_meta import read_model_layout
    def string(value):
        raw = value.encode()
        return struct.pack("<Q", len(raw)) + raw
    meta = string("general.architecture") + struct.pack("<I", 8) + string("llama")
    meta += string("llama.block_count") + struct.pack("<II", 4, 2)
    names = ["token_embd.weight", "blk.0.attn.weight", "blk.1.attn.weight", "output.weight"]
    tensors = b"".join(string(name) + struct.pack("<IQIQ", 1, 8, 0, i*32) for i,name in enumerate(names))
    header = struct.pack("<4sIQQ", b"GGUF", 3, 4, 2) + meta + tensors
    path = tmp_path / "layout.gguf"
    path.write_bytes(header + b"\0" * ((-len(header)) % 32) + b"\0" * 128)
    result = read_model_layout(path)
    assert result["layers"] == [32, 32]
    assert result["input_bytes"] == result["output_bytes"] == 32


def test_fast_probe_starts_transfer_before_slow_probe_finishes(tmp_path, monkeypatch):
    path = tmp_path / "tree.gguf"
    path.write_bytes(b"payload")
    pulling = threading.Event()
    def probe(host, *args):
        if host == "slow":
            assert pulling.wait(2), "fast transfer was blocked behind the slow probe"
        return None
    def pull(host, *args, **kw):
        if host == "fast":
            pulling.set()
        return {"ok": True, "sha256": kw["digest"]}
    monkeypatch.setattr(models, "_probe_skip", probe)
    monkeypatch.setattr(models, "request_worker_pull", pull)
    results = models.distribute_to_workers(path, [{"host": "slow"}, {"host": "fast"}], "key", master_host="master")
    assert len(results) == 2 and all(r["ok"] for r in results)


def test_different_manifest_discards_old_resume_binding(transfer):
    tmp = transfer.path / "dest.gguf.partial"
    tmp.write_bytes(transfer.payload)
    atomic_json(tmp.with_name(tmp.name + ".resume.json"), {
        "binding": {"sha256": "0"*64, "size": 24, "piece_size": 8, "pieces": transfer.manifest["pieces"]},
        "done": {str(i): h for i,h in enumerate(transfer.manifest["pieces"])}
    })
    transfer.pull()
    assert len(transfer.calls) == 3


@pytest.mark.asyncio
async def test_gateway_streams_directly_without_diagnostics(monkeypatch):
    monkeypatch.setattr(gateway, "require_inference", lambda _: pytest.fail("health preflight"))
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("synchronous subprocess"))
    calls = []
    class Stream(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b"data: first\n\n"
            yield b"data: second\n\n"
        async def aclose(self):
            self.closed = True
    stream = Stream()
    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        app = FastAPI()
        app.state.inference_client = client
        request = Request({"type": "http", "app": app})
        response = await gateway._proxy(request, 8080, "/v1/chat/completions", {"stream": True})
        iterator = response.body_iterator
        assert await anext(iterator) == b"data: first\n\n"
        await iterator.aclose()
    assert stream.closed
    assert calls == ["/v1/chat/completions"]


@pytest.mark.asyncio
async def test_gateway_connect_failure_is_503_before_stream_headers():
    def handle(request):
        raise httpx.ConnectError("offline", request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        app = FastAPI()
        app.state.inference_client = client
        with pytest.raises(HTTPException) as error:
            await gateway._proxy(Request({"type": "http", "app": app}), 8080, "/v1/chat/completions", {"stream": True})
    assert error.value.status_code == 503
