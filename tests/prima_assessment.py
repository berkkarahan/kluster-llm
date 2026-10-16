"""Standalone, evidence-only assessment. Never selects an agent's Prima runtime."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import httpx

from kllm.modules.primacpp.entities import LayerRange, RankAllocation
from kllm.modules.primacpp.interactors.compatibility import BASELINE_COMMIT, launch_matrix
from kllm.modules.primacpp.interactors.execution import ExecutionManifest, Segment, asset_files
from kllm.modules.primacpp.interactors.recipe import recipe, recipe_digest
from kllm.modules.runtimes.interactors.registry import file_digest
from kllm.modules.primacpp.interactors.devices import (
    discover_vulkan_devices, select_vulkan_devices, vulkan_environment, remapped_vulkan_names,
)
from kllm.modules.primacpp.interactors.evidence import execution_reports, verify_vulkan_execution
from kllm.modules.primacpp.interactors.assessment import response_errors


def capture_runtime(directory: Path) -> dict:
    value = json.loads((directory / "manifest.json").read_text())
    definition = recipe()
    if (value.get("repository"), value.get("commit"), value.get("recipe_digest")) != (
        definition["source_repository"],
        definition["source_commit"],
        recipe_digest(),
    ):
        raise ValueError("Assessment runtime is stale; build the pinned Prima recipe")
    artifacts = Path(value["artifact_dir"]).resolve()
    for name, digest in value["artifacts"].items():
        path = (artifacts / name).resolve()
        if not path.is_relative_to(artifacts) or file_digest(path) != digest:
            raise ValueError("Assessment artifact failed digest verification")
    return value


def capture_development_runtime(runtime: dict, binary: Path) -> dict:
    """Bind a diagnostic override to its actual binary and capability report."""
    binary = binary.resolve()
    digest = file_digest(binary)
    environment = {key: value for key, value in {**os.environ, **runtime.get("runtime_env", {})}.items()
                   if value is not None}
    probe = subprocess.run([str(binary), "--list-devices"], env=environment, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60, check=True)
    reports = [json.loads(line.split("=", 1)[1]) for line in probe.stdout.splitlines()
               if line.startswith("KLLM_PRIMA_CAPABILITIES=")]
    if (len(reports) != 1 or not isinstance(reports[0], dict)
            or not isinstance(reports[0].get("features"), list)
            or any(not isinstance(feature, str) for feature in reports[0]["features"])
            or file_digest(binary) != digest):
        raise ValueError("Development binary capability report or digest is invalid")
    return {**runtime, "llama_server": str(binary), "capabilities": reports[0],
            "artifacts": {**runtime.get("artifacts", {}), binary.name: digest}}


def capture_baseline(binary: Path) -> dict:
    """Reject an unknown baseline before starting either assessment server."""
    digest = file_digest(binary)
    completed = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, timeout=60
    )
    output = completed.stdout + completed.stderr
    commits = re.findall(r"\bcommit\s+([0-9a-f]{7,40})\b", output, flags=re.IGNORECASE)
    if (
        completed.returncode != 0
        or not any(BASELINE_COMMIT.startswith(commit.lower()) for commit in commits)
        or file_digest(binary) != digest
    ):
        raise ValueError("Baseline binary must report the pinned llama.cpp commit " + BASELINE_COMMIT)
    return {
        "sha256": digest,
        "reported_version": output.strip(),
        "expected_commit": BASELINE_COMMIT,
        "identity_check": "reported commit prefix and binary digest; not a build provenance attestation",
    }


def baseline_placement(ngl: int, output_gpu: bool, *, device="Vulkan0") -> list[str]:
    """Match transformer/output ownership against the pinned 7fe450e baseline."""
    if ngl < 0 or (output_gpu and not ngl):
        raise ValueError("Baseline placement requires explicit transformer/output offload")
    # This baseline includes output in every positive --gpu-layers count.
    args = ["--gpu-layers", str(ngl + 1 if ngl else 0), "--device", device if ngl else "none"]
    if not output_gpu:
        # Tied models project with token_embd.weight instead of output.weight.
        args += ["--override-tensor", r"^(output[._].*|token_embd\.weight)$=CPU"]
    return [*args, "--no-op-offload"]


def observation_status(data: dict) -> str:
    # Equal failures and empty streams cannot count as parity successes.
    expected = {"chat", "structured", "tools", "stream"}
    if any({case.get("case") for case in result["cases"]} != expected
           or len(result["cases"]) != len(expected)
           for result in (data["prima"], data["baseline"])):
        return "failed"
    if not data["equal_http_statuses"] or any(
        case["status"] >= 400 or case.get("semantic_errors", ["Missing semantic check"])
        for result in (data["prima"], data["baseline"]) for case in result["cases"]
    ):
        return "failed"
    return "observed"


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request_cases(port: int) -> list[dict]:
    results = []
    body = {
        "model": "assessment",
        "messages": [{"role": "user", "content": "Output exactly the lowercase ASCII word hello, without translation, explanation, punctuation or other text."}],
        "temperature": 0,
        "seed": 0,
        "max_tokens": 128,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60) as client:
        for name, payload in [
            ("chat", body),
            ("structured", {**body, "messages": [{"role": "user", "content": 'Return JSON: {"reply":"hello"}.'}],
                            "response_format": {"type": "json_schema", "json_schema": {"name": "reply", "strict": True,
                                "schema": {"type": "object", "properties": {"reply": {"type": "string", "const": "hello"}},
                                           "required": ["reply"], "additionalProperties": False}}}}),
            (
                "tools",
                {
                    **body,
                    "messages": [{"role": "user", "content": "Call echo with text hello."}],
                    "tool_choice": {"type": "function", "function": {"name": "echo"}},
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "echo",
                                "description": "Echo text",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"text": {"type": "string"}},
                                    "required": ["text"],
                                    "additionalProperties": False,
                                },
                            },
                        }
                    ],
                },
            ),
        ]:
            start = time.monotonic()
            response = client.post("/v1/chat/completions", json=payload)
            try:
                data = response.json()
            except ValueError:
                data = {"unparsed": response.text[:1000]}
            results.append(
                {
                    "case": name,
                    "status": response.status_code,
                    "elapsed_ms": (time.monotonic() - start) * 1000,
                    "body": data,
                    "semantic_errors": response_errors(name, response.status_code, data),
                }
            )
        start = time.monotonic()
        first = None
        chunks = 0
        done = False
        finish_reason = None
        content_parts = []
        with client.stream(
            "POST", "/v1/chat/completions", json={**body, "stream": True}
        ) as response:
            for line in response.iter_lines():
                if line.strip() == "data: [DONE]":
                    done = True
                if line.startswith("data:") and line.strip() != "data: [DONE]":
                    event = json.loads(line[5:])
                    choice = (event.get("choices") or [{}])[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    content = choice.get("delta", {}).get("content")
                    if content:
                        first = first or time.monotonic()
                        chunks += 1
                        content_parts.append(content)
            results.append(
                {
                    "case": "stream",
                    "status": response.status_code,
                    "ttft_ms": (first - start) * 1000 if first else None,
                    "elapsed_ms": (time.monotonic() - start) * 1000,
                    "content_events": chunks,
                    "content": "".join(content_parts),
                    "done": done,
                    "finish_reason": finish_reason,
                    "semantic_errors": [] if done and finish_reason == "stop" and "".join(content_parts).strip().lower() == "hello" else ["Incomplete stream, terminal event or answer"],
                }
            )
    return results


def serve_cases(
    binary: Path, model: Path, environment: dict, arguments: list[str], root: Path
) -> dict:
    port = unused_port()
    log = root / "server.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            [
                str(binary),
                "--model",
                str(model),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--alias",
                "assessment",
                "--ctx-size",
                "512",
                *arguments,
            ],
            env={**os.environ, **environment},
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            until = time.monotonic() + 120
            while time.monotonic() < until:
                if process.poll() is not None:
                    raise ValueError("Server exited before readiness: " + log.read_text()[-2000:])
                try:
                    with httpx.Client(timeout=1) as client:
                        if client.get(f"http://127.0.0.1:{port}/health").status_code == 200:
                            break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                raise ValueError("Model readiness timed out")
            cases = request_cases(port)
            output.flush()
            logged = log.read_text(encoding="utf-8", errors="replace")
            return {"cases": cases, "log": logged[-8192:], "execution_reports": execution_reports(logged)}
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


def model_assessment(runtime, baseline, model, backend, ngl, *, device=None):
    files = asset_files(model)
    rank = RankAllocation(
        node_id="assessment",
        role="master",
        rank=0,
        address="127.0.0.1",
        next_node_id="assessment",
        layers=[LayerRange(start=0, end=1)],
        ngl=0,
    )
    # Metadata comes from the same GGUF reader used by the application.
    from kllm.shared.native import require_native

    raw_metadata = require_native()._read_gguf_metadata_json(str(model), False)
    if raw_metadata is None:
        raise ValueError("GGUF metadata is unavailable")
    metadata = json.loads(raw_metadata)
    layers = metadata.get("block_count") or metadata.get("n_layer")
    if not layers:
        # Native reader returns the architecture-specific GGUF keys under metadata.
        values = metadata.get("metadata", metadata)
        layers = next((v for k, v in values.items() if k.endswith(".block_count")), None)
    if not isinstance(layers, int) or layers <= 0:
        raise ValueError("GGUF transformer layer count is unavailable")
    devices = runtime["capabilities"]["devices"]
    selected_devices = select_vulkan_devices(discover_vulkan_devices(runtime), [device] if device else None) if backend == "vulkan" else []
    selected = selected_devices[0] if selected_devices else next((d for d in devices if d["backend"].lower() == backend), None)
    if backend != "cpu" and not selected:
        raise ValueError("Requested assessment backend has no device; no substitution allowed")
    rank = rank.model_copy(
        update={
            "layers": [LayerRange(start=0, end=layers)],
            "ngl": min(ngl, layers) if backend != "cpu" else 0,
            "devices": remapped_vulkan_names(selected_devices) if backend == "vulkan" and ngl else ([selected["name"]] if selected and ngl else []),
            "tensor_split": [1.0] if selected and ngl else [],
        }
    )
    manifest = ExecutionManifest(
        runtime_id=runtime["runtime_id"],
        run_id=str(uuid4()),
        plan_digest="a" * 64,
        total_layers=layers,
        ranks=[rank],
        assets=[a for a, _ in files],
        settings={"context": 512},
        inventory_digest="b" * 64,
        segments=[Segment(start=0, end=layers, node_id="assessment")],
        backends={"assessment": backend},
    )
    with tempfile.TemporaryDirectory(prefix="prima-assessment-") as directory:
        root = Path(directory)
        contract = root / "execution.json"
        contract.write_text(
            json.dumps(manifest.model_dump(), sort_keys=True, separators=(",", ":"))
        )
        env = runtime.get("runtime_env", {}).copy()
        env["KLLM_PRIMA_BIND"] = "127.0.0.1"
        if backend == "vulkan":
            env.update(vulkan_environment(selected_devices))
            env["KLLM_PRIMA_EXECUTION_EVIDENCE"] = "1"
        prima = serve_cases(
            Path(runtime["llama_server"]), model, env, ["--execution-manifest", str(contract)], root
        )
        baseline_result = serve_cases(baseline, model, env,
            baseline_placement(rank.ngl, rank.output_gpu, device="Vulkan0" if backend == "vulkan" else (selected or {}).get("name", "none")), root)
        equal_status = all(
            p["status"] == b["status"] for p, b in zip(prima["cases"], baseline_result["cases"])
        )
        gpu_evidence = "unverified" if rank.ngl else "not_requested"
        if backend == "vulkan" and rank.ngl:
            try:
                gpu_evidence = verify_vulkan_execution(prima["execution_reports"], manifest, rank, selected_devices)
            except ValueError as error:
                gpu_evidence = {"status": "unavailable" if not prima["execution_reports"] else "failed", "reason": str(error)}
        return {
            "model_sha256": files[0][0].sha256,
            "assets": [a.model_dump() for a, _ in files],
            "manifest_digest": manifest.digest,
            "effective_manifest": manifest.model_dump(),
            "model_metadata": metadata,
            "backend": backend,
            "ngl": rank.ngl,
            "selected_devices": selected_devices,
            "prima": prima,
            "baseline": baseline_result,
            "equal_http_statuses": equal_status,
            "gpu_execution_evidence": gpu_evidence,
            "qualification": "open: API status checks do not prove semantic parity or GPU placement",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--baseline-server", type=Path)
    parser.add_argument("--dense-model", type=Path)
    parser.add_argument("--moe-model", type=Path)
    parser.add_argument(
        "--suite", choices=["api", "lifecycle", "distributed", "vulkan", "all"], default="all"
    )
    parser.add_argument(
        "--backend", choices=["cpu", "vulkan", "metal", "cuda", "rocm"], default="vulkan"
    )
    parser.add_argument("--ngl", type=int, default=1)
    parser.add_argument("--device", help="Exact Vulkan UUID, device name or description")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.ngl < 0:
        parser.error("--ngl must be nonnegative; assessment uses an explicit offload count")
    if args.suite == "vulkan" and (args.backend != "vulkan" or args.ngl == 0):
        parser.error("Vulkan assessment requires --backend vulkan and a positive --ngl")
    evidence = {
        "schema_version": 1,
        "os": platform.platform(),
        "architecture": platform.machine(),
        "recipe_digest": recipe_digest(),
        "source_commit": recipe()["source_commit"],
        "baseline_commit": BASELINE_COMMIT,
        "suite": args.suite,
        "results": [],
        "qualification": "open",
    }
    try:
        runtime = capture_runtime(args.runtime_dir)
        evidence["devices"] = runtime["capabilities"]["devices"]
        if args.baseline_server:
            evidence["baseline_binary"] = capture_baseline(args.baseline_server.resolve())
            evidence["compatibility"] = launch_matrix(
                Path(runtime["llama_server"]), args.baseline_server
            )
        if args.suite in ("all", "lifecycle"):
            env = {**os.environ, "KLLM_TEST_PRIMA_SERVER": runtime["llama_server"]}
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "tests/test_prima_lifecycle.py"],
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            evidence["results"].append(
                {
                    "case": "controlled_lifecycle",
                    "status": "passed" if completed.returncode == 0 else "failed",
                    "output": completed.stdout[-4000:] + completed.stderr[-1000:],
                    "kind": "controlled process tests; not distributed model execution",
                }
            )
        if args.suite in ("all", "api", "vulkan"):
            for kind, model in [("dense", args.dense_model), ("moe", args.moe_model)]:
                if model is None or args.baseline_server is None:
                    evidence["results"].append(
                        {
                            "case": kind,
                            "status": "unavailable",
                            "reason": "Supply model and baseline server paths",
                        }
                    )
                else:
                    try:
                        data = model_assessment(
                            runtime, args.baseline_server, model, args.backend, args.ngl, device=args.device
                        )
                        evidence["results"].append(
                            {"case": kind, "status": observation_status(data), **data}
                        )
                        if args.backend != "cpu" and args.ngl > 0:
                            proof = data["gpu_execution_evidence"]
                            evidence["results"].append(
                                {
                                    "case": kind + "_gpu_execution",
                                    **(proof if isinstance(proof, dict) else {"status": "unavailable", "reason": "Selected-device and assigned-layer execution proof is unavailable"}),
                                }
                            )
                    except Exception as error:
                        evidence["results"].append(
                            {"case": kind, "status": "failed", "error": str(error)}
                        )
        if args.suite in ("all", "distributed"):
            evidence["results"].append(
                {
                    "case": "physical_distributed",
                    "status": "unavailable",
                    "reason": "Multi-machine rank/model execution and arbitrary placement are not implemented/qualified",
                }
            )
    except Exception as error:
        evidence["results"].append({"case": "runtime", "status": "failed", "error": str(error)})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({"receipt": str(args.output), "qualification": "open"}))
    if any(r["status"] == "failed" for r in evidence["results"]):
        return 1
    if any(r["status"] == "unavailable" for r in evidence["results"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
