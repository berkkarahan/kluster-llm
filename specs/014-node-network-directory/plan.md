# Implementation Plan: Central Node Network Directory and Runtime Sync Recovery

**Branch**: `014-node-network-directory` | **Date**: 2026-10-08 | **Spec**: [spec.md](./spec.md)

## Summary

Make `managed-network.sqlite` own durable per-node network identity (hostname, LAN host, RPC
bind, private address, private state, link speed), mirror it to workers through the membership
snapshot, and serve both the cluster view and Settings → llama.cpp from one read model. Repair
master→worker runtime sync: same-key master rotation adopts even with a live managed network,
stale targets are re-stamped or surfaced, and failed preparations retry with backoff.

## Technical Context

- Python 3.11+ (`src/kllm`), Svelte 5 + TypeScript (`dashboard`), SQLite (`managed-network.sqlite`).
- No new dependencies. Additive schema; existing stores and membership transport reused.
- Tests: `uv run pytest -q`, `npm --prefix dashboard run test` (node --test runner).

## Constitution Check

| Principle | Assessment |
|-----------|------------|
| I. Simple, consistent code | PASS — extends existing `NetworkStore`, `ManagedNetworkService`, membership snapshot; no new abstraction without a caller |
| II. Behavior-focused regression tests | PASS — pairing rotation, directory mirroring, stale-target, retry tests |
| III. Verified integration/portability | PASS with note — boundary tests are ASGI/local; two-node hardware rotation stays a labeled follow-up |
| IV. Explicit contracts/failures | PASS — directory rows validated by a typed record; stale target states are explicit; changed keys still refused |
| V. Maintainable/reproducible | PASS — `docs/troubleshooting.md`, `docs/control-api-v2.md`, `docs/engine-decision.md` updated in the same change |

## Structure

Backend:

- `src/kllm/managed_network/models.py` — `NodeNetworkIdentity` record.
- `src/kllm/managed_network/store.py` — `node_directory` table + CRUD; same-key peer replacement.
- `src/kllm/managed_network/bootstrap.py` — adoption uses same-key replacement path.
- `src/kllm/agent/schemas.py`, `services/heartbeat_payload.py` — carry `hostname`.
- `src/kllm/agent/handlers/membership.py` — directory upsert/remove; snapshot directory.
- `src/kllm/agent/services/managed_network.py` — assignment-driven directory writes, backfill,
  `directory_view()`.
- `src/kllm/agent/services/dashboard_status.py` — `network_directory` in status payloads.
- `src/kllm/agent/services/usage_overview.py`, `managed_network/guided_setup.py` — directory names.
- `src/kllm/agent/services/runtime_control.py` — directory name/address, target states, channel
  state, auto-retry.
- `src/kllm/agent/__init__.py` — wiring and worker-side directory mirror.

Dashboard:

- `lib/types.ts`, `lib/runtime-types.ts`, `lib/store.svelte.ts`.
- `lib/components/LlamaRuntimes.svelte`, `lib/components/NodeMap.svelte`,
  `lib/components/ClusterTopology.svelte`, `routes/+page.svelte`.

## Phases

1. Directory store + model + master writers + worker mirror + backfill + tests.
2. Shared read paths (runtime, dashboard status, usage, guided setup) + dashboard UI + tests.
3. Sync repair (same-key adoption, target re-stamp/status, auto-retry, channel status) + tests.
4. Documentation and targeted verification.

## Verification

- `uv run pytest -q tests/test_managed_network_critical.py tests/test_network_pairing_recovery.py tests/test_runtime_versions.py tests/test_dashboard_api.py tests/test_network_channel.py`
- `npm --prefix dashboard run test`
- `uv run ruff check src/kllm tests`
- Hardware two-node rotation: labeled follow-up (no live cluster in CI).
