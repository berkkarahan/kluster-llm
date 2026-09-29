# Research: Log Source Tags

**Created**: 2026-09-29
**Specification**: [spec.md](spec.md)
**Status**: Technical research complete.

Findings were checked against the log hub, the Logs page, the live snapshot validator,
and the CLI formatter. No cluster, GPU, or browser run was required for this planning
research.

## 1. Assign the tag from the producer source

**Decision**: `make_event` sets `tag` from `source` and ignores any tag on the input.

| Incoming source | Stored source | Tag | Display |
|-----------------|---------------|-----|---------|
| `agent` | `agent` | `ui` | UI |
| `llama` | `llama` | `inference` | Inference |
| `rpc` | `rpc` | `inference` | Inference |
| missing or anything else | `unknown` | `unclassified` | Unclassified |

`agent.log` is the control-application file, including lines about starting or supervising
inference. `llama-server.log` is model serving. `rpc-server.log` is the activity used only
to spread that serving. The tag follows the file source, not words inside the line.

**Rationale**: Those three files are already the only local producers
(`LOCAL_SOURCES` in `python/kllm/cluster_log.py`). One mapping keeps the CLI source
filter, the Source column, and the new tag in agreement. Trusting a caller-supplied tag
would let a relayed event claim UI for a model-serving line.

**Alternatives considered**: Keyword rules on the message text mislabel control lines that
mention inference. A free-form tag list exceeds the fixed set in FR-014. Leaving the tag
only in the browser would let the hub keep storing an unknown producer as `agent`.

**Evidence**: `python/kllm/cluster_log.py:LOCAL_SOURCES`, `make_event`, `tail_new_events`.

## 2. Stop storing an unknown producer as agent

**Decision**: Missing and unrecognized sources are stored as `unknown` with tag
`unclassified`. Ingest and journal load pass the raw source into `make_event` instead of
substituting `agent` first.

**Rationale**: `make_event` currently keeps only `agent`, `llama`, and `rpc`, and both
`ingest` and `ensure_journal_loaded` turn a missing source into `agent` before that call.
Classifying after that substitution would show Unclassified producers as UI and would fail
FR-004. Older rows that already contain `agent`, `llama`, or `rpc` still receive the
matching tag on load. A row with no source becomes `unknown` on load.

**Alternatives considered**: Keeping the `agent` fallback and adding a second hidden flag
would leave the Source column saying `agent` on an Unclassified row. Rejecting unknown
events would drop lines the trail is supposed to show.

**Evidence**: `python/kllm/cluster_log.py:make_event`, `ingest`, `ensure_journal_loaded`.
`dashboard/src/lib/log-trail.svelte.ts` and `dashboard/src/lib/live-updates.ts` currently
reject any source outside `agent|llama|rpc`, including the fixture value `unknown` in
`dashboard/tests/logs.test.mjs`. That fixture moves to a different illegal token when
`unknown` becomes valid.

## 3. Filter in the current visit

**Decision**: The Logs page adds a single-choice tag filter defaulting to All. Visible
rows are the visit buffer filtered by both the selected node and the selected tag.
Changing the tag does not change the request, cursor, node, refresh mode, or Clear.
`GET /v1/logs` gains no tag parameter.

**Rationale**: The page already holds up to 2,000 rows and filters nodes in
`LogTrail.visibleEntries`. The 004 contract says the dashboard requests the unfiltered
feed because a checkpoint must not be reused with a changed server filter. A tag query
would repeat that hazard. Local filtering also makes a tag change immediate, which
SC-002 and FR-007 require. Clear already assigns `entries = []`, so tag-hidden rows
leave with the rest of the visit.

**Alternatives considered**: Server-side tag filtering would need a separate checkpoint
per filter and would still have to retain hidden rows for an immediate All view.
Filtering only future polls would leave the opposite tag on screen until the next read.

**Evidence**: `dashboard/src/lib/log-trail.svelte.ts:visibleEntries`, `clear`;
`specs/004-cluster-log-trail/contracts/logs.md` snapshot request rules.

## 4. Accept events that omit the tag

**Decision**: A line with no `tag` is shown with the tag derived from `source`. A line
with a `tag` outside `ui|inference|unclassified`, or a `tag` that disagrees with the
source map, makes the snapshot invalid. New hub events always include a matching `tag`.

**Rationale**: Current dashboard fixtures and an older master omit `tag`. Deriving it
keeps those trails readable during a mixed upgrade. A conflicting tag is invalid input,
not a hint the page should prefer. The live frame already checks each line; the extra
short field stays inside the existing log-frame budget.

**Alternatives considered**: Requiring `tag` immediately would reject every current
snapshot and fixture. Preferring a conflicting tag over `source` would let a bad peer
relabel model-serving output as UI.

**Evidence**: `dashboard/tests/usage-workspace.test.mjs` log fixtures have `source` and
no `tag`. `dashboard/src/lib/live-updates.ts:validLogs` checks `source` per line.
`python/kllm/agent/live.py` caps the log frame at 256 KiB after trimming lines.

## 5. Leave command-line reading unchanged

**Decision**: `kllm logs --source` still accepts only `agent`, `llama`, and `rpc`.
The printed line stays time, node, source, and message. The formatter ignores `tag`.
No `--tag` flag is added. An unclassified line remains visible in an unfiltered dump,
with source `unknown`.

**Rationale**: The spec keeps the current command-line source choices and does not ask
for a command-line tag filter. `_format_log_event` already prints `source` and skips
unknown JSON fields, so an additive `tag` does not change the line shape. `--source agent`
no longer matches a line that was previously coerced from an empty source; that line is
Unclassified and was not an agent-file line.

**Alternatives considered**: Printing the tag in the CLI would change the line operators
and tests already read. Adding `--tag` widens the public command beyond the spec.

**Evidence**: `python/kllm/cli.py:logs_cmd`, `_format_log_event`.
