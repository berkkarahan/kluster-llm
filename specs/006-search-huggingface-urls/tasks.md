---
description: "Executable tasks for Hugging Face URL search in the existing MODELS modal"
---

# Tasks: Search Hugging Face URLs

**Created**: 2026-09-29
**Input**: Design documents from `specs/006-search-huggingface-urls/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contract](contracts/models-search.md),
[quickstart.md](quickstart.md), and the reviewed [design reference](design.md).

**Tests**: The project constitution requires behavior-focused regressions and affected
interface checks; the accepted plan/quickstart specifies their scope. Extend existing
pytest, Node/Vite and Chrome/Edge CDP patterns. Write the focused regression checks before
their fixes, observe the relevant failure, and make them pass. No new test framework,
live Hub dependency, GPU requirement or multi-GB download is needed.

**Organization**: Three independently verifiable user journeys, ordered P1, P1, P2.
Shared parser/source compatibility comes first so each increment has a usable download
path. Preserve the existing search bar, navigation, copy and spacing. Keyword and URL
Search must continue opening the existing modal; only result information/states change.

## Format: `[ID] [P?] [Story] Description`

- `[P]` marks disjoint-file work that can run concurrently after its stated prerequisites.
- `[US1]`, `[US2]`, `[US3]` identify story tasks; setup, foundation and polish are unlabelled.
- File paths are repository-relative. All tasks start unchecked; this document does not
  claim that the already-reviewed prototype implements application behavior.

## Path Conventions

- Shared Python behavior: `python/kllm/models.py`; existing agent boundary:
  `python/kllm/agent/__init__.py`; CLI caller: `python/kllm/cli.py`.
- Dashboard: `dashboard/src/routes/models/+page.svelte`,
  `dashboard/src/lib/store.svelte.ts`, `dashboard/src/lib/types.ts`.
- Existing Python tests: `tests/test_models.py`, `tests/test_dashboard_api.py`,
  `tests/test_model_management_api.py`; store tests: `dashboard/tests/store.test.mjs`.
- New focused browser check: `dashboard/tests/models.test.mjs`, using the existing
  `dashboard/tests/usage-workspace.test.mjs` pattern rather than a new framework.
- Record execution outcomes in `specs/006-search-huggingface-urls/quickstart.md`.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing environment and capture the source-faithful baseline;
reuse the established project rather than scaffold another application.

- [X] T001 Verify the existing development environment against `docs/agent-install.md`, `.python-version`, `pyproject.toml`, `uv.lock`, and `dashboard/package.json`; use the documented installer only if setup is missing, keep `SPECIFY_FEATURE_DIRECTORY=specs/006-search-huggingface-urls` and `SPECIFY_FEATURE_NO_PERSIST=1` process-scoped, and record environment/pre-existing failures in `specs/006-search-huggingface-urls/quickstart.md` without changing feature 005's selection or dependencies.
- [X] T002 Re-read the shared helper callers in `python/kllm/models.py`, `python/kllm/agent/__init__.py`, and `python/kllm/cli.py`; retrieve the full reviewed design bundle through the requested MCP workflow using `specs/006-search-huggingface-urls/design.md`, compare the current `dashboard/src/routes/models/+page.svelte`, `dashboard/src/routes/+layout.svelte`, and `dashboard/src/app.css` at 1280x720 and 390x844, and record the unchanged bar/navigation baseline in `specs/006-search-huggingface-urls/quickstart.md` before UI edits.

**Checkpoint**: Actual source/callers and the reviewed Local Codex reference are available;
the baseline accounts for the real 13px root and any concurrent feature 005 changes.
Further design decisions/refinements must use the requested MCP workflow.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish shared URL identity and additive types used by all three stories.
Complete this phase before story work; T003 and T004 are independent, T005 follows T003.

- [X] T003 [P] Extend `parse_model_search_query`, `_parse_hf_url`, `_hf_show_file_info`, and website recognition in `python/kllm/models.py`, with boundary regressions in `tests/test_models.py`, using the grammar in `specs/006-search-huggingface-urls/contracts/models-search.md`: `raw` is a "Trimmed submitted string" and "Nonempty"; `kind` retains "Existing `url`, `repo`, or `search`" with "Classification precedes shorthand colon splitting"; `url` is "Original direct/Hub URL or null" with "HTTP(S); website validation before use"; `repo_id` is "`owner/repository` or null" with "Validated Hub model repository ID"; `search_term` is "Keyword string or null" with "Existing keyword behavior"; `suffix` is "Existing shorthand filename/quant selector or null" with "Only shorthand uses quant inference"; `revision` is "Decoded Hub revision or null" with "`main` for default Hub lookup; encoded slashes accepted"; `folder` is "Decoded repository-relative folder or null" with "Valid POSIX components; slash-boundary recursive selection"; `filename` is "Exact decoded repository-relative file or null" with "Full case-sensitive path, never inferred as quant". Decode once, preserve explicit-file precedence over `show_file_info` and selector exclusivity, validate aliases/default ports/encoded revisions and POSIX paths, and reject all malformed/reserved website forms before network access; adapt both existing helper callers in the same change.
- [X] T004 [P] Extend `ModelSearchHit` and `ModelSearchResponse` in `dashboard/src/lib/types.ts` additively: retain "String or null" for `repo_id`/`quant`, "String" for `filename`/`source`, "Boolean" for `gated`, and the `fit` values "`fits`, `overflow`, `unknown`"; retain `size_bytes` as "Nonnegative integer" with "Existing zero sentinel for unknown; display unknown, not zero bytes"; add `path`/`revision` as "Optional string or null" and optional `warnings: string[]`; keep `total_vram_bytes` as "existing positive capacity or null" and older responses valid without the additions.
- [X] T005 Update the shared `download_model` URL branch in `python/kllm/models.py` and its regressions in `tests/test_models.py` to consume T003's exact revision/full-path identity via `hf_hub_url(..., revision=...)`, reject repository/tree-only download sources instead of saving HTML, and keep "Opaque selected-download identity; canonical exact URL for Hub URL input" intact; preserve signed external URLs without attaching configured Hub credentials, existing CLI exact refs without forced metadata listing, basename storage, cancellation, partial-file cleanup and sidecars.

**Checkpoint**: Shared parser and downloader agree on revision/path; invalid website input
fails safely; the existing shorthand/direct URL contracts and additive frontend types hold.
This shared work supports downloading US1's folder/revision results as well as US2's file
links; it does not implement exact-file metadata selection yet.

---

## Phase 3: User Story 1 — Browse downloadable options from a repository URL (Priority: P1) — MVP

**Goal**: Paste a repository/tree/folder URL into the unchanged bar, browse real eligible
GGUF options in the existing modal, and explicitly download the chosen source.

**Independent Test**: Use controlled metadata for `demo/model-GGUF`, with different
`main` and `release/v2` contents, nested paths, duplicate basenames, auxiliary/shard files,
unknown size and an empty/docs-only folder. Assert complete eligible results, correct
revision/subtree, unchanged bar/modal interaction, no transfer during search, and an
explicit selected-source download through the real route/runner/downloader.

### Required verification for User Story 1

Author T006–T009 concurrently if useful; they touch different test files. Run checks
serially where the Node/Vite harness shares generated SvelteKit state.

- [X] T006 [P] [US1] Add repository/tree/folder URL regressions in `tests/test_models.py`: assert revision-specific metadata with `files_metadata=True`, `timeout=15.0` and configured token/False, slash-boundary recursive folder selection, more than 40 eligible files for explicit lookup, standalone-GGUF filtering, docs-only empty versus absent-folder error, known/unknown sizes and memory fit, no discovery call for explicit targets, and no HEAD-per-file or transfer during search.
- [X] T007 [P] [US1] Extend `tests/test_dashboard_api.py` with a real repository/tree search-to-selected-download boundary case: control only external Hub/HTTP/GPU boundaries and completion timing, submit the returned source to the real download route/runner/downloader, and assert preserved revision/full path, tiny saved bytes and existing response/progress/sidecar contracts; include empty success and isolate/reset process-global state between scenarios.
- [X] T008 [P] [US1] Add retained-terminal-snapshot regressions in `tests/test_model_management_api.py` proving a new selected download is accepted after complete/error/cancelled, an active transfer still returns 409, and cleanup/cancellation behavior remains valid without resetting production state to make each request pass.
- [X] T009 [P] [US1] Extend `dashboard/tests/store.test.mjs` for trimmed query submission, unchanged selected-source forwarding and old/new result shapes; create `dashboard/tests/models.test.mjs` using the existing CDP approach to assert keyword/URL click and Enter open the modal, the recorded bar/navigation baseline is preserved, full duplicate-basename paths and unknown values are readable, only Download sends a transfer request, and keyboard/mobile behavior holds at 1280x720 and 390x844.

### Implementation for User Story 1

- [X] T010 [US1] Implement repository/revision/folder metadata selection in `_inspect_repo_ggufs`, `_hit_row`, and `search_hub_ggufs` in `python/kllm/models.py`: use one revision-specific `model_info` call with a 15-second transport timeout, establish folder existence from any sibling, apply `_candidate_ggufs`, return every eligible explicit-target match without the keyword cap with `results` retaining "zero or more supported options for successful inspection", emit safe explicit-target errors rather than fabricated/empty failures, preserve basename/gated/fit semantics and nonnegative integer sizes with zero for unknown, and add full `path`/effective `revision` with canonical exact URL sources for URL-derived options (null path/revision for external direct inputs); keep metadata credentials out of returned rows.
- [X] T011 [US1] Integrate additive results into `searchModels` in `dashboard/src/lib/store.svelte.ts`: reuse the current fields plus `searchWarnings`, retain one in-flight search, implement "Set searching; clear results, capacity, warnings and error together", preserve `query` as "the accepted trimmed query, not later unsubmitted input edits", default absent warnings to `[]`, and retain "Hide results only; retain single-flight guard until completion" when the modal closes; keep `downloadModel` forwarding the chosen source without parsing and avoid unrelated global-polling changes.
- [X] T012 [US1] Adapt only the existing results modal in `dashboard/src/routes/models/+page.svelte` from the reviewed reference in `specs/006-search-huggingface-urls/design.md`: use `path ?? filename`, preserve rows keyed by `source`, show repository/revision/quant/known-or-unknown size and textual heuristic fit, retain explicit Download and modal open/close behavior, and provide modal naming, focus containment/restoration, announcements and full-path wrapping; preserve all current bar/navigation copy, controls, placement, root-relative spacing and token-row geometry, and exclude prototype controls/fixtures/resolver shortcuts.
- [X] T013 [US1] Correct the narrow active-versus-retained-terminal download guard in `python/kllm/agent/__init__.py` if still present, making "A terminal retained snapshot must not be mistaken for an active transfer when selecting the next result; active transfers still reject an additional request" true while preserving the existing executor/lock, progress response and 409 conflict behavior; verify with `tests/test_model_management_api.py` without redesigning scheduling.
- [X] T014 [US1] Run the US1 Python, store and actual-page browser checks from `specs/006-search-huggingface-urls/quickstart.md`, verify SC-001/004/006 including the real selected-download boundary, and record commands/results and any genuine environment skips in that file before declaring the MVP complete.

**Checkpoint**: Repository/tree browsing and explicit downloads are usable end-to-end;
both keywords and URLs still open the existing modal from the unchanged bar.

---

## Phase 4: User Story 2 — Find the exact model from a file URL (Priority: P1)

**Goal**: A shared file URL selects one confirmed eligible file with its exact revision
and path, without basename substitution or synthetic missing-file results.

**Independent Test**: Submit `blob`, `resolve`, `raw`, and `show_file_info` forms for
nested files at `release/v2`, including duplicate basenames and literal `Q4_K_M.gguf`.
Verify one correct row and the same source at the actual transfer boundary. Missing,
wrong-case and unsupported files must produce feedback and no downloadable option.

### Required verification for User Story 2

- [X] T015 [P] [US2] Extend `tests/test_models.py` with exact-file selection regressions for `blob`/`resolve`/`raw`/`show_file_info`, explicit-path precedence, encoded revision slashes, spaces/plus/literal percent and single decoding, duplicate/wrong-case/missing paths, literal `Q4_K_M.gguf`, and projector/imatrix/shard/non-GGUF exclusions; retain CLI shorthand quant normalization, ambiguity behavior and exact-ref-without-listing checks.
- [X] T016 [P] [US2] Extend the real boundary coverage in `tests/test_dashboard_api.py` for an exact file URL at a non-default revision: feed its actual search response source into the real download route/runner, assert the full encoded revision/path reaches streaming, and verify missing/unsupported exact targets yield HTTP 400 with no synthetic row or transfer request.
- [X] T017 [P] [US2] Extend `dashboard/tests/models.test.mjs` with an exact-file URL journey that displays one full path/revision row in the retained modal, preserves the selected source in the Download request, and does not offer an unrelated same-basename file; use controlled responses and preserve the US1 bar baseline.

### Implementation for User Story 2

- [X] T018 [US2] Implement exact-file metadata selection in `_files_for_suffix`, `_inspect_repo_ggufs`, and `search_hub_ggufs` in `python/kllm/models.py`: enforce "Full case-sensitive path, never inferred as quant", apply standalone eligibility before selection, reject absent/unsupported exact files, remove cross-folder and nonexistent-file fallback, and never call `_synthetic_url_hit` for recognized Hub website inputs; emit canonical exact sources when a legacy result filename could be reinterpreted as quant while preserving legitimate shorthand search behavior.
- [X] T019 [US2] Run the exact-file resolver/API/browser regressions and existing CLI-source checks in `tests/test_models.py`, `tests/test_dashboard_api.py`, and `dashboard/tests/models.test.mjs`; record SC-002/003 results in `specs/006-search-huggingface-urls/quickstart.md`, including the actual captured transfer URL rather than only parser assertions.

**Checkpoint**: File URLs work as their own user journey without first browsing a
repository; exact remote identity survives all boundaries and unavailable files never
become selectable options.

---

## Phase 5: User Story 3 — Recover from lookup problems and retain existing search (Priority: P2)

**Goal**: Distinguish empty results from invalid/unavailable/access/network failures,
expose partial keyword coverage, allow corrected retries, and preserve legacy input forms.

**Independent Test**: With controlled upstream outcomes, exercise invalid input,
gated/private/missing targets, missing revisions, timeout and rate limit, then correct/retry.
Verify safe feedback, zero fabricated options, partial keyword warnings and successful
legacy keyword/repo/quant/direct-URL searches without credential exposure.

### Required verification for User Story 3

- [X] T020 [P] [US3] Extend `tests/test_models.py` and `tests/test_dashboard_api.py` with the contract's complete validation/error matrix, safe token-aware access, missing revision/folder/file distinctions, timeout/rate-limit handling, partial keyword success, zero rows plus inspection failure, all-success empty results, ranking/20-8-40 caps, legacy shorthand and unchanged signed external URLs; assert malformed website inputs make no network calls and tokens/headers/upstream signed URLs never enter returned errors or warnings.
- [X] T021 [P] [US3] Extend `dashboard/tests/store.test.mjs` and `dashboard/tests/models.test.mjs` with deferred duplicate submissions, close-while-pending, HTTP/transport failure, empty success, partial warnings and corrected retries; verify stale rows/capacity/warnings clear together, older responses without additions still work, states remain modal-only and no error/retry starts a download.

### Implementation for User Story 3

- [X] T022 [US3] Complete explicit and keyword failure handling in `python/kllm/models.py` and `python/kllm/agent/__init__.py`: retain the existing HTTP 400 `{detail: string}` search envelope, sanitize categorized errors with safe repo/path/revision context, catch gated errors before their repository-error superclass, preserve successful keyword rows with optional `warnings: string[]`, implement "No results plus at least one failed inspection is a full failure; no results after entirely successful inspection is empty success", retain ranking/caps, and keep original external direct sources/queries without configured Hub authorization.
- [X] T023 [US3] Complete result/error/partial transitions in `dashboard/src/lib/store.svelte.ts`: honor warnings as "sanitized partial keyword-search failures; omitted or empty means none", on failure "Set actionable error; keep result-associated state empty; clear searching", on retry use the same accepted-submission reset, and retain duplicate/busy guards without adding superseding requests or modifying feature 005's global refresh architecture.
- [X] T024 [US3] Complete loading, successful-empty, invalid, unavailable, gated, connection-failure and partial-warning feedback inside the existing modal in `dashboard/src/routes/models/+page.svelte`, using the reviewed states in `specs/006-search-huggingface-urls/design.md`; preserve retry through the existing bar, announce status/errors accessibly, and keep all bar/navigation geometry and controls unchanged.
- [X] T025 [US3] Run the failure, compatibility and retry checks in `tests/test_models.py`, `tests/test_dashboard_api.py`, `dashboard/tests/store.test.mjs`, and `dashboard/tests/models.test.mjs`; record SC-003/005 and FR-008/009/010/011 evidence in `specs/006-search-huggingface-urls/quickstart.md`, including partial coverage and secret-redaction assertions.

**Checkpoint**: Every story is independently exercisable; failures are actionable,
retries recover, old inputs remain usable, and explicit Hub targets never fabricate hits.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Document the implemented contract and verify the integrated change.

- [X] T026 [P] Update existing Dashboard/model-download guidance in `docs/cli.md` with repository/tree/file URLs, encoded revision boundaries, full-path selection, unknown metadata, explicit modal Download actions and preserved inputs; distinguish dashboard keyword searches from CLI source syntax and retain the master-local download workflow.
- [X] T027 [P] Update model lookup guidance in `docs/troubleshooting.md` to distinguish empty eligible results from unsupported links, unavailable repo/revision/path, gated/token access, timeout/rate-limit and partial keyword failures, with safe retry guidance and no credential-disclosing examples.
- [X] T028 Execute the integrated commands in `specs/006-search-huggingface-urls/quickstart.md`: `uv run pytest -q tests/test_models.py tests/test_dashboard_api.py tests/test_model_management_api.py`, `node --test --test-concurrency=1 dashboard/tests/*.test.mjs`, then sequentially `npm --prefix dashboard run check` and `npm --prefix dashboard run build`; broaden to `uv run pytest -q` when shared-call changes/failures justify it, record actual OS/browser coverage and skips, and verify all SC-001–SC-006 without claiming live-Hub/GPU validation.
- [X] T029 Audit the final diff in `python/kllm/models.py`, `python/kllm/agent/__init__.py`, `dashboard/src/lib/store.svelte.ts`, `dashboard/src/lib/types.ts`, and `dashboard/src/routes/models/+page.svelte` against `specs/006-search-huggingface-urls/contracts/models-search.md` and `specs/006-search-huggingface-urls/design.md`; confirm shared caller compatibility, exact sources, safe failures, unchanged bar/navigation and no prototype scaffolding or unrelated feature 005 changes, then record remaining limitations and final requirement coverage in `specs/006-search-huggingface-urls/quickstart.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

```text
T001 -> T002
          |
          +-> T003 --+-> T005 --+
          |         |         |
          +-> T004 -+---------+-> US1 (T006–T014)
                                  |
                                  v
                              US2 (T015–T019)
                                  |
                                  v
                              US3 (T020–T025)
                                  |
                         T026 || T027
                                  |
                             T028 -> T029
```

- T003/T004 may be authored concurrently; T005 depends on T003. All three foundational
  tasks finish before US1 tests/implementation begin.
- US1: T006–T009 are disjoint test-authoring tasks. T010 follows their authoring and T005;
  T011 follows T004/T009/T010; T012 follows T009/T011; T013 follows T008/T010;
  T014 waits for T010–T013 and all US1 checks to pass.
- US2: T015–T017 may be authored concurrently after US1. T018 follows those regressions;
  T019 waits for the fix and all US2 checks.
- US3: T020/T021 may be authored concurrently after US2. T022 follows T020;
  T023 follows T021/T022; T024 follows T023; T025 waits for all US3 work.
- T026/T027 can proceed together after US3's contract is stable. T028 then T029 close
  the integrated release checks and scope review.

### User Story Dependencies

Use **US1 -> US2 -> US3** as the integration order because they edit the same Python
helpers, store and page. This is an editing dependency, not a required operator sequence:
an exact-file URL does not require a prior repository search, and a failure/retry case
does not require prior successful discovery. Each story has its own executable checkpoint.
Do not mark overlapping story implementations as parallel just because their labels differ.

### Parallel Opportunities

The safe `[P]` groups are T003/T004, T006/T007/T008/T009, T015/T016/T017,
T020/T021, and T026/T027. Markers refer to **authoring disjoint files after prerequisites**;
they do not authorize concurrent writes to a shared test fixture or simultaneous SvelteKit
check/build operations. Run Node/Vite checks sequentially where generated state is shared.
No subagents are required to execute this task list.

## Parallel Example: User Story 1

After foundation, independently author repository metadata regressions (T006,
`tests/test_models.py`), API round-trip coverage (T007, `tests/test_dashboard_api.py`),
terminal-job coverage (T008, `tests/test_model_management_api.py`) and dashboard coverage
(T009, `dashboard/tests/store.test.mjs` plus `dashboard/tests/models.test.mjs`). Reuse
existing local fixture patterns; then implement and validate in the dependency order above.

## Parallel Example: User Story 2

After US1, independently author exact-file resolver tests (T015, `tests/test_models.py`),
actual API/transfer checks (T016, `tests/test_dashboard_api.py`) and exact-file modal
interaction checks (T017, `dashboard/tests/models.test.mjs`). T018 waits for these; run
the shared-file implementation serially and validate it with T019.

## Parallel Example: User Story 3

After US2, independently author backend error/compatibility coverage (T020,
`tests/test_models.py` and `tests/test_dashboard_api.py`) and frontend retry/state coverage
(T021, `dashboard/tests/store.test.mjs` and `dashboard/tests/models.test.mjs`). Implement
the agreed response behavior in T022–T024, then run the story checkpoint T025.

## Requirement Coverage

| Requirement | Main task coverage |
|-------------|--------------------|
| FR-001 existing field accepts URLs | T003, T009–T012 |
| FR-002 repository/revision/folder options | T003, T006, T007, T010 |
| FR-003 exact path/revision through download | T005, T007, T015–T019 |
| FR-004 supported hosts and URL forms | T003, T006, T015, T018, T020 |
| FR-005 distinguishable metadata and fit | T004, T006, T009–T012 |
| FR-006 explicit-only selected download | T005, T007–T014, T016, T017 |
| FR-007 confirmed eligible Hub files only | T006, T010, T015, T016, T018, T020, T022 |
| FR-008 loading/empty/error distinctions | T010–T012, T020–T025 |
| FR-009 configured access without disclosure | T003, T005–T007, T020, T022, T025 |
| FR-010 preserve legacy inputs | T003–T005, T015, T020–T025 |
| FR-011 latest submitted result and retry | T009, T011, T021, T023–T025 |
| FR-012 unchanged bar/navigation and modal | T002, T009, T012, T017, T021, T024, T028, T029 |

The constraint quotations in T003–T005, T011, T013, T018 and T022–T023 come directly
from [data-model.md](data-model.md). There are no new persistent entities, migrations,
field-length limits or configuration knobs to invent.

## Implementation Strategy

### MVP First (User Story 1 Only)

Complete T001–T014. Demonstrate repository/tree URL -> existing modal -> selected download
with preserved path/revision, visible metadata and unchanged bar. This is a useful tested
increment; it does not claim completion of exact-file and full failure/compatibility work.

### Incremental Delivery

1. Setup/foundation: validated shared URL identity, download compatibility and additive types.
2. US1: complete repository/folder browsing and explicit download; validate independently.
3. US2: exact-file selection with no substitution/fabrication; validate independently.
4. US3: actionable/partial outcomes, retry and complete legacy compatibility; validate.
5. Documentation and integrated verification: finish T026–T029 before declaring the feature done.

### Parallel Team Strategy

Use the disjoint authoring groups above if multiple contributors are available. Keep a
single integration owner for `python/kllm/models.py`, the store and the Models page.
Coordinate with feature 005 when touching `dashboard/src/lib/store.svelte.ts`; preserve
its work and incorporate its current lifecycle instead of overwriting the file wholesale.

## Notes

- Total: **29 tasks** — setup 2, foundation 3, US1 9, US2 5, US3 6, polish 4.
- Existing implementation is partial; inspect before adding code and reuse helpers.
  A task can be satisfied by verified existing behavior; do not duplicate it for task count.
- Do not copy prototype fixture data, inferred URL shortcuts or scenario controls into
  production. The reviewed modal appearance is guidance; the contract governs behavior.
- Keep all checks deterministic and explicitly distinguish actual application checks from
  the completed prototype review. Unknown size and memory fit remain unknown, not promises.
- Task setup was scoped to this feature with `SPECIFY_FEATURE_NO_PERSIST=1`. Windows
  redirected output required `PYTHONIOENCODING=utf-8`; no setup script changes were needed.
- Do not automatically switch branches, commit other work, change the active feature
  pointer, alter model storage naming or add dependencies as part of these tasks.

## Phase 7: Convergence

- [X] T030 Show the accepted submitted query in the MODELS results modal in `dashboard/src/routes/models/+page.svelte`, retaining the search response `query` in `dashboard/src/lib/store.svelte.ts` so a later edit to the search field does not relabel the open results, empty state, or error, and cover that identity in `dashboard/tests/store.test.mjs` and `dashboard/tests/models.test.mjs` per FR-011 (partial). Keep the current search bar unchanged and do not add prototype scenario controls.
