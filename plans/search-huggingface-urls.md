# MODELS Hugging Face URL search plan

> **Created:** 2026-09-29
> **Status:** Planned
> **Repo / scope:** kluster-llm — existing MODELS search and shared model downloader

## Conventions captured at creation

- `python/kllm/models.py:parse_model_search_query`, `_parse_hf_url`, `search_hub_ggufs`,
  and `download_model` already implement partial URL support. Revision loss, folder
  omission, fabricated exact-file fallback, and swallowed lookup failures are shared gaps.
- `python/kllm/agent/__init__.py:search_models` and `download_model` expose existing
  search/selection endpoints; `python/kllm/cli.py:model_download` is a shared caller.
- `dashboard/src/routes/models/+page.svelte:runSearch` and `downloadRow` use
  `dashboard/src/lib/store.svelte.ts:searchModels` and `downloadModel`; selected `source`
  is already passed unchanged. Keep basename/numeric-size semantics wire-compatible.
- `huggingface_hub` 1.32.0 is locked and supports revision-specific metadata and canonical
  URLs. Existing pytest, Node/Vite and Chrome/Edge CDP patterns cover the relevant boundaries.
- The constitution requires scoped changes, deterministic observable tests, explicit
  failures, credential protection, and synchronized existing docs. No new dependency is needed.
- Concurrent feature 005 owns dashboard live-update work and the active feature pointer.
  This work uses `specs/006-search-huggingface-urls` with explicit no-persist selection.

## Intended result

One pasted model URL lists real eligible GGUF options from the selected repository,
revision and folder. Exact links identify exact files. Selecting an option downloads
that source through the existing workflow. Ordinary keyword/repo/quant/direct inputs
remain usable; failures do not masquerade as empty or fabricated success.
The user's clarified brief preserves the current search bar and surrounding navigation.
Both keywords and URLs still open the existing downloadable-results modal; any UI
adaptation is confined to the modal's information and states.

## Sequence

1. Complete the requested feature-specific design brief through OpenDesign Cloud or
   Local Codex, commission the design, review it, and record its actual reference.
2. Extend shared URL selectors and revision-specific metadata filtering; validate exact
   paths and disclose explicit/partial lookup failures.
3. Preserve selected revision/path through the shared downloader. Add compatible result
   metadata, then integrate it in the existing store/page using reviewed design guidance.
4. Check resolver, real search-to-download boundary, UI wiring and input compatibility;
   update existing CLI/dashboard and troubleshooting docs.

## Detailed handoff and status

- [Specification](../specs/006-search-huggingface-urls/spec.md)
- [Implementation plan](../specs/006-search-huggingface-urls/plan.md)
- [Research](../specs/006-search-huggingface-urls/research.md)
- [Data model](../specs/006-search-huggingface-urls/data-model.md)
- [Contract](../specs/006-search-huggingface-urls/contracts/models-search.md)
- [Validation guide](../specs/006-search-huggingface-urls/quickstart.md)
- [Design handoff/status](../specs/006-search-huggingface-urls/design.md)

Technical research and Phase 1 are complete. The user supplied the fixed bar/modal brief
and chose Local Codex; the generated reference was refined and passed desktop/mobile
browser checks for baseline bar geometry and modal interactions. Production behavior is
not yet implemented or validated. Freeze this snapshot when the work is realized;
maintain actual behavior in operational docs.
