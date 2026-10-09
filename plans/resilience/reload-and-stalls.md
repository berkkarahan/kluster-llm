# Reload scope and stalled inference

> **Created:** 2026-10-08
> **Status:** Planned
> **Repo / scope:** kluster-llm — RPC recovery and the inference proxy

## Conventions captured at creation

- `src/kllm/agent/services/rpc_runs.py` `RpcRunCoordinator.observe_health` returns `healthy`, `wait`, or `recover`. A dead local llama-server or a `runtime_error` recovers immediately. A worker that is not `serving`, or a managed network that is not `ready`, is tolerated for `RECOVERY_GRACE_POLLS` (2) samples. The monitor sleeps 5 seconds between samples.
- `RpcRunCoordinator._recover` stops the current run and calls `run` again, up to 3 attempts with delays 1, 2, and 4 seconds. The comment in `_recover` says recovery keeps the original participants and does not move the workload onto machines that joined later.
- Worker lease expiry is independent: `WorkerRpcService.expire` stops the owned `rpc-server` after `LEASE_SECONDS` (60) without a renew. `ManagedNetworkService._expire_worker_rpc` calls that about once a second. The process must be bound to one private address before start.
- `src/kllm/agent/application_lifecycle.py` builds the inference client with `httpx.Timeout(None, connect=5.0)` and `INFERENCE_LIMITS` (`keepalive_expiry` 120). `src/kllm/gateway.py` `_proxy` streams upstream bytes with no read deadline. A llama-server that accepts the socket and then sends nothing holds the client until the process is gone.
- `src/kllm/agent/services/membership_loop.py` `MembershipLoop.run` logs a tick exception and continues. Capacity probes are scheduled off that thread (`_schedule_local_capacity_refresh` in `src/kllm/agent/__init__.py`) because `llama-server --list-devices` can stall. The membership thread itself is not restarted if `run` raises `BaseException`.
- Tests patch `kllm.agent` and keep request models on the existing Pydantic bodies. New helpers take their clocks and clients as arguments. `docs/memory/architecture/2026-09-29-structural-standards.md` says to keep that shape. `.specify/memory/workflow.md` and `.specify/memory/architecture_constitution.md` were not in the tree on this date.

## Intended change

A worker blip should not reload weights, and a wedged upstream should free the client without cutting off a long but progressing generation.

1. **Narrow recovery.** If the local server's pid and start token still match and the only failure is a worker renew, renew or restart that worker's `rpc-server` and keep the loaded llama-server. Reload the model only when the local process is dead, the token changed, or the worker restart cannot restore `serving` within the existing 3 attempts.
2. **Stall timeout, not a total timeout.** In the inference proxy, if no upstream bytes arrive for a bounded interval (start at 120 seconds), fail that request with 504 and return the connection to the pool. Any byte resets the interval, so a slow prefill that streams progress, and a long token stream, keep going. Do not set a total read timeout on the shared client; that would cap generation length.
3. **Membership thread.** If `MembershipLoop.run` exits while the stop event is clear, start it again from `start` without clearing the "already started" guard incorrectly. The existing test that a failed thread factory clears the guard must stay true.

## Phases

1. Split `_recover` into worker restart versus full `run`. Add a coordinator test where the master pid stays alive, one worker renew fails twice, and launch is not called again. A dead pid must still call launch.
2. Add the stall watchdog around `gateway._proxy` chunks and the non-streaming `aread`, with a fake upstream that is silent and one that dribbles a byte inside the window. Assert the silent case returns 504 and the dribble case does not.
3. Supervise the membership thread and extend `tests/test_background_tasks.py` so an unexpected exit schedules one new thread and a stop event does not.

## Open questions

- After a worker-only restart, is the llama-server RPC slot still valid, or does the engine require the whole process to reconnect before the next token?
- Should the stall window be 120 seconds, matching the inference keepalive, or longer because a cold GPU prefill can sit silent?
