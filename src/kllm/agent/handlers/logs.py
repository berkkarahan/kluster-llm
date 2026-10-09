"""HTTP handlers for cluster and worker log access."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import iterate_in_threadpool

from kllm import cluster_log
from kllm.agent.schemas import LogsIngestBody
from kllm.state import ClusterState


class LogHandlers:
    """Serve log snapshots and streams using injected cluster state and log hub."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        hub: cluster_log.LogHub,
        master_control_base: Callable[[], str],
        http_client: Callable[..., httpx.Client] | None = None,
    ) -> None:
        self._state = state
        self._hub = hub
        self._master_control_base = master_control_base
        self._http_client = http_client or httpx.Client

    def proxy_master(
        self,
        n: int,
        follow: bool,
        node: str | None,
        source: str | None,
        after: str | None = None,
    ) -> dict[str, Any] | StreamingResponse | None:
        """Fetch a worker log request from its master without hiding HTTP errors."""
        state = self._state()
        params: dict[str, Any] = {"n": n, "follow": follow}
        if node:
            params["node"] = node
        if source:
            params["source"] = source
        if after:
            params["after"] = after
        headers = {"X-Kllm-Key": state.join_key} if state.join_key else {}
        url = f"{self._master_control_base()}/v2/logs"
        if follow:
            client = self._http_client(timeout=None)
            try:
                response = client.send(
                    client.build_request("GET", url, params=params, headers=headers),
                    stream=True,
                )
            except Exception:
                client.close()
                return None
            if response.status_code != 200:
                status = response.status_code
                response.close()
                client.close()
                raise HTTPException(status, f"master log request returned HTTP {status}")

            def stream_chunks() -> Iterator[bytes]:
                try:
                    yield from response.iter_bytes()
                finally:
                    response.close()
                    client.close()

            return StreamingResponse(
                iterate_in_threadpool(stream_chunks()), media_type="text/event-stream"
            )

        try:
            with self._http_client(timeout=2.0) as client:
                response = client.get(url, params=params, headers=headers)
        except Exception:
            return None
        if response.status_code != 200:
            raise HTTPException(
                response.status_code,
                f"master log request returned HTTP {response.status_code}",
            )
        try:
            data = response.json()
        except Exception:
            raise HTTPException(502, "master returned an invalid log response") from None
        if not isinstance(data, dict):
            raise HTTPException(502, "master returned an invalid log response")
        return data

    def snapshot(
        self, n: int, node: str | None, source: str | None, after: str | None = None
    ) -> dict[str, Any]:
        state = self._state()
        origin = "cluster" if state.role == "master" else "local"
        local_node = state.node_id if state.role == "worker" and not node else node
        try:
            result = self._hub.snapshot(n, local_node, source, after)
        except cluster_log.CursorError as exc:
            raise HTTPException(400, f"invalid log cursor: {exc}") from exc
        result["source"] = origin
        return result

    def ensure_journal(self) -> None:
        try:
            self._hub.ensure_journal_loaded()
        except OSError as exc:
            raise HTTPException(
                503, "cluster log journal is temporarily unavailable; retry"
            ) from exc

    def stream(
        self, n: int, follow: bool, node: str | None, source: str | None
    ) -> StreamingResponse:
        state = self._state()
        local_node = state.node_id if state.role == "worker" and not node else node
        return StreamingResponse(
            iterate_in_threadpool(self._hub.iter_sse(n, follow, local_node, source)),
            media_type="text/event-stream",
        )

    def ingest(self, body: LogsIngestBody) -> dict[str, Any]:
        if self._state().role != "master":
            raise HTTPException(403, "only the master accepts log ingest")
        self._hub.set_enabled(self._state().logging_enabled)
        if not self._state().logging_enabled:
            return {"ok": True, "ingested": 0, "logging_enabled": False}
        self.ensure_journal()
        events = [
            cluster_log.make_event(
                body.node_id,
                item.source,
                item.line,
                ts=item.ts,
                event_id=str(item.event_id) if item.event_id is not None else None,
            )
            for item in body.lines
        ]
        return {"ok": True, "ingested": self._hub.ingest(events, persist=True)}

    def read(
        self,
        n: int,
        follow: bool,
        node: str | None,
        source: str | None,
        after: str | None,
        proxy: Callable[
            [int, bool, str | None, str | None, str | None],
            dict[str, Any] | StreamingResponse | None,
        ],
    ) -> dict[str, Any] | StreamingResponse:
        n = max(1, min(int(n), 5000))
        if source and source not in cluster_log.VALID_SOURCES:
            raise HTTPException(400, "source must be agent, llama, or rpc")
        if after is not None:
            if follow:
                raise HTTPException(400, "after cannot be combined with follow")
            try:
                cluster_log.parse_cursor(after)
            except cluster_log.CursorError as exc:
                raise HTTPException(400, f"invalid log cursor: {exc}") from exc
        state = self._state()
        if state.role == "master":
            self.ensure_journal()
        if state.role == "worker" and state.master_host:
            proxied = proxy(n, follow, node, source, after)
            if proxied is not None:
                if isinstance(proxied, dict):
                    try:
                        cluster_log.parse_cursor(proxied.get("cursor"))
                    except cluster_log.CursorError as exc:
                        raise HTTPException(
                            502, "master does not support valid incremental log snapshots"
                        ) from exc
                    lines = proxied.get("lines")
                    if not isinstance(lines, list) or any(
                        not isinstance(line, dict)
                        or cluster_log.normalize_event_id(line.get("event_id")) is None
                        for line in lines
                    ):
                        raise HTTPException(
                            502, "master does not support identified incremental log entries"
                        )
                return proxied
            warning = "master unreachable; showing local logs"
            if follow:

                def local_events() -> Iterator[bytes]:
                    yield cluster_log.encode_sse(cluster_log.make_event("kllm", "agent", warning))
                    yield from self._hub.iter_sse(n, True, node or state.node_id, source)

                return StreamingResponse(
                    iterate_in_threadpool(local_events()), media_type="text/event-stream"
                )
            result = self.snapshot(n, node, source, after)
            result.update(warning=warning, source="local")
            return result
        if follow:
            return self.stream(n, True, node, source)
        return self.snapshot(n, node, source, after)
