# Data model: Log Source Tags

**Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

Unchanged trail fields, cursors, and visit lifecycle stay as defined in
[the log-trail data model](../004-cluster-log-trail/data-model.md). This feature adds
the tag and the visit filter.

## LogTag

Closed set, stored in lowercase. The Logs page shows the display label.

| Stored value | Display | Producer |
|--------------|---------|----------|
| `ui` | UI | `source` is `agent` |
| `inference` | Inference | `source` is `llama` or `rpc` |
| `unclassified` | Unclassified | `source` is `unknown` |

An entry has exactly one tag. The tag is a function of `source`. Message text, node, and
a caller-supplied tag do not change it.

## LogEntry

Existing fields remain: `event_id`, `ts`, `node_id`, `source`, `line`.

| Field | Rule |
|-------|------|
| `source` | `agent`, `llama`, `rpc`, or `unknown` |
| `tag` | The LogTag for that source. New hub events always include it. A reader derives it when an older event omits it. |

Validation:

- `agent` pairs only with `ui`.
- `llama` and `rpc` pair only with `inference`.
- `unknown` pairs only with `unclassified`.
- A present tag outside the three values, or a tag that breaks a pair above, is invalid.
- A missing tag is valid and is replaced by the pair for `source`.

`unknown` is not a CLI `--source` choice. It is the stored label for a producer the hub
could not classify.

## TagFilter

| Value | Visible tags |
|-------|----------------|
| All (`""` in page state, same empty-value pattern as All nodes) | `ui`, `inference`, and `unclassified` |
| `ui` | `ui` |
| `inference` | `inference` |
| `unclassified` | `unclassified` |

The control always offers all four choices. A new visit starts at All. The selection is
not stored beyond that visit.

## LogViewSession

Adds `selectedTag`, default All. Existing node, refresh mode, buffer, seen IDs, cursor,
Clear flag, coverage, gap, and omission count are unchanged.

Visible rows are buffer rows whose node matches `selectedNodeId` (or all nodes) and whose
tag matches `selectedTag` (or all tags). The buffer itself is not reduced by the filter,
so a hidden row still counts toward the 2,000-row cap.

### Transitions added by this feature

| Event | Change |
|-------|--------|
| Visit starts | `selectedTag` is All |
| Tag change | Replace `selectedTag` only. Keep node, refresh mode, buffer, cursor, Clear flag, and reading position when the anchor row remains visible |
| Filter hides every buffered row | Visit state stays ready when the buffer is non-empty. The page shows filter-empty copy, not the healthy-empty, cleared, loading, or error copy |
| Clear | Empty the whole buffer, including tag-hidden rows. Keep `selectedTag` |
| Snapshot line omits `tag` | Derive and store the tag on the accepted row |
| Snapshot line has a conflicting or illegal `tag` | Reject the snapshot. Do not advance the cursor |

Gap, trim, local-only, and disconnected behavior stay on the visit and are not rewritten
as consequences of `selectedTag`.

## Persistence

New journal objects include `tag` plus the existing event fields. Loading a journal row
re-derives `tag` from its stored `source`, then writes the normalized source and tag into
the ring. Rows already stored as `agent`, `llama`, or `rpc` keep that source. A row with
a missing or unrecognized source becomes `unknown` / `unclassified` when loaded. The
journal file is not rewritten as a migration.
