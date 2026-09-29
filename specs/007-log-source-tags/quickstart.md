# Validation guide: Log Source Tags

**Date**: 2026-09-29
**Status**: Automated checks recorded on 2026-09-29. The manual operator sample in SC-001 is still unverified.

## Prerequisites

- Follow [the agent installation guide](../../docs/agent-install.md). Use the existing uv
  environment. This feature does not need a GPU or a live cluster.
- Use temporary `KLLM_HOME` directories and synthetic log lines. Do not use real prompts,
  tokens, or production journals as fixtures.
- Browser checks need installed Chrome or Edge, or `CHROME_BIN`. Record an unavailable
  browser as skipped. Do not report that skip as an accessibility pass.

## Automated checks

Run from the repository root after implementation.

```text
uv run pytest -q tests/test_cluster_log.py
node --test --test-concurrency=1 dashboard/tests/logs.test.mjs dashboard/tests/usage-workspace.test.mjs
npm --prefix dashboard run check
```

Run `npm --prefix dashboard run check` and `npm --prefix dashboard run build` sequentially.
The shared log event gains a field, so also run `uv run pytest -q` before treating the
change as integrated. Report failures with evidence, and separate any failure that already
existed on `main`.

## Hub checks

In `tests/test_cluster_log.py`, assert the mapping in [data-model.md](data-model.md):

- A tailed `agent.log` line is `source=agent`, `tag=ui`.
- A tailed `llama-server.log` line is `source=llama`, `tag=inference`, including a line
  whose text talks about the control application.
- A tailed `rpc-server.log` line is `source=rpc`, `tag=inference`.
- Ingest of a missing source, an empty source, and a source such as `bogus` stores
  `source=unknown`, `tag=unclassified`. A supplied `tag` of `ui` on those inputs does not
  stick.
- A journal row written by this hub reloads with the same source and tag.
- A legacy journal row with `source=agent` and no `tag` reloads as `tag=ui`.
- A legacy journal row with no `source` reloads as `unknown` / `unclassified`.
- `recent(..., source="agent")` returns agent lines and excludes `unknown` lines.
- Worker relay still posts the event, and the master stores the tag derived from source.

## Dashboard controller checks

In `dashboard/tests/logs.test.mjs`:

- A visit starts with All tags. A buffer of UI, Inference, and Unclassified rows shows
  each subset when that tag is selected and shows all of them for All. The change is
  local: no new request is recorded for the selection itself.
- A selected node and a selected tag both apply. Changing the tag leaves the node and
  refresh mode in place.
- Rows hidden by the tag remain in the buffer. Clear removes them. A later All selection
  does not bring them back. Clear before the first successful snapshot stays a no-op.
- A line with `source=llama` and no `tag` is accepted as Inference. `source=unknown`
  with no `tag` is accepted as Unclassified.
- `source=llama` with `tag=ui`, and `tag=other`, reject the snapshot and do not advance
  the cursor. Move the existing malformed `source: "unknown"` case to a source outside
  `agent|llama|rpc|unknown`.
- With only UI rows buffered, the Inference selection exposes an empty visible list while
  the visit state stays ready. The page copy for that condition is distinct from loading,
  cleared, healthy-empty, and error text.

## Browser check

Extend the existing `/logs` section of `dashboard/tests/usage-workspace.test.mjs`.
The current fixture lines are an agent line (`boot ok`) and an rpc line (`worker ready`).
After load:

- Both rows show tags UI and Inference.
- Choosing Inference leaves `worker ready` visible and hides `boot ok`.
- Choosing All tags shows both again without a reload.
- Choosing UI, then Clear, leaves the visit empty. Choosing All tags does not restore
  `boot ok`.
- At 1280-by-720 and 390-by-844, the tag control, both tags, node control, four refresh
  choices, and Clear are present and `document.documentElement.scrollWidth <= innerWidth`.

The four-of-five operator timing in SC-001 is a manual usability check. The automated
browser check covers that the control exists, is labeled, and changes the visible rows.
Record the manual check separately when it is run. Do not treat the automated test as
that operator sample.

## Documentation check

`docs/cli.md` describes the Logs page tags and states that `kllm logs --source` is still
`agent|llama|rpc` with no tag flag. `docs/troubleshooting.md` tells the operator that a
tag filter with no rows is a selection result, not an outage, a cleared display, or a
history gap.

## Not in this validation

- No new public listener, journal migration, or log deletion.
- No command-line `--tag` flag.
- No check that an old master preserves `source=unknown` from a new worker. That limitation
  is stated in [contracts/logs.md](contracts/logs.md).

## Results (2026-09-29)

Baseline before the tag change, from the repository root:

- `uv run pytest -q tests/test_cluster_log.py` — 27 passed.
- `node --test --test-concurrency=1 dashboard/tests/logs.test.mjs` — 12 passed.

After implementation:

- `uv run pytest -q tests/test_cluster_log.py` — 29 passed.
- `node --test --test-concurrency=1 dashboard/tests/logs.test.mjs dashboard/tests/live-updates.test.mjs` — 36 passed.
- `node --test --test-concurrency=1 dashboard/tests/usage-workspace.test.mjs` — 2 passed, including the Logs tag, filter, Clear, and 1280-by-720 / 390-by-844 checks.
- `npm --prefix dashboard run check` — 0 errors.
- `npm --prefix dashboard run build` — completed.
- `uv run pytest -q` — 424 passed, 3 failed.

The three failures are pre-existing install-script checks and do not import the log tag change:

- `tests/test_install_script.py::test_discards_cmake_build_with_dangling_openssl_path`
- `tests/test_install_script.py::test_dangling_linker_flag_alone_does_not_discard_build`
- `tests/test_install_script.py::test_cache_reset_flags_only_scrub_dangling_linker_flags`

SC-001, the four-of-five operator timing sample, was not run. The browser harness confirms the tag control is present, labeled, and changes the visible rows. That is not a substitute for the operator sample.
