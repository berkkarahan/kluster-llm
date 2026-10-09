# Feature Memory - 007-log-source-tags

## Scope Notes
- Classify each cluster log entry shown on the Logs page as UI, Inference, or Unclassified, and filter that page by tag.
- UI means the kllm control application (`agent`), including messages about starting or supervising model serving.
- Inference means model serving (`llama`) and activity used only to spread that serving (`rpc`).
- Unclassified means the producer source is missing or outside those three values. Store that source as `unknown`.
- The tag is assigned from the producer source. Message text does not select it. Callers cannot supply a conflicting tag.
- The Logs page filters tags locally. Default is All. Clear still drops hidden rows for the current visit only.

## Relevant Durable Memory
- Governance constitution: keep the change scoped; cover observable behavior with tests; keep public command-line log reading compatible; update the existing logs documentation with the behavior change; do not place secrets in tags or new status text.
- Durable `docs/memory/` index has no decision, architecture, or bug rows for this area.

## Open Questions
- None. Research resolved tag assignment, the `unknown` source, and display-only filtering. See `research.md`.

## Watchlist
- Journal load and ingest currently pass a missing source through as `agent` before classification. Both call sites must pass the raw source into `make_event`.
- Dashboard snapshot validation rejects `source: "unknown"` today. That token becomes legal; use a different illegal token in the malformed-snapshot test.
- Lines that omit `tag` must still render, with the tag derived from `source`, so current masters and existing dashboard fixtures keep working.
- A present `tag` that disagrees with `source` is an invalid snapshot, not a display override.
- Tag filtering must not add a query parameter or change cursor, Clear, refresh, or the 2,000-row cap.
- `kllm logs --source` stays `agent|llama|rpc`. Do not add a CLI tag flag.

## Never Store Here
- permanent project decisions
- general bug patterns unless they are directly reused
- implementation history after the feature ships
