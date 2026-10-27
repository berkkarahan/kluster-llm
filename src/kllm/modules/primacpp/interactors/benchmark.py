"""Acceptance of matched, repeated before/after inference measurements."""

from statistics import median

from pydantic import Field
from kllm.modules.primacpp.entities import Record


class Measurement(Record):
    ttft_ms: float = Field(gt=0)
    tokens_s: float = Field(gt=0)
    elapsed_ms: float = Field(gt=0)
    peak_memory_bytes: int = Field(gt=0)


class Workload(Record):
    identity: dict
    target_distributed: bool
    memory_budget_bytes: int = Field(gt=0)
    llama: list[Measurement] = Field(min_length=5)
    prima: list[Measurement] = Field(min_length=5)
    quality_passed: bool
    oom_failures: int = Field(ge=0)


def evaluate(workloads: list[Workload], *, scope: str = "distributed") -> dict:
    if scope not in ("local", "distributed"):
        raise ValueError("Unknown Prima performance qualification scope")
    if not workloads:
        raise ValueError("Performance gate requires measured workloads")
    if scope == "local" and any(w.target_distributed for w in workloads):
        raise ValueError("Local qualification cannot label a workload as physical distributed")
    reports = []
    for workload in workloads:
        required = {
            "model_sha256",
            "runtime_commits",
            "hardware",
            "backend",
            "context",
            "precision",
            "sampling",
            "concurrency",
            "prompt_sha256",
            "output_tokens",
        }
        if not required <= workload.identity.keys():
            raise ValueError(
                "Benchmark identity must describe the matched model, hardware and settings"
            )
        if len(workload.llama) != len(workload.prima):
            raise ValueError("Benchmark requires an equal number of paired runs")
        latency = [b.ttft_ms / a.ttft_ms for a, b in zip(workload.llama, workload.prima)]
        speed = [b.tokens_s / a.tokens_s for a, b in zip(workload.llama, workload.prima)]
        reports.append(
            {
                "identity": workload.identity,
                "pairs": len(speed),
                "ttft_ratio": median(latency),
                "throughput_ratio": median(speed),
                "ttft_ratio_range": [min(latency), max(latency)],
                "throughput_ratio_range": [min(speed), max(speed)],
                "elapsed_ms": {
                    "llama": median(v.elapsed_ms for v in workload.llama),
                    "prima": median(v.elapsed_ms for v in workload.prima),
                },
                "peak_memory_bytes": {
                    "llama": max(v.peak_memory_bytes for v in workload.llama),
                    "prima": max(v.peak_memory_bytes for v in workload.prima),
                },
                "passed": median(latency) <= 1.05
                and median(speed) >= 0.95
                and workload.quality_passed
                and workload.oom_failures == 0
                and max(v.peak_memory_bytes for v in workload.prima)
                <= workload.memory_budget_bytes,
                "target_improved": workload.target_distributed
                and (median(speed) >= 1.10 or median(latency) <= 0.90),
            }
        )
    return {
        "scope": scope,
        "passed": all(r["passed"] for r in reports)
        and (scope == "local" or any(r["target_improved"] for r in reports)),
        "workloads": reports,
    }
