"""Real isolated agent/run/proxy lifecycle with a temporary candidate test policy.

This never changes the committed policy or the user's home/cluster. Candidate policy
permits exercising production paths; it does not pass any qualification gate.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile
import threading
import time
from uuid import uuid4

import httpx
import psutil
import pytest  # Agent lifespan disables LAN membership threads for isolated tests.
import uvicorn

from prima_assessment import capture_development_runtime, capture_runtime, unused_port
from prima_protocol_assessment import cases as protocol_cases
from prima_auto_tool_assessment import cases as auto_tool_cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--development-server", type=Path)
    parser.add_argument("--profile", action="store_true", help="Measure the approved placement and verify normal planner cache use")
    parser.add_argument("--logprobs", action="store_true", help="Exercise native choice/stream logprobs through the authenticated gateway")
    parser.add_argument("--parallel-slots", type=int, choices=(1, 2), default=1)
    parser.add_argument("--stream-tools", action="store_true")
    parser.add_argument("--tool-history", action="store_true")
    parser.add_argument("--auto-tools", action="store_true")
    parser.add_argument("--chat-template", default="default")
    parser.add_argument("--chat-template-file", type=Path)
    parser.add_argument("--draft-model", type=Path)
    parser.add_argument("--draft-max", type=int, default=3)
    args = parser.parse_args()
    runtime = capture_runtime(args.runtime_dir)
    checks = []
    server = thread = None
    assert pytest is not None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prima isolated gateway ", dir=args.output.parent.resolve()) as temp:
        home = Path(temp)
        os.environ["KLLM_HOME"] = str(home)
        from kllm.shared.state import ClusterState
        from kllm.shared.auth import ensure_api_token
        from kllm.shared.config import save_config
        from kllm.modules.runtimes.entities import RuntimeVersion
        from kllm.modules.runtimes.interactors.registry import file_digest
        from kllm.modules.primacpp.interactors import qualification
        from kllm.modules.primacpp.interactors.devices import discover_vulkan_devices, select_vulkan_devices
        from kllm.modules.primacpp.interactors.evidence import execution_reports, verify_vulkan_execution
        if args.development_server:
            runtime = capture_development_runtime(runtime, args.development_server)
        if args.stream_tools and "streaming_named_tools_v1" not in runtime["capabilities"]["features"]:
            raise ValueError("Upgrade the native runtime for named tool streaming")
        if args.tool_history and "tool_history_text_v1" not in runtime["capabilities"]["features"]:
            raise ValueError("Upgrade the native runtime for text tool history")
        if args.auto_tools and not {"automatic_tools_v1", "streaming_automatic_tools_v1"} <= set(runtime["capabilities"]["features"]):
            raise ValueError("Upgrade the native runtime for automatic tools")
        if args.draft_model and "draft_simple_v1" not in runtime["capabilities"]["features"]:
            raise ValueError("Upgrade the native runtime for isolated draft decoding")
        runtime["runtime_env"] = {**runtime.get("runtime_env", {}), "KLLM_PRIMA_EXECUTION_EVIDENCE": "1"}
        control_port, infer_port = unused_port(), unused_port()
        join_key = secrets.token_urlsafe(24)
        state = ClusterState(role="master", node_id="isolated-" + str(uuid4()), join_key=join_key,
                             rpc_bind="127.0.0.1", control_port=control_port, infer_port=infer_port)
        state.save()
        save_config({"accel_backend": "vulkan", "device": "gpu", "gpu_ids": [0], "split_mode": "layer"})
        token = ensure_api_token()
        model = home / "models" / args.model.name
        model.parent.mkdir(exist_ok=True)
        model.hardlink_to(args.model.resolve())
        draft_model = None
        if args.draft_model:
            draft_model = model.parent / args.draft_model.name
            draft_model.hardlink_to(args.draft_model.resolve())
        from kllm.agent import runtime as host
        registry = host._runtime_registry()
        directory = registry.root / "published assessment with spaces" / "bin"
        directory.mkdir(parents=True)
        for name in runtime["artifacts"]:
            source = args.development_server if args.development_server and name == args.development_server.name else Path(runtime["artifact_dir"]) / name
            shutil.copyfile(source, directory / name)
        runtime = {**runtime, "artifact_dir": str(directory), **{name: str(directory / Path(runtime[name]).name)
            for name in ("llama_server", "llama_cli", "profile_tool")}}
        version = RuntimeVersion(engine="primacpp", repository=runtime["repository"], ref=runtime["commit"],
            ref_type="commit", commit=runtime["commit"], runtime_id=runtime["runtime_id"], recipe_digest=runtime["recipe_digest"])
        registry.add(version)
        registry.data["versions"][version.runtime_id]["build"] = runtime
        registry._save()
        registry.capture(version.runtime_id)  # Actual relocated artifact verification.
        candidate = copy.deepcopy(qualification.policy())
        record = candidate["scopes"]["windows-vulkan-local"]
        record.update(source_commit=runtime["commit"], recipe_digest=runtime["recipe_digest"],
            models=[{"sha256": file_digest(model)}], devices=runtime["capabilities"]["devices"], placements=["layer"],
            receipts=["controlled isolated candidate policy; not qualification"],
            features=["chat", "responses", "messages", "streaming", "template:default", "moe_offload", "enable_thinking",
                      "thinking_toggle", "thinking:off", "structured_output", "tools:forced"])
        if args.logprobs:
            record["features"].append("token_logprobs")
        if args.stream_tools:
            record["features"].append("streaming_tools")
        if args.tool_history:
            record["features"].append("tool_turns")
        if args.auto_tools:
            record["features"] += ["tools:auto", "streaming_tools", "parallel_tools", "thinking:on"]
        record["features"].append("template:" + args.chat_template)
        if args.chat_template_file:
            record["features"].append("chat_template_text")
        record["gates"] = {key: True for key in record["gates"]}
        # This placeholder permits library selection only; it cannot match a run.
        record["execution_scopes"] = [{"execution": {"pending": "isolated plan"},
            "features": record["features"], "receipts": ["controlled candidate; not qualification"]}]
        original_policy = qualification.policy
        qualification.policy = lambda: candidate
        headers = {"Authorization": "Bearer " + token, "X-Kllm-Key": join_key}
        configuration = {"model": model.name, "context": 4096, "cache_type_k": "f16", "cache_type_v": "f16",
            "flash_attention": "off", "parallel_slots": args.parallel_slots, "checkpoints": 0, "cache_ram": 0,
            "batch_size": 512, "micro_batch": 128, "threads_batch": 8,
            "enable_thinking": False, "settings_source": "defaults", "device": "gpu", "threads": 8}
        configuration["chat_template"] = args.chat_template
        if draft_model:
            configuration.update(draft_model=str(draft_model), speculative="draft-simple", draft_max=args.draft_max)
            record["features"] += ["draft_model", "speculative:draft-simple"]
        if args.chat_template_file:
            configuration["chat_template_text"] = args.chat_template_file.read_text(encoding="utf-8")
        def require(response, expected=200):
            if response.status_code != expected:
                raise ValueError(f"{response.request.url.path}: HTTP {response.status_code}: {response.text[:1000]}")
            return response.json()
        try:
            registry.select(version.runtime_id)
            server = uvicorn.Server(uvicorn.Config(host.app, host="127.0.0.1", port=control_port, log_level="error"))
            thread = threading.Thread(target=server.run)
            thread.start()
            with httpx.Client(base_url=f"http://127.0.0.1:{control_port}", headers=headers, timeout=300, trust_env=False) as client:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise ValueError("Isolated agent startup timed out")
                plan = require(client.post("/v2/inference/plans", json=configuration))
                if args.profile:
                    rank = plan["prima_topology"]["ranks"][0]
                    job = require(client.post("/v2/node/prima-profiles", json={
                        "runtime_id": version.runtime_id, "model": model.name, "mode": "generation",
                        "gpu_layers": rank["ngl"], "output_gpu": rank["output_gpu"], "devices": rank["devices"],
                        "context": 4096, "threads": 8, "request_id": str(uuid4())}))
                    deadline = time.monotonic() + 900
                    while job["phase"] not in ("prepared", "failed", "cancelled", "interrupted") and time.monotonic() < deadline:
                        time.sleep(0.25)
                        job = require(client.get("/v2/node/prima-profiles/" + job["job_id"]))
                    if job["phase"] != "prepared":
                        raise ValueError("Gateway generation profile did not prepare: " + str(job.get("error", job["phase"])))
                    checks.append({"case": "generation_profile", "status": "passed", "job": job})
                    plan = require(client.post("/v2/inference/plans", json=configuration))
                    if (plan["profiling"]["status"] != "profiled_model_probe"
                            or plan["profiling"]["predicted_decode_ms"] != job["profile"]["decode_ms"]):
                        raise ValueError("Normal plan did not use its exact matching generation profile")
                    checks.append({"case": "measured_plan", "status": "passed", "profiling": plan["profiling"]})
                checks.append({"case": "plan", "status": "passed", "plan": plan})
                from kllm.modules.inference.entities import RunBody
                from kllm.modules.primacpp.entities import RankAllocation
                from kllm.modules.primacpp.interactors.execution import ExecutionManifest, Asset, Segment
                topology = plan["prima_topology"]
                ranks = [RankAllocation.model_validate(rank) for rank in topology["ranks"]]
                from kllm.modules.primacpp.interactors.launch import execution_settings
                from types import SimpleNamespace
                settings = execution_settings(RunBody(**configuration), plan, SimpleNamespace(name=model.name), infer_port)
                expected_manifest = ExecutionManifest(runtime_id=runtime["runtime_id"], run_id=str(uuid4()),
                    plan_digest=plan["plan_digest"], inventory_digest=plan["inventory_digest"],
                    total_layers=topology["total_layers"], ranks=ranks,
                    assets=[Asset(name=model.name, kind="model", sha256=record["models"][0]["sha256"], size=model.stat().st_size)] +
                           ([Asset(name=draft_model.name, kind="draft", sha256=file_digest(draft_model), size=draft_model.stat().st_size)] if draft_model else []),
                    settings=settings, segments=[Segment(start=v.start, end=v.end, node_id=r.node_id)
                        for r in ranks for v in r.layers], backends={r.node_id: runtime["backend"] for r in ranks})
                selected = select_vulkan_devices(discover_vulkan_devices(runtime), ranks[0].devices)
                expected_context = qualification.manifest_context(expected_manifest, runtime, devices=selected)
                record["execution_scopes"] = [{"execution": expected_context["execution"],
                    "features": record["features"], "receipts": ["controlled candidate; not qualification"]}]
                refused = client.post("/v2/inference/runs", json={**configuration, "batch_size": 2048})
                if refused.status_code != 409 or "execution_settings_placement_not_qualified" not in refused.text:
                    raise ValueError("Normal launch did not reject an unmeasured batching scope: " + refused.text[:1000])
                checks.append({"case": "unmeasured_launch_refused", "status": "passed"})
                result = require(client.post("/v2/inference/runs", json=configuration))
                pid = result["launch"]["pid"]
                if result.get("engine") != "primacpp" or not psutil.pid_exists(pid):
                    raise ValueError("Normal run did not dispatch an owned Prima child")
                template_source = settings.get("prima_chat_template_source")
                if template_source:
                    command = psutil.Process(pid).cmdline()
                    if "--chat-template-file" not in command or "--chat-template" in command:
                        raise ValueError("Managed Jinja launch did not use its owned template file")
                    template_path = Path(command[command.index("--chat-template-file") + 1])
                    if template_path.read_text(encoding="utf-8") != template_source:
                        raise ValueError("Launched template file differs from the signed source")
                    with httpx.Client(timeout=10, trust_env=False) as observer:
                        properties = require(observer.get(f"http://127.0.0.1:{infer_port}/props"))
                    if properties.get("chat_template") != template_source or properties.get("jinja") is not True:
                        raise ValueError("Native properties did not report the effective Jinja template")
                    checks.append({"case": "owned_template_file_and_properties", "status": "passed",
                                   "source_sha256": file_digest(template_path)})
                command = psutil.Process(pid).cmdline()
                for flag, value in (("--ctx-size", "4096"), ("--threads", "8"), ("--threads-batch", "8"),
                                    ("--batch-size", "512"), ("--ubatch-size", "128"),
                                    ("--parallel", str(args.parallel_slots)),
                                    ("--cache-type-k", "f16"), ("--cache-type-v", "f16")):
                    if command.count(flag) != 1 or command[command.index(flag) + 1] != value:
                        raise ValueError("Effective native launch changed measured setting " + flag)
                checks.append({"case": "effective_launch_settings", "status": "passed", "arguments": command})
                if draft_model:
                    manifest_path = Path(command[command.index("--execution-manifest") + 1])
                    contract = json.loads(manifest_path.read_text(encoding="utf-8"))
                    draft_asset = next(asset for asset in contract["assets"] if asset["kind"] == "draft")
                    draft_path = Path(command[command.index("--model-draft") + 1])
                    if (draft_path.name != draft_asset["name"] or file_digest(draft_path) != draft_asset["sha256"]
                            or command[command.index("--gpu-layers-draft") + 1] != "0"):
                        raise ValueError("Draft launch did not use verified staged CPU assets")
                    checks.append({"case": "verified_cpu_draft_asset", "status": "passed", "asset": draft_asset})
                checks.append({"case": "run", "status": "passed", "run_id": result["run_id"]})
                from kllm.shared.state import state_path
                active = host.app.state.managed_network.state().active_config
                persisted = json.loads(state_path().read_text(encoding="utf-8"))
                if (active.get("qualification_context") != expected_context
                        or persisted["active_config"].get("qualification_context") != expected_context):
                    raise ValueError("Active run did not retain its exact qualification context")
                saved_scopes = record["execution_scopes"]
                other_scope = copy.deepcopy(saved_scopes[0])
                other_scope["execution"]["settings"]["batch_size"] = 2048
                record["execution_scopes"] = [other_scope]
                try:
                    refused = client.post("/v1/chat/completions", json={"model": model.name,
                        "messages": [{"role": "user", "content": "hello"}]})
                    if refused.status_code != 422 or "no longer qualified" not in refused.text:
                        raise ValueError("Gateway did not enforce exact-scope revocation: " + refused.text[:1000])
                finally:
                    record["execution_scopes"] = saved_scopes
                checks.append({"case": "active_scope_revocation", "status": "passed"})
                catalog = require(client.get("/v2/inference/models"))
                if model.name not in [entry["id"] for entry in catalog["data"]]:
                    raise ValueError("Served model alias is absent from catalog")
                bad = client.post("/v1/chat/completions", headers={"Authorization": "Bearer invalid", "X-Kllm-Key": join_key},
                                  json={"model": model.name, "messages": [{"role": "user", "content": "hello"}]})
                if bad.status_code != 401:
                    raise ValueError("Inference authentication did not reject a bad token")
                checks.append({"case": "authentication_and_catalog", "status": "passed"})
                body = {"model": model.name, "messages": [{"role": "user", "content": "Output exactly the lowercase word hello and no other text."}],
                        "temperature": 0, "max_tokens": 128, "chat_template_kwargs": {"enable_thinking": False}}
                chat = require(client.post("/v1/chat/completions", json=body))
                if chat["choices"][0]["message"]["content"].strip().lower() != "hello" or chat["model"] != model.name:
                    raise ValueError("Gateway chat answer or alias is incorrect")
                checks.append({"case": "chat", "status": "passed", "response": chat})
                events, text = [], []
                with client.stream("POST", "/v1/chat/completions", json={**body, "stream": True}) as response:
                    if response.status_code != 200:
                        raise ValueError("Gateway stream refused")
                    for line in response.iter_lines():
                        if line.startswith("data:"):
                            if line[5:].strip() == "[DONE]":
                                events.append("done")
                            else:
                                event = json.loads(line[5:])
                                text.append((event.get("choices") or [{}])[0].get("delta", {}).get("content", ""))
                if "done" not in events or "".join(text).strip().lower() != "hello":
                    raise ValueError("Gateway stream lost content or terminal event")
                checks.append({"case": "stream", "status": "passed"})
                with ThreadPoolExecutor(max_workers=2) as pool:
                    queued = list(pool.map(lambda _: require(client.post("/v1/chat/completions", json=body)), range(2)))
                if any(reply["choices"][0]["message"]["content"].strip().lower() != "hello" for reply in queued):
                    raise ValueError("Concurrent gateway requests lost their answers")
                checks.append({"case": "concurrent_requests", "status": "passed", "requests": 2, "slots": args.parallel_slots})
                target = " ".join(["hello"] * 1000)
                cancel_body = {**body, "max_tokens": 2048, "stream": True,
                    "response_format": {"type": "json_schema", "json_schema": {"name": "cancel", "strict": True,
                        "schema": {"type": "string", "const": target}}}}
                with httpx.Client(base_url=f"http://127.0.0.1:{infer_port}", timeout=10, trust_env=False) as observer:
                    with client.stream("POST", "/v1/chat/completions", json=cancel_body) as response:
                        if response.status_code != 200:
                            raise ValueError("Cancellation stream was refused")
                        # Retain the iterator through the observation. Discarding
                        # it on break can close httpcore's stream via GeneratorExit.
                        lines = response.iter_lines()
                        for line in lines:
                            if line.startswith("data:") and line[5:].strip() != "[DONE]":
                                event = json.loads(line[5:])
                                if (event.get("choices") or [{}])[0].get("delta", {}).get("content"):
                                    break
                        else:
                            raise ValueError("Cancellation stream did not start generating")
                        slots = require(observer.get("/slots"))
                        if not any(slot["state"] != 0 for slot in slots):
                            raise ValueError("Generation completed before disconnect; cancellation was not exercised: "
                                + json.dumps([{key: slot.get(key) for key in ("id_task", "state", "next_token", "n_predict")} for slot in slots]))
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        slots = require(observer.get("/slots"))
                        if all(slot["state"] == 0 for slot in slots):
                            break
                        time.sleep(0.05)
                    if any(slot["state"] != 0 or slot["next_token"]["n_decoded"] >= 1000 for slot in slots):
                        raise ValueError("Client disconnect did not cancel native generation promptly")
                checks.append({"case": "stream_disconnect_cancellation", "status": "passed", "slots": slots})
                protocol_results = protocol_cases(control_port, model.name, headers, include_logprobs=args.logprobs,
                                                  include_stream_tools=args.stream_tools, include_tool_history=args.tool_history)
                if args.auto_tools:
                    protocol_results += auto_tool_cases(control_port, model.name, headers, native_port=infer_port, include_logprobs=args.logprobs)
                checks.extend({"case": "gateway_" + case["case"],
                    "status": "failed" if case["semantic_errors"] else "passed", "result": case}
                    for case in protocol_results)
                prima = host.app.state.managed_network.runtimes.prima
                manifest = prima.runs.manifest
                rank = manifest.ranks[0]
                selected = select_vulkan_devices(discover_vulkan_devices(runtime), rank.devices)
                log = prima.rank.root / manifest.run_id / "rank.log"
                reports = execution_reports(log.read_text(encoding="utf-8", errors="replace"))
                args.output.with_suffix(".rank.log").write_bytes(log.read_bytes())
                checks.append({"case": "gpu_execution_reports", "status": "passed", "reports": reports})
                proof = verify_vulkan_execution(reports, manifest, rank, selected)
                checks.append({"case": "gpu_execution", "status": "passed", "proof": proof,
                    "output_gpu": rank.output_gpu, "reports": reports})
                require(client.post("/v2/inference/runs/stop"))
                if psutil.pid_exists(pid) or registry.data["running"] is not None:
                    raise ValueError("Stop left an owned inference process or running registry record")
                checks.append({"case": "stop", "status": "passed"})
                restarted = require(client.post("/v2/inference/runs", json=configuration))
                if restarted["run_id"] == result["run_id"]:
                    raise ValueError("Restart reused the previous run ownership")
                checks.append({"case": "restart", "status": "passed"})
                service = host.app.state.managed_network
                def monitor_diagnostics():
                    task = service.worker_expiry_task
                    return {"done": task.done(), "cancelled": task.cancelled(),
                        "error": str(task.exception()) if task.done() and not task.cancelled() else None,
                        "stack": [{"function": frame.f_code.co_name, "line": frame.f_lineno} for frame in task.get_stack()],
                        "apply_error": prima.service.state().apply_error,
                        "run_cancelled": prima.runs.cancelled.is_set(), "run_present": prima.runs.manifest is not None}
                child = psutil.Process(restarted["launch"]["pid"])
                child.terminate()
                child.wait(10)
                deadline = time.monotonic() + 15
                while (registry.data["running"] is not None or prima.service.state().process_pid or prima.rank.owner) and time.monotonic() < deadline:
                    time.sleep(0.1)
                if registry.data["running"] is not None or prima.service.state().process_pid or prima.rank.owner:
                    raise ValueError("Crash monitor retained state: " + json.dumps({
                        "running": registry.data["running"], "pid": prima.service.state().process_pid,
                        "owner": prima.rank.status(), "monitor": monitor_diagnostics()}))
                checks.append({"case": "crash_cleanup", "status": "passed"})
                recovered = require(client.post("/v2/inference/runs", json=configuration))
                if recovered["run_id"] in (result["run_id"], restarted["run_id"]):
                    raise ValueError("Crash recovery reused old ownership")
                require(client.post("/v2/inference/runs/stop"))
                if psutil.pid_exists(recovered["launch"]["pid"]):
                    raise ValueError("Recovered process was left running")
                checks.append({"case": "crash_restart", "status": "passed"})
        except Exception as error:
            checks.append({"case": "gateway", "status": "failed", "error": str(error)})
        finally:
            if getattr(host.app.state, "managed_network", None) is not None:
                try:
                    prima = host.app.state.managed_network.runtimes.prima
                    manifest = prima.runs.manifest
                    if manifest is not None:
                        log = prima.rank.root / manifest.run_id / "rank.log"
                        if log.is_file():
                            args.output.with_suffix(".rank.log").write_bytes(log.read_bytes())
                except Exception as error:
                    checks.append({"case": "failure_log_capture", "status": "failed", "error": str(error)})
                try:
                    host.app.state.managed_network.runtimes.prima.runs.stop()
                except Exception as error:
                    checks.append({"case": "cleanup", "status": "failed", "error": str(error)})
            if server is not None:
                server.should_exit = True
            if thread is not None:
                thread.join(30)
                if thread.is_alive():
                    checks.append({"case": "agent_shutdown", "status": "failed"})
            qualification.policy = original_policy
            result = {"checks": checks, "runtime_id": runtime["runtime_id"], "source_commit": runtime["commit"],
                "development_server": bool(args.development_server), "server_sha256": file_digest(Path(runtime["llama_server"])),
                "model_sha256": file_digest(model), "logprobs": args.logprobs,
                "launch_settings": configuration,
                "qualification": "open: isolated candidate policy; no production gate passed"}
            args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(args.output), "passed": all(c["status"] == "passed" for c in checks)}))
    return 0 if checks and all(c["status"] == "passed" for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
