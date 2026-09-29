# Implementation Plan: Log Source Tags

**Branch**: `main` | **Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/007-log-source-tags/spec.md`

## Summary

Show each cluster log entry as UI, Inference, or Unclassified, and let the Logs page
filter by that tag. Assign the tag in `make_event` from the producer source: `agent`
is UI, `llama` and `rpc` are Inference, and any other or missing source is stored as
`unknown` and tagged Unclassified. The page filters the current visit locally, together
with the existing node filter. Refresh, checkpoints, Clear, and `kllm logs --source`
stay as they are.

## Technical Context

**Language/Version**: Python 3.12 workspace (package supports >=3.10); TypeScript 5/Svelte 5.

**Primary Dependencies**: Existing FastAPI log snapshot, Svelte 5 runes, and the current
Logs page. No new dependencies.

**Storage**: Existing in-memory ring and rotating `cluster.jsonl` journal. New events
gain a `tag` field. Older journal rows have no `tag` and are classified from `source`
when loaded. Browser filter state lasts for one mounted visit.

**Testing**: Existing pytest `tests/test_cluster_log.py` and Node `dashboard/tests/logs.test.mjs`,
plus the current `/logs` section of `dashboard/tests/usage-workspace.test.mjs`.

**Target Platform**: Current desktop and narrow-screen browsers; existing Windows, Linux,
and macOS agents.

**Project Type**: Static dashboard served by the existing Python agent.

**Performance Goals**: A tag change updates the visible rows from the current visit within
one second. No extra log request is issued for the filter. The 2,000-row cap and existing
read deadlines stay unchanged.

**Constraints**: One tag per entry, chosen only from the producer source. The dashboard
keeps requesting the unfiltered feed. `kllm logs` keeps `--source agent|llama|rpc` and
its current printed line shape. Filter-empty copy stays distinct from loading, cleared,
healthy-empty, local-only, and error states. Gap and trim notices stay attributed to
retention, not to the tag filter.

**Scale/Scope**: One classifier in the log hub, one filter and one column on the existing
Logs page, and the live-snapshot validator that already checks log lines. No new route,
archive, or command-line flag.

## Constitution Check

| Principle | Pre-research gate | Post-design gate |
|-----------|-------------------|------------------|
| I. Simple, consistent code | PASS: tag fits the existing event and the existing page filter | PASS: one classifier in `make_event`; local filter beside the node filter; no new dependency |
| II. Behavior-focused tests | PASS: producer mapping, unknown source, and hidden-row Clear need checks | PASS: hub, journal reload, controller filter, and `/logs` browser checks in [quickstart.md](quickstart.md) |
| III. Integration and portability | PASS: CLI source filter and worker relay share the event | PASS: relay and journal keep the same source filter; omitted `tag` still displays; no GPU check |
| IV. Explicit contracts and failures | PASS: unknown producer must not be stored as UI | PASS: [contracts/logs.md](contracts/logs.md) defines `unknown`, derived tags, and rejection of a conflicting tag |
| V. Maintainable changes | PASS: `docs/cli.md` and `docs/troubleshooting.md` already describe Logs | PASS: those sections gain the tag control and the filter-empty meaning |

No exceptions requested. Gates assess this design, not implementation test results.

## Project Structure

### Documentation (this feature)

```text
specs/007-log-source-tags/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── memory.md
├── memory-synthesis.md
├── checklists/requirements.md
└── contracts/logs.md
```

`tasks.md` is the next `/speckit.tasks` output, not created by this command.

### Source Code (repository root)

```text
python/kllm/cluster_log.py                 # Classify source and tag in make_event
python/kllm/cli.py                         # Unchanged flags and line format
dashboard/src/lib/types.ts                 # LogTag and optional tag on LogEntry
dashboard/src/lib/log-trail.svelte.ts      # Accept unknown, derive or check tag, filter rows
dashboard/src/lib/live-updates.ts          # Same line rules for the live snapshot
dashboard/src/routes/logs/+page.svelte     # Tag filter, tag column, filter-empty copy
tests/test_cluster_log.py                 # Classifier, journal reload, source filter
dashboard/tests/logs.test.mjs             # Filter, Clear, omitted and conflicting tags
dashboard/tests/usage-workspace.test.mjs  # Visible tag filter on /logs
docs/cli.md
docs/troubleshooting.md
```

**Structure Decision**: Classification lives in the existing log hub so every reader sees
the same tag. The Logs page filters rows it already holds, the same way it filters nodes.
`AppStore` and the log request parameters stay unchanged.

## Implementation sequence

1. **Classify at the hub.** Map `agent` to `ui`, `llama` and `rpc` to `inference`, and
   every other source to stored source `unknown` with tag `unclassified`. Pass raw source
   into `make_event` from ingest and journal load. Ignore a caller-supplied tag. Reload
   older journal rows by deriving the tag from the stored source.
2. **Accept the event on the dashboard.** Allow source `unknown`. If `tag` is absent,
   derive it. If `tag` is present, require it to match the source map; otherwise reject
   the snapshot. Keep requesting logs without a tag parameter.
3. **Filter the visit.** Add an All / UI / Inference / Unclassified control that starts
   on All and does not change node, refresh mode, cursor, or Clear. Visible rows match
   both selections. Clear still removes hidden rows. Filter-empty copy is separate from
   gap and trim notices.
4. **Verify and document.** Follow [quickstart.md](quickstart.md). Update the Logs
   sections of `docs/cli.md` and `docs/troubleshooting.md`. Leave `kllm logs --source`
   and the printed line shape unchanged.

## Complexity Tracking

No constitution violations. A separate tag service, a server-side tag query, or text
matching inside log lines would duplicate the producer source the hub already stores.
