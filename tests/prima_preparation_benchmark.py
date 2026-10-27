"""Small CPU-only matched progress experiment; no model, disk or network benchmark.

uv run python tests/prima_preparation_benchmark.py

Replays a virtual 256 MiB transfer at 16 MiB/s, alternating three matched
baseline/candidate pairs. Buffer changes here change callback counts only;
physical read/write/hash throughput and instrumentation overhead need the later
approved hardware window. No agent, model file or KLLM_HOME is accessed.
"""

import json
import platform
from statistics import median
import threading
import time
from unittest.mock import patch

from kllm.modules.primacpp.interactors.control import PrimaControl


TOTAL = 256 * 1024 * 1024
RATE = 16 * 1024 * 1024


def replay(chunk_bytes, bounded):
    control = PrimaControl.__new__(PrimaControl)
    control.jobs = {"fixture": {"phase": "preparing"}}
    control.lock = threading.RLock()
    control.cancelled = set()
    control._job_samples = {}
    samples = []
    peak = publications = callbacks = 0
    clock = [0.0]
    start = time.perf_counter()
    with patch("kllm.modules.primacpp.interactors.control.time.monotonic", lambda: clock[0]):
        for done in range(0, TOTAL + chunk_bytes, chunk_bytes):
            done = min(done, TOTAL)
            clock[0] = done / RATE
            callbacks += 1
            if bounded:
                control._update_job_progress("fixture", phase="staging", label="copy",
                    fraction=done / TOTAL, bytes_done=done, bytes_total=TOTAL, rate_kind="local_copy")
                peak = max(peak, len(control._job_samples["fixture"].samples))
            else:
                # Original append/full-filter estimator and publication work.
                rate = eta = None
                if done > 0:
                    samples.append((clock[0], done))
                    samples[:] = [(t, b) for t, b in samples if clock[0] - t <= 8]
                    if len(samples) >= 2:
                        dt, db = samples[-1][0] - samples[0][0], samples[-1][1] - samples[0][1]
                        if dt >= .05 and db > 0:
                            rate = round(db / dt, 1)
                            if done < TOTAL:
                                eta = round((TOTAL - done) / (db / dt), 1)
                control.jobs["fixture"]["progress"] = {
                    "phase": "staging", "label": "copy", "step_index": 1, "step_total": 1,
                    "fraction": round(done / TOTAL, 4), "pct": round(done / TOTAL * 100, 1),
                    "bytes_done": done, "bytes_total": TOTAL, "rate_bps": rate,
                    "eta_seconds": eta, "updated_at": time.time(),
                }
                publications += 1
                peak = max(peak, len(samples))
    elapsed = time.perf_counter() - start
    if bounded:
        publications = control.jobs["fixture"]["progress_statistics"]["publications"]
    return dict(seconds=elapsed, callbacks=callbacks, publications=publications, peak_samples=peak)


def main():
    variants = {"baseline_24k": (24 * 1024, False), "progress_only_24k": (24 * 1024, True),
                "buffer_only_4m": (4 * 1024 * 1024, False), "combined_4m": (4 * 1024 * 1024, True)}
    results = {name: [] for name in variants}
    for pair in range(3):
        order = list(variants) if pair % 2 == 0 else list(reversed(variants))
        for name in order:
            results[name].append(replay(*variants[name]))
    print(json.dumps({"kind": "CPU-only synthetic callback replay; no physical throughput claim",
        "platform": platform.platform(), "python": platform.python_version(),
        "virtual_bytes": TOTAL, "virtual_bytes_per_second": RATE, "runs": results,
        "median_seconds": {name: median(r["seconds"] for r in runs) for name, runs in results.items()}}, indent=2))


if __name__ == "__main__":
    main()
