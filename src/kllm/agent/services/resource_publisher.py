"""Worker-to-master resource snapshot publication."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from kllm import usage
from kllm.state import ClusterState


class ResourcePublisher:
    """Publish snapshots with capability detection and generation-safe sessions."""

    def __init__(
        self,
        state: Callable[[], ClusterState],
        resources: usage.ResourceState,
        remote: dict[str, Any],
        master_control_base: Callable[[], str],
        heartbeat_payload: Callable[[], dict[str, Any]],
        tracked_process_token: Callable[[], str | None],
        control_client: Callable[[], Any],
    ) -> None:
        self._state = state
        self._resources = resources
        self._remote = remote
        self._master_control_base = master_control_base
        self._heartbeat_payload = heartbeat_payload
        self._tracked_process_token = tracked_process_token
        self._control_client = control_client

    def publish(self, snapshot: dict[str, Any]) -> None:
        state = self._state()
        if state.role == "master":
            with self._resources.lock:
                self._resources.latest[state.node_id] = snapshot
                self._resources.received[state.node_id] = time.monotonic()
            return
        if state.role != "worker" or not state.master_host or not state.join_key:
            return

        base = self._master_control_base()
        session_key = (base, state.node_id, state.join_key)
        client = self._control_client()
        headers = {"X-Kllm-Key": state.join_key}
        if self._remote.get("key") != session_key:
            self._remote.clear()
            self._remote["key"] = session_key
        if (
            "supported" not in self._remote
            or time.monotonic() - self._remote.get("capability_checked_at", 0) >= 30
        ):
            response = client.get(f"{base}/v2/telemetry", headers=headers, timeout=1.5)
            response.raise_for_status()
            versions = response.json().get("resource_schema_versions", [])
            if (
                not isinstance(versions, list)
                or len(versions) > 8
                or any(type(version) is not int for version in versions)
            ):
                raise ValueError("invalid resource capability response")
            self._remote["supported"] = 1 in versions
            self._remote["capability_checked_at"] = time.monotonic()
        if not self._remote["supported"]:
            return

        process_identity = (state.process_pid, self._tracked_process_token())
        if self._remote.get("process_identity") != process_identity:
            response = client.post(
                f"{base}/v2/cluster/nodes/heartbeat",
                json=self._heartbeat_payload(),
                headers=headers,
                timeout=1.5,
            )
            response.raise_for_status()
            if not response.json().get("accepted"):
                raise RuntimeError("resource publisher requires worker registration")
            self._remote["process_identity"] = process_identity

        if self._remote.get("source_generation") != snapshot["generation"] or (
            self._remote.get("sequence", 0) >= 9007199254740991
            and self._remote.get("last_snapshot_id") != snapshot["snapshot_id"]
        ):
            self._remote.pop("generation", None)
            self._remote["source_generation"] = snapshot["generation"]
        if not self._remote.get("generation"):
            previous = None
            for _attempt in range(2):
                request_id = uuid.uuid4().hex
                request = dict(
                    self._heartbeat_payload(),
                    resource_session={"request_id": request_id, "previous_generation": previous},
                )
                response = client.post(
                    f"{base}/v2/cluster/nodes/register",
                    json=request,
                    headers=headers,
                    timeout=1.5,
                )
                if response.status_code == 409:
                    previous = response.json().get("detail", {}).get("resource_generation")
                    continue
                response.raise_for_status()
                acknowledgement = response.json()
                generation = acknowledgement.get("resource_generation")
                if (
                    acknowledgement.get("request_id") != request_id
                    or not isinstance(generation, str)
                    or len(generation) != 32
                    or any(char not in "0123456789abcdef" for char in generation)
                ):
                    raise ValueError("resource session acknowledgement mismatch")
                self._remote.update(generation=generation, sequence=0, last_snapshot_id=None)
                break
            if not self._remote.get("generation"):
                raise RuntimeError("resource session conflict; retrying next sample")

        if self._remote.get("last_snapshot_id") != snapshot["snapshot_id"]:
            self._remote["sequence"] += 1
            self._remote["last_snapshot_id"] = snapshot["snapshot_id"]
        outgoing = dict(
            snapshot,
            generation=self._remote["generation"],
            sequence=self._remote["sequence"],
        )
        outgoing["snapshot_id"] = f"{outgoing['generation']}:{outgoing['sequence']}"
        response = client.post(
            f"{base}/v2/telemetry/ingest",
            json={"node_id": state.node_id, "readings": [], "resources": outgoing},
            headers=headers,
            timeout=1.5,
        )
        if response.status_code == 409:
            self._remote.pop("generation", None)
        response.raise_for_status()
