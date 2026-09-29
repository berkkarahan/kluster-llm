# Memory Synthesis

## Current Scope
Log source tags for `specs/007-log-source-tags`. Classify each cluster log entry as UI, Inference, or Unclassified from the activity that produced it, and filter the existing Logs page by that tag. Affected modules: `python/kllm/cluster_log.py`, the dashboard Logs trail (`dashboard/src/lib/log-trail.svelte.ts`, `dashboard/src/routes/logs/+page.svelte`, `dashboard/src/lib/types.ts`, `dashboard/src/lib/live-updates.ts`), and the existing logs sections of `docs/cli.md` and `docs/troubleshooting.md`.

## Relevant Decisions
- [none] (Reason Included: durable decision index is empty, Status: n/a, Source: docs/memory/INDEX.md)

## Active Architecture Constraints
- Keep the change in the existing Python log hub and dashboard Logs page; do not add a logging service or dependency. (Reason Included: module ownership, Source: .specify/memory/constitution.md)
- Cover the classifier, journal reload, dashboard filter, and Clear-with-hidden-rows behavior with deterministic tests. (Reason Included: behavior-test gate, Source: .specify/memory/constitution.md)
- Keep `kllm logs --source agent|llama|rpc` valid and leave the CLI line format unchanged. (Reason Included: public contract compatibility, Source: .specify/memory/constitution.md)
- Tag filtering stays a view over the current visit. The dashboard keeps reading the unfiltered log feed so 004 checkpoints are not reused under a changed server filter. (Reason Included: architectural drift risk, Source: specs/004-cluster-log-trail/contracts/logs.md)
- Unknown producers stay Unclassified. Today `make_event` stores any other source as `agent`, which would label them UI. (Reason Included: spec FR-004, Source: python/kllm/cluster_log.py)

## Accepted Deviations
- [none] (Reason Included: no accepted deviation applies, Status: Accepted-Deviation)

## Relevant Security Constraints
- Tags and filter text stay inside the existing private cluster log boundary and carry no secrets. (Reason Included: log access boundary, Source: .specify/memory/constitution.md)
- Inbound tag values are not trusted; the hub assigns the tag from the producer source. (Reason Included: trust boundary, Source: .specify/memory/constitution.md)

## Related Historical Lessons
- [none] (Reason Included: no durable bug or worklog rows exist for this area)

## Conflict Warnings
- No hard or soft conflict. A display tag and local filter stay compatible with the 004 trail when hidden rows remain buffered until Clear and gap notices stay attributed to retention.

## Retrieval Notes
- Index entries considered: 0. Constitution and the 004 logs contract were the selected constraints. Budget: within 900 words.
- Shared-memory sync was not run. The project has no `project_profile`, and confidence that an external lesson applies is below 50%.
- No durable capture. The producer-tag rule is feature-local until implementation evidence exists.
- Cached token savings: not measured. The memory optimizer CLI is not built in this checkout.
