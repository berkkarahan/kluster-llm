# Design handoff: MODELS Hugging Face URL search

**Created**: 2026-09-29
**Status**: Generated, refined and reviewed — ready as a Phase 1 design reference
**Specification**: [spec.md](spec.md)

## User-supplied brief constraint

"do not change the current bar, search should still open a modal with downloadable results"

Preserve the current search bar's appearance, placement, copy and controls, and preserve
the surrounding navigation. Submitting Search must still open the existing results modal.
Keep downloadable options and their actions inside that modal. Any design work is limited
to the modal's result information and states needed for explicit Hugging Face URLs;
inline results, a separate results page, or a replacement bar are outside this brief.

## Workflow evidence

The requested MCP connection was queried successfully. It reported no active project.
A new product-prototype brief card titled **Kluster LLM Models — Hugging Face URL search**
was opened through that connection. The user then supplied the fixed bar/modal brief
in conversation and explicitly selected local execution when asked which mode to use.
Generation was commissioned through the same connection using Local Codex, in the
project **Kluster LLM Models URL Search Modal**, with the application source supplied
as read-only reference. The requested entry is `models-url-search-modal.html`.

The generation prompt makes the current bar/navigation immutable and keeps results in
the existing modal. It requests mock interactions and a browser review, with all output
confined to the design project. No application implementation was commissioned.

## Initial review and requested correction

The first generation completed and its full artifact bundle was retrieved. A separate
Chrome CDP review on Windows exercised the prototype at 1280x720 and 390x844: Search
click/Enter, modal focus wrapping/restoration, Escape dismissal, explicit simulated
Download preserving source, and twelve scenario states passed with no runtime exceptions
or page-wide overflow. The generation agent's own renderer had only supplied a static
1440x1000 preview; the CDP checks provide separate interaction evidence.

The baseline comparison found a fidelity defect: the prototype translated rem-based
Tailwind utilities using 16px rather than the application's actual 13px root. At 1280px,
the production page was 728px wide and the header 32.5px tall, while the prototype used
896px and 40px; form gaps were 6.5px versus 8px. The prototype also omitted the empty
download/load area's layout contribution. This violates the unchanged-bar constraint.

The correction was commissioned through the same Local Codex workflow and completed
successfully. Final browser checks below verify the corrected source-derived geometry.

## Confirmed requirements supplied for generation

These are behavioral requirements and existing context, not a proposed visual layout:

- Extend the existing MODELS search/download flow, using `dashboard/DESIGN.md`,
  `dashboard/src/app.css`, and `dashboard/src/routes/models/+page.svelte` as context.
- Treat the unchanged bar and existing results modal as fixed constraints, not alternative
  layout choices. Search by keyword and by URL must open that same modal.
- Accept repository, folder/revision and exact file URLs in the existing search field.
  Use the behavior and metadata in [the contract](contracts/models-search.md).
- Make nested paths and duplicate basenames distinguishable, show quantization when
  identifiable, and express unknown size/memory fit explicitly. Preserve opaque source
  identity and the existing selected-download workflow.
- Include populated multi-option, exact-file, loading, empty, invalid link, unavailable
  target, restricted access, connection failure, and partial keyword-result states.
- Searching never downloads automatically. Keep an explicit download action for each
  option. A user can retry after failure and continue ordinary keyword/quant searches.
- Controls and complete file paths must be reachable at desktop and narrow mobile widths
  and by keyboard. Results need accessible identification; loading/errors need appropriate
  announcements; fit meaning cannot depend solely on color. The retained modal must have
  accessible naming, focus management and restoration.
- Use simulated data with fake repository IDs and no credentials. Prototype demonstration
  switches must not become production controls.
- Keep existing application styling and scope; this is not a dashboard redesign.

## Final artifact and review evidence

- Execution: Local Codex through the requested MCP workflow; initial generation and
  source-fidelity refinement both succeeded. Final HTML syntax validation passed.
- Project: **Kluster LLM Models URL Search Modal**.
- Entry: `models-url-search-modal.html`; supporting source review: `REVIEW.md`.
- [Interactive preview](http://127.0.0.1:51074/api/projects/kluster-llm-models-url-search-modal-d9c4/raw/models-url-search-modal.html).
  The local preview requires the design workspace to be running; use the named project
  and entry to recover its current preview if the daemon address changes.
- Final outer review used real headless Chrome/CDP on Windows against the existing
  dashboard build served on loopback with controlled API responses, and the generated
  prototype. Fonts were awaited before geometry comparison. Temporary browser profiles
  and the local validation server were cleaned up after each run.

| Checked viewport | Search panel x / y | Search panel width / height | Form gap | Result |
|------------------|--------------------|----------------------------|----------|--------|
| 1280x720 | 289 / 103.6875 | 702 / 134.28125 | 6.5px | Matches baseline |
| 390x844 | 13 / 156.765625 | 364 / 134.28125 | 6.5px | Matches baseline |

The header, panel, form and input bounds matched the baseline within a 1.1px comparison
tolerance at both viewports; the search placeholder matched exactly. Actual tests passed
for URL Search click/Enter, keyword Enter, initial modal focus, Shift+Tab/Tab wrap,
Escape and Close dismissal, focus restoration, and an explicit simulated Download that
closes the modal and preserves the selected source. Search alone created no transfer
feedback. All twelve prototype scenarios had the expected result counts, contained modal
bounds and no page-wide overflow at both sizes. No runtime exceptions were observed.

Source review confirmed complete path wrapping, revision/quant/unknown metadata, explicit
fit-heuristic wording, and accessible result labels. The prototype's hard-coded fixtures
and URL-shape shortcuts are illustrative; do not copy them as the application's resolver.
Keep prototype scenario controls out of production. Implement the real search/download
contract and its tests from [quickstart.md](quickstart.md).

**Design gate: PASS.** Phase 1 has a generated and reviewed feature-specific reference.
This verifies the recorded prototype interactions and geometry, not production Hub access,
download correctness, screen-reader behavior, or the real application's regression suite.
