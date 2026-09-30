# Measured Codebase and Test Performance

> **Created:** 2026-09-30
> **Status:** Planned
> **Repo / scope:** kluster-llm — Python, native, dashboard and complete test feedback

## Conventions captured at creation

- Root: `D:/dev/kluster-llm`; working branch at planning: `main`; feature directory
  `specs/011-codebase-performance`. Spec Kit setup labels the feature slug as BRANCH when
  `SPECIFY_FEATURE` is unset; it is not the checked-out Git branch.
- Python is uv-managed (reference 3.12.13), native code is Rust edition 2021 / PyO3 0.23,
  dashboard is Svelte 5/Vite 6 on Node 22, tests use pytest, Cargo and Node `node:test`.
- Tests patch names on `kllm.agent` and the loaded `scripts/install.py` module; facade names
  stay there and delegate patched values rather than rebinding them in siblings.
- `LiveFeed` in `python/kllm/agent/live.py` bounds source result size/work, coalesces readers
  and tracks source scope. `LogHub` in `python/kllm/cluster_log.py` maintains receiver
  cursor/epoch and journal identity; worker mirrors live in `python/kllm/agent/live_ws.py`.
- `dashboard/src/lib/log-trail.svelte.ts::LogTrail` caps visible rows at 2,000 but preserves
  exact UUID identity for the mounted visit. Clear is display-only. Producer positions and
  retirement proofs do not exist; receiver cursor is not producer identity.
- The browser validator caps the complete logs result at 256 KiB. Python `_log_result`
  historically sized the snapshot alone; contract alignment must size the complete result.
- Existing boundaries: `tests/test_dashboard_live.py`, `tests/test_cluster_log.py`,
  `tests/test_cp_proxy.py`, `dashboard/tests/logs.test.mjs`, real Chrome workspace/model tests,
  and native process/helper deadline checks.
- Native helper slots already have deadlines/quarantine/reaping. Capacity and live usage are
  distinct. RPC port 50052 remains private. No inference tuning or llama.cpp pin change planned.
- Planning baseline (one Windows 11 build 26200 run): Python 516 pass/5 skip in 80.53s,
  dashboard 47 pass in 46.689s, Rust 34 pass in 10.418s command / 0.23s test bodies. Synthetic
  500-row log preparation 245.57ms median; no suite median or delivered gain is claimed.

## Intended sequence

1. Re-capture matched repeated full-suite baselines; separate setup, test work and teardown.
2. Optimize oversized-log suffix selection with existing serializer and complete-result byte
   budget; prove maximal suffix and gap/cursor/error semantics.
3. Make unit failures deterministic and reduce redundant test-only waits while retaining every
   integration, process, real deadline, browser and serial complete check.
4. Deliver an explicitly negotiated producer-position/frontier protocol for bounded exact
   replay identity. Preserve legacy behavior and exact UUID suppression on legacy transitions;
   fail negotiated mode if durable authority, restart, hole or capacity guarantees cannot be
   proven. Never ship silent eviction.
5. Re-measure suites/log workload. Other cache/database/concurrency/engine candidates remain
   conditional on their own evidence.

## Detailed plan and artifacts

Feature-scoped plan: [`../specs/011-codebase-performance/plan.md`](../specs/011-codebase-performance/plan.md).
It records research decisions, Constitution gates, risk stop conditions and `/speckit.tasks`
handoff. Design artifacts: [`../specs/011-codebase-performance/data-model.md`](../specs/011-codebase-performance/data-model.md),
[`../specs/011-codebase-performance/contracts/log-identity.md`](../specs/011-codebase-performance/contracts/log-identity.md),
[`../specs/011-codebase-performance/contracts/performance-validation.md`](../specs/011-codebase-performance/contracts/performance-validation.md),
and [`../specs/011-codebase-performance/quickstart.md`](../specs/011-codebase-performance/quickstart.md).

Freeze this plan after work is realized, abandoned or superseded; record actual verification
in living docs/change history rather than rewriting this point-in-time intent.
