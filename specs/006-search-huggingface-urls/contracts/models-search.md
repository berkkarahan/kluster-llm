# Models Search and Selected Download Contract

**Created**: 2026-09-29
**Status**: Proposed additive contract; implementation pending
**Specification**: [spec.md](../spec.md)
**Data model**: [data-model.md](../data-model.md)

## 1. Existing entry points

- Browser: MODELS search form on `/models`.
- Search: `POST /v1/models/search`, JSON `{ "query": "..." }`.
- Selection: `POST /v1/models/download`, existing JSON `{ "source": "..." }`
  with optional existing destination `name`.
- Progress/cancellation: existing `/v1/models/download-progress` and
  `/v1/models/download/cancel` behavior.

No new endpoint or access model. Keep existing private dashboard/agent access behavior.
Search reads metadata only; it never queues downloads or changes the local model store.
The current search bar and navigation retain their appearance, placement and controls.
Submitting either keywords or URLs opens the existing modal; downloadable results and
actions stay inside it. Inline results, another results page or a redesigned bar do not
satisfy this interaction contract.

## 2. Hugging Face URL grammar

In the table, `REV` is one encoded segment, `FOLDER` and `FILE` are repository-relative
paths, and `ORG/REPO` is a validated model repository. Hosts are `huggingface.co`, `hf.co`
and their `www` aliases. Scheme/hostname matching is case-insensitive; HTTP(S) and their
default ports are accepted, and selected Hub downloads use canonical HTTPS.

| URL path/query | Search selection |
|----------------|------------------|
| `/ORG/REPO` or `/ORG/REPO/` | All eligible files at `main` |
| `/ORG/REPO/tree/REV` | All eligible files at REV |
| `/ORG/REPO/tree/REV/FOLDER` | Eligible descendants under FOLDER at REV |
| `/ORG/REPO/blob/REV/FILE` | Exact eligible FILE at REV |
| `/ORG/REPO/resolve/REV/FILE` | Exact eligible FILE at REV |
| `/ORG/REPO/raw/REV/FILE` | Exact eligible FILE at REV |
| `/ORG/REPO?show_file_info=FILE` | Exact eligible FILE at `main` |
| `/ORG/REPO/tree/REV/FOLDER?show_file_info=FILE` | Exact repository-relative FILE at REV, not relative to FOLDER |

Retain structurally valid existing `/model/ORG/REPO` and `/models/ORG/REPO` prefixes.
An explicit file route wins over `show_file_info`. A tree/repository trailing slash is
accepted; a file route with missing filename or a trailing file slash is invalid.

### Encoding and validation

- Trim outer whitespace. Split encoded path segments before decoding; decode once.
- Revision `release%2Fv2` identifies `release/v2`. Unencoded slash boundaries are not
  remotely guessed: `/tree/release/v2` means revision `release`, folder `v2`.
- Query parsing decodes `show_file_info` once. `%252F` represents literal `%2F`, not a
  second path separator. A literal query `+` must use `%2B`; path `+` stays literal.
- Preserve case and full repository-relative file path. Do not match another directory
  by basename or interpret a URL file named `Q4_K_M.gguf` as a quantization request.
- Reject malformed escapes, empty repo components, embedded userinfo, non-default ports,
  controls, backslashes, absolute file selectors, traversal, and empty internal components.
- Decoded repo components cannot contain separators; decoded file/folder paths are
  validated component by component. Encoded slashes in revisions are allowed.
- Fragments and presentation query parameters such as `download=true` do not alter
  selection. Reject active empty/duplicate `show_file_info` and unsupported `?revision=`
  with guidance to use a revision route.
- Profiles, datasets, Spaces, collections, discussions, and unknown website routes are
  unsupported inputs, not whole-repository searches or synthetic download targets.

## 3. Eligibility and scope

Hub options must be present in revision-specific metadata and pass the existing
standalone GGUF filter: `.gguf`, excluding projector (`mmproj`), importance-matrix
(`imatrix`), and recognized multi-part shard names. Apply this to exact file URLs as well
as repository/quant searches. No binary-content inspection or format conversion.

Explicit repository/root/folder searches return all eligible matching options, including
nested paths, without the keyword search's 40-file cap. Folder prefix matching requires
a separator boundary. If no sibling of any type exists under a requested folder, report
an unavailable folder; if it exists but contains no eligible model, return empty success.

Existing keyword search keeps its ranking, quant normalization and limits: 20 discovery
candidates, at most 8 repository inspections, at most 40 result rows. Existing shorthand
`org/repo`, `org/repo:quant`, and `org/repo:path.gguf` remain supported. A supplied path
must be exact. Any preserved basename-only shorthand matching returns real eligible
siblings, never fabricated filenames. Explicit URL selection is always full-path exact.

## 4. Success response

Retain existing fields/types. Add `path`, `revision`, and top-level `warnings` without
requiring them in older responses. Example for
`https://huggingface.co/demo/model-GGUF/tree/release%2Fv2/gguf`:

```json
{
  "query": "https://huggingface.co/demo/model-GGUF/tree/release%2Fv2/gguf",
  "total_vram_bytes": 8589934592,
  "results": [
    {
      "repo_id": "demo/model-GGUF",
      "filename": "model.Q4_K_M.gguf",
      "path": "gguf/model.Q4_K_M.gguf",
      "revision": "release/v2",
      "quant": "Q4_K_M",
      "size_bytes": 4294967296,
      "gated": false,
      "source": "https://huggingface.co/demo/model-GGUF/resolve/release%2Fv2/gguf/model.Q4_K_M.gguf",
      "fit": "fits"
    }
  ],
  "warnings": []
}
```

`filename` stays a basename. `size_bytes: 0` retains the unknown sentinel; presentation
must say unknown rather than zero. `fit` is the existing weight-size/VRAM heuristic, not
a loadability guarantee. `gated` does not prove that the caller can download the file.
Canonical sources contain no credentials. An absent revision on an older row is not an
invitation for the browser to reconstruct the source.

Successful empty repositories/folders return HTTP 200 with `results: []` and no warning.
Partial keyword success returns valid rows with nonempty sanitized `warnings`; the UI
must expose the incomplete coverage. Direct-target lookups do not return partial results.

## 5. Failure response

Keep the existing search HTTP 400 and `{ "detail": "actionable message" }` envelope.
Message categories must distinguish these cases without requiring a new error-code schema:

| Condition | Required meaning |
|-----------|------------------|
| Blank query | Search input is required |
| Invalid/unsupported website input | Supply a supported model repository, tree, or file link |
| Missing/inaccessible repository | Repository unavailable or access required; do not assert private repo nonexistence |
| Gated/unauthorized access | Existing token/access settings may need attention |
| Missing revision | Referenced revision is unavailable |
| Missing folder/file | Requested path is unavailable in the selected revision |
| Existing unsupported exact file | File is not a supported standalone GGUF model |
| Timeout/network/rate-limit/upstream failure | Lookup failed; retry or check connectivity/access as appropriate |

Do not return an empty result or synthetic file on a failed Hub lookup. Safe errors may
include validated repo/path/revision context, but not tokens, request headers, credentials,
or full upstream/signed URLs. Keep detailed diagnostic context only where secrets are
redacted. Catch specific gated errors before their repository-error superclass.

For keyword searches, preserve successful rows from other candidates and disclose skipped
failures with warnings. If there are zero rows and at least one candidate failed, return
an error. A failure of discovery itself is an error.

## 6. Selected download round trip

The browser sends `row.source` byte-for-byte as the `source` value. It must not build a
reference from the basename, remove the revision, or turn a result into a keyword query.
URL-derived Hub rows use `hf_hub_url` canonical sources. The shared downloader extracts
the same revision/full path and passes them to `hf_hub_url` before streaming.

Legacy keyword/repo rows may keep `org/repo:path`. If that source would reinterpret an
exact filename as a quant selector, emit a canonical URL instead. The source is opaque
to consumers in both cases. CLI exact references continue to work without forcing an
additional metadata listing; transfer-time HTTP failures remain download errors.

The existing acceptance response stays
`{"ok": true, "accepted": true, "source": "..."}`. Active-download conflicts remain
409. Complete/error/cancelled snapshots must not permanently reject a new selected
download; validate that narrow state guard without changing scheduling semantics.
Search/download cancellation, partial-file cleanup, hashing, and progress remain shared.
Repository/tree-only inputs are not file downloads and must fail rather than save HTML.

The exact selected remote revision/path is preserved, but a moving branch can change
between search and download. Existing local sanitized-basename storage remains in force.

## 7. External direct URLs and compatibility

Non-website HTTP(S) URLs retain existing direct-URL search/download behavior, including
signed query strings and best-effort HEAD size. This is the existing direct-URL exception
to Hub metadata confirmation. Arbitrary CDN subdomains are not repository website aliases.
Do not attach configured Hub credentials to unrelated direct URLs. Metadata for these
rows may have null `path`/`revision`; the original `source` must remain unchanged.

Old clients can ignore additive fields. New clients default missing warnings to `[]`
and missing path to `filename`. No saved settings, CLI syntax, or native-runtime contract
changes are required. Previously fabricated missing-file rows deliberately become errors.
