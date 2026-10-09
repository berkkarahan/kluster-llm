# Research: Search Hugging Face URLs

**Created**: 2026-09-29
**Specification**: [spec.md](spec.md)
**Status**: Technical research complete; constrained UI reference generated and reviewed.

Two read-only research agents examined the shared Python resolver/download boundary and
the dashboard/store/test boundary. Findings were checked against the affected source,
installed library signatures, package scripts, and constitution. No live Hub requests,
model downloads, runtime tests, or GPU checks were needed for this planning research.

## 1. Fix the shared resolver rather than add a URL-only frontend path

**Decision**: Extend the existing dictionary returned by `parse_model_search_query` and
the shared `_parse_hf_url` helper with separate revision, folder, and exact-file selectors.
Keep both search and `download_model` using that interpretation. The UI continues passing
the selected `source` unchanged to the existing download endpoint.

**Rationale**: `_parse_hf_url` currently discards revisions; tree folders and unsupported
page routes become whole-repository lookups. `_files_for_suffix` can substitute a different
folder's basename or invent a nonexistent result. `_inspect_repo_ggufs` swallows every
lookup failure. The requested behavior already exists in part; these are shared defects.

**Alternatives considered**: Client-side URL parsing duplicates the Python CLI/download
path. A separate resolver service or new source syntax adds unnecessary migration work.

**Evidence**: `python/kllm/models.py:parse_model_search_query`, `_parse_hf_url`,
`_files_for_suffix`, `_inspect_repo_ggufs`, `search_hub_ggufs`, `download_model`;
`python/kllm/agent/__init__.py:search_models`, `_download_job_runner`;
`python/kllm/cli.py:model_download`.

## 2. Use existing Hub metadata and canonical URLs

**Decision**: Use `model_info(repo_id, revision=revision, files_metadata=True,
timeout=15.0, token=token_or_false)`. Construct an exact selected source with
`hf_hub_url(repo_id, filename, revision=revision)` when it originates from a Hub URL.
No dependency changes.

**Rationale**: Installed and locked `huggingface_hub` 1.32.0 already supports both calls.
`model_info` provides all revision-specific siblings and sizes in one metadata lookup.
`hf_hub_url` does no network I/O, quotes revision slashes, and preserves file separators.
Its default revision is `main`, the Hub's default for links with no explicit revision.
The transport timeout is not an end-to-end search deadline: retries and multi-repository
keyword searches can take longer. Do not claim a measured performance improvement.

**Alternatives considered**: Scraping HTML, cloning repositories, binary probing, and one
HEAD request per Hub file add requests without improving this metadata-based feature.

**Evidence**: `pyproject.toml`, `uv.lock`; installed
`huggingface_hub.hf_api.HfApi.model_info` and `huggingface_hub.file_download.hf_hub_url`.
The project uses Python 3.12 while its package supports Python >=3.10.

## 3. Define URL identity without guessing revision boundaries

**Decision**: Split encoded URL path segments before decoding once. The segment immediately
after `tree`, `blob`, `resolve`, or `raw` is the revision. A revision containing `/` must
encode it as `%2F`; remaining path components identify the folder or file. File selectors
are exact and case-sensitive. `show_file_info` is repository-relative, is decoded once by
query parsing, and loses precedence to an explicit file route.

Repository and tree-root links list eligible descendants. Folder links use a slash-boundary
prefix, so `foo/` never selects `foobar/`. An empty tree folder is not tracked by Git;
at least one sibling of any type under its prefix proves that folder exists. Existing
folders containing only unsupported files yield an empty success; absent prefixes fail.

Use parsed case-insensitive HTTP(S) scheme/hostname for website recognition. Supported
website hosts are `huggingface.co`, `hf.co`, and their `www` aliases, with default ports.
Retain existing `/model/ORG/REPO` and `/models/ORG/REPO` prefixes when structurally valid.
Reject userinfo, non-default ports, invalid repo IDs, incomplete file routes, unsupported
website routes, traversal components, backslashes, controls, and empty internal path
components. Dataset/Space/collection routes must not become model repositories.
Use existing Hub repo-ID validation and POSIX semantics for Hub paths.

Ignore presentation parameters and fragments on Hub URLs; reject an active blank or
duplicate `show_file_info` and unsupported `?revision=` rather than broaden the search.
Reject malformed percent escapes. Decoded repository components cannot contain `/`;
decoded revision slashes are allowed; decoded file/folder separators define validated
path components. An encoded literal percent must survive a single decode/re-encode.

**Rationale**: Unencoded `/tree/a/b/c` cannot distinguish revision `a/b` from revision
`a` plus folder `b/c`. Probing alternative interpretations risks selecting a different
valid file. Platform filesystem parsing must not alter a remote POSIX path.

**Alternatives considered**: Longest-branch-prefix probing, twice-unquoting query values,
and basename fallback are ambiguous and require additional requests or select wrong files.

**Evidence**: `python/kllm/models.py:_hf_show_file_info`, `_parse_hf_url`, `_is_hf_host`;
`tests/test_models.py:test_parse_search_query_show_file_info`.

## 4. Keep confirmed options and expose failures

**Decision**: Apply `_candidate_ggufs` before every Hub selection mode, including exact
URLs. A confirmed option means a real metadata sibling satisfying the current standalone
GGUF policy, not a guarantee of loadability or permission at a later download time.
Missing or unsupported explicit files are actionable errors. Remove synthetic fallback
for recognized Hub website inputs. Preserve quant filtering and keyword search caps.

Explicit repository/URL metadata failures propagate as sanitized errors. During keyword
search, successful candidate results survive other candidates' failures, accompanied by
an additive `warnings` array. If any candidate failed and no results can be returned,
report failure rather than an apparently authoritative empty result. If every inspection
succeeded and no eligible option exists, return empty success.

**Rationale**: Existing tests intentionally retain useful keyword results despite one
inaccessible repository. Additive warnings preserve this while making partial coverage
explicit, as required by the constitution. Existing `searchError` handles full failures.

**Alternatives considered**: Swallowing all errors loses diagnostics; failing the first
keyword candidate regresses useful results; new error-code infrastructure is unnecessary.

**Evidence**: `python/kllm/models.py:_candidate_ggufs`, `_is_skipped_gguf`,
`_synthetic_url_hit`, `search_hub_ggufs`; `tests/test_models.py:_fake_hub_hits`,
`_fake_orca_info`; `tests/test_dashboard_api.py:_patch_hub_search`.

## 5. Preserve wire compatibility and direct-URL behavior

**Decision**: Keep the existing `filename` basename, numeric `size_bytes` (`0` means
unknown), and all existing result fields. Add nullable `path` and `revision`, plus optional
response `warnings`. The UI renders unknown values explicitly and uses `path ?? filename`.
Older servers may omit all additions. Existing result sources remain opaque strings.

URL-derived Hub results use canonical HTTPS resolve URLs carrying full revision/path.
Existing repo/keyword sources retain `org/repo:path` unless a filename such as `Q4_K_M.gguf`
would be reinterpreted as a quant selector; that case uses an exact canonical URL too.
Download parses the same revision/path and does not reconstruct a default-revision URL.
Do not silently download a repository/tree HTML page when a file is required.

External direct HTTP(S) URLs retain the existing best-effort size behavior and original
query string. They are not Hub-metadata-confirmed; the confirmed-file rule applies to
Hub repository resolution. This preserves the explicit compatibility requirement rather
than inventing universal binary validation. Arbitrary Hub CDN subdomains are direct URLs,
not repository websites. Attach configured Hub credentials only to recognized, canonical
Hub operations; never add them to unrelated direct URLs or reveal them in error messages.

**Rationale**: Changing basename semantics or replacing numeric unknowns with null would
break existing consumers unnecessarily. Canonical URLs already fit `DownloadBody.source`.

**Alternatives considered**: Replacing `filename` with the path, making size nullable,
introducing `repo@revision:file`, and adding download request fields were rejected.

**Evidence**: `dashboard/src/lib/types.ts:ModelSearchHit`, `ModelSearchResponse`;
`dashboard/src/lib/store.svelte.ts:searchModels`, `downloadModel`;
`python/kllm/agent/__init__.py:download_model`.

## 6. Keep the UI lifecycle small; route visual decisions through the requested workflow

**Decision**: Reuse the form, result collection, download panel, and error state. Guard
duplicate searches in `searchModels`, use existing `searching` for search lifecycle, and
clear result-associated metadata/warnings together at each accepted submission. Support
one in-flight search, not superseding parallel searches or a new controller. Closing the
result view does not start a second request. Interpret results for the submitted input.

The user subsequently fixed the UI boundary explicitly: retain the current bar and keep
Search opening the existing downloadable-results modal. This rules out inline results,
a new results route and bar/navigation restyling. Modal metadata and failure states remain
within the requested design handoff; the container choice is no longer open.

Visual/interaction design is delegated through the requested MCP workflow. After the
brief card was opened, the user supplied the fixed bar/modal constraints and selected
local execution. Local Codex generated the reference and corrected an initial geometry
mismatch; [design.md](design.md) records the desktop/mobile review. Existing
`dashboard/DESIGN.md` supplies context, not a substitute for the feature-specific artifact.

**Rationale**: The existing page already guards global busy state; a matching store guard
avoids parallel-result races without a request scheduler. Any broader polling fixes belong
to concurrent feature 005, not this search change.

**Alternatives considered**: New page/route, frontend URL parsing, replacement download UI,
and hand-designed mockups bypassing the requested workflow were rejected.

**Evidence**: `dashboard/src/routes/models/+page.svelte:runSearch`, `downloadRow`;
`dashboard/src/lib/store.svelte.ts:searchModels`; `dashboard/DESIGN.md`.

## 7. Verification and bounded related issues

**Decision**: Extend existing pytest and Node/Vite test patterns; add a focused Models
browser test using the existing Chrome/Edge CDP approach. Exercise a real search response
through the real download route with only external Hub/HTTP/GPU boundaries controlled.
Test input forms, revision/path fidelity, empty/error/partial results, and legacy inputs.
Validate keyboard/mobile behavior against the eventual design artifact.

The agent currently treats any retained download progress snapshot as busy, even after a
terminal state. Cover a second selected download after completion/error/cancellation;
correct this narrow active-versus-terminal guard if still present during implementation.
Keep concurrent-active rejection and existing lock/executor behavior. Do not redesign job
scheduling. Local filename collisions continue to follow existing basename storage rules;
this feature guarantees remote selection identity, not simultaneous archival of revisions.

**Rationale**: Parser tests alone cannot prove a selected revision reaches the downloader.
Repeated option selection is part of the existing workflow; a retained terminal snapshot
must not permanently block it. No new test framework or browser dependency is needed.

**Alternatives considered**: Live multi-GB downloads, GPU-dependent correctness tests,
SSR-only modal checks, and broad storage/job refactors were rejected.

**Evidence**: `tests/test_models.py`, `tests/test_dashboard_api.py`,
`tests/test_model_management_api.py:test_download_start_and_complete`;
`dashboard/tests/store.test.mjs`, `dashboard/tests/usage-workspace.test.mjs`;
`dashboard/package.json` has no `test` script.

## Resolution status

All technical unknowns are resolved above. The user supplied the UI constraints and chose
Local Codex. The generated/refined reference passed the recorded browser review, completing
the Phase 1 design dependency; real implementation verification remains outstanding.
