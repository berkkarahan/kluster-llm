"""Private, bounded helper ownership for potentially blocking native sources."""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
from datetime import datetime
import json
import os
import queue
import sys
import threading
import time
import uuid
from concurrent.futures import Future
from importlib import import_module
from typing import Any

from kllm.usage_probe_protocol import (
    LIMIT as LIMIT,
    decode as decode,
    encode as encode,
    failure as failure,
    memory_totals as memory_totals,
    metric as metric,
    utc as utc,
)

from kllm.usage_probe_supervisor import (
    Job as Job,
    Slot as Slot,
    _lock as _lock,
    _slots as _slots,
    close_slots as _close_slots,
    helper_command as helper_command,
    helper_environment as helper_environment,
)

_node: str | None = None
_generation = uuid.uuid4().hex
_sequence = 0


def close() -> None:
    global _node, _generation, _sequence
    _close_slots()
    with _lock:
        if not _slots:
            _node, _generation, _sequence = None, uuid.uuid4().hex, 0


def probe(
    node_id: str,
    selections: list[dict[str, Any]],
    *,
    supported: bool = True,
) -> dict[str, Any]:
    global _node, _generation, _sequence
    started, deadline = utc(), time.monotonic() + 1.8
    if not isinstance(node_id, str) or not 1 <= len(node_id) <= 128:
        raise ValueError("node_id must contain 1–128 characters")
    if not isinstance(selections, list) or len(selections) > 8:
        raise ValueError("managed_processes must contain at most eight selections")
    selections = copy.deepcopy(selections)
    if selections:
        # Deferred import: runtime imports native, which imports this supervisor.
        from kllm import runtime

        registered = {p.pid: p for p in runtime.load().children}
        seen = set()
        for selection in selections:
            if not isinstance(selection, dict) or set(selection) != {
                "pid",
                "role",
                "start_token",
                "started_at",
            }:
                raise ValueError("invalid managed process selection fields")
            pid = selection["pid"]
            if (
                isinstance(pid, bool)
                or not isinstance(pid, int)
                or pid <= 0
                or pid > 0xFFFFFFFF
                or pid in seen
            ):
                raise ValueError("invalid or duplicate managed PID")
            seen.add(pid)
            if (
                selection["role"] not in ("master", "worker")
                or not isinstance(selection["start_token"], str)
                or not 1 <= len(selection["start_token"]) <= 128
                or not isinstance(selection["started_at"], str)
                or not selection["started_at"].endswith("Z")
            ):
                raise ValueError("invalid process role/start identity")
            try:
                datetime.fromisoformat(selection["started_at"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("invalid process start timestamp") from exc
            child = registered.get(pid)
            if (
                not child
                or not child.start_token
                or not child.started_at
                or selection["start_token"] != child.start_token
                or selection["started_at"] != child.started_at
            ):
                raise ValueError("process selection does not match registered start identity")
            expected_role = {"llama-server": "master", "rpc-server": "worker"}.get(child.kind)
            if selection["role"] != expected_role:
                raise ValueError("process role does not match registration")
    if not _lock.acquire(timeout=max(0, deadline - time.monotonic())):
        raise RuntimeError("probe supervisor busy")
    try:
        if _node is not None and _node != node_id:
            raise ValueError("probe supervisor already belongs to another node")
        _node = node_id
        if _sequence >= 9007199254740991:
            _generation, _sequence = uuid.uuid4().hex, 0
        _sequence += 1
        generation, sequence = _generation, _sequence
        pending: dict[str, tuple[Slot, Future[dict[str, Any]]]] = {}
        if supported:
            for source in ("host", "gpu"):
                slot = _slots.get(source)
                if slot is None:
                    slot = _slots[source] = Slot(source, helper_command, helper_environment)
                pending[source] = (
                    slot,
                    slot.request(node_id, selections if source == "host" else []),
                )
    finally:
        _lock.release()
    results = {}
    for source in ("host", "gpu"):
        if not supported:
            results[source] = failure(
                source, "native_unavailable", selections if source == "host" else (), node_id
            )
            continue
        slot, future = pending[source]
        try:
            results[source] = future.result(timeout=max(0, deadline - time.monotonic()))
        except concurrent.futures.TimeoutError:
            slot.kill()
            results[source] = failure(
                source, "timeout", selections if source == "host" else (), node_id
            )
    host = results["host"]["host"]
    result = dict(
        schema_version=1,
        node_id=node_id,
        generation=generation,
        sequence=sequence,
        snapshot_id=f"{generation}:{sequence}",
        requested_at=started,
        completed_at=utc(),
        host=host,
        devices=results["gpu"]["devices"],
        device_discovery=results["gpu"]["device_discovery"],
        processes=results["host"]["processes"],
        totals=memory_totals(host, results["gpu"]),
        collector=dict(kind="native", platform=sys.platform),
    )
    if len(encode(dict(node_id=node_id, readings=[], resources=result))) > LIMIT:
        # Pool totals can expand GPU identities across several aggregates. Fail that
        # source explicitly instead of dropping the independent host/process readings.
        failed_gpu = failure("gpu", "source_error")
        result.update(
            devices=[],
            device_discovery=failed_gpu["device_discovery"],
            totals=memory_totals(host, failed_gpu),
        )
        if len(encode(dict(node_id=node_id, readings=[], resources=result))) > LIMIT:
            raise RuntimeError("host probe output exceeded its reserved budget")
    return copy.deepcopy(result)


def helper(source: str) -> None:
    # Optional extension: an older wheel must fail this private helper explicitly.
    native_module = import_module("kllm._native")

    jobs: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def collect() -> None:
        collector = native_module._UsageCollector(source)
        while True:
            request = jobs.get()
            try:
                result = json.loads(collector.collect_json(json.dumps(request)))
                reply = {k: request[k] for k in ("version", "generation", "request_id")}
                reply["result"] = result
                budget = request["reply_budget_bytes"]
                if len(encode(reply)) > budget:

                    def strip_details(value: Any) -> None:
                        if isinstance(value, dict):
                            value.pop("detail", None)
                            for item in value.values():
                                strip_details(item)
                        elif isinstance(value, list):
                            for item in value:
                                strip_details(item)

                    strip_details(reply["result"])
                if len(encode(reply)) > budget:
                    reply["result"] = failure(
                        source, "source_error", request["managed_processes"], request["node_id"]
                    )
                    if source == "host":
                        reply["result"]["host"]["cpu"]["detail"] = (
                            "native source payload exceeds reply budget"
                        )
                if len(encode(reply)) > budget:
                    os._exit(2)
                sys.stdout.buffer.write(encode(reply))
                sys.stdout.buffer.flush()
            except Exception:
                os._exit(2)

    threading.Thread(target=collect, daemon=True).start()
    try:
        while True:
            frame = sys.stdin.buffer.readline(LIMIT + 1)
            if not frame:
                os._exit(0)  # Also terminates a blocked GIL-releasing native call on parent death.
            request = decode(frame)
            jobs.put_nowait(request)
    except Exception:
        os._exit(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("host", "gpu"), required=True)
    helper(parser.parse_args().source)
