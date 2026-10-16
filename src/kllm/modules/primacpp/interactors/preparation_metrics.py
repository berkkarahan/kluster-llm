"""Bounded, process-local preparation diagnostics; never execution identity.

Context propagation is confined to an owned synchronous operation. No diagnostic
fields are added to signed requests, manifests, or profile cache records.
"""

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import os
import re
import time
from functools import wraps


LOCAL_CHUNK_ENV = "KLLM_PRIMA_LOCAL_CHUNK_MIB"
_active = ContextVar("prima_preparation_metrics", default=None)


def local_chunk_bytes():
    value = os.environ.get(LOCAL_CHUNK_ENV)
    if value is None:
        return 4 * 1024 * 1024
    if not re.fullmatch(r"[0-9]{1,2}", value) or not 1 <= int(value) <= 64:
        raise ValueError(
            f"{LOCAL_CHUNK_ENV} must be an integer from 1 through 64 MiB; "
            "unset it for the 4 MiB default and set it before starting the owning agent"
        )
    return int(value) * 1024 * 1024


class Diagnostics:
    def __init__(self, job, lock, check, node):
        self.lock, self.check, self.node = lock, check, node
        self.data = job.setdefault("diagnostics", {
            "version": 1, "clock": "process monotonic seconds", "stages": [],
            "omitted_stage_calls": 0,
        })
        self.records = {(r["stage"], r["node"], r["asset"]): r for r in self.data["stages"]}

    def record(self, stage, node, asset):
        with self.lock:
            key = (stage, node or self.node, asset)
            record = self.records.get(key)
            if record is None:
                if len(self.records) >= 128:
                    self.data["omitted_stage_calls"] += 1
                    return None
                record = dict(stage=stage, node=key[1], asset=asset, calls=0,
                              started_monotonic=time.monotonic(), ended_monotonic=None,
                              elapsed_seconds=0.0, timed_calls=0,
                              latency_buckets=[0] * 6, counters={})
                self.records[key] = record
                self.data["stages"].append(record)
            record["calls"] += 1
            return record


@contextmanager
def diagnostics_scope(job, lock, check, node):
    token = _active.set(Diagnostics(job, lock, check, node))
    try:
        yield
    finally:
        _active.reset(token)


def check_owned():
    active = _active.get()
    if active is not None:
        active.check()


def measured(stage):
    def decorate(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            with measure(stage):
                return function(*args, **kwargs)
        return invoke
    return decorate


def wire_status(job):
    # Diagnostics can exceed the legacy frame budget and have no negotiated wire
    # schema yet. They remain available on the owning node's HTTP job status.
    return {key: value for key, value in job.items() if key != "diagnostics"}


def channel_timing(node, sample, asset=""):
    """Inject local timing across the synchronous admission/async channel gateway."""
    observer = _active.get()
    if observer is None:
        return None
    @contextmanager
    def timing(stage):
        token = _active.set(observer)
        try:
            with measure(stage, node=node, asset=asset, sample=sample):
                yield
        finally:
            _active.reset(token)
    return timing


@contextmanager
def measure(stage, *, asset="", node="", sample=1, started_at=None):
    """Exact counters; hot-call latency samples at 1/N, explicitly labelled.

    elapsed_seconds is the sum of timed calls, not an extrapolated duration.
    Buckets: <=1ms, <=10ms, <=100ms, <=1s, <=10s, >10s.
    """
    active = _active.get()
    record = active.record(stage, node, asset) if active is not None else None
    counters = {}
    timed = record is not None and (record["calls"] - 1) % sample == 0
    started = (time.monotonic() if started_at is None else started_at) if timed else None
    if record is not None and started_at is not None:
        record["started_monotonic"] = min(record["started_monotonic"], started_at)
    try:
        yield counters
    except BaseException:
        counters["failed_calls"] = counters.get("failed_calls", 0) + 1
        raise
    finally:
        if record is not None:
            with active.lock:
                for key, value in counters.items():
                    record["counters"][key] = record["counters"].get(key, 0) + value
                record["sample_every"] = sample
                if timed:
                    ended = time.monotonic()
                    elapsed = max(0.0, ended - started)
                    record["ended_monotonic"] = ended
                    record["elapsed_seconds"] += elapsed
                    record["timed_calls"] += 1
                    bucket = next((i for i, bound in enumerate((.001, .01, .1, 1, 10)) if elapsed <= bound), 5)
                    record["latency_buckets"][bucket] += 1


def digest_file(path, *, check=None, progress=None, stage="source_hash", asset=None):
    """Full destination/source read-back with bounded cancellation checkpoints."""
    check = check or check_owned
    check()
    sha = hashlib.sha256()
    done = 0
    size = path.stat().st_size
    with measure(stage, asset=asset or path.name) as counts:
        if progress:
            progress(0, size)
        with path.open("rb") as source:
            while True:
                check()
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                sha.update(chunk)
                done += len(chunk)
                counts["read_bytes"] = done
                counts["hashed_bytes"] = done
                if progress:
                    progress(done, size)
        check()
    return sha.hexdigest()


class ProgressWindow:
    """At most four regular publications/second and 40 samples per job."""
    def __init__(self):
        self.samples = deque(maxlen=40)
        self.key = None
        self.last_done = 0
        self.published_at = None

    def update(self, key, done, total, now, *, force=False):
        reset = key != self.key or done < self.last_done
        if reset:
            self.samples.clear()
            self.key = key
        self.last_done = done
        if not (force or reset or self.published_at is None or now - self.published_at >= .25):
            return None
        self.published_at = now
        while self.samples and now - self.samples[0][0] > 8:
            self.samples.popleft()
        self.samples.append((now, done))
        rate = eta = None
        if total > 0 and len(self.samples) > 1:
            dt = now - self.samples[0][0]
            db = done - self.samples[0][1]
            if dt >= .05:
                rate = max(0.0, db / dt)
                if rate > 0 and done < total:
                    eta = (total - done) / rate
        return rate, eta
