"""Owned draft-context isolation and generation checks; no performance acceptance."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import httpx

from prima_assessment import unused_port
from kllm.modules.primacpp.entities import LayerRange, RankAllocation
from kllm.modules.primacpp.interactors.execution import ExecutionManifest, Segment, asset_files
from kllm.modules.runtimes.interactors.registry import file_digest
from kllm.shared.native import require_native


def assess(args):
    metadata = json.loads(require_native()._read_gguf_metadata_json(str(args.model.resolve()), False))
    values = metadata.get("metadata", metadata)
    layers = metadata.get("block_count") or next(v for k, v in values.items() if k.endswith(".block_count"))
    rank = RankAllocation(node_id="assessment", role="master", rank=0, address="127.0.0.1", next_node_id="assessment",
                          layers=[LayerRange(start=0, end=layers)], ngl=layers, devices=["Vulkan0"], tensor_split=[1.0])
    assets = [asset for asset, _ in asset_files(args.model, {"draft": args.draft})]
    binary_hash = file_digest(args.binary)
    manifest = ExecutionManifest(runtime_id=binary_hash, run_id=str(uuid4()), plan_digest="a" * 64,
                                 total_layers=layers, ranks=[rank], assets=assets,
                                 settings={"context": 1024, "speculative": "draft-simple", "draft_model": args.draft.name},
                                 inventory_digest="b" * 64, segments=[Segment(start=0, end=layers, node_id="assessment")],
                                 backends={"assessment": "vulkan"})
    contract = args.output.with_suffix(".manifest.json")
    contract.write_text(json.dumps(manifest.model_dump(), sort_keys=True, separators=(",", ":")), encoding="utf-8")
    port = unused_port()
    command = [str(args.binary.resolve()), "--model", str(args.model.resolve()), "--model-draft", str(args.draft.resolve()),
               "--gpu-layers-draft", "0", "--draft-max", "3", "--draft-min", "0", "--draft-p-min", "0",
               "--device", "Vulkan0", "--execution-manifest", str(contract.resolve()), "--ctx-size", "1024",
               "--batch-size", "128", "--ubatch-size", "32",
               "--threads", "8", "--threads-batch", "8", "--parallel", "1", "--host", "127.0.0.1", "--port", str(port)]
    environment = {**os.environ, "GGML_VK_VISIBLE_DEVICES": "0", "KLLM_PRIMA_EXECUTION_EVIDENCE": "1", "KLLM_PRIMA_BIND": "127.0.0.1"}
    log = args.output.with_suffix(".native.log")
    receipt = {"command": command, "binary_sha256": binary_hash, "assets": [asset.model_dump() for asset in assets],
               "qualification": "functional only; performance/physical cluster acceptance deferred"}
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(command, env=environment, stdout=output, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=180, trust_env=False) as client:
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise ValueError("Draft server exited before readiness")
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise ValueError("Draft server readiness timed out")
                text = log.read_text(encoding="utf-8", errors="replace")
                selections = [json.loads(line.split("=", 1)[1]) for line in text.splitlines() if line.startswith("KLLM_PRIMA_DEVICE_SELECTION=")]
                receipt["selections"] = selections
                if len(selections) != 3 or selections[0]["backends"] != ["CPU"] or selections[-1]["backends"] != ["CPU"]:
                    raise ValueError("CPU draft inherited target manifest/device ownership")
                if selections[1]["backends"] != ["Vulkan0", "CPU"]:
                    raise ValueError("Target Vulkan ownership changed while loading the draft")
                response = client.post("/v1/chat/completions", json={
                    "messages": [{"role": "user", "content": "Write the integers from 1 to 12, separated by spaces."}],
                    "max_tokens": 64, "temperature": 0, "logprobs": True, "chat_template_kwargs": {"enable_thinking": False}})
                response.raise_for_status()
                receipt["response"] = response.json()
                choice = receipt["response"]["choices"][0]
                if choice["message"]["content"].strip() != "1 2 3 4 5 6 7 8 9 10 11 12":
                    raise ValueError("Draft verification changed the expected target text")
                if len(choice["logprobs"]["content"]) != receipt["response"]["usage"]["completion_tokens"]:
                    raise ValueError("Accepted draft tokens lost logprobs or inflated usage")
                limited = client.post("/v1/chat/completions", json={
                    "messages": [{"role": "user", "content": "Write the integers from 1 to 12, separated by spaces."}],
                    "max_tokens": 4, "temperature": 0, "logprobs": True, "chat_template_kwargs": {"enable_thinking": False}})
                limited.raise_for_status()
                receipt["limited_response"] = limited.json()
                if (receipt["limited_response"]["usage"]["completion_tokens"] != 4
                        or len(receipt["limited_response"]["choices"][0]["logprobs"]["content"]) != 4
                        or receipt["limited_response"]["choices"][0]["finish_reason"] != "length"):
                    raise ValueError("Draft batch exceeded the requested output limit")
                invalid = client.post("/completion", json={"prompt": "hello", "n_predict": 4, "speculative.n_max": 1025})
                if invalid.status_code != 400:
                    raise ValueError("Invalid draft budget was not refused before allocation")
                long_response = client.post("/v1/chat/completions", json={
                    "messages": [{"role": "system", "content": "Background padding: " + "quiet " * 650},
                                 {"role": "user", "content": "Output exactly the lowercase word hello and no other text."}],
                    "max_tokens": 4, "temperature": 0, "logprobs": True, "chat_template_kwargs": {"enable_thinking": False}})
                long_response.raise_for_status()
                receipt["long_response"] = long_response.json()
                if (receipt["long_response"]["usage"]["prompt_tokens"] <= 128
                        or receipt["long_response"]["choices"][0]["message"]["content"].strip() != "hello"):
                    raise ValueError("Draft prompt prefill did not preserve text across bounded batches")
                text = log.read_text(encoding="utf-8", errors="replace")
                reports = [json.loads(line.split("=", 1)[1]) for line in text.splitlines() if line.startswith("KLLM_PRIMA_SPECULATION=")]
                if not reports or not any(report["accepted"] > 0 for report in reports):
                    raise ValueError("No completed target-verified draft token work was observed")
                receipt["speculation"] = reports
                receipt["status"] = "passed"
        except Exception as error:
            receipt.update(status="failed", error=str(error), native_exit=process.poll())
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(10)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "receipt": str(args.output)}))
    return int(receipt["status"] != "passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("binary", "model", "draft", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    return assess(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
