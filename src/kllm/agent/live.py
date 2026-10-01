"""Bounded dashboard snapshot cache shared by HTTP and WebSocket viewers."""

import math
import threading
import time
import uuid
from concurrent.futures import (
    CancelledError,
    Future,
    ThreadPoolExecutor,
    TimeoutError as FutureTimeout,
)
from typing import Any, Callable

from kllm.agent.live_payloads import (
    SourceDenied,
    encoded as _encoded,
    fit_log_suffix as _fit_log_suffix,
    safe_value as _safe_value,
    server_time,
)


TOPICS = frozenset({"status", "usage", "load", "download"})
_AREA_LIMIT = 1024 * 1024
_LOG_LIMIT = 256 * 1024


class LiveFeed:
    """A short-lived, single-in-flight cache over the agent's existing read paths."""

    def __init__(
        self,
        sources: dict[str, tuple[Callable[[], Any], str | Callable[[], str]]],
        logs: Callable[[str | None], Any],
        upstream_success: Callable[[str], str | None] | None = None,
        scope: Callable[[], Any] | None = None,
        wait_budget: float = 2.0,
    ) -> None:
        self.epoch = str(uuid.uuid4())
        self._wait_budget = float(wait_budget)
        if not math.isfinite(self._wait_budget) or self._wait_budget < 0:
            raise ValueError("wait_budget must be a finite nonnegative number")
        self._sources = sources
        self._logs = logs
        self._upstream_success = upstream_success or (lambda _topic: None)
        self._scope_source = scope or (lambda: None)
        self._scope = self._scope_source()
        self._lock = threading.Lock()
        self._areas: dict[str, tuple[float, dict[str, Any], bytes]] = {}
        self._area_flights: dict[str, threading.Event] = {}
        self._futures: dict[str, Future[dict[str, Any]]] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=len(sources) + 1, thread_name_prefix="kllm-live"
        )
        self._viewers: dict[str, set[str]] = {}
        self._leases: dict[str, float] = {}
        self._stop = threading.Event()
        self._scheduler: threading.Thread | None = None
        self._log_cache: dict[
            tuple[str | None, str | None], tuple[float, dict[str, Any], bytes]
        ] = {}
        self._log_flights: dict[tuple[str | None, str | None], threading.Event] = {}
        self._log_futures: dict[tuple[str | None, str | None], Future[dict[str, Any]]] = {}

    def start(self) -> None:
        if self._scheduler is not None:
            return
        self._scheduler = threading.Thread(
            target=self._schedule_loop, name="kllm-live-sources", daemon=True
        )
        self._scheduler.start()

    def stop(self) -> None:
        self._stop.set()
        if self._scheduler is not None:
            self._scheduler.join(timeout=1)
            self._scheduler = None
        self._executor.shutdown(wait=False, cancel_futures=True)

    def subscribe(self, viewer_id: str, topics: list[str]) -> None:
        with self._lock:
            self._viewers[viewer_id] = set(topics)

    def unsubscribe(self, viewer_id: str) -> None:
        with self._lock:
            self._viewers.pop(viewer_id, None)

    def _schedule_loop(self) -> None:
        while not self._stop.wait(0.1):
            self._refresh_scope()
            with self._lock:
                now = time.monotonic()
                self._leases = {
                    topic: expiry for topic, expiry in self._leases.items() if expiry > now
                }
                topics = set(self._leases)
                for subscribed in self._viewers.values():
                    topics.update(subscribed)
            for topic in topics:
                self._schedule_area(topic)

    def _refresh_scope(self) -> None:
        current = self._scope_source()
        with self._lock:
            if current != self._scope:
                self._scope = current
                self.epoch = str(uuid.uuid4())
                self._areas.clear()
                self._log_cache.clear()

    def current_epoch(self) -> str:
        self._refresh_scope()
        return self.epoch

    def _schedule_area(self, topic: str) -> Future[dict[str, Any]] | None:
        with self._lock:
            cached = self._areas.get(topic)
            age_limit = (
                0.5
                if topic == "download"
                else 30
                if cached and cached[1]["availability"] == "denied"
                else 1
            )
            if cached and time.monotonic() - cached[0] < age_limit:
                return None
            flight = self._futures.get(topic)
            if flight is not None and not flight.done():
                return flight
            future = self._executor.submit(self._area, topic)
            self._futures[topic] = future
            return future

    def _cached_record(
        self, topic: str, cached: tuple[float, dict[str, Any], bytes], now: float
    ) -> dict[str, Any]:
        record = cached[1]
        if topic != "usage" or record["availability"] != "available":
            return record
        record = dict(record)
        data = dict(record["data"])
        residence = max(0.0, now - cached[0])
        nodes = {}
        for node_id, raw in (data.get("nodes") or {}).items():
            node = dict(raw)
            age = node.get("age_seconds")
            if isinstance(age, (int, float)) and age >= 0:
                node["age_seconds"] = round(age + residence, 1)
                if node["age_seconds"] >= 10 and node.get("reading") is not None:
                    node["freshness"] = "stale"
            resource_age = node.get("resource_age_seconds")
            if isinstance(resource_age, (int, float)) and resource_age >= 0:
                node["resource_age_seconds"] = round(resource_age + residence, 1)
                if node["resource_age_seconds"] >= 10 and node.get("resources") is not None:
                    node["resource_freshness"] = "stale"
            nodes[node_id] = node
        data["nodes"] = nodes
        record["data"] = data
        return record

    def _loading_area(self, topic: str) -> dict[str, Any]:
        with self._lock:
            cached = self._areas.get(topic)
        previous = cached[1] if cached else {}
        return {
            "revision": previous.get("revision", 0),
            "sampled_at": previous.get("sampled_at"),
            "availability": "loading",
            "coverage": previous.get("coverage", "local"),
            "upstream_last_success_at": previous.get("upstream_last_success_at"),
            "data": None,
            "error": None,
        }

    def _area(self, topic: str) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            cached = self._areas.get(topic)
            age_limit = (
                0.5
                if topic == "download"
                else 30
                if cached and cached[1]["availability"] == "denied"
                else 1
            )
            if cached is not None and now - cached[0] < age_limit:
                return self._cached_record(topic, cached, now)
            flight = self._area_flights.get(topic)
            owner = flight is None
            if owner:
                flight = threading.Event()
                self._area_flights[topic] = flight
            previous = cached[1] if cached else None
            previous_bytes = cached[2] if cached else None
            scope = self._scope

        if not owner:
            # Wait outside the shared lock; a stalled source cannot hold up another area.
            if flight is not None and flight.wait(self._wait_budget):
                with self._lock:
                    latest = self._areas.get(topic)
                    if latest is not None:
                        return latest[1]
            return {
                "revision": (previous or {}).get("revision", 0),
                "sampled_at": None,
                "availability": "loading",
                "coverage": (previous or {}).get("coverage", "local"),
                "upstream_last_success_at": None,
                "data": None,
                "error": None,
            }

        source, coverage_source = self._sources[topic]
        coverage = (previous or {}).get("coverage", "local")
        try:
            coverage = coverage_source() if callable(coverage_source) else coverage_source
            if coverage not in {"local", "cluster", "local_with_cluster_view"}:
                raise ValueError("invalid source coverage")
            data = _safe_value(source())
            payload = _encoded((data, coverage))
            if len(payload) > _AREA_LIMIT:
                raise OverflowError
            record = {
                "revision": (previous or {}).get("revision", 0) + (payload != previous_bytes),
                "sampled_at": server_time(),
                "availability": "available",
                "coverage": coverage,
                "upstream_last_success_at": self._upstream_success(topic),
                "data": data,
                "error": None,
            }
        except OverflowError:
            record = self._error_area(
                previous, coverage, "payload_too_large", "Source payload is too large"
            )
            payload = _encoded(record["data"])
        except (TypeError, ValueError):
            record = self._error_area(
                previous, coverage, "invalid_source_data", "Source returned invalid data"
            )
            payload = _encoded(record["data"])
        except SourceDenied:
            record = self._error_area(
                previous, coverage, "forbidden", "Source access denied", "denied"
            )
            payload = _encoded(record["data"])
        except Exception:
            record = self._error_area(
                previous, coverage, "source_unavailable", "Source is unavailable"
            )
            payload = _encoded(record["data"])
        finally:
            with self._lock:
                if scope == self._scope and not self._stop.is_set():
                    self._areas[topic] = (time.monotonic(), record, payload)
                else:
                    record = self._loading_area_unlocked(topic)
                self._area_flights.pop(topic, None)
                if flight is not None:
                    flight.set()
        return record

    def _loading_area_unlocked(self, topic: str) -> dict[str, Any]:
        cached = self._areas.get(topic)
        previous = cached[1] if cached else {}
        return {
            "revision": previous.get("revision", 0),
            "sampled_at": previous.get("sampled_at"),
            "availability": "loading",
            "coverage": previous.get("coverage", "local"),
            "upstream_last_success_at": previous.get("upstream_last_success_at"),
            "data": None,
            "error": None,
        }

    def _schedule_log(
        self, after: str | None, continuity_id: str | None
    ) -> Future[dict[str, Any]] | None:
        key = (after, continuity_id)
        with self._lock:
            cached = self._log_cache.get(key)
            if cached is not None and time.monotonic() - cached[0] < 0.5:
                return None
            self._log_futures = {
                item: pending for item, pending in self._log_futures.items() if not pending.done()
            }
            future = self._log_futures.get(key)
            if future is not None:
                return future
            if len(self._log_futures) >= 8:
                full: Future[dict[str, Any]] = Future()
                full.set_result(
                    {
                        "availability": "unavailable",
                        "sampled_at": None,
                        "snapshot": None,
                        "error": {"code": "slow_consumer", "message": "Log source is busy"},
                    }
                )
                return full
            future = self._executor.submit(self._log_result, after, continuity_id)
            self._log_futures[key] = future
            return future

    @staticmethod
    def _error_area(
        previous: dict[str, Any] | None,
        coverage: str,
        code: str,
        message: str,
        availability: str = "unavailable",
    ) -> dict[str, Any]:
        previous_error = (previous or {}).get("error") or {}
        changed = (previous or {}).get("availability") != availability or previous_error.get(
            "code"
        ) != code
        return {
            "revision": (previous or {}).get("revision", 0) + changed,
            "sampled_at": (previous or {}).get("sampled_at"),
            "availability": availability,
            "coverage": coverage,
            "upstream_last_success_at": (previous or {}).get("upstream_last_success_at"),
            "data": None,
            "error": {"code": code, "message": message},
        }

    def _log_result(self, after: str | None, continuity_id: str | None) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            scope = self._scope
            cache_key = (after, continuity_id)
            cached = self._log_cache.get(cache_key)
            if cached is not None and now - cached[0] < 0.5:
                return cached[1]
            flight = self._log_flights.get(cache_key)
            owner = flight is None
            if owner:
                flight = threading.Event()
                self._log_flights[cache_key] = flight

        if not owner:
            if flight is not None and flight.wait(self._wait_budget):
                with self._lock:
                    cached = self._log_cache.get(cache_key)
                    if cached is not None:
                        return cached[1]
            return {"availability": "loading", "sampled_at": None, "snapshot": None, "error": None}

        failure: ValueError | None = None
        try:
            snapshot = _safe_value(self._logs(after))
            snapshot.setdefault("continuity_id", self.epoch)
            snapshot.setdefault("discontinuity", None)
            if continuity_id is not None and continuity_id != snapshot["continuity_id"]:
                snapshot["reset"] = snapshot.get("reset") or "origin_changed"
                snapshot["gap"] = True
                snapshot["gap_reason"] = snapshot.get("gap_reason") or "feed_reset"
                snapshot["discontinuity"] = {
                    "id": snapshot["continuity_id"],
                    "reason": "coverage_change",
                    "detected_at": server_time(),
                }
            result_prefix = {
                "availability": "available",
                "sampled_at": server_time(),
                "error": None,
            }
            _encoded({**result_prefix, "snapshot": snapshot})
            all_lines = list(snapshot.get("lines") or [])
            lines = all_lines[-500:]
            snapshot["lines"] = lines
            if len(all_lines) > len(lines):
                snapshot["gap"] = True
                snapshot["gap_reason"] = snapshot.get("gap_reason") or "batch_limit"
            _fit_log_suffix(snapshot, _LOG_LIMIT, result_prefix)
            result = {**result_prefix, "snapshot": snapshot}
        except ValueError as exc:
            failure = exc
            result = {
                "availability": "unavailable",
                "sampled_at": None,
                "snapshot": None,
                "error": {
                    "code": "invalid_source_data",
                    "message": "Log source returned invalid data",
                },
            }
        except OverflowError:
            result = {
                "availability": "unavailable",
                "sampled_at": None,
                "snapshot": None,
                "error": {"code": "payload_too_large", "message": "Log payload is too large"},
            }
        except SourceDenied:
            result = {
                "availability": "denied",
                "sampled_at": None,
                "snapshot": None,
                "error": {"code": "forbidden", "message": "Log access denied"},
            }
        except Exception:
            result = {
                "availability": "unavailable",
                "sampled_at": None,
                "snapshot": None,
                "error": {"code": "source_unavailable", "message": "Logs are unavailable"},
            }
        with self._lock:
            if failure is None and scope == self._scope and not self._stop.is_set():
                self._log_cache[cache_key] = (time.monotonic(), result, _encoded(result))
            elif scope != self._scope or self._stop.is_set():
                result = {
                    "availability": "loading",
                    "sampled_at": None,
                    "snapshot": None,
                    "error": None,
                }
            self._log_flights.pop(cache_key, None)
            if flight is not None:
                flight.set()
            while len(self._log_cache) > 8:
                self._log_cache.pop(next(iter(self._log_cache)))
        if failure is not None:
            raise failure
        return result

    def snapshot(
        self,
        topics: list[str],
        *,
        logs: bool = False,
        after: str | None = None,
        continuity_id: str | None = None,
        lease: bool = True,
    ) -> dict[str, Any]:
        self._refresh_scope()
        if lease:
            with self._lock:
                self._leases.update({topic: time.monotonic() + 5 for topic in topics})
        flights = {topic: self._schedule_area(topic) for topic in topics}
        log_future = self._schedule_log(after, continuity_id) if logs else None
        deadline = time.monotonic() + self._wait_budget
        areas = {}
        for topic, future in flights.items():
            if future is not None:
                try:
                    future.result(timeout=max(0, deadline - time.monotonic()))
                except (FutureTimeout, CancelledError):
                    areas[topic] = self._loading_area(topic)
                    continue
            with self._lock:
                cached = self._areas.get(topic)
                areas[topic] = (
                    self._cached_record(topic, cached, time.monotonic())
                    if cached
                    else self._loading_area_unlocked(topic)
                )
        log_result = None
        if logs:
            if log_future is not None:
                try:
                    log_result = log_future.result(timeout=max(0, deadline - time.monotonic()))
                except (FutureTimeout, CancelledError):
                    log_result = {
                        "availability": "loading",
                        "sampled_at": None,
                        "snapshot": None,
                        "error": None,
                    }
            else:
                with self._lock:
                    log_result = self._log_cache[(after, continuity_id)][1]
        result = {
            "version": 1,
            "transport": "websocket",
            "epoch": self.epoch,
            "server_time": server_time(),
            "areas": areas,
            "logs": log_result,
        }
        if len(_encoded(result)) > 5 * 1024 * 1024:
            raise OverflowError("dashboard snapshot is too large")
        return result
