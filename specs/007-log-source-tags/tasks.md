# Tasks: Log Source Tags

**Input**: Design documents from `specs/007-log-source-tags/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/logs.md](contracts/logs.md), [quickstart.md](quickstart.md)

**Tests**: Constitution principles II–III and the approved plan require behavior-focused automated checks. Extend `tests/test_cluster_log.py`, `dashboard/tests/logs.test.mjs`, `dashboard/tests/live-updates.test.mjs`, and `dashboard/tests/usage-workspace.test.mjs`. Write the new checks first, observe the relevant failure, then make them pass. No new test framework, dependency, GPU, or live cluster is needed.

**Organization**: Five phases. User Story 1 shows the tag. User Story 2 filters by it. User Story 3 keeps that filter inside the existing node, refresh, and Clear behavior. All boxes start unchecked.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: `US1`, `US2`, or `US3` on user-story tasks only
- Include exact file paths in descriptions

## Path Conventions

- Log hub: `python/kllm/cluster_log.py`. CLI formatter stays `python/kllm/cli.py`.
- Dashboard: `dashboard/src/lib/types.ts`, `dashboard/src/lib/log-trail.svelte.ts`, `dashboard/src/lib/live-updates.ts`, `dashboard/src/routes/logs/+page.svelte`.
- Tests: `tests/test_cluster_log.py`, `dashboard/tests/logs.test.mjs`, `dashboard/tests/live-updates.test.mjs`, `dashboard/tests/usage-workspace.test.mjs`.
- Docs: `docs/cli.md`, `docs/troubleshooting.md`.
- The working tree already has unrelated model-search edits in `dashboard/src/lib/types.ts`, `dashboard/src/lib/store.svelte.ts`, `dashboard/src/routes/models/+page.svelte`, `dashboard/tests/store.test.mjs`, `dashboard/tests/models.test.mjs`, `docs/cli.md`, `docs/troubleshooting.md`, `python/kllm/models.py`, and the model API tests. Add this feature on top of those edits. Do not revert them.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing log trail and leave unrelated work untouched.

- [X] T001 Read `specs/007-log-source-tags/plan.md` and `specs/007-log-source-tags/quickstart.md`, then run `uv run pytest -q tests/test_cluster_log.py` and `node --test --test-concurrency=1 dashboard/tests/logs.test.mjs` from the repository root. Record the commands, results, and any pre-existing failures in `specs/007-log-source-tags/quickstart.md`. Do not add a dependency and do not revert unrelated working-tree edits.

**Checkpoint**: Baseline log checks are recorded. User-story work has not started.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Assign one producer tag on every hub event before any Logs page story starts.

**CRITICAL**: No user story work begins until this phase is complete.

- [X] T002 [P] Add `LogTag` and the `unknown` source to `dashboard/src/lib/types.ts` using the closed pairs from `specs/007-log-source-tags/data-model.md`: stored `ui` displays UI when `source` is `agent`; stored `inference` displays Inference when `source` is `llama` or `rpc`; stored `unclassified` displays Unclassified when `source` is `unknown`. `tag` may be absent on older events. "`unknown` is not a CLI `--source` choice. It is the stored label for a producer the hub could not classify." Preserve the unrelated edits already in this file.
- [X] T003 [P] Classify events in `make_event`, `ingest`, and `ensure_journal_loaded` in `python/kllm/cluster_log.py`, and extend `tests/test_cluster_log.py`. "`make_event` assigns both fields. A supplied `tag` is ignored." "`An entry has exactly one tag. The tag is a function of source. Message text, node, and a caller-supplied tag do not change it.`" Missing or unrecognized sources are stored as `unknown` with `unclassified`; pass the raw source in instead of substituting `agent` first. "`The journal file is not rewritten as a migration.`" A legacy row with `source=agent` and no `tag` reloads as `ui`; a legacy row with no `source` reloads as `unknown` / `unclassified`; `recent(..., source="agent")` excludes `unknown`. In `python/kllm/cli.py`, do not add a `--tag` option. "`The CLI formatter keeps printing time, node, source, and message.`" Assert that an event carrying `tag` still formats without that word and that `logs_cmd` still rejects a source other than `agent`, `llama`, or `rpc`.

**Checkpoint**: Run `uv run pytest -q tests/test_cluster_log.py`. New events and reloaded journal rows carry the source/tag pairs. CLI source flags and printed line shape stay as they are.

---

## Phase 3: User Story 1 - Tell UI logs from inference logs (Priority: P1) — MVP

**Goal**: Every row on the Logs page shows UI, Inference, or Unclassified, including retained history and lines that omit `tag`.

**Independent Test**: Load a visit containing an `agent` line whose text mentions starting inference, a `llama` line, an `rpc` line, and an `unknown` line. Each row shows exactly one display tag for its source, plus the existing time, node, source, and message. A conflicting `tag` does not change the cursor.

### Tests for User Story 1

- [X] T004 [P] [US1] Extend `dashboard/tests/logs.test.mjs` so these checks fail before the trail accepts tags: a line that omits `tag` follows "`A missing tag is valid and is replaced by the pair for source.`"; `source=llama` with text about the control application becomes Inference; `source=unknown` with no `tag` becomes Unclassified; "`A present tag outside the three values, or a tag that breaks a pair above, is invalid.`" and "`Reject the snapshot. Do not advance the cursor.`" Move the current malformed `source: "unknown"` fixture to a source outside `agent`, `llama`, `rpc`, and `unknown`.
- [X] T005 [P] [US1] Extend `dashboard/tests/live-updates.test.mjs` with the same acceptance and rejection rules for a live log line: omitted `tag` is valid, `source: "unknown"` is valid, and a `tag` that is not `ui`, `inference`, or `unclassified`, or that disagrees with `source`, is invalid. "`Any other new source string remains invalid.`"

### Implementation for User Story 1

- [X] T006 [P] [US1] Derive and validate `tag` in `dashboard/src/lib/log-trail.svelte.ts` before a row is stored. "`Snapshot line omits tag | Derive and store the tag on the accepted row.`" "`Snapshot line has a conflicting or illegal tag | Reject the snapshot. Do not advance the cursor.`" Keep the unfiltered `GET /v1/logs` request. "`There is no tag query.`" Satisfy T004.
- [X] T007 [P] [US1] Apply those same line rules in `validLogs` in `dashboard/src/lib/live-updates.ts` so a live snapshot cannot relabel a producer. Omitted `tag` stays valid. A conflicting or illegal `tag` makes the live log payload invalid. Satisfy T005.
- [X] T008 [US1] Show the display label UI, Inference, or Unclassified on each row in `dashboard/src/routes/logs/+page.svelte`, beside the existing time, node, source, and message. Do not add a filter in this task. Keep message text inert and the current newest-first order.
- [X] T009 [US1] Extend the `/logs` section of `dashboard/tests/usage-workspace.test.mjs` so the existing `agent` row shows UI and the existing `rpc` row shows Inference, without changing the initial two-row load those tests already wait for. Run T004, T005, and this browser check, and record the US1 results in `specs/007-log-source-tags/quickstart.md`.

**Checkpoint**: US1 shows a tag on every accepted row. Filtering, Clear changes, and layout changes are not part of this slice.

---

## Phase 4: User Story 2 - Filter the Logs page by tag (Priority: P1)

**Goal**: The operator can show All tags, UI, Inference, or Unclassified, and the visible rows change immediately from the current visit.

**Independent Test**: With UI, Inference, and Unclassified rows already buffered, each choice shows only that tag, All shows every buffered tag, and a tag with no rows shows filter-empty copy while the other rows stay buffered. No extra log request is issued for the selection.

### Tests for User Story 2

- [X] T010 [US2] Extend `dashboard/tests/logs.test.mjs` so a new visit starts on All, each single tag shows only its rows, All restores rows hidden only by the previous tag, and the selection issues no new log request. "`Visible rows are buffer rows whose node matches selectedNodeId (or all nodes) and whose tag matches selectedTag (or all tags). The buffer itself is not reduced by the filter, so a hidden row still counts toward the 2,000-row cap.`" With only UI rows buffered, Inference leaves the visit state ready and the visible list empty.

### Implementation for User Story 2

- [X] T011 [US2] Add `selectedTag` to `dashboard/src/lib/log-trail.svelte.ts`. "`The control always offers all four choices. A new visit starts at All. The selection is not stored beyond that visit.`" All is the empty selection, matching All nodes. "`Tag change | Replace selectedTag only. Keep node, refresh mode, buffer, cursor, Clear flag, and reading position when the anchor row remains visible.`" "`Filter hides every buffered row | Visit state stays ready when the buffer is non-empty.`" Satisfy T010.
- [X] T012 [US2] Add the single-choice tag control to `dashboard/src/routes/logs/+page.svelte` with exactly All tags, UI, Inference, and Unclassified, labeled for assistive tech and bound to `selectedTag`. When the buffer has rows and the selection shows none, the empty copy states that the current selection has no matching entries and uses different text from loading, a visit with no entries, a cleared visit, local-only coverage, a failed read, a history gap, and the trimmed-row notice.
- [X] T013 [US2] Extend `dashboard/tests/usage-workspace.test.mjs` so choosing Inference hides `boot ok` and leaves `worker ready`, and choosing All tags shows both again without a reload. Run T010 and this browser check, and record the US2 results in `specs/007-log-source-tags/quickstart.md`.

**Checkpoint**: US1 and US2 work together. The tag filter is local to the visit and does not change stored logs.

---

## Phase 5: User Story 3 - Keep tag filtering within the existing trail (Priority: P2)

**Goal**: Node, refresh, Clear, gaps, and keyboard/narrow layout keep their current meaning when a tag is selected.

**Independent Test**: Select one node, one tag, and a non-default refresh mode. A row appears only when it matches both selections. Changing the tag leaves the node and refresh mode in place. Clear removes tag-hidden rows for this visit only; All tags does not bring them back. Gap and trim notices stay visible and are not described as effects of the tag filter. Keyboard use at 1280-by-720 and 390-by-844 reaches the tag control, tags, node control, four refresh choices, and Clear without page-wide horizontal overflow.

### Tests for User Story 3

- [X] T014 [P] [US3] Extend `dashboard/tests/logs.test.mjs` for the combined node and tag selection, a tag change that preserves node and refresh mode, Clear removing tag-hidden rows while keeping `selectedTag`, and Clear before the first successful snapshot remaining a no-op. A later All selection does not restore cleared rows. An active gap or omission count stays in place when the tag changes and is not cleared by the filter.
- [X] T015 [P] [US3] Extend `dashboard/tests/usage-workspace.test.mjs` for keyboard operation of the tag control, Clear after UI is selected, and All tags not restoring `boot ok`. At 1280-by-720 and 390-by-844, assert the tag control, tag text, node control, four refresh choices, and Clear are present and `document.documentElement.scrollWidth <= innerWidth`. Assert the filter-empty sentence does not reuse the history-gap or trimmed-row text.

### Implementation for User Story 3

- [X] T016 [US3] Preserve the existing visit lifecycle in `dashboard/src/lib/log-trail.svelte.ts`. "`Clear | Empty the whole buffer, including tag-hidden rows. Keep selectedTag.`" `setMode` and node changes do not replace `selectedTag`. Gap, trim, local-only, and disconnected fields stay on the visit and are not rewritten as consequences of `selectedTag`. Satisfy T014.
- [X] T017 [US3] Update `dashboard/src/routes/logs/+page.svelte` so the tag column and tag control wrap at desktop and narrow widths, keyboard focus stays visible, and a tag change keeps the reading position when the anchor row remains visible. When the anchor row is hidden by the filter, show the oldest remaining visible row instead of jumping to the newest edge unless the operator was already following. Run T014 and T015, and record the US3 results in `specs/007-log-source-tags/quickstart.md`.

**Checkpoint**: All three stories work. Stored logs and a second Logs page stay unchanged by Clear or by the tag filter.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Document the operator-visible behavior and run the quickstart checks.

- [X] T018 [P] Update the Logs page section of `docs/cli.md` so it describes the UI, Inference, and Unclassified tags and the All / UI / Inference / Unclassified filter. State that `kllm logs --source` remains `agent|llama|rpc` and that there is no `--tag` flag. Preserve unrelated edits already in the file.
- [X] T019 [P] Update the Logs section of `docs/troubleshooting.md` so a tag filter with no rows is a selection result, distinct from an outage, a cleared display, a healthy empty trail, and a history gap. Preserve unrelated edits already in the file.
- [X] T020 Run the automated commands in `specs/007-log-source-tags/quickstart.md`: `uv run pytest -q tests/test_cluster_log.py`, `node --test --test-concurrency=1 dashboard/tests/logs.test.mjs dashboard/tests/usage-workspace.test.mjs`, then `npm --prefix dashboard run check` and `npm --prefix dashboard run build` sequentially. Also run `uv run pytest -q`. Record exact results in `specs/007-log-source-tags/quickstart.md`, and separate pre-existing failures from regressions. An unavailable browser is skipped, not passed.
- [X] T021 Review the feature diff against `specs/007-log-source-tags/spec.md`, `specs/007-log-source-tags/contracts/logs.md`, and `.specify/memory/constitution.md`. Confirm there is no tag query, no `--tag` flag, no journal migration, and no new dependency. Record the manual SC-001 check (four of five operators switch to inference-only, then UI-only, within 20 seconds) as unverified until it is actually run.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup. Blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational. No dependency on US2 or US3.
- **User Story 2 (Phase 4)**: Depends on Foundational and on the US1 row label, because it edits the same page and controller.
- **User Story 3 (Phase 5)**: Depends on the US2 control so node, refresh, and Clear can be checked with a tag selected.
- **Polish (Phase 6)**: Depends on the stories being implemented.

### User Story Dependencies

- **US1 (P1)**: Starts after T002 and T003. T004 and T005 can be written together. T006 and T007 can then proceed together. T008 follows T006. T009 follows T007 and T008.
- **US2 (P1)**: Starts after the US1 checkpoint. T010, then T011, then T012, then T013. Independently testable with a buffered mixed trail.
- **US3 (P2)**: Starts after the US2 checkpoint. T014 and T015 can be written together. T016 follows T014. T017 follows T015 and T016.
- Do not edit `dashboard/src/lib/log-trail.svelte.ts`, `dashboard/src/routes/logs/+page.svelte`, or the same test file from two stories at once.

### Within Each User Story

- Write the new checks and confirm they fail before implementation.
- Keep the hub mapping in the foundational phase; stories consume it.
- Controller behavior lands before the page wiring that depends on it.
- Run that story's checks before its checkpoint.

### Parallel Opportunities

- T002 and T003 touch different files.
- T004 and T005 touch different test files.
- T006 and T007 touch different runtime files after those tests exist.
- T014 and T015 touch different test files.
- T018 and T019 touch different docs.

---

## Parallel Example: User Story 1

```text
After Phase 2:
  T004: dashboard/tests/logs.test.mjs
  T005: dashboard/tests/live-updates.test.mjs

After those checks fail for the missing tag behavior:
  T006: dashboard/src/lib/log-trail.svelte.ts
  T007: dashboard/src/lib/live-updates.ts

Then T008: dashboard/src/routes/logs/+page.svelte
Then T009: dashboard/tests/usage-workspace.test.mjs
```

## Parallel Example: User Story 2

```text
After the US1 checkpoint:
  T010: dashboard/tests/logs.test.mjs
  T011: dashboard/src/lib/log-trail.svelte.ts
  T012: dashboard/src/routes/logs/+page.svelte
  T013: dashboard/tests/usage-workspace.test.mjs
```

## Parallel Example: User Story 3

```text
After the US2 checkpoint:
  T014: dashboard/tests/logs.test.mjs
  T015: dashboard/tests/usage-workspace.test.mjs

Then T016: dashboard/src/lib/log-trail.svelte.ts
Then T017: dashboard/src/routes/logs/+page.svelte
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete Phase 3.
3. Stop and validate US1: every row shows the producer tag, and a bad tag does not move the cursor.

US1 is a usable viewing increment. The filter requested for this feature arrives in US2.

### Incremental Delivery

1. Foundation: the hub assigns `ui`, `inference`, or `unclassified` from the producer source.
2. US1: the Logs page shows that tag.
3. US2: the page filters the current visit by tag.
4. US3: node, refresh, Clear, gaps, and keyboard/narrow layout still behave as they do today.
5. Polish: operator docs and the quickstart evidence.

### Parallel Team Strategy

After Phase 2, one person can own `dashboard/src/lib/log-trail.svelte.ts` and another can own `dashboard/src/lib/live-updates.ts` for US1. Later stories share the trail controller and the Logs page, so those files stay with one editor at a time.

---

## Notes

- [P] tasks use different files and do not depend on an unfinished task.
- Story labels map to `specs/007-log-source-tags/spec.md`.
- Each story is independently testable at its checkpoint.
- This command generates tasks only. It does not implement, commit, or create a branch.

## Coverage

| Scope | Task IDs | Count |
|-------|----------|-------|
| Setup | T001 | 1 |
| Foundation | T002–T003 | 2 |
| US1 | T004–T009 | 6 |
| US2 | T010–T013 | 4 |
| US3 | T014–T017 | 4 |
| Polish | T018–T021 | 4 |
| **Total** | **T001–T021** | **21** |

## Phase 7: Convergence

- [X] T022 Show the tag-filter and combined node-and-tag empty copy in `dashboard/src/routes/logs/+page.svelte` only when the visit buffer already holds rows, and add a regression check that an empty visit keeps the empty-visit copy when a tag is selected per FR-010 (partial)
