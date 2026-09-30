"""Small, read-only activity view for a running llama-server."""

from __future__ import annotations

from typing import Any

import httpx


def _fetch_slots(client: httpx.Client, base: str, server_ready: bool, *, timeout: float | None) -> tuple[bool, Any]:
    kwargs = {"timeout": timeout} if timeout is not None else {}
    if not server_ready:
        health = client.get(f"{base}/health", **kwargs)
        if health.status_code != 200:
            return False, None
        server_ready = True
    slots_response = client.get(f"{base}/slots", **kwargs)
    return server_ready, slots_response.json() if slots_response.status_code == 200 else []


def idle() -> dict[str, Any]:
    return {
        "phase": "idle",
        "active_requests": 0,
        "prompt_tokens": 0,
        "prompt_tokens_processed": 0,
        "output_tokens": 0,
        "slots": 0,
        "server_ready": False,
    }


def sample(
    port: int,
    *,
    running: bool,
    load: dict[str, Any] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Return the current loading or request phase without affecting inference."""
    if not running:
        return idle()

    load_phase = (load or {}).get("phase")
    if load_phase in ("starting", "loading", "error"):
        return {
            "phase": "error" if load_phase == "error" else "loading_model",
            "active_requests": 0,
            "prompt_tokens": 0,
            "prompt_tokens_processed": 0,
            "output_tokens": 0,
            "slots": 0,
            "server_ready": False,
            "load_fraction": (load or {}).get("fraction", 0.0),
            "detail": (load or {}).get("error"),
        }

    server_ready = load_phase == "ready"
    base = f"http://127.0.0.1:{port}"
    try:
        if client is None:
            with httpx.Client(timeout=0.35, trust_env=False) as owned_client:
                server_ready, slots_raw = _fetch_slots(owned_client, base, server_ready, timeout=None)
        else:
            server_ready, slots_raw = _fetch_slots(client, base, server_ready, timeout=0.35)
        if slots_raw is None:
            return {**idle(), "phase": "starting", "detail": "waiting for inference server"}
    except (httpx.HTTPError, ValueError):
        return {
            **idle(),
            "phase": "ready" if server_ready else "starting",
            "server_ready": server_ready,
            "detail": "slots endpoint unavailable" if server_ready else "waiting for inference server",
        }

    slots = [slot for slot in slots_raw if isinstance(slot, dict)] if isinstance(slots_raw, list) else []
    busy = [slot for slot in slots if slot.get("is_processing")]
    prompt_total = sum(int(slot.get("n_prompt_tokens") or 0) for slot in busy)
    prompt_done = sum(int(slot.get("n_prompt_tokens_processed") or 0) for slot in busy)
    output = sum(
        int((slot.get("next_token") or {}).get("n_decoded") or 0)
        for slot in busy
        if isinstance(slot.get("next_token"), dict)
    )
    processing_prompt = any(
        int(slot.get("n_prompt_tokens_processed") or 0) < int(slot.get("n_prompt_tokens") or 0)
        for slot in busy
    )
    return {
        "phase": "processing_input" if processing_prompt else "generating" if busy else "ready",
        "active_requests": len(busy),
        "prompt_tokens": prompt_total,
        "prompt_tokens_processed": prompt_done,
        "output_tokens": output,
        "slots": len(slots),
        "server_ready": server_ready,
    }
