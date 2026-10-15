"""Bounded, model-backed measurements through the prepared native server."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import time
from statistics import median
from uuid import uuid4

import httpx
import psutil

from kllm.shared.native import require_native
from kllm.modules.primacpp.entities import LayerRange, RankAllocation
from kllm.modules.primacpp.interactors.devices import vulkan_environment
from kllm.modules.primacpp.interactors.execution import ExecutionManifest, Segment, asset_files, require_execution_capabilities
from kllm.modules.primacpp.interactors.evidence import execution_reports, verify_vulkan_execution


PROMPT = "This is an inference profiling request. Continue this sentence with a description of a clear morning: The sky is"


def profile_generation(runtime: dict, model: Path, request, selected: list[dict], directory: Path, *, check, track, stop, progress=None):
    """Return CPU and selected-offload measurements; never qualifies inference."""
    if request.gpu_layers is None or request.gpu_layers <= 0 or len(selected) != 1:
        raise ValueError("Generation profiling requires one selected Vulkan device and an explicit positive gpu_layers count")
    metadata = json.loads(require_native()._read_gguf_metadata_json(str(model), False))
    layers = metadata.get("block_count") or metadata.get("n_layer")
    if not layers:
        layers = next(v for k, v in metadata.get("metadata", metadata).items() if k.endswith(".block_count"))
    if request.gpu_layers > layers:
        raise ValueError("Profile GPU offload exceeds the model's transformer layers")
    files = asset_files(model)
    model_size = sum(path.stat().st_size for _, path in files if path.exists())
    measured = {}
    for pass_index, (backend, ngl) in enumerate((("cpu", 0), ("vulkan", request.gpu_layers)), start=1):
        check()
        base_fraction = 0.0 if pass_index == 1 else 0.5
        pass_label = f"Pass {pass_index}/2 ({backend.upper()})"
        if progress is not None:
            progress(f"{pass_label}: starting profiler server…", base_fraction, 0, model_size)
        phase = directory / backend
        phase.mkdir()
        rank = RankAllocation(node_id="profile", role="master", rank=0, address="127.0.0.1", next_node_id="profile",
            layers=[LayerRange(start=0, end=layers)], ngl=ngl,
            devices=["Vulkan0"] if ngl else [], tensor_split=[1.0] if ngl else [], output_gpu=bool(ngl and request.output_gpu))
        manifest = ExecutionManifest(runtime_id=runtime["runtime_id"], run_id=str(uuid4()), plan_digest="a" * 64,
            total_layers=layers, ranks=[rank], assets=[a for a, _ in files],
            settings={"context": request.context, "swa_full": False, "parallel_slots": 1, "prima_kv_sequences": 2},
            inventory_digest="b" * 64,
            segments=[Segment(start=0, end=layers, node_id="profile")], backends={"profile": backend})
        require_execution_capabilities(manifest, runtime.get("capabilities", {}))
        contract = phase / "execution.json"
        contract.write_text(json.dumps(manifest.model_dump(), sort_keys=True, separators=(",", ":")), encoding="utf-8")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        environment = {**os.environ, **runtime.get("runtime_env", {}), **vulkan_environment(selected),
                       "KLLM_PRIMA_BIND": "127.0.0.1", "KLLM_PRIMA_EXECUTION_EVIDENCE": "1" if ngl else "0"}
        log_path = phase / "profile.log"
        peak = 0
        with log_path.open("w", encoding="utf-8") as output:
            process = subprocess.Popen([runtime["llama_server"], "--model", str(model), "--host", "127.0.0.1",
                "--port", str(port), "--ctx-size", str(request.context), "--threads", str(request.threads),
                "--threads-batch", str(request.threads),
                "--parallel", "1", "--batch-size", "512", "--ubatch-size", "128",
                "--cache-type-k", "f16", "--cache-type-v", "f16", "--execution-manifest", str(contract)],
                env=environment, stdout=output, stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
            try:
                track(process)
                child = psutil.Process(process.pid)
                deadline = time.monotonic() + 300
                last_marker: tuple[int, float, int] | None = None
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=1, trust_env=False) as client:
                    while time.monotonic() < deadline:
                        check()
                        if process.poll() is not None:
                            raise ValueError("Generation profiler exited before readiness")
                        rss = child.memory_info().rss
                        peak = max(peak, rss)
                        try:
                            cpu_times = child.cpu_times()
                            cpu_total = round(float(cpu_times.user + cpu_times.system), 2)
                        except (psutil.Error, OSError, AttributeError):
                            cpu_total = 0.0
                        log_bytes = log_path.stat().st_size if log_path.exists() else 0
                        marker = (rss // (4 * 1024 * 1024), cpu_total, log_bytes)
                        if last_marker is not None and marker != last_marker:
                            deadline = max(deadline, time.monotonic() + 300)
                        last_marker = marker
                        if progress is not None:
                            load_frac = min(0.28, (min(rss, model_size) / max(1, model_size)) * 0.28) if model_size else 0.1
                            progress(f"{pass_label}: loading model weights…", base_fraction + load_frac, min(rss, model_size), model_size)
                        reports = [json.loads(line.split("=", 1)[1]) for line in log_path.read_text(errors="replace").splitlines()
                                   if line.startswith("KLLM_PRIMA_RANK_CONFIGURED=")]
                        expected = {"runtime_id": manifest.runtime_id, "manifest_digest": manifest.digest,
                                    "rank": 0, "ngl": ngl, "manifest_mode": True}
                        if reports and (len(reports) != 1 or any(reports[0].get(k) != v for k, v in expected.items())):
                            raise ValueError("Generation profile acknowledgement disagrees with its manifest")
                        try:
                            if reports and client.get("/health").status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.1)
                    else:
                        raise ValueError("Generation profile readiness timed out")
                    samples = []
                    for index in range(4):
                        check()
                        if progress is not None:
                            progress(
                                f"{pass_label}: benchmark sample {index + 1}/4…",
                                base_fraction + 0.30 + ((index + 1) / 4.0) * 0.18,
                                model_size,
                                model_size,
                            )
                        body = {"prompt": PROMPT, "n_predict": 16, "temperature": 0, "seed": 0, "cache_prompt": False}
                        # Stream in native format so cancellation remains checked between decoded tokens.
                        with client.stream("POST", "/completion", json={**body, "stream": True}, timeout=120) as response:
                            if response.status_code != 200:
                                raise ValueError(f"Generation profiler HTTP {response.status_code}")
                            final = None
                            for line in response.iter_lines():
                                check()
                                if line.startswith("data:"):
                                    event = json.loads(line[5:])
                                    if event.get("stop"):
                                        final = event
                                peak = max(peak, child.memory_info().rss)
                                if log_path.stat().st_size > 1024 * 1024:
                                    raise ValueError("Generation profiler exceeded its output limit")
                        timings = (final or {}).get("timings", {})
                        if timings.get("prompt_n", 0) < 2 or timings.get("predicted_n", 0) < 2:
                            raise ValueError("Generation profile lacks completed prefill/decode timing")
                        prefill, decode = timings.get("prompt_ms", 0), timings.get("predicted_per_token_ms", 0)
                        if prefill <= 0 or decode <= 0:
                            raise ValueError("Generation timings are not positive")
                        if index:  # First generation warms kernels and records placement evidence.
                            samples.append({"prefill_ms": prefill, "decode_ms": decode})
                output.flush()
                proof = None
                if ngl:
                    proof = verify_vulkan_execution(execution_reports(log_path.read_text(errors="replace")), manifest, rank, selected)
                measured[backend] = {"samples": samples, "prefill_ms": median(s["prefill_ms"] for s in samples),
                    "decode_ms": median(s["decode_ms"] for s in samples), "peak_memory_bytes": peak,
                    "manifest_digest": manifest.digest, "execution_proof": proof}
                if progress is not None:
                    progress(f"{pass_label}: completed ({measured[backend]['decode_ms']:.1f} ms/tok)", base_fraction + 0.5, model_size, model_size)
            finally:
                if process.poll() is None:
                    stop(process)
                    process.wait(timeout=10)
    return {"measurement_kind": "end_to_end", "layers": layers, "gpu_layers": request.gpu_layers,
            "settings": {"context": request.context, "threads": request.threads, "cache_type_k": "f16",
                         "threads_batch": request.threads, "batch_size": 512, "micro_batch": 128,
                         "cache_type_v": "f16", "flash_attention": "off", "parallel_slots": 1,
                         "output_gpu": request.output_gpu},
            "cpu": measured["cpu"], "vulkan": measured["vulkan"],
            "memory_measurement": "sampled process RSS at startup and streamed events; GPU proof assessed separately"}
