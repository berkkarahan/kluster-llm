"""Owned metadata-only inspection by the exact prepared inference runtime."""

import json
import os
from pathlib import Path
import subprocess
import time
from kllm.modules.primacpp.interactors.preparation_metrics import measured


FEATURE = "model_structure_v1"
MARKER = "KLLM_PRIMA_MODEL_STRUCTURE="
OUTPUT_LIMIT = 1024 * 1024


def require_model_inspection(runtime):
    if FEATURE not in runtime.get("capabilities", {}).get("features", []):
        raise ValueError("Generation profiling requires model_structure_v1; prepare the current managed Prima runtime and retry")


def log_tail(path: Path, limit=4096):
    try:
        with path.open("rb") as source:
            source.seek(max(0, path.stat().st_size - limit))
            return source.read(limit).decode("utf-8", errors="replace").strip()
    except OSError:
        return "native log unavailable"


def process_failure(phase, process, path, reason):
    # Put the final diagnostic first so bounded job summaries retain the cause.
    lines = log_tail(path).splitlines()
    diagnostic = " | ".join(lines[-3:])[-700:]
    return ValueError(f"{phase}: {reason} (exit={process.poll()}): {diagnostic}")


@measured("model_inspection")
def inspect_model(runtime, model, directory, *, check, track, stop):
    require_model_inspection(runtime)
    check()
    path = directory / "inspection.log"
    with path.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [runtime["llama_server"], "--inspect-model", "--model", str(model)],
            env={**os.environ, **runtime.get("runtime_env", {})},
            stdout=output, stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
        phase = f"Model inspection ({runtime.get('backend', 'metadata')})"
        try:
            track(process)
            deadline = time.monotonic() + 60
            while process.poll() is None:
                check()
                if time.monotonic() >= deadline or path.stat().st_size > OUTPUT_LIMIT:
                    raise ValueError("time/output limit exceeded")
                time.sleep(0.05)
            check()
            if process.returncode:
                raise ValueError("native inspection failed")
            if path.stat().st_size > OUTPUT_LIMIT:
                raise ValueError("Model inspection exceeded its output limit")
            reports = [json.loads(line[len(MARKER):]) for line in path.read_text(errors="replace").splitlines()
                       if line.startswith(MARKER)]
            if len(reports) != 1:
                raise ValueError("Model inspection requires exactly one structure report")
            report = reports[0]
            if (not isinstance(report, dict) or type(report.get("schema_version")) is not int
                    or report["schema_version"] != 1
                    or not isinstance(report.get("architecture"), str) or not report["architecture"]
                    or any(type(report.get(k)) is not int or report[k] <= 0
                           for k in ("stored_blocks", "execution_layers"))
                    or report["execution_layers"] > report["stored_blocks"]):
                raise ValueError("Invalid native model structure report")
            return report
        except (ValueError, OSError) as error:
            raise process_failure(phase, process, path, str(error)) from error
        finally:
            if process.poll() is None:
                stop(process)
                process.wait(timeout=10)
