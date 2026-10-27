"""Run the real isolated model-backed ProfileJobs path; never selects an engine."""

import argparse
import json
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

import psutil

from prima_assessment import capture_runtime
from kllm.modules.primacpp.entities import ProfileRequest
from kllm.modules.primacpp.interactors.preparation import ProfileJobs, TERMINAL
from kllm.modules.runtimes.interactors.registry import RuntimeRegistry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--gpu-layers", type=int, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--output-gpu", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cancel-vulkan", action="store_true", help="Cancel after the actual Vulkan rank acknowledges readiness")
    args = parser.parse_args()
    runtime = capture_runtime(args.runtime_dir)
    state = SimpleNamespace(node_id="profile", process_pid=None, members={"profile": {
        "backend": "vulkan", "total_ram_bytes": psutil.virtual_memory().total}})
    root = args.output.parent / ("prima-generation-profile-" + str(uuid4()))
    controller = ProfileJobs(root, lambda _: runtime, lambda _: args.model.resolve(), lambda: state, RuntimeRegistry._kill)
    request = ProfileRequest(runtime_id=runtime["runtime_id"], model=args.model.name, mode="generation",
        gpu_layers=args.gpu_layers, devices=[args.device], output_gpu=args.output_gpu, context=4096, threads=8, request_id=str(uuid4()))
    try:
        job = controller.start(request)
        deadline = time.monotonic() + 900
        cancelled = False
        while job["phase"] not in TERMINAL and time.monotonic() < deadline:
            if args.cancel_vulkan:
                log = root / job["job_id"] / "vulkan" / "profile.log"
                if log.exists() and "KLLM_PRIMA_RANK_CONFIGURED=" in log.read_text(errors="replace"):
                    controller.cancel(job["job_id"])
                    cancelled = True
            time.sleep(0.01 if args.cancel_vulkan else 0.25)
            job = controller.job(job["job_id"])
        if job["phase"] not in TERMINAL:
            controller.cancel(job["job_id"])
            job = controller.job(job["job_id"])
        controller.close()
        child_stopped = not psutil.pid_exists(job.get("pid", 0))
        passed = (cancelled and job["phase"] == "cancelled" and child_stopped) if args.cancel_vulkan else job["phase"] == "prepared"
        args.output.write_text(json.dumps({"job": job, "runtime": runtime, "logs_directory": str(root),
            "cancelled_after_vulkan_acknowledgement": cancelled, "child_stopped": child_stopped, "passed": passed,
            "qualification": "open: model-backed profile observation, not performance acceptance"}, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"receipt": str(args.output), "phase": job["phase"], "error": job.get("error")}))
        return 0 if passed else 1
    finally:
        controller.close()


if __name__ == "__main__":
    raise SystemExit(main())
