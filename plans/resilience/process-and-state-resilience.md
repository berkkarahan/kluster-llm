# Process and state resilience

> **Created:** 2026-10-08
> **Status:** Planned
> **Repo / scope:** kluster-llm — membership thread, cluster-state writes, and who is allowed to start `rpc-server`

## Conventions captured at creation

`MembershipLoop` in `src/kllm/agent/services/membership_loop.py` runs one daemon thread. `tick` does master sync or the worker heartbeat, then schedules capacity refresh. It does not start `rpc-server`. `run` logs `membership tick failed (<ExceptionType>)` and continues on the next interval. The regression in `tests/test_background_tasks.py` requires that the exception message itself is not logged.

`RuntimeSetupService.ensure_worker` in `src/kllm/agent/services/runtime_setup.py` returns `None`. `kllm.agent._ensure_worker_runtime` is the same no-op. RPC starts only from `WorkerRpcService` after a signed master command (`start_commanded_worker`). Heartbeats keep a joined worker in the member map while it is idle. Tests in `tests/test_membership.py` lock that a dead worker process is not restarted from the membership tick.

A successful worker heartbeat always calls `state.save()` at the end of `MembershipHeartbeatService.post_heartbeat`, including when the snapshot did not change. Cluster state is the JSON file behind `ClusterState`. Capacity probes stay off this thread (`CapacityRefreshScheduler`) because `llama-server --list-devices` can stall longer than the 20 second liveness window.

`RpcRunCoordinator._recover` reloads the saved run up to three times at 1, 2, and 4 seconds, keeps the original workers, and gives up with an apply error. Stop cancels that loop. Startup recovery releases a previous child; it does not resume inference. Log output for llama-server is either an append-only file or a helper whose stdin is the child's stdout (`stdio` setup in `crates/kllm-native/src/process.rs`). A helper that stops reading can stall the engine on a full pipe.

Structural rules: helpers take their dependencies as arguments, imports stay at module top except for a documented cycle, and façade names on `kllm.agent` remain patchable. No new subprocess escape hatch.

## Intended change

Stop rewriting cluster state on every identical heartbeat, and make a stuck membership tick or a stuck log sink visible without giving membership permission to spawn `rpc-server`.

The idle-worker rule stays. A membership tick that launches RPC would race the signed run coordinator and can start a second server on port 50052. The gap is durability and observability: a 2 second `state.save()` of an unchanged member map wears the disk and holds the membership thread, and a log helper that blocks is invisible until generation stalls.

## Phased steps

1. Compare the heartbeat snapshot to the last applied payload inside `MembershipHeartbeatService`. Call `state.save()` only when role, membership, warnings, or the applied fields changed. A regression should post two identical 200 responses and observe one save, then a changed `cluster_members` map and observe a second save. Rejection (`accepted is False`) still leaves immediately and still persists that transition.
2. Count consecutive `MembershipLoop` failures in memory. After the same exception type repeats for a full liveness window (10 ticks at 2 seconds), log one warning that names the type and the count, still without the exception text. Reset the count on a successful tick. Do not back off the interval; liveness depends on the 2 second cadence.
3. On the log-helper path, choose a bounded non-blocking write or a measured pipe buffer so a stalled helper cannot stop llama-server token generation. If the platform cannot do that safely, drop or spill helper output and surface one operator warning, and keep the direct-to-file path unchanged. Add a test with a helper that never reads and assert the child is still writable or that the warning is set. Do not claim a tokens-per-second change from that test.
4. Leave `ensure_worker` as a no-op. If the dashboard needs to show a serving worker whose RPC process died before the master reloads, read `rpc_status` from the renew result that `observe_health` already classifies. Do not start a process from that view.

## Open questions

- Does `ClusterState.save` already coalesce identical writes, or does every call hit the disk? Step 1 should confirm that before adding a second cache.
- Is the log helper on the default Run path, or only when a log-helper argv is configured? The pipe fix should follow the path `start_process` actually uses for a normal master launch.
- Should a membership thread that has not completed a tick within 20 seconds be restarted, or only reported? Restarting it can overlap two heartbeats if the old tick is stuck in HTTP.
