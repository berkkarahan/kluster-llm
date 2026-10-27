"""Owned native device selection and completed inference, without performance acceptance."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import httpx

from prima_assessment import unused_port
from kllm.modules.runtimes.interactors.registry import file_digest
from kllm.modules.primacpp.entities import LayerRange, RankAllocation
from kllm.modules.primacpp.interactors.execution import ExecutionManifest, Segment, asset_files
from kllm.modules.primacpp.interactors.evidence import execution_reports, verify_vulkan_execution
from kllm.shared.native import require_native


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--visible", default="0")
    parser.add_argument("--split", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    environment = {**os.environ, "GGML_VK_VISIBLE_DEVICES": args.visible,
                   "KLLM_PRIMA_EXECUTION_EVIDENCE": "1", "KLLM_PRIMA_BIND": "127.0.0.1"}
    wanted = [] if args.device == "none" else args.device.split(",")
    metadata = json.loads(require_native()._read_gguf_metadata_json(str(args.model.resolve()), False))
    values = metadata.get("metadata", metadata)
    layers = metadata.get("block_count") or metadata.get("n_layer") or next((v for k, v in values.items() if k.endswith(".block_count")), None)
    if type(layers) is not int or layers <= 0:
        raise ValueError("GGUF transformer layer count is unavailable")
    proportions = [float(value) for value in args.split.split(",")] if args.split else [1.0] * len(wanted)
    rank = RankAllocation(node_id="assessment", role="master", rank=0, address="127.0.0.1", next_node_id="assessment",
                          layers=[LayerRange(start=0, end=layers)], ngl=layers if wanted else 0, devices=wanted, tensor_split=proportions)
    binary_hash = file_digest(args.binary)
    manifest = ExecutionManifest(runtime_id=binary_hash, run_id=str(uuid4()), plan_digest="a" * 64,
                                 total_layers=layers, ranks=[rank], assets=[asset for asset, _ in asset_files(args.model)],
                                 settings={"context": 1024}, inventory_digest="b" * 64,
                                 segments=[Segment(start=0, end=layers, node_id="assessment")],
                                 backends={"assessment": "vulkan" if wanted else "cpu"})
    contract = args.output.with_suffix(".manifest.json")
    contract.write_text(json.dumps(manifest.model_dump(), sort_keys=True, separators=(",", ":")), encoding="utf-8")
    probe = subprocess.run([str(args.binary.resolve()), "--list-devices"], env=environment,
                           capture_output=True, text=True, timeout=30, check=True)
    capabilities = next(json.loads(line.split("=", 1)[1]) for line in probe.stdout.splitlines() if line.startswith("KLLM_PRIMA_CAPABILITIES="))
    selected = [next(device for device in capabilities["devices"] if device["name"] == name) for name in wanted]
    port = unused_port()
    command = [str(args.binary.resolve()), "--model", str(args.model.resolve()), "--host", "127.0.0.1",
               "--port", str(port), "--alias", "assessment", "--device", args.device, "--gpu-layers", "999",
               "--ctx-size", "1024", "--threads", "8", "--threads-batch", "8", "--parallel", "1"]
    command += ["--execution-manifest", str(contract.resolve())]
    if args.split:
        command += ["--tensor-split", args.split]
    receipt = {"command": command, "binary_sha256": file_digest(args.binary), "model_sha256": file_digest(args.model),
               "visible": args.visible, "qualification": "functional only; performance/physical cluster gates deferred"}
    log = args.output.with_suffix(".native.log")
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(command, env=environment, stdout=output, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=120, trust_env=False) as client:
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise ValueError("Native device process exited before readiness")
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise ValueError("Device process readiness timed out")
                body = {"model": "assessment", "messages": [{"role": "user", "content": "Output exactly the lowercase word hello and no other text."}],
                        "max_tokens": 128, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}
                response = client.post("/v1/chat/completions", json=body)
                response.raise_for_status()
                receipt["response"] = response.json()
                assert receipt["response"]["choices"][0]["message"]["content"].strip().lower() == "hello"
                output.flush()
                text = log.read_text(encoding="utf-8", errors="replace")
                records = [json.loads(line.split("=", 1)[1]) for line in text.splitlines() if line.startswith("KLLM_PRIMA_DEVICE_SELECTION=")]
                assert len(records) == 1 and records[0]["explicit"] is True
                assert records[0]["selected"] == wanted
                assert [name for name in records[0]["backends"] if not name.startswith("CPU")] == wanted
                receipt["selection"] = records[0]
                reports = execution_reports(text)
                if wanted:
                    receipt["placement"] = verify_vulkan_execution(reports, manifest, rank, selected, logical_names=wanted)
                else:
                    assert {report["phase"] for report in reports} == {"prefill", "decode"}
                    assert all(not report["devices"] and set(report["executed_weights"]) == {"CPU"} for report in reports)
                receipt["execution_reports"] = reports
                receipt["status"] = "passed"
        except Exception as error:
            receipt.update(status="failed", error=str(error))
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
    return receipt["status"] != "passed"


if __name__ == "__main__":
    raise SystemExit(main())
