"""Compare raw generation on identical rendered token IDs, including CPU reference.

Diagnostic evidence only: this does not replace gateway or paired performance gates.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
from uuid import uuid4

import httpx

from prima_assessment import baseline_placement, capture_baseline, capture_development_runtime, capture_runtime
from prima_local_benchmark import SAMPLING, TARGET, server
from kllm.modules.primacpp.entities import LayerRange, RankAllocation
from kllm.modules.primacpp.interactors.devices import discover_vulkan_devices, select_vulkan_devices, vulkan_environment
from kllm.modules.primacpp.interactors.execution import ExecutionManifest, Segment, asset_files
from kllm.modules.primacpp.interactors.evidence import execution_reports, verify_vulkan_execution
from kllm.modules.runtimes.interactors.registry import file_digest
from kllm.shared.native import require_native


def template_contract(client):
    """Exercise the native renderer's generation flag and HTTP error boundary."""
    body = {"messages": [{"role": "user", "content": "hello"}],
            "chat_template_kwargs": {"enable_thinking": False}}
    replies = {}
    for name, extra in (("default", {}), ("generation", {"add_generation_prompt": True}),
                        ("history", {"add_generation_prompt": False})):
        response = client.post("/apply-template", json={**body, **extra})
        response.raise_for_status()
        replies[name] = response.json()["prompt"]
    if (replies["default"] != replies["generation"]
            or replies["history"] == replies["generation"]
            or not replies["generation"].startswith(replies["history"])):
        raise ValueError("Template generation flag did not preserve the message history")
    refusals = []
    for bad in ({**body, "add_generation_prompt": value} for value in (0, "true", None)):
        response = client.post("/apply-template", json=bad)
        if response.status_code != 400 or response.json().get("error", {}).get("type") != "invalid_request_error":
            raise ValueError("Invalid template generation flag did not receive HTTP400")
        refusals.append(response.status_code)
    for bad in ({**body, "__prima_tool_name": "foreign"}, {"messages": "invalid"}, {}):
        response = client.post("/apply-template", json=bad)
        if response.status_code != 400:
            raise ValueError("Invalid/internal template parameters were accepted")
        refusals.append(response.status_code)
    return {"status": "passed", "prompts": replies, "invalid_request_statuses": refusals}


def assess(runtime, baseline, model, ngl, selector, include_cpu, output_gpu=False, expect_single_graph=False,
           chat_logprobs=False, concurrency=1, repetitions=1, cpu_only=False, prefix_tokens=None, server_logs_dir=None):
    if concurrency not in (1, 2) or repetitions < 1:
        raise ValueError("Use concurrency1/2 and a positive repetition count")
    if prefix_tokens is not None and not 2 <= prefix_tokens <= 256:
        raise ValueError("Prefix diagnosis requires 2..256 tokens, including a completed decode")
    limit = prefix_tokens or 256
    assets = asset_files(model)
    metadata = json.loads(require_native()._read_gguf_metadata_json(str(model), False))
    layers = metadata.get("block_count") or metadata.get("n_layer")
    if not layers:
        layers = next(v for k, v in metadata.get("metadata", metadata).items() if k.endswith(".block_count"))
    if not 0 < ngl <= layers:
        raise ValueError("Supply a positive transformer offload count within this model")
    selected = select_vulkan_devices(discover_vulkan_devices(runtime), [selector])
    prompts = {"short": "Return the exact JSON string requested by the schema.",
        "long": "Context for this test: " + "The sky is blue and the grass is green. " * 100
            + "\nReturn the exact JSON string requested by the schema."}
    rendered, engine_inputs, results, template_checks = {}, {}, [], {}
    with tempfile.TemporaryDirectory(prefix="prima raw token comparison ") as temp:
        root = Path(temp)
        for backend in (("cpu",) if cpu_only else (("vulkan", "cpu") if include_cpu else ("vulkan",))):
            for engine in ("llama", "prima"):
                directory = root / (backend + "-" + engine)
                directory.mkdir()
                count = ngl if backend == "vulkan" else 0
                arguments = ["--parallel", str(concurrency), "--batch-size", "512", "--ubatch-size", "128",
                             "--threads", "8", "--threads-batch", "8", "--cache-type-k", "f16", "--cache-type-v", "f16"]
                environment = {**runtime.get("runtime_env", {}), **vulkan_environment(selected),
                               "KLLM_PRIMA_BIND": "127.0.0.1", "KLLM_PRIMA_EXECUTION_EVIDENCE": "1"}
                manifest = rank = None
                if engine == "prima":
                    rank = RankAllocation(node_id="assessment", role="master", rank=0, address="127.0.0.1",
                        next_node_id="assessment", layers=[LayerRange(start=0, end=layers)], ngl=count,
                        devices=["Vulkan0"] if count else [], tensor_split=[1.0] if count else [], output_gpu=bool(count and output_gpu))
                    manifest = ExecutionManifest(runtime_id=runtime["runtime_id"], run_id=str(uuid4()),
                        plan_digest="a" * 64, total_layers=layers, ranks=[rank], assets=[a for a, _ in assets],
                        settings={"context": 4096}, inventory_digest="b" * 64,
                        segments=[Segment(start=0, end=layers, node_id=rank.node_id)], backends={rank.node_id: backend})
                    contract = directory / "execution.json"
                    contract.write_text(json.dumps(manifest.model_dump(), sort_keys=True, separators=(",", ":")), encoding="utf-8")
                    arguments += ["--execution-manifest", str(contract)]
                    binary = Path(runtime["llama_server"])
                else:
                    arguments += [*baseline_placement(count, bool(count and output_gpu)), "--flash-attn", "off", "--no-kv-unified"]
                    binary = baseline
                saved_log = server_logs_dir / (backend + "-" + engine + ".log") if server_logs_dir else None
                with server(binary, model, environment, arguments, directory, 4096, log_copy=saved_log) as (port, _, log):
                    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=180, trust_env=False) as client:
                        if engine == "prima" and "apply_template_v1" in runtime.get("capabilities", {}).get("features", []):
                            template_checks[backend] = template_contract(client)
                        for length, prompt in prompts.items():
                            supported = engine == "llama" or "apply_template_v1" in runtime.get("capabilities", {}).get("features", [])
                            local_render = {"status": "unavailable", "reason": "Native template rendering capability is absent"}
                            if supported:
                                response = client.post("/apply-template", json={**SAMPLING,
                                    "messages": [{"role": "user", "content": prompt}],
                                    "chat_template_kwargs": {"enable_thinking": False},
                                    "response_format": {"type": "json_schema", "json_schema": {
                                        "name": "fixed", "schema": {"type": "string", "const": TARGET}}}})
                                response.raise_for_status()
                                text = response.json()["prompt"]
                                response = client.post("/tokenize", json={"content": text, "add_special": False, "parse_special": True})
                                response.raise_for_status()
                                tokens = response.json()["tokens"]
                                if not isinstance(text, str) or not isinstance(tokens, list) or not tokens or any(type(t) is not int or t < 0 for t in tokens):
                                    raise ValueError("Template/tokenizer response is incomplete or invalid")
                                local_render = {"status": "observed", "prompt": text, "tokens": tokens}
                            engine_inputs.setdefault(backend + "-" + engine, {})[length] = local_render
                            if length not in rendered:
                                if not supported:
                                    raise ValueError("Baseline rendered input is unavailable")
                                rendered[length] = {"prompt": text, "tokens": tokens}
                            raw_request = {**SAMPLING, "prompt": rendered[length]["tokens"],
                                "n_predict": limit, "cache_prompt": False, "json_schema": {"type": "string", "const": TARGET}}
                            chat_request = {**SAMPLING,
                                **({"logprobs": True, "top_logprobs": 3} if chat_logprobs else {}), "model": "assessment",
                                "messages": [{"role": "user", "content": prompt}], "max_tokens": limit,
                                "cache_prompt": False, "chat_template_kwargs": {"enable_thinking": False},
                                "response_format": {"type": "json_schema", "json_schema": {
                                        "name": "fixed", "schema": {"type": "string", "const": TARGET}}}}
                            def generate(path, payload):
                                response = client.post(path, json=payload)
                                response.raise_for_status()
                                return response.json()
                            for repetition in range(repetitions):
                                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                                    raw = list(pool.map(lambda _: generate("/completion", raw_request), range(concurrency)))
                                    chat = list(pool.map(lambda _: generate("/v1/chat/completions", chat_request), range(concurrency)))
                                for request_index, (body, chat_body) in enumerate(zip(raw, chat)):
                                    output = body["content"]
                                    if prefix_tokens is None and (json.loads(output) != TARGET
                                            or json.loads(chat_body["choices"][0]["message"]["content"]) != TARGET):
                                        raise ValueError("Generation did not satisfy the same constrained output")
                                    response = client.post("/tokenize", json={"content": output, "add_special": False})
                                    response.raise_for_status()
                                    results.append({"engine": engine, "backend": backend, "length": length,
                                        "repetition": repetition, "request_index": request_index,
                                        "input_tokens": len(rendered[length]["tokens"]), "output": output,
                                        "chat_input_matches_baseline": local_render.get("tokens") == rendered[length]["tokens"] if supported else None,
                                        "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                                        "generated_tokens": body["timings"]["predicted_n"], "canonical_output_tokens": response.json()["tokens"],
                                        "native_result": body, "chat_result": chat_body})
                    if engine == "prima" and count:
                        reports = execution_reports(log.read_text(errors="replace"))
                        results[-1]["gpu_execution_evidence"] = verify_vulkan_execution(reports, manifest, rank, selected)
                        results[-1]["completed_graphs"] = [r.get("completed_graphs") for r in reports]
                        if expect_single_graph and any(r.get("completed_graphs") != 1 for r in reports):
                            raise ValueError("Local generation did not complete one connected graph per batch")
                    elif engine == "prima":
                        reports = execution_reports(log.read_text(errors="replace"))
                        if ({r.get("phase") for r in reports} != {"prefill", "decode"}
                                or any(name != "CPU" for r in reports for name in r["executed_weights"])):
                            raise ValueError("The explicit CPU reference executed accelerator matrix work or lacks completed phases")
                        results[-1]["cpu_execution_evidence"] = reports
                        if expect_single_graph and any(r.get("completed_graphs") != 1 for r in reports):
                            raise ValueError("Local CPU generation did not complete one connected graph per batch")
                print(json.dumps({"backend": backend, "engine": engine, "completed": True}), flush=True)
    return {"runtime_id": runtime["runtime_id"], "recipe_digest": runtime["recipe_digest"],
        "source_commit": runtime["commit"], "server_sha256": file_digest(Path(runtime["llama_server"])),
        "baseline": capture_baseline(baseline), "model_sha256": assets[0][0].sha256,
        "rendered_inputs": rendered, "rendered_inputs_by_engine": engine_inputs,
        "template_contracts": template_checks,
        "results": results, "output_gpu": output_gpu,
        "chat_logprobs": chat_logprobs, "concurrency": concurrency, "repetitions": repetitions,
        "cpu_only": cpu_only, "prefix_tokens": prefix_tokens,
        "server_logs_directory": str(server_logs_dir) if server_logs_dir else None,
        "qualification": "open: prefix diagnostic only" if prefix_tokens else "open: diagnostic only"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--baseline-server", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--ngl", type=int, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--skip-cpu", action="store_true")
    parser.add_argument("--cpu-only", action="store_true")
    parser.add_argument("--concurrency", type=int, choices=(1, 2), default=1)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--output-gpu", action="store_true")
    parser.add_argument("--expect-single-graph", action="store_true")
    parser.add_argument("--chat-logprobs", action="store_true", help="Capture generated chat token pieces to diagnose parser delimiters")
    parser.add_argument("--prefix-tokens", type=int, help="Diagnose initial logits without requiring a completed answer (2..256)")
    parser.add_argument("--development-server", type=Path)
    parser.add_argument("--server-logs-dir", type=Path, help="Retain owned server logs, including failures, in a new directory")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.server_logs_dir:
            args.server_logs_dir.mkdir(parents=True, exist_ok=False)
        capture_baseline(args.baseline_server.resolve())
        runtime = capture_runtime(args.runtime_dir)
        if args.development_server:
            runtime = capture_development_runtime(runtime, args.development_server)
        result = assess(runtime, args.baseline_server.resolve(),
                        args.model.resolve(), args.ngl, args.device, not args.skip_cpu, args.output_gpu,
                        args.expect_single_graph, args.chat_logprobs, args.concurrency, args.repetitions, args.cpu_only,
                        args.prefix_tokens, args.server_logs_dir)
        result["development_server"] = bool(args.development_server)
        if args.development_server:
            result["qualification"] = "open: developmental worktree diagnostic"
        result["status"] = "observed"
    except Exception as error:
        result = {"status": "failed", "error": str(error), "qualification": "open",
                  "server_logs_directory": str(args.server_logs_dir) if args.server_logs_dir else None}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(args.output), "status": result["status"]}))
    return 0 if result["status"] == "observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
