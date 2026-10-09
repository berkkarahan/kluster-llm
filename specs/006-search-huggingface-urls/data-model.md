# Data Model: Search Hugging Face URLs

**Created**: 2026-09-29
**Specification**: [spec.md](spec.md)
**Contract**: [models-search.md](contracts/models-search.md)

All entities are transient values in existing helpers or browser state. No persistent
database/schema or new class hierarchy is needed.

## Search target

The existing parsed dictionary gains independent selectors instead of overloading `suffix`.

| Field | Type / meaning | Validation |
|-------|----------------|------------|
| `raw` | Trimmed submitted string | Nonempty |
| `kind` | Existing `url`, `repo`, or `search` | Classification precedes shorthand colon splitting |
| `url` | Original direct/Hub URL or null | HTTP(S); website validation before use |
| `repo_id` | `owner/repository` or null | Validated Hub model repository ID |
| `search_term` | Keyword string or null | Existing keyword behavior |
| `suffix` | Existing shorthand filename/quant selector or null | Only shorthand uses quant inference |
| `revision` | Decoded Hub revision or null | `main` for default Hub lookup; encoded slashes accepted |
| `folder` | Decoded repository-relative folder or null | Valid POSIX components; slash-boundary recursive selection |
| `filename` | Exact decoded repository-relative file or null | Full case-sensitive path, never inferred as quant |

The shared HF URL helper supplies the Hub identity/selectors to both search and download.
Exact-file routes take precedence over `show_file_info`; a file selector is mutually
exclusive with a folder filter. The tree revision survives file-information selection.
Repository-relative paths must not be absolute, contain traversal or backslashes, or
contain controls/empty internal components. Decode once; do not apply local filesystem
normalization to a remote identifier. Supported grammar is defined in the contract.

## Repository inspection

Input: validated repository, effective revision, optional folder/exact file, existing
shorthand suffix, and privately resolved access token.

Metadata siblings provide full paths and optional sizes. Apply existing standalone-GGUF
eligibility before selecting results. Folder existence can be established by any sibling
under its prefix; supported-file eligibility is a separate check. Exact-file existence
and eligibility failures are errors, not empty or invented options.

The configured token is request context, never a field in returned entities or messages.
Metadata access does not guarantee a future download will remain available.

## Downloadable option

| Field | Wire type | Meaning / compatibility |
|-------|-----------|-------------------------|
| `repo_id` | String or null | Existing repository identity; external direct results retain existing host identity |
| `filename` | String | Existing display basename; retain semantics |
| `path` | Optional string or null | New full repository-relative Hub file path; null for direct external URLs |
| `revision` | Optional string or null | New effective Hub revision; null for external URLs |
| `quant` | String or null | Existing quant label from the verified filename |
| `size_bytes` | Nonnegative integer | Existing zero sentinel for unknown; display unknown, not zero bytes |
| `gated` | Boolean | Existing Hub metadata, not a guarantee of permission |
| `source` | String | Opaque selected-download identity; canonical exact URL for Hub URL input |
| `fit` | `fits`, `overflow`, `unknown` | Existing weight-size/VRAM heuristic; never guaranteed runtime fit |

Rows are keyed by `source`; duplicate basenames in different folders remain different rows.
New readers use `path ?? filename` and tolerate missing additions from older servers.
Named revisions are preserved rather than silently replaced by `main`; this feature does
not pin moving branches to commits or promise unchanged bytes between search and download.

## Search outcome

Success retains `{query, total_vram_bytes, results}` and adds optional `warnings: string[]`.

- `query`: the accepted trimmed query, not later unsubmitted input edits.
- `total_vram_bytes`: existing positive capacity or null.
- `results`: zero or more supported options for successful inspection.
- `warnings`: sanitized partial keyword-search failures; omitted or empty means none.
- Full failure: existing HTTP error envelope `{detail: string}` with actionable safe text.

Explicit target failure never becomes partial success. Keyword queries may return useful
rows plus warnings. No results plus at least one failed inspection is a full failure;
no results after entirely successful inspection is empty success.

## Browser search state

Reuse `modelSource`, `searchResults`, `searchTotalVram`, `searchError`, and `searching`;
add only `searchWarnings` for the additive response metadata. Keep the existing global
busy convention and guard the store boundary against duplicate search dispatch.

| Event | State change |
|-------|--------------|
| Blank submission | No request; existing validation/disabled action |
| Submission while searching/busy | No second search request |
| Accepted submission | Set searching; clear results, capacity, warnings and error together |
| Nonempty success | Commit results/capacity/warnings from that query; clear searching |
| Empty success | Commit empty results with no error; clear searching |
| Failed lookup or transport | Set actionable error; keep result-associated state empty; clear searching |
| Retry after completion | Same accepted-submission transition |
| Close results while pending | Hide results only; retain single-flight guard until completion |
| Select Download | Pass the exact selected `source` through the existing download action |

One request at a time removes the need for request sequencing or a new cancellation
controller. Changing the text without submitting does not change the identity of results.
The current bar stays unchanged and accepted searches open the existing results modal.
Modal information, focus and announcements must follow the reviewed reference in
[design.md](design.md), while production behavior follows this data model and contract.

## Download lifecycle boundary

Preserve existing idle/running/complete/error/cancelled states and background executor.
A terminal retained snapshot must not be mistaken for an active transfer when selecting
the next result; active transfers still reject an additional request. Reuse the existing
lock, cancellation, partial-file cleanup and sidecar logic. Validation must prove source
identity survives the route, runner and shared downloader.

Remote paths remain distinct during selection; local files still use existing sanitized
basename storage rules. Search does not introduce multi-revision local storage or alter
overwrite semantics.
