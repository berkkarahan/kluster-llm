"""Matched physical local benchmarks; never selects an existing Kluster runtime."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
from statistics import median
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

import httpx
import psutil

from prima_assessment import baseline_placement, capture_baseline, capture_development_runtime, capture_runtime, unused_port
from kllm.shared.native import require_native
from kllm.modules.runtimes.interactors.registry import file_digest
from kllm.modules.primacpp.entities import LayerRange, RankAllocation
from kllm.modules.primacpp.interactors.benchmark import Measurement, Workload, evaluate
from kllm.modules.primacpp.interactors.devices import discover_vulkan_devices, select_vulkan_devices, vulkan_environment
from kllm.modules.primacpp.interactors.execution import ExecutionManifest, Segment, asset_files
from kllm.modules.primacpp.interactors.qualification import execution_host


TARGET = " ".join(["hello"] * 64)
SAMPLING = {"temperature": 0, "seed": 0, "top_k": 0, "top_p": 1.0, "min_p": 0.0,
            "repeat_penalty": 1.0, "repeat_last_n": 0, "presence_penalty": 0.0, "frequency_penalty": 0.0}


@contextmanager
def server(binary, model, env, args, directory, context, *, log_copy=None):
    port = unused_port()
    log = directory / "server.log"
    child_env = {key: value for key, value in {**os.environ, **env}.items() if value is not None}
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen([str(binary), "--model", str(model), "--host", "127.0.0.1",
            "--port", str(port), "--alias", "assessment", "--ctx-size", str(context), *args],
            env=child_env, stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 180
            with httpx.Client(timeout=1, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise ValueError("Benchmark server exited: " + log.read_text(errors="replace")[-2000:])
                    try:
                        if client.get(f"http://127.0.0.1:{port}/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise ValueError("Benchmark model readiness timed out")
            yield port, process, log
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(10)
            if log_copy is not None:
                output.flush()
                log_copy.write_bytes(log.read_bytes())


def stream(port, prompt):
    body = {"model": "assessment", "messages": [{"role": "user", "content": prompt}],
        **SAMPLING, "max_tokens": 256, "cache_prompt": False,
        "chat_template_kwargs": {"enable_thinking": False}, "stream": True,
        "stream_options": {"include_usage": True},
        "response_format": {"type": "json_schema", "json_schema": {"name": "fixed",
            "schema": {"type": "string", "const": TARGET}}}}
    started = time.perf_counter()
    first, done, finish, usage = None, False, None, None
    content = []
    with httpx.Client(timeout=180, trust_env=False) as client:
        with client.stream("POST", f"http://127.0.0.1:{port}/v1/chat/completions", json=body) as response:
            if response.status_code != 200:
                raise ValueError(f"Benchmark HTTP {response.status_code}: {response.read()[:500]!r}")
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                if line[5:].strip() == "[DONE]":
                    done = True
                    continue
                event = json.loads(line[5:])
                usage = event.get("usage") or usage
                choice = (event.get("choices") or [{}])[0]
                finish = choice.get("finish_reason") or finish
                text = choice.get("delta", {}).get("content")
                if text:
                    first = first or time.perf_counter()
                    content.append(text)
    ended = time.perf_counter()
    if not done or finish != "stop" or first is None or not usage or usage.get("completion_tokens", 0) < 2:
        raise ValueError("Missing completed stream, usage or timing")
    if json.loads("".join(content)) != TARGET:
        raise ValueError("Benchmark answer failed deterministic correctness")
    return {"ttft_ms": (first - started) * 1000, "elapsed_ms": (ended - started) * 1000,
            "completion_tokens": usage["completion_tokens"], "prompt_tokens": usage["prompt_tokens"],
            "decode_ms": (ended - first) * 1000, "output": "".join(content),
            "output_sha256": hashlib.sha256("".join(content).encode()).hexdigest()}


def measure(port, process, prompt, concurrency):
    peak, stopped = [0], threading.Event()
    child = psutil.Process(process.pid)
    def sample():
        while not stopped.is_set():
            try:
                peak[0] = max(peak[0], child.memory_info().rss)
            except psutil.NoSuchProcess:
                break
            stopped.wait(0.01)
    sampler = threading.Thread(target=sample)
    sampler.start()
    try:
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(lambda _: stream(port, prompt), range(concurrency)))
        elapsed = (time.perf_counter() - started) * 1000
    finally:
        stopped.set()
        sampler.join()
    if len({r["output_sha256"] for r in results}) != 1:
        raise ValueError("Concurrent deterministic outputs differ")
    # Aggregate decode throughput excludes prefill; TTFT includes queueing.
    measured = Measurement(ttft_ms=median(r["ttft_ms"] for r in results),
        tokens_s=sum(r["completion_tokens"] - 1 for r in results) / (max(r["decode_ms"] for r in results) / 1000),
        elapsed_ms=elapsed, peak_memory_bytes=peak[0])
    return measured, results


def benchmark(runtime, baseline, model, ngl, device, *, pairs=5, context=4096, output_gpu=False):
    if "diagnostic_vulkan_timing_v1" in runtime.get("capabilities", {}).get("features", []):
        raise ValueError("Diagnostic Vulkan timing builds cannot supply paired acceptance measurements")
    assets = asset_files(model)
    metadata = json.loads(require_native()._read_gguf_metadata_json(str(model), False))
    layers = metadata.get("block_count") or metadata.get("n_layer")
    if not layers:
        layers = next(v for k, v in metadata.get("metadata", metadata).items() if k.endswith(".block_count"))
    if not 0 < ngl <= layers:
        raise ValueError("Use an explicit positive model offload count")
    selected = select_vulkan_devices(discover_vulkan_devices(runtime), [device])
    environment = {**runtime.get("runtime_env", {}), **vulkan_environment(selected), "KLLM_PRIMA_BIND": "127.0.0.1"}
    environment.pop("KLLM_PRIMA_EXECUTION_EVIDENCE", None)
    # Child-only telemetry is explicitly disabled even if inherited from the shell.
    environment["KLLM_PRIMA_EXECUTION_EVIDENCE"] = "0"
    # The older Prima scheduler enables debug on presence, even for the string 0.
    environment["GGML_SCHED_DEBUG"] = None
    prompts = {"short": "Return the exact JSON string requested by the schema.",
        "long": "Context for this test: " + "The sky is blue and the grass is green. " * 100
                + "\nReturn the exact JSON string requested by the schema."}
    measurements = {(length, slots): {"llama": [], "prima": [], "details": []}
                    for length in prompts for slots in (1, 2)}
    with tempfile.TemporaryDirectory(prefix="prima-local-paired-") as temp:
        root = Path(temp)
        for pair in range(pairs):
            order = ("llama", "prima") if pair % 2 == 0 else ("prima", "llama")
            for engine in order:
                for slots in (1, 2):
                    directory = root / f"{pair}-{engine}-{slots}"
                    directory.mkdir()
                    args = ["--parallel", str(slots), "--batch-size", "512", "--ubatch-size", "128",
                            "--threads", "8", "--threads-batch", "8",
                            "--cache-type-k", "f16", "--cache-type-v", "f16"]
                    if engine == "prima":
                        rank = RankAllocation(node_id="benchmark", role="master", rank=0, address="127.0.0.1",
                            next_node_id="benchmark", layers=[LayerRange(start=0, end=layers)],
                            ngl=ngl, devices=["Vulkan0"], tensor_split=[1.0], output_gpu=output_gpu)
                        manifest = ExecutionManifest(runtime_id=runtime["runtime_id"], run_id=str(uuid4()),
                            plan_digest="a" * 64, total_layers=layers, ranks=[rank], assets=[a for a, _ in assets],
                            settings={"context": context}, inventory_digest="b" * 64,
                            segments=[Segment(start=0, end=layers, node_id="benchmark")], backends={"benchmark": "vulkan"})
                        contract = directory / "execution.json"
                        contract.write_text(json.dumps(manifest.model_dump(), sort_keys=True, separators=(",", ":")), encoding="utf-8")
                        args += ["--execution-manifest", str(contract)]
                        binary = Path(runtime["llama_server"])
                    else:
                        args += [*baseline_placement(ngl, output_gpu), "--flash-attn", "off", "--no-kv-unified"]
                        binary = baseline
                    with server(binary, model, environment, args, directory, context) as (port, child, log):
                        command = psutil.Process(child.pid).cmdline()
                        for flag, expected in (("--ctx-size", str(context)), ("--parallel", str(slots)),
                                               ("--threads", "8"), ("--threads-batch", "8"),
                                               ("--batch-size", "512"), ("--ubatch-size", "128"),
                                               ("--cache-type-k", "f16"), ("--cache-type-v", "f16")):
                            if command.count(flag) != 1 or command[command.index(flag) + 1] != expected:
                                raise ValueError("Paired native launch changed measured setting " + flag)
                        stream(port, "Warm-up: return the JSON string requested by the schema.")
                        for length, prompt in prompts.items():
                            measured, details = measure(port, child, prompt, slots)
                            row = measurements[length, slots]
                            row[engine].append(measured)
                            row["details"].append({"pair": pair, "engine": engine, "requests": details})
                        logged = log.read_text(errors="replace")
                        if "KLLM_PRIMA_GPU_EXECUTION=" in logged or "## SPLIT #" in logged or "KLLM_PRIMA_VULKAN_TIMING=" in logged:
                            raise ValueError("Execution tracing remained enabled during the paired benchmark")
                        if re.search(r"out of memory|allocation failed", logged, re.I):
                            raise ValueError("Benchmark reported an allocation/OOM failure")
                    print(json.dumps({"pair": pair, "engine": engine, "concurrency": slots}), flush=True)
    workloads, details, errors = [], [], []
    for (length, slots), row in measurements.items():
        all_requests = [r for d in row["details"] for r in d["requests"]]
        identical = len({(r["completion_tokens"], r["output_sha256"]) for r in all_requests}) == 1
        if not identical:
            errors.append(f"{length}/concurrency={slots}: paired output bytes or token counts differ")
        identity = {"model_sha256": assets[0][0].sha256,
            "runtime_commits": {"prima": runtime["commit"], "llama": capture_baseline(baseline)["expected_commit"]},
            "hardware": {**execution_host(), "devices": selected}, "backend": "vulkan",
            "context": context, "precision": {"cache": "f16", "model": model.name, "ngl": ngl, "output_gpu": output_gpu},
            "launch_settings": {"context": context, "threads": 8, "threads_batch": 8,
                "parallel_slots": slots, "batch_size": 512, "micro_batch": 128,
                "cache_type_k": "f16", "cache_type_v": "f16", "flash_attention": "off"},
            "sampling": {**SAMPLING, "constrained_output": TARGET}, "concurrency": slots,
            "prompt_sha256": hashlib.sha256(prompts[length].encode()).hexdigest(),
            "output_tokens": all_requests[0]["completion_tokens"]}
        workloads.append(Workload(identity=identity, target_distributed=False, memory_budget_bytes=psutil.virtual_memory().total,
            llama=row["llama"], prima=row["prima"], quality_passed=identical, oom_failures=0))
        details.append({"length": length, "concurrency": slots, "measurements": row["details"]})
    return {"acceptance": evaluate(workloads, scope="local"), "workloads": [w.model_dump() for w in workloads],
            "runtime_id": runtime["runtime_id"], "recipe_digest": runtime["recipe_digest"],
            "source_commit": runtime["commit"], "server_sha256": file_digest(Path(runtime["llama_server"])),
            "baseline": capture_baseline(baseline),
            "details": details, "validation_errors": errors,
            "memory_measurement": "10ms sampled process RSS; GPU allocation/placement assessed separately",
            "method": "five or more alternating process pairs; one warm-up per process; telemetry disabled",
            "qualification": "open: native local performance only; gateway/lifecycle and GPU proofs required separately"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--baseline-server", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--ngl", type=int, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--output-gpu", action="store_true")
    parser.add_argument("--development-server", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        capture_baseline(args.baseline_server.resolve())
        runtime = capture_runtime(args.runtime_dir)
        if args.development_server:
            runtime = capture_development_runtime(runtime, args.development_server)
        if "diagnostic_vulkan_timing_v1" in runtime.get("capabilities", {}).get("features", []):
            raise ValueError("Diagnostic Vulkan timing builds cannot supply paired acceptance measurements")
        from prima_placement_assessment import assess as assess_placement
        placement_args = argparse.Namespace(**{**vars(args), "output": args.output.with_name(args.output.stem + "-placement.json")})
        placement = assess_placement(placement_args, runtime=runtime)
        placement_args.output.write_text(json.dumps(placement, indent=2) + "\n", encoding="utf-8")
        if any(check["status"] != "passed" for check in placement["checks"]):
            raise ValueError("Matched placement probe failed; see " + str(placement_args.output))
        result = benchmark(runtime, args.baseline_server.resolve(), args.model.resolve(), args.ngl, args.device, output_gpu=args.output_gpu)
        result["placement_receipt"] = str(placement_args.output)
        result["development_server"] = bool(args.development_server)
        if args.development_server:
            result["qualification"] = "open: developmental worktree paired benchmark"
    except Exception as error:
        result = {"status": "failed", "error": str(error), "qualification": "open"}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(args.output), "passed": result.get("acceptance", {}).get("passed", False)}))
    return 0 if result.get("acceptance", {}).get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
