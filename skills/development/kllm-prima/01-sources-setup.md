# 01 · Sources, recipe pins & checkout

**Use when:** You are starting Prima work: reading project docs, resolving the pinned fork, computing the recipe digest, creating or reusing a checkout, or deciding whether a failure belongs to Prima or Kluster.

**Keywords:** sources, recipe.json, digest, pin, revision, checkout, boundary, owner, spec 017.

## Quick rules

- Kluster's `AGENTS.md`, memory workflow and `kllm-develop` conventions apply first; read the Prima docs and the spec 017 set where present, and consult current source when historical receipts disagree with it.
- `src/kllm/modules/primacpp/recipe.json` is the single source of truth for the fork repository, source commit and dependency pins.
- Compute the digest programmatically through `kllm.modules.primacpp.interactors.recipe.recipe_digest` — never hardcode a digest and never infer the pin from the fork branch's moving HEAD.
- Work in an isolated checkout of the pinned commit; check both repositories' status, origin and branch before editing; keep unrelated work and downloaded models intact.
- Classify every failing behavior by owner (native execution/server/profiler, managed installer/packaging, Kluster manifests/planning/lifecycle, gateway/UI) and fix the owner; modernize Prima's existing execution path — never silently swap engines or replace the accepted architecture.

## Full text (normative)

## Establish both sources

1. Follow Kluster's `AGENTS.md`, memory workflow and `kllm-develop` conventions.
   Read `docs/prima-integration.md`, `docs/prima-windows-assessment.md` and
   `specs/017-prima-integration/{spec,plan,tasks,validation}.md` where present.
   Consult current source when historical receipts disagree with it.
2. Read `src/kllm/modules/primacpp/recipe.json`; its source repository/commit and
   dependency pins are authoritative. Compute the current digest through
   `kllm.modules.primacpp.interactors.recipe.recipe_digest`. Do not hardcode a
   digest or infer the pin from the fork branch's moving HEAD.
3. Locate an existing Prima checkout or create an isolated checkout of the pinned
   fork commit. Check both repositories' status, origin and branch before edits.
   Use CodeGraph first when that checkout has its own `.codegraph/`; unindexed
   native sources can be read directly. Retain unrelated work and downloaded models.
4. Identify the failing behavior and its boundary: native execution/server/profiler,
   managed installer/packaging, Kluster manifests/planning/lifecycle, or gateway/UI.
   Fix the owner. Modernize Prima's existing execution path; do not silently swap
   engines or replace the accepted architecture with a different runtime.


---

**Related:** 02-build-lifecycle.md (build/validation), 12-placement-qualification.md (publication & qualification).
