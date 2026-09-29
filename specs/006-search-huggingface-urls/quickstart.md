# Validation Guide: Search Hugging Face URLs

**Created**: 2026-09-29
**Status**: Production checks recorded below on 2026-09-29. Prototype review remains in design.md.
**Specification**: [spec.md](spec.md)
**Contract**: [models-search.md](contracts/models-search.md)

## Prerequisites

- Use the established checkout and development environment. For a missing environment,
  follow [the agent install guide](../../docs/agent-install.md) and its primary
  `python scripts/install.py` flow; do not invent a separate installation procedure.
- Run the commands below from the repository root. Python uses `uv`; dashboard packages
  and an installed Chrome/Edge provide the existing Node/Vite/CDP test tools.
- Automated checks use isolated `KLLM_HOME`, fake tokens, fixed GPU capacity, controlled
  Hub responses, and tiny simulated transfer bytes. No live Hub, GPU, cluster or real model
  is needed to prove URL-selection behavior. Prevent accidental external network access.
- Use the completed [design handoff](design.md) for visual implementation and browser
  acceptance. A missing browser or an unexecuted check is reported as unverified, not a pass.

## Feature selection for downstream commands

Feature 005 is concurrent work, so downstream specification commands must explicitly
select this feature. In the terminal/session running those commands, use:

```powershell
$env:SPECIFY_FEATURE_DIRECTORY = 'specs/006-search-huggingface-urls'
$env:SPECIFY_FEATURE_NO_PERSIST = '1'
```

These are session-scoped settings; they intentionally avoid changing the other task's
`.specify/feature.json`. Git remains on `main`; the setup script's derived feature label
is not a newly created branch.

## Deterministic acceptance fixtures

Extend existing test fixtures rather than creating a separate service or stored dataset.
Use fake repository `demo/model-GGUF` and revisions `main` and `release/v2`. At the latter
revision, metadata contains:

- `model.Q8_0.gguf` at repository root;
- `gguf/model.Q4_K_M.gguf` and `other/model.Q4_K_M.gguf` (identical basenames);
- `gguf/nested/model.Q5_K_M.gguf`;
- `Q4_K_M.gguf` (a literal filename, not a quant request);
- `gguf/mmproj.gguf`, `gguf/imatrix.gguf`, and recognized split-shard names;
- `gguf/README.md` and `docs/README.md`;
- parameterized valid filenames containing a space, plus sign and literal percent sequence.

Give known sizes to most files and omit one size. Set fixture capacity to exercise fits,
overflow and unknown. Make the `main` fixture contain different files so silently dropping
the selected revision fails the test. Separate fixture modes produce gated/missing-repo,
missing-revision, timeout and rate-limit responses; they must not make real network calls.

## 1. Shared resolver and actual selected-download boundary

After implementing the checks in the existing Python test files, run:

```text
uv run pytest -q tests/test_models.py tests/test_dashboard_api.py tests/test_model_management_api.py
```

Required scenarios:

| Scenario | Expected observable outcome |
|----------|-----------------------------|
| Repository URL | Every eligible sibling is listed; no unsupported files or downloads |
| Tree revision URL | Metadata lookup uses the decoded selected revision, not main |
| Tree `gguf` folder | Includes eligible nested descendants; excludes `other/`, root files and `gguf-extra/` |
| `blob`, `resolve`, `raw`, `show_file_info` | Exact case-sensitive path, one verified option |
| Same basename in another folder | Never substituted for the requested missing file |
| Literal `Q4_K_M.gguf` URL | Exact file, not quant expansion |
| Unknown file size/capacity | Numeric unknown sentinel preserved; fit is unknown |
| Empty repo or existing docs-only folder | HTTP 200 with empty results, no error |
| Missing folder, file or revision | Actionable error, no synthetic row |
| Exact projector/shard/non-GGUF | Unsupported-model error, no download option |
| Restricted or unreachable repo | Safe actionable error distinct from empty success |
| Keyword results with one failed repo | Valid results retained and partial warning provided |
| Keyword zero results with a failed repo | Lookup error rather than authoritative empty success |
| Trimmed URL, encoded revision/path, casing/default port | Correct normalized identity; exactly one decode |
| Malformed/unsupported website URL | Validation error before metadata/transfer calls |
| External signed direct URL | Original source/query preserved; no configured Hub credential attached |
| Legacy keywords, repo references and quant filters | Ranking, eligible options and existing limits preserved |

The decisive boundary test must use the real search handler/helper, take its returned
`source`, POST it through the real download handler/runner and shared downloader, then
assert the captured stream URL preserves the selected revision and full path. Mock only
external metadata/streaming and hardware boundaries; use a controlled executor or events
to wait for completion. Verify saved tiny bytes and existing progress/sidecar behavior.
Reset process-global download state between scenarios and restore all patched globals.

Also assert a subsequent selection is accepted after complete/error/cancelled, while a
truly active transfer still returns 409. Existing cancellation/partial-file cleanup and
CLI exact-ref tests remain applicable. Do not fix this test by resetting real production
state before every request or by bypassing the actual route.

Cover invalid input without network requests and assert tokens/headers/signed upstream
URLs do not appear in returned error details or partial warnings. Use fake token strings
only. Run these deterministic checks on supported OS environments when available; report
the actual OS coverage, especially path/encoding behavior, rather than claiming unrun jobs.

## 2. Browser-store contract

Extend the existing Node/Vite harness and run:

```text
node --test dashboard/tests/store.test.mjs
```

Assert submitted query trimming, unchanged source forwarding, additive metadata/warning
handling, and old-server responses without the new fields. Deferred mock responses must
prove that a second submission while searching is not sent, that lifecycle resets on
success/error, and that corrected retries replace prior results/errors/warnings.
Verify lookup failures never initiate a download.

## 3. Browser interaction and design acceptance

The implementation adds the focused browser check identified in the plan. This command
is a future implementation gate; its proposed test file does not exist at planning time:

```text
node --test dashboard/tests/models.test.mjs
```

Reuse the Chrome/Edge CDP approach in `dashboard/tests/usage-workspace.test.mjs`, setting
`CHROME_BIN` if needed. The test should run the real Models page against controlled search,
download and progress responses, with no real models downloaded. SSR-only checks cannot
exercise the result view because the current modal begins closed.

At desktop 1280x720 and mobile 390x844:

1. Compare the current bar and navigation with the baseline: appearance, placement, copy
   and controls must be preserved. Paste a repository/tree URL and submit using Enter.
   The existing results modal opens with loading followed by expected downloadable options;
   no download request occurs yet. Repeat with a keyword search and the Search button.
2. Read both duplicate-basename paths and available metadata, including an unknown size.
3. Select one result and assert exactly its `source` is sent; show normal download status.
4. Repeat with exact-file and non-default-revision URLs.
5. Exercise empty, invalid, unavailable, restricted, connection-failure and partial-result
   states; correct an input and retry successfully.
6. Verify visible keyboard focus, accessible result/download identification, full-path
   reachability, textual fit/unknown states and suitable status/error announcements.
   Verify the retained modal's focus containment/restoration and Escape dismissal.
7. Verify controls remain usable without page-wide horizontal overflow. Compare against
   the actual generated design reference, not an assumed or unrelated prototype.

A mocked browser test proves frontend wiring, while the Python boundary test proves real
search-to-download integration. Do not describe either alone as a live Hub deployment test.

## 4. Final integration checks and documentation

Run sequentially to avoid competing SvelteKit generated-state changes:

```text
node --test --test-concurrency=1 dashboard/tests/*.test.mjs
npm --prefix dashboard run check
npm --prefix dashboard run build
```

There is no `npm test` script. Do not add a framework simply to run these checks.
Use `uv run pytest -q` as the wider Python gate if shared-model changes extend beyond the
targeted callers or reveal integration failures. No Rust/native-runtime changes are planned.

Update the existing Dashboard/model-download guidance in [docs/cli.md](../../docs/cli.md)
and model lookup troubleshooting in [docs/troubleshooting.md](../../docs/troubleshooting.md).
Document URL forms, encoded revision boundaries, full-path selection, empty/error handling,
partial keyword coverage, and compatibility with old inputs. Avoid suggesting CLI keyword
downloads: keyword search is a dashboard capability, not a new CLI source syntax.

Optional live smoke, after deterministic checks, uses an operator-chosen accessible
repository URL in `uv run kllm ui`. Compare the listed options with that repository; only
select Download when an actual model transfer is intended. Record the repository/revision,
OS and result without recording credentials. Live availability is not a deterministic gate.

## Evidence to record

- Exact commands, outcomes, OS/browser, and any skipped checks.
- SC-001/005: complete eligible lists and preserved legacy inputs.
- SC-002: selected revision/path observed at the actual stream boundary.
- SC-003: no fabricated option across all explicit failure cases.
- SC-004: paste, Search, Download completes the intended interaction.
- SC-006: unchanged bar/navigation and retained modal for both keyword and URL searches.
- Actual design artifact and review results: Local Codex generation/refinement and the
  separate desktop/mobile browser review are recorded in [design.md](design.md).
- Documentation link/contract checks during planning are not runtime validation results.

## Recorded validation (2026-09-29)

OS: Windows 10 (win32 10.0.26200). Python 3.12.13 via the repo virtualenv. Browser: headless Chrome discovered by the existing CDP harness (`CHROME_BIN` unset). No live Hugging Face lookup, GPU, or model transfer was used.

```text
uv run pytest -q tests/test_models.py tests/test_dashboard_api.py tests/test_model_management_api.py
node --test --test-concurrency=1 dashboard/tests/store.test.mjs dashboard/tests/models.test.mjs
node --test --test-concurrency=1 dashboard/tests/*.test.mjs
npm --prefix dashboard run check
npm --prefix dashboard run build
uv run pytest -q
```

Outcomes:

- Targeted Python: 86 passed.
- Models browser and store tests: passed, including the full dashboard Node suite (43 passed).
- `svelte-check`: 0 errors, 0 warnings.
- Dashboard production build: succeeded.
- Full `uv run pytest -q`: 422 passed, 3 failed in `tests/test_install_script.py` (`test_discards_cmake_build_with_dangling_openssl_path`, `test_dangling_linker_flag_alone_does_not_discard_build`, `test_cache_reset_flags_only_scrub_dangling_linker_flags`). Those failures are the existing CMake/OpenSSL cache checks and were not changed by this feature.

Coverage against the scenarios:

- SC-001 / SC-005: repository, tree, and folder lookups return every eligible standalone GGUF without the keyword 20/8/40 caps; keyword search keeps those caps and ranking. Encoded revision `release/v2`, default ports, and one-time path decoding are covered in `tests/test_models.py`.
- SC-002: `tests/test_dashboard_api.py` posts the search handler's returned `source` through the real download route and asserts the streamed Hub URL keeps the revision and full path.
- SC-003: missing file, folder, revision, unsupported GGUF, gated/unavailable repository, timeout, and rate-limit cases raise before any synthetic row. Empty and docs-only folders return HTTP 200 with `results: []`.
- SC-004: the Models browser test pastes a tree URL, submits with Enter, opens the existing modal, and sends a download only after the explicit Download control.
- SC-006: the same test compares the search bar, Search label, placeholder, navigation, and `gap-2` spacing before and after a keyword search at 1280x720, and checks 390x844 for page-wide overflow.

OpenDesign Cloud was not connected during implementation, so the reviewed handoff in design.md was the design source. The browser test checks the retained bar and the modal states from that handoff; it does not open a live design preview.

This work does not update the active feature pointer. Optional live Hub smoke was not run.
