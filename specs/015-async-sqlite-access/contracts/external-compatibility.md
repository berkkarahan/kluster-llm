# Contract: External Compatibility

**Scope**: Preserve current CLI, control routes and dashboard behavior while replacing internal SQLite access. `docs/control-api-v2.md` and current Pydantic/route models remain authoritative for complete payload schemas. No new endpoints, commands, auth schemes or dashboard controls are introduced.

## Usage control surface

| Existing surface | Preserved contract | Persistence integration |
|---|---|---|
| `GET /v2/telemetry` | Current live node/resource view, coverage and recording status | Sync sources remain supported; failures remain explicit |
| `GET /v2/telemetry/lifetimes` | node identity, retained-node IDs, lifetimes and coverage | Compound response shares one consistent leased read |
| `GET /v2/telemetry/series` | Existing node/lifetime/window rules and ordered aggregation | Core queries reproduce existing filter/order/bucket results |
| `GET /v2/telemetry/readings` | Window `15m`, `1h`, `24h`; existing cursor; limit 1–1000, default 500 | Preserve pagination, signature secret, record IDs and ordering |
| `POST /v2/telemetry/ingest` | Existing authentication, master role, body size/schema/process validation | Await tracked persistence after request validation |

Successful ingest still reports `accepted`, `duplicates`, and `last_successful_save`, plus `resources_accepted` when a resource snapshot is supplied. Preserve current optionality and durable acknowledgement semantics. A recorder/retention failure returns unavailable even when earlier insertion was committed; duplicate handling makes retry safe according to existing reading identity, not a claim that nothing was saved.

Preserve existing validation 400, authentication 401/403, missing-history 404, resource-generation conflict 409, model/schema boundary validation 422 where currently produced, and history/recorder-unavailable 503. New pool/lifecycle/uncertain-commit failures map to unavailable with sanitized context. History failures retain previously saved records. Worker proxy status mapping and authorization remain unchanged. Preserve registered legacy v1 routes as well.

## Managed network and runtime control

Keep the existing managed-network, bootstrap, signed-channel, directory and runtime-control route schemas. Async handlers must await tracked offloads for synchronous storage-backed views/lookups rather than call them on the event loop. Preserve synchronous service return contracts, and test affected routes with a held network-store lease: unrelated status remains responsive and acquisition timeout yields the existing sanitized unavailable response. Preserve StoreConflict responses, request IDs/digests, revision checks, authority/channel fencing and operation result shapes. Use existing correlation IDs to reconcile unknown outcomes. Do not add a second public SQL API or change pairing/authentication behavior.

Network directory still appears in `/v2/status`, live status, and registration/heartbeat responses with its current fields and liveness meaning. Loading durable records does not establish current readiness. Failure must not manufacture healthy empty state or reuse saved monotonic deadlines.

## Status, CLI and operational settings

`/health` remains a database-independent liveness/status boundary with its existing payload. `/v2/status` keeps current contracts; its storage-dependent components may report existing unavailable behavior when that domain fails. Responsiveness measurements use `/health` as the unrelated probe and also exercise `/v2/status` while usage storage is delayed and network storage remains available; a status dependency on the deliberately blocked domain is not labeled unrelated work.

Existing CLI start/join/run/stop behavior, exit/error reporting and package entry points remain stable. New environment settings are deployment settings only, documented in installation/troubleshooting guidance and snapshotted when each owner opens. Invalid settings fail with an actionable persistence/configuration error before serving operations.

No secrets or SQL parameter content enter HTTP errors, recorder messages or logs. Public JSON failure formats remain those of existing boundaries. Unknown outcome is represented as unavailable and existing operation/history reconciliation, not a new successful response field.

## Compatibility acceptance

1. Seed known legacy databases before running the new layer. Compare node/lifetime/association/readings/meta contents, directory/authority/network/operation/journal contents and result ordering before/after reopen.
2. Compare history responses/cursors and network revision/idempotency/recovery decisions with expected legacy results.
3. Send real local HTTP requests through the FastAPI application for good, invalid, unknown, conflict, saturated and failed-store cases; verify authentication and response contracts.
4. Cancel a real ingest request at queued/pre-commit/commit boundaries; assert no false success and reconcile durable outcome by existing reading IDs.
5. Exercise two local processes competing for the same protected mutation; check only the valid owner changes state and stale requests fail as before.
6. Verify lifecycle failure cannot publish a healthy store or a replacement generation while old work remains active. Hold a source job and, separately, protected reconciliation beyond the common deadline; the complete lifecycle attempt must fail within that budget even before owner close starts, preserve tracking, and drain safely after the barrier is released.

All external behavior checks are planned verification. They have not been run during design.
