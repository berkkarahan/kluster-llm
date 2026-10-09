# Validation quickstart: node and process usage monitor

Run these scenarios after implementing [plan.md](plan.md) and the [HTTP/UI contract](contracts/usage-monitor.md). This is a validation guide, not a test suite or implementation recipe. Work within the existing private LAN/WireGuard control plane; never expose port 50052 publicly. See `docs/agent-install.md` for toolchain/setup and `docs/cli.md` for supported cluster commands.

## Prerequisites

- Python/uv and dashboard dependencies installed per `docs/agent-install.md`; run commands below from the repository root. Use an isolated `KLLM_HOME` in automated checks and test-only in-memory/fake process/network/sensor inputs. Do not use a production history directory for retention tests.
- For live checks: master plus up to three joined nodes with managed llama.cpp `llama-server`/`rpc-server`; for the 1/4/16-node performance envelope, use matched private-network nodes. A CPU-only node is valid; per-device sensors require a supported GPU/tool and separate hardware verification. Record OS/backend/model/hardware for performance results.
- Complete and record the spec-requested OpenDesign MCP feature artifact before comparing visual design; the general `dashboard/DESIGN.md` is background direction only. If MCP access is unavailable, record the blocked design prerequisite rather than claiming the artifact exists.

## Automated boundary checks

```sh
uv run pytest -q tests/test_usage.py tests/test_dashboard_api.py tests/test_membership.py
node --test dashboard/tests/*.test.mjs
npm --prefix dashboard run check
npm --prefix dashboard run build
```

`tests/test_usage.py` is the planned focused test added during implementation. Expect green checks with isolated fake processes/clock/network/filesystem covering: one core on eight logical processors → 12.5% process CPU; measured 2 GiB RSS; first CPU sample unavailable; true PID/create-time restart distinction; expired/disconnected node history retained; duplicate/invalid/late events and two-second gaps; last successful save and storage read/write failure; restart-safe history, 24-hour/256 MiB oldest-first pruning. Dashboard checks cover accessible node/point selection and formatted shared GiB values. If a command cannot run on the current machine, record its exact failure and rerun on the named platform—do not count it as passed.

## Private API and dashboard walkthrough

1. Start/join through the existing Home controls or documented CLI; launch a model through `/run`. Open the dashboard on the master, and separately from a worker. Check the current `GET /v1/status` and the new `GET /v1/usage/live` and `GET /v1/usage/lifetimes?node_id=<actual-id>` via the agent's same-origin/private control address. Expect one card per node, only master→worker relationships, scope/unit/time on measurements, no fabricated GPU/worker compute figures and the same cluster actions/chat availability as before. Worker-served history should proxy through the private master route without any browser join-key exchange.
2. Select each node, pan/zoom/Fit, wait for updates, then inspect details. Expect stable selection/viewport, process CPU and RSS separate from host and estimate, 8/16/24 GiB displayed as 8.0/16.0/24.0 GiB; shared RAM counted once, missing sensor shown unavailable with reason. On a CPU-only node process CPU/RSS remain readable.
3. With a managed child running, close all dashboard tabs for five minutes; reopen and query 15m/1h/24h history. Expect stored UTC points and visible interruptions, `last_successful_save` and no invented pre-monitoring data. Stop and relaunch the process (include PID reuse in a controlled test); verify two distinct lifetimes and no readings after the first exit. Restart the monitoring agent while a child is still running; the old lifetime must continue if its OS start identity is unchanged, and the outage shows a gap.
4. Disconnect a worker. At ten seconds after its latest observation expect `stale` with timestamp. Within 25 seconds of its last contact it should no longer be connected, yet its retained history and selected details remain. Rejoin with the same node ID: expect a single live card, earlier lifetimes intact. Inject a rejected malformed/duplicate point, a late valid point and a disk-write failure in an isolated test: expect actionable error/failed status, preserved earlier history, no inference termination and no live time reversal.
5. With test-only accelerated clock/fill data, verify that records older than 24 hours or beyond the **physical** 256 MiB cluster allowance (database plus any SQLite sidecars) are retired oldest-first. Inspect actual earliest coverage and `storage_truncated`, ensure remaining identities and points are readable; measure committed-record delay <=10 seconds during healthy collection. Inspect saved records to confirm absence of prompts, responses, credentials and unrelated process inventory.
6. Inspect at 1280×720 and 390×844 with keyboard alone: reach node/map controls, history filters and selected values with visible focus, no horizontal page overflow, textual live/stale/gap states, no hover/color dependency; enable reduced motion and check nonessential animation is suppressed. Verify loading, empty cluster, dashboard offline and recording failure states separately. Compare to the recorded feature-specific OpenDesign artifact.

## Hardware and performance evidence

On each supported OS (Windows, Linux, macOS), run a real managed child, confirm psutil PID/start/CPU/RSS against OS measurements, and document any unsupported sensor/device as unavailable. Validate NVIDIA/other device metrics only on their respective installed backend/tool; check process-attributed accelerator usage only with direct attribution. Run five matched inference runs with monitoring off and five with monitoring on using the same model, prompt, backend, OS and hardware, record median tokens/second and percentage difference; target no more than 5% regression. For 1, 4 and 16 private-network nodes, record live-change latency, interaction latency, collection interval coverage and actual disk size against the spec's SC-002/003/007 thresholds; report unrun cluster/hardware configurations explicitly.
