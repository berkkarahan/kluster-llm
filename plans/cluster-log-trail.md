# Master and worker log-trail plan

> **Created:** 2026-09-29
> **Status:** Planned
> **Repo / scope:** kluster-llm — dashboard Logs page and existing cluster-log read contract

## Conventions captured at creation

- `python/kllm/cluster_log.py:LogHub` already tails agent/llama/rpc logs, relays workers,
  maintains an 8,000-event ring, and persists the master's rotating JSON-lines journal.
- `python/kllm/agent/__init__.py:get_logs` already serves snapshots and SSE, with worker
  proxying and local fallback. Records currently lack stable IDs and resume cursors.
- `python/kllm/cli.py:logs_cmd` consumes the same endpoint; additions must preserve its
  existing JSON/SSE behavior and old worker ingest bodies.
- `dashboard/src/routes/+layout.svelte:nav` owns navigation. Svelte 5, same-origin fetch,
  native controls, and `dashboard/src/app.css` provide the existing implementation style.
- `dashboard/src/lib/poll-loop.ts:PollLoop` uses jitter and completion-relative timing;
  exact selectable log cadence should not silently alter its existing consumers.
- `dashboard/DESIGN.md` defines dark graphite, green accents, and IBM Plex typography.
  The usage monitor's amber is feature-local. OpenDesign's local MCP is reachable.
- `.specify/memory/constitution.md` requires scoped changes, observable regression checks,
  compatibility, explicit failures, and documentation. No new package is needed.
- The checkout already contains other work, including feature 003. Feature 004 owns these
  documents; shared navigation changes must integrate with that work.

## Intended result

One combined master/worker trail, node selection, display-only Clear, and exactly
Live / 5s / 30s / 1m. Live is one-second incremental polling. Preserve selection and reading
position, make local-only/disconnected/gapped coverage explicit, and keep existing logs intact.

## Execution sequence

1. Save the feature specification and quality checklist; generate/review its OpenDesign
   prototype and record its actual artifact reference.
2. Add stable event identities and an additive cursor-based snapshot read to the existing
   hub/endpoint. Preserve relay IDs, journal compatibility, CLI behavior, and private access.
3. Add one page-owned refresh controller and `/logs` page using existing UI primitives.
   Clear retains deduplication/resume state; stale asynchronous replies cannot repopulate it.
4. Verify collection/read/proxy boundaries, timing and Clear races, keyboard/mobile behavior,
   then update existing logging documentation. Run dashboard checks sequentially.

## Detailed handoff

- [Specification](../specs/004-cluster-log-trail/spec.md)
- [Spec Kit implementation plan](../specs/004-cluster-log-trail/plan.md)
- [Research decisions](../specs/004-cluster-log-trail/research.md)
- [OpenDesign brief and evidence](../specs/004-cluster-log-trail/design.md)
- [Validation guide](../specs/004-cluster-log-trail/quickstart.md)

No product decisions remain open. Prototype review and runtime validation are execution
evidence, not claims that the feature is already implemented. This snapshot and the Spec Kit
plan are frozen when the work is realized; subsequent behavior belongs in operational docs.
