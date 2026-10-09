# Logs tag contract

**Date**: 2026-09-29 | **Status**: Proposed additive contract

Extends [the log-trail contract](../../004-cluster-log-trail/contracts/logs.md). Snapshot
query parameters, cursors, coverage, Clear, and CLI `--source agent|llama|rpc` are
unchanged. This feature adds the producer tag and the Logs page filter.

## Event

`make_event` assigns both fields. A supplied `tag` is ignored.

```json
{
  "event_id": "57a75357-6338-4e17-a49e-08af5f5751c0",
  "ts": 1790632000.25,
  "node_id": "worker-02",
  "source": "llama",
  "tag": "inference",
  "line": "slot 0 ready"
}
```

| `source` | `tag` |
|----------|-------|
| `agent` | `ui` |
| `llama` | `inference` |
| `rpc` | `inference` |
| `unknown` | `unclassified` |

`unknown` is used only when the incoming source is missing or outside `agent|llama|rpc`.
Relay posts may include `tag`; the receiving hub still classifies from `source`. An old
master that does not know `unknown` can still coerce that source. Unclassified preservation
requires the master that stores the event to be on this contract. `agent`, `llama`, and
`rpc` classify correctly on a newer dashboard even when the event has no `tag`.

Journal appends include `tag`. Load accepts a row without `tag` and derives it. Load does
not accept a stored pair that breaks the table above; the row is normalized from `source`.

## Snapshot

`GET /v1/logs` and the live log snapshot keep their current parameters. There is no `tag`
query. The dashboard continues to request the unfiltered feed and filters nodes and tags
locally.

Each line in `lines` SHOULD include `tag`. Readers MUST accept a line that omits `tag`
and derive it from `source`. Readers MUST reject the snapshot when a line includes a
`tag` that is not `ui`, `inference`, or `unclassified`, or that disagrees with `source`.
Rejection does not advance the cursor.

`source: "unknown"` is a valid line source. Any other new source string remains invalid.

The CLI formatter keeps printing time, node, source, and message. It does not gain a tag
column or a `--tag` option. `--source` still rejects values other than `agent`, `llama`,
and `rpc`, so it does not select `unknown` lines. An unfiltered dump still prints those
lines with source `unknown`.

## Logs page

The tag control is a single choice labeled for assistive tech, with exactly:

| Choice | Page state | Shows |
|--------|------------|-------|
| All tags | empty selection | every tag |
| UI | `ui` | `ui` |
| Inference | `inference` | `inference` |
| Unclassified | `unclassified` | `unclassified` |

A new visit starts on All tags. The choice survives refresh changes and Clear, and it is
dropped when the page instance is destroyed.

Each row shows the display label UI, Inference, or Unclassified in addition to time, node,
source, and message. The tag control and the tag text are keyboard reachable, with visible
focus, at 1280-by-720 and 390-by-844, without page-wide horizontal overflow.

When the buffer has rows and the combined node and tag selection shows none, the empty
copy states that the current selection has no matching entries. It uses different text
from loading, a visit with no entries, a cleared visit, local-only coverage, a failed
read, a history gap, and the trimmed-row notice. Selecting All tags shows the hidden rows
still in the visit without waiting for another read.

Clear removes every buffered row, including rows hidden by the tag or the node. Stored
logs and another Logs page stay unchanged.
