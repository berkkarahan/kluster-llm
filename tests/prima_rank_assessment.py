"""Controlled same-host native rank readiness; not physical cluster qualification."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from uuid import uuid4

import httpx

from prima_assessment import capture_runtime, unused_port
from kllm.shared.native import require_native
from kllm.modules.primacpp.entities import LayerRange, RankAllocation
from kllm.modules.primacpp.interactors.execution import ExecutionManifest, Segment, asset_files
from kllm.modules.primacpp.interactors.devices import discover_vulkan_devices, select_vulkan_devices, vulkan_environment
from kllm.modules.primacpp.interactors.evidence import execution_reports, verify_vulkan_execution
from kllm.modules.primacpp.interactors.launch import rank_arguments, device_arguments
from kllm.modules.primacpp.interactors.participants import observe_participant
from kllm.modules.runtimes.interactors.registry import file_digest


def assess(args):
    runtime = capture_runtime(args.runtime_dir)
    if args.development_bin:
        runtime = {**runtime, **{key: str(args.development_bin.resolve() / Path(runtime[key]).name)
                                  for key in ("llama_server", "llama_cli")}}
    files = asset_files(args.model.resolve(), {"draft": args.draft_model.resolve()} if args.draft_model else None)
    meta = json.loads(require_native()._read_gguf_metadata_json(str(args.model.resolve()), False))
    layers = meta.get("block_count") or meta.get("n_layer")
    if not layers:
        layers = next(v for k, v in meta.get("metadata", meta).items() if k.endswith(".block_count"))
    worker_layers = layers if args.zero_work_master else max(1, layers // 2)
    selected = select_vulkan_devices(discover_vulkan_devices(runtime), [args.device] if args.device else None) if args.backend == "vulkan" else []
    master_layers = layers - worker_layers
    data_port, signal_port, http_port = unused_port(), unused_port(), unused_port()
    while {data_port, data_port + 1} & {signal_port, signal_port + 1}:
        signal_port = unused_port()
    ranks = [
        RankAllocation(node_id="master", role="master", rank=0, address="127.0.0.1", next_node_id="worker",
            layers=[] if args.zero_work_master else [LayerRange(start=worker_layers, end=layers)],
            ngl=master_layers if selected else 0, devices=["Vulkan0"] if selected and master_layers else [],
            tensor_split=[1.0] if selected and master_layers else [], output_gpu=bool(selected and master_layers)),
        RankAllocation(node_id="worker", role="worker", rank=1, address="127.0.0.2", next_node_id="master",
            layers=[LayerRange(start=0, end=worker_layers)], ngl=worker_layers if selected else 0,
            devices=["Vulkan0"] if selected else [], tensor_split=[1.0] if selected else []),
    ]
    settings = {"context": 512, "threads": 2, "threads_batch": 2, "parallel_slots": 1,
                "batch_size": 128, "micro_batch": 32, "cache_type_k": "f16", "cache_type_v": "f16",
                "checkpoints": 0, "cache_ram": 0, "flash_attention": "off"}
    if args.draft_model:
        settings.update(speculative="draft-simple", draft_model=str(args.draft_model.resolve()), draft_max=3,
                        draft_gpu_layers=0, draft_cache_type_k="f16", draft_cache_type_v="f16")
    manifest = ExecutionManifest(runtime_id=runtime["runtime_id"], run_id=str(uuid4()), plan_digest="a" * 64,
        total_layers=layers, data_port=data_port, signal_port=signal_port, ranks=ranks,
        assets=[asset for asset, _ in files], settings=settings, inventory_digest="b" * 64,
        segments=[Segment(start=0, end=worker_layers, node_id="worker")] + ([] if args.zero_work_master else
            [Segment(start=worker_layers, end=layers, node_id="master")]),
        backends={rank.node_id: "vulkan" if rank.ngl else "cpu" for rank in ranks})
    if args.participant_evidence:
        manifest = ExecutionManifest.model_validate(manifest.model_copy(update={"participant_evidence": {
            rank.node_id: observe_participant(runtime, rank.node_id, rank, manifest.backends[rank.node_id])
            for rank in ranks}}).model_dump())
    result = {"source_commit": runtime["commit"], "recipe_digest": runtime["recipe_digest"],
        "development_server": bool(args.development_bin), "manifest": manifest.model_dump(), "checks": [],
        "server_sha256": file_digest(Path(runtime["llama_server"])),
        "cli_sha256": file_digest(Path(runtime["llama_cli"])),
        "selected_devices": selected,
        "qualification": "open: controlled same-host ranks; physical distributed validation will be tested later"}
    processes = []
    with tempfile.TemporaryDirectory(prefix="prima controlled ranks ") as temp:
        root = Path(temp)
        contract = root / "execution.json"
        contract.write_text(json.dumps(manifest.model_dump(), sort_keys=True, separators=(",", ":")), encoding="utf-8")
        try:
            for rank in (ranks[1], ranks[0]):
                binary = Path(runtime["llama_server"] if rank.rank == 0 else runtime["llama_cli"])
                common = ["--model", str(args.model.resolve()), "--ctx-size", "512", "--threads", "2",
                          "--rank", str(rank.rank), "--execution-manifest", str(contract)]
                arguments = common + rank_arguments(binary, settings, rank=rank.rank) + device_arguments(
                    binary, runtime, rank, selected=selected if rank.ngl else [])
                if rank.rank == 0:
                    arguments += ["--host", "127.0.0.1", "--port", str(http_port), "--alias", "assessment"]
                    if args.draft_model:
                        arguments += ["--model-draft", str(args.draft_model.resolve()), "--draft-min", "0", "--draft-p-min", "0"]
                environment = {**os.environ, **runtime.get("runtime_env", {}), "KLLM_PRIMA_BIND": rank.address}
                if selected:
                    environment.update(vulkan_environment(selected))
                    environment["KLLM_PRIMA_EXECUTION_EVIDENCE"] = "1"
                environment.pop("GGML_SCHED_DEBUG", None)
                with (root / (rank.node_id + ".log")).open("w", encoding="utf-8") as output:
                    processes.append(subprocess.Popen([str(binary), *arguments], env=environment,
                                                      stdout=output, stderr=subprocess.STDOUT))
            deadline = time.monotonic() + 90
            reports = {}
            with httpx.Client(timeout=1, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if any(process.poll() is not None for process in processes):
                        raise ValueError("A native rank exited before readiness")
                    for rank in ranks:
                        text = (root / (rank.node_id + ".log")).read_text(encoding="utf-8", errors="replace")
                        entries = [json.loads(line.split("=", 1)[1]) for line in text.splitlines()
                                   if line.startswith("KLLM_PRIMA_RANK_READY=")]
                        if entries:
                            expected = {"runtime_id": manifest.runtime_id, "manifest_digest": manifest.digest,
                                "rank": rank.rank, "ngl": rank.ngl, "layers": sum(w.end - w.start for w in rank.layers),
                                "manifest_mode": True, "phase": "ready", "schema_version": 1,
                                "readiness_stage": "server_request_loop" if rank.rank == 0 else "worker_decode_loop"}
                            if len(entries) != 1 or entries[0] != expected:
                                raise ValueError("Native rank readiness differs from its exact manifest")
                            reports[rank.node_id] = entries[0]
                    try:
                        if len(reports) == 2 and client.get(f"http://127.0.0.1:{http_port}/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.05)
                else:
                    raise ValueError("Controlled native ranks did not reach the readiness barrier")
                result["checks"].append({"case": "native_rank_barrier", "status": "passed", "reports": reports})
                response = client.post(f"http://127.0.0.1:{http_port}/v1/chat/completions", timeout=60, json={
                    "model": "assessment", "messages": [{"role": "user", "content":
                        "Write the integers from 1 to 12, separated by spaces." if args.draft_model else
                        "Output exactly the lowercase word hello and no other text."}],
                    "max_tokens": 128, "temperature": 0, "seed": 0, "logprobs": bool(args.draft_model), "chat_template_kwargs": {"enable_thinking": False}})
                response.raise_for_status()
                body = response.json()
                expected_text = "1 2 3 4 5 6 7 8 9 10 11 12" if args.draft_model else "hello"
                if body["choices"][0]["message"]["content"].strip().lower() != expected_text:
                    raise ValueError("Controlled native ranks returned an incorrect answer")
                result["checks"].append({"case": "controlled_generation", "status": "passed", "response": body})
                if args.draft_model:
                    text = (root / "master.log").read_text(encoding="utf-8", errors="replace")
                    spec = [json.loads(line.split("=", 1)[1]) for line in text.splitlines() if line.startswith("KLLM_PRIMA_SPECULATION=")]
                    if (len(body["choices"][0]["logprobs"]["content"]) != body["usage"]["completion_tokens"]
                            or not spec or not any(report["accepted"] > 0 for report in spec)):
                        raise ValueError("Controlled ranks lost verified draft tokens or logprobs")
                    result["checks"].append({"case": "controlled_draft_verification", "status": "passed", "reports": spec})
                for rank in ranks:
                    if rank.ngl:
                        text = (root / (rank.node_id + ".log")).read_text(encoding="utf-8", errors="replace")
                        proof = verify_vulkan_execution(execution_reports(text), manifest, rank, selected)
                        result["checks"].append({"case": rank.node_id + "_gpu_execution", "status": "passed", "proof": proof})
        except Exception as error:
            result["checks"].append({"case": "controlled_ranks", "status": "failed", "error": str(error)})
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(10)
            for rank in ranks:
                log = root / (rank.node_id + ".log")
                if log.exists():
                    args.output.with_suffix("." + rank.node_id + ".log").write_bytes(log.read_bytes())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--development-bin", type=Path)
    parser.add_argument("--draft-model", type=Path)
    parser.add_argument("--zero-work-master", action="store_true")
    parser.add_argument("--participant-evidence", action="store_true",
                        help="Include observed per-rank hardware/build metadata in the native manifest")
    parser.add_argument("--backend", choices=("cpu", "vulkan"), default="cpu")
    parser.add_argument("--device", help="Exact physical Vulkan UUID or device name")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = assess(args)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    passed = all(check["status"] == "passed" for check in result["checks"])
    print(json.dumps({"receipt": str(args.output), "passed": passed}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
