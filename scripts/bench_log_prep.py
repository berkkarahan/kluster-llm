"""Measure uncached live-log result preparation with a deterministic synthetic batch."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "performance"))
sys.path.insert(0, str(ROOT / "python"))

from log_fixture import oversized_log_rows  # noqa: E402
from kllm.agent.live import LiveFeed, _encoded  # noqa: E402

BUDGET = 256 * 1024


def measure(rows: list[dict]) -> tuple[float, dict]:
    feed = LiveFeed({}, lambda _after: {
        "lines": rows,
        "cursor": "00000000-0000-4000-8000-000000000001:500",
        "reset": None,
        "gap": False,
        "gap_reason": None,
        "source": "cluster",
    })
    try:
        began = time.perf_counter()
        result = feed._log_result(None, None)
        elapsed = time.perf_counter() - began
        snapshot = result["snapshot"]
        assert result["availability"] == "available"
        assert len(_encoded(result)) <= BUDGET
        assert snapshot["lines"] == rows[-len(snapshot["lines"]):]
        assert snapshot["gap"] and snapshot["gap_reason"] == "batch_limit"
        return elapsed, result
    finally:
        feed.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batches", type=int, default=5)
    parser.add_argument("--per-batch", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.batches < 1 or args.per_batch < 1:
        parser.error("batches and per-batch must be positive")

    rows = oversized_log_rows()
    measure(rows)  # warm the interpreter outside the measured batches
    samples: list[float] = []
    batch_medians: list[float] = []
    retained = None
    result_bytes = None
    for batch in range(args.batches):
        current = []
        for _ in range(args.per_batch):
            elapsed, result = measure(rows)
            current.append(elapsed)
            samples.append(elapsed)
            retained = len(result["snapshot"]["lines"])
            result_bytes = len(_encoded(result))
        batch_medians.append(statistics.median(current))
        print(json.dumps({"batch": batch + 1, "median_seconds": batch_medians[-1],
                          "samples": current}), flush=True)
    ordered = sorted(samples)
    summary = {
        "workload": {"events": len(rows), "message_chars": 1024, "batches": args.batches,
                     "calls_per_batch": args.per_batch},
        "median_seconds": statistics.median(samples),
        "p95_seconds": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "batch_medians_seconds": batch_medians,
        "retained_events": retained,
        "complete_result_bytes": result_bytes,
    }
    output = json.dumps(summary, indent=2)
    print(output)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
