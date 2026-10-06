"""Master run launch steps. Callers pass the native and runtime objects tests patch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException


def stop_previous_master(state: Any, native: Any, runtime: Any, load_progress: Any, inference_activity: Any) -> None:
    """Stop a llama-server this master already started."""
    if not state.process_pid:
        return
    native.stop_process(state.process_pid, force=True)
    state.active_config = None
    runtime.clear_children()
    load_progress.end()
    state.inference_activity = inference_activity.idle()


def spawn_master(
    native: Any,
    runtime: Any,
    launch_cfg: dict[str, Any],
    ensure_alive: Callable[[int | None], None],
) -> dict[str, Any]:
    """Start llama-server and stop it again if the spawn check fails."""
    launched: dict[str, Any] | None = None
    try:
        launched = native.start_master(**launch_cfg)
        ensure_alive(launched.get("pid"))
    except HTTPException:
        if launched and launched.get("pid"):
            native.stop_process(int(launched["pid"]), force=True)
            runtime.clear_children()
        raise
    except Exception as exc:
        if launched and launched.get("pid"):
            native.stop_process(int(launched["pid"]), force=True)
            runtime.clear_children()
        raise HTTPException(500, str(exc)) from exc
    return launched


def finish_master_launch(
    state: Any,
    path: Any,
    plan_result: dict[str, Any],
    launched: dict[str, Any],
    runtime: Any,
    load_progress: Any,
    persist_settings: Callable[[], None],
    remember: Callable[[], None],
) -> None:
    """Record a successful master launch in the same order as the historical route."""
    state.process_pid = launched.get("pid")
    state.process_role = "master"
    state.model_name = path.name
    state.last_plan = plan_result
    state.apply_error = None
    state.apply_warning = plan_result.get("warning") if plan_result.get("fit_status") == "tight" else None
    persist_settings()
    if launched.get("pid"):
        runtime.register_child(int(launched["pid"]), "llama-server")
        try:
            load_progress.begin(path.name, int(launched["pid"]), path.stat().st_size)
        except OSError:
            pass
    state.save()
    remember()


def run_response(plan_result: dict[str, Any], launched: dict[str, Any]) -> dict[str, Any]:
    """HTTP body for a successful ``/v1/run``."""
    out: dict[str, Any] = {"plan": plan_result, "launch": launched}
    if plan_result.get("fit_status") == "tight":
        out["warning"] = plan_result.get("warning") or plan_result.get("hint")
    return out
