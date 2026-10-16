"""Prove long-prefill/decode matrix placement before matched local timing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tempfile
from uuid import uuid4

import httpx

from prima_assessment import baseline_placement, capture_baseline, capture_runtime
from prima_local_benchmark import server
from kllm.shared.native import require_native
from kllm.modules.primacpp.entities import LayerRange, RankAllocation
from kllm.modules.primacpp.interactors.devices import discover_vulkan_devices, select_vulkan_devices, vulkan_environment
from kllm.modules.primacpp.interactors.execution import ExecutionManifest, Segment, asset_files
from kllm.modules.primacpp.interactors.evidence import execution_reports, verify_vulkan_execution


def baseline_matrix_placement(log: str, layers: int, ngl: int, output_gpu: bool) -> dict:
    observed: dict[int, set[str]] = {}
    output = set()
    for line in log.splitlines():
        if not re.search(r"node #\s*\d+ \(\s*MUL_MAT(?:_ID)?\)", line):
            continue
        backend = re.search(r"\[\s*(\w+)\s", line)
        if not backend:
            continue
        # The baseline truncates backend names to five and tensor names to twenty characters.
        owner = "vulkan" if backend[1].startswith("Vulka") else backend[1].lower()
        weight = re.search(r"blk\.(\d+)\.[\w.]+", line)
        if weight:
            observed.setdefault(int(weight[1]), set()).add(owner)
        elif "output.weight" in line or "result_output" in line:
            output.add(owner)
    expected = {layer: {"vulkan" if layer >= layers - ngl else "cpu"} for layer in range(layers)}
    if observed != expected or output != {"vulkan" if output_gpu else "cpu"}:
        raise ValueError("Baseline matrix placement differs from the requested transformer/output layout: "
                         + json.dumps({"layers": observed, "output": sorted(output)}, default=list))
    return {"layers": {str(k): sorted(v) for k, v in observed.items()}, "output": sorted(output),
            "kind": "scheduler matrix assignments with completed long prefill and decode"}


def assess(args, *, runtime=None):
    runtime = runtime or capture_runtime(args.runtime_dir)
    baseline = capture_baseline(args.baseline_server.resolve())
    files = asset_files(args.model.resolve())
    metadata = json.loads(require_native()._read_gguf_metadata_json(str(args.model.resolve()), False))
    layers = metadata.get("block_count") or metadata.get("n_layer")
    if not layers:
        layers = next(v for k, v in metadata.get("metadata", metadata).items() if k.endswith(".block_count"))
    selected = select_vulkan_devices(discover_vulkan_devices(runtime), [args.device])
    rank = RankAllocation(node_id="placement", role="master", rank=0, address="127.0.0.1",
        next_node_id="placement", layers=[LayerRange(start=0, end=layers)], ngl=args.ngl,
        devices=["Vulkan0"], tensor_split=[1.0], output_gpu=args.output_gpu)
    manifest = ExecutionManifest(runtime_id=runtime["runtime_id"], run_id=str(uuid4()), plan_digest="a" * 64,
        total_layers=layers, ranks=[rank], assets=[a for a, _ in files], settings={"context": 4096},
        inventory_digest="b" * 64, segments=[Segment(start=0, end=layers, node_id="placement")],
        backends={"placement": "vulkan"})
    common = ["--parallel", "1", "--batch-size", "512", "--ubatch-size", "128",
              "--threads", "8", "--threads-batch", "8", "--cache-type-k", "f16", "--cache-type-v", "f16"]
    result = {"source_commit": runtime["commit"], "recipe_digest": runtime["recipe_digest"],
        "runtime_id": runtime["runtime_id"], "baseline": baseline, "manifest": manifest.model_dump(),
        "selected_devices": selected, "checks": [], "qualification": "open: placement assessment only"}
    with tempfile.TemporaryDirectory(prefix="prima-placement-") as temp:
        root = Path(temp)
        contract = root / "execution.json"
        contract.write_text(json.dumps(manifest.model_dump(), sort_keys=True, separators=(",", ":")), encoding="utf-8")
        for engine in ("prima", "baseline"):
            directory = root / engine
            directory.mkdir()
            env = {**runtime.get("runtime_env", {}), **vulkan_environment(selected),
                   "KLLM_PRIMA_BIND": "127.0.0.1", "KLLM_PRIMA_EXECUTION_EVIDENCE": "1" if engine == "prima" else "0"}
            options = [*common, "--execution-manifest", str(contract)] if engine == "prima" else [
                *common, *baseline_placement(args.ngl, args.output_gpu), "--flash-attn", "off", "--no-kv-unified",
                "--no-warmup", "--verbose"]
            if engine == "baseline":
                env["GGML_SCHED_DEBUG"] = "2"
            binary = Path(runtime["llama_server"]) if engine == "prima" else args.baseline_server.resolve()
            try:
                with server(binary, args.model.resolve(), env, options, directory, 4096) as (port, _, log):
                    with httpx.Client(timeout=300, trust_env=False) as client:
                        response = client.post(f"http://127.0.0.1:{port}/completion", json={
                            "prompt": "The sky is blue and the grass is green. " * 100,
                            "n_predict": 4, "temperature": 0, "seed": 0, "cache_prompt": False})
                        response.raise_for_status()
                        timings = response.json()["timings"]
                        if timings.get("prompt_n", 0) < 128 or timings.get("predicted_n", 0) < 2:
                            raise ValueError("Placement probe did not complete long prefill and decode")
                    logged = log.read_text(encoding="utf-8", errors="replace")
                    proof = verify_vulkan_execution(execution_reports(logged), manifest, rank, selected) if engine == "prima" else baseline_matrix_placement(logged, layers, args.ngl, args.output_gpu)
                    result["checks"].append({"engine": engine, "status": "passed", "proof": proof, "timings": timings})
            except Exception as error:
                result["checks"].append({"engine": engine, "status": "failed", "error": str(error)})
            finally:
                log = directory / "server.log"
                if log.is_file():
                    args.output.with_suffix("." + engine + ".log").write_bytes(log.read_bytes())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--baseline-server", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--ngl", type=int, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--output-gpu", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = assess(args)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    passed = all(check["status"] == "passed" for check in result["checks"])
    print(json.dumps({"receipt": str(args.output), "passed": passed}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
