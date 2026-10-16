# 12 · Placement, qualification & publication

**Use when:** You work on placement (exact placement, manifest mode, arbitrary/segmented placement, graph reservation pricing, candidate generation), qualification presets and evidence binding, or the publication and delivery workflow for a validated change.

**Keywords:** placement, manifest mode, segmented, segmented_placement, prima_graphs, prima_graph_counts, recipe47, recipe48, ring, wave, visit, relay, candidate, capacity-first, qualification, presets, policy, ngl, output placement, publish, recipe bump, fast-forward, main, rebuild, build.py, delivery, deferred gates.

**Related:** 01-sources-setup.md, 11-run-lifecycle-network.md, 10-profile-costs.md.

## Quick rules

- Exact placement & manifest mode: preserve exact placement and fail unsupported layouts/settings explicitly; manifest mode must not invoke autonomous profiling, prune ranks or reschedule; test zero-work coordination and partial offload where affected.
- Distributed qualification binding: bind distributed qualification to signed node-local platform/CPU/core/RAM, source/recipe/build/artifact and selected Vulkan UUID/driver/capacity observations; recheck them before any spawn and during owned renewal; membership labels and inventory discovery alone cannot prove inference; preserve legacy manifest canonical digests when adding optional metadata and test the resulting manifest with published native ranks, including an explicit zero-work CPU coordinator.
- Qualification policy & evidence: use the source-controlled qualification policy, not writable runtime manifests; evaluate OS/backend, topology, exact model/feature/placement combinations and device/driver evidence; policy updates must not invalidate the build they qualify while implementation changes invalidate affected evidence; keep controlled tests, physical local Vulkan, local multi-rank and physical multi-node results separate — local Windows/Vulkan qualification cannot enable workers, other platforms, untested models or features; missing assets/hardware/checks are unavailable, not passes; match paired benchmarks on bytes/settings/device/offload with warm-up and repeated alternating pairs under the existing scope's thresholds — the distributed improvement gate needs physical distributed measurements.
- Delivery: update feature tasks, validation receipts and affected operational/API docs with actual results and remaining blockers; deliver both repository revisions, commands and results, the new recipe identity, compatibility impact and any still-blocked selection paths; never describe build/device-discovery success as completed inference or complete Prima qualification.
- Qualification presets: bind effective launch settings, exact rank/offload/output placement, complete named assets and host/device/driver identity; persist the admitted context and recheck it during serving; global model/feature lists must not grant a different preset's request features; empty or revoked preset evidence remains closed.
- Baseline flag semantics: before calling a comparison matched, check baseline flag semantics — the pinned llama.cpp `7fe450e19305b828c199d602c23a8337aaa1f03b` counts output in every positive GPU-layer count while Prima counts transformer layers separately; use the shared assessment translation and long-prefill/decode placement probe including tied embedding output weights when CPU projection is requested; keep placement tracing outside timed runs; preserve historical mismatched receipts, label their limits, and repeat affected acceptance without lowering its criteria.
- Publication (push to `main` by default): inspect the fork diff, run affected native checks and check whitespace; commit only relevant source/tests/CI/docs — never build trees or model assets; compare the remote branch with the tested base and fast-forward push directly to `main` on `https://github.com/berkkarahan/prima.cpp` unless explicitly told otherwise; if `origin/main` advanced, integrate its changes and rerun affected checks — do not force-push away another change; report unresolved conflicts or authentication failures instead of rewriting history; verify the published full SHA with the remote, update Kluster's recipe to that exact commit, increment its build recipe when its contract changes, and preserve upstream/dependency provenance (the normal managed path applies no hidden local patches; existing builds remain historical); resolve/rebuild from the published source in a fresh isolated runtime directory verifying source cleanliness, artifact hashes, capabilities and relocation; rebuild Kluster through `uv run python scripts/build.py` when packaging/native bindings change and check the imported native extension.
- Publication boundary: push validated Prima changes directly to `main` on `https://github.com/berkkarahan/prima.cpp` unless the user explicitly specifies another branch or asks to hold off on pushing; never relax qualification policy without physical evidence.
- Arbitrary placement groundwork: carry immutable absolute layer ownership through model, context, tensor loading, KV storage, graph callbacks and sequence snapshots; precompute local/segment indices, keep model/context identity and lifetimes exact, clear target maps in local drafts, and count explicit GPU layers in rank-local ordering; representation alone does not enable segmented execution — keep current window validation and do not advertise `segmented_placement` until the actual graph and rank handoff executor can follow every approved visit; keep row/tensor/expert and architecture-specific graph support explicit; test execution after full implementation — compilation and map construction cannot qualify it.
- Segmented execution: use only manifest-approved physical ring edges and bind activation packets to the exact manifest, micro-batch wave, stage, row count and model width; preserve local graph visit order, handle zero-work relay ranks, and wait for a complete ring acknowledgement before another micro-batch or KV command; refuse malformed/foreign/expired waves and make failed contexts unusable; reserve all local visit schedulers and price each visit's host/device graph memory before managed admission; recipe47 executor groundwork does not enable the managed `segmented_placement` capability — finish launch/admission/memory integration first, and existing one-handoff-per-rank typed costs cannot score repeated visits or relays (keep those candidates capacity-first and explicitly unmeasured until covered).
- Graph reservation pricing: price retained Prima graph visits in the owning placement allocator, including rank-zero input/output, worker host storage and additional per-device visits; keep exact absolute assignments — excess host/device/shared-pool capacity must report overflow; carry the internal `prima_graphs` reservation flag through typed resolved settings and the immutable execution qualification scope, requiring it before admitting a noncanonical layout from a segmented-capable runtime; a conservative scratch estimate is not measured backend allocation or acceptance; local single-rank graphs retain their shared estimate while row/tensor/expert paths need their own implemented pricing/execution before being enabled.
- Recipe48 admission: recipe48 advertises `segmented_placement` for implemented Llama/Qwen2/Qwen3 layer graphs after adding manifest admission; freeze the planner's `prima_graph_counts` alongside `prima_graphs` and independently compare rank visit counts against exact absolute ownership in both Kluster and native startup; count head input/output separately and merge adjacent ownership when counting local visits; missing or altered reservations must refuse noncanonical startup; negotiate segmentation before using absolute layer-count launch translation or adding candidate profile ring edges; keep unsupported architectures, row/tensor/expert work and one-handoff timing limitations explicit — a capability report grants no qualification.
- Segmented planner costs: derive contiguous absolute ownership visits and count completed GPU uploads/downloads per GPU-containing visit, keeping the head output upload/logits download independent; route each activation through actual physical ring hops including relays and repeated visits, and include full-ring settlement plus the unchanged batch metadata chain; bound native marker/JSON payload sizes using the largest wave counter; keep transport framing, buffering and relay CPU work outside an explicitly labelled application-link proxy (these estimates are not generation timings or performance acceptance); require exact planned graph counts and the current published segmented capability with fresh signed model/source/settings/hardware/storage/link evidence; keep cold logical weight reads separate from resident decode and canonical legacy wire estimates; missing evidence must retain capacity-first selection and previews must not measure or mutate assets/caches.
- Placement candidate generation: honor required nodes and explicit priority, enumerate bounded optional-worker subsets with at least one worker in a network-bound run, and include participant identity when deduplicating capacity-equivalent plans (a common capacity shape can still have different network edges and profiling costs); keep candidate generation separate from measured selection and do not infer a performance-gate pass from estimates.

## Full text (normative)

### Exact placement & manifest mode

- Preserve exact placement and fail unsupported layouts/settings explicitly.
  Manifest mode must not invoke autonomous profiling, prune ranks or reschedule.
  Test zero-work coordination and partial offload where affected.


### Arbitrary placement groundwork

For arbitrary placement, carry immutable absolute layer ownership through model,
context, tensor loading, KV storage, graph callbacks and sequence snapshots.
Precompute local/segment indices; keep model/context identity and lifetimes exact,
clear target maps in local drafts and count explicit GPU layers in rank-local
ordering. Representation alone does not enable segmented execution. Keep current
window validation and do not advertise segmented_placement until the actual graph
and rank handoff executor can follow every approved visit. Keep row/tensor/expert
and architecture-specific graph support explicit. Test execution after full
implementation; compilation and map construction cannot qualify it.


### Segmented execution

For segmented execution, use only manifest-approved physical ring edges and bind
activation packets to the exact manifest, micro-batch wave, stage, row count and
model width. Preserve local graph visit order, handle zero-work relay ranks and
wait for a complete ring acknowledgement before another micro-batch or KV command.
Refuse malformed/foreign/expired waves and make failed contexts unusable. Reserve
all local visit schedulers and price each visit's host/device graph memory before
managed admission. The recipe47 executor groundwork does not enable the managed
segmented_placement capability: finish launch/admission/memory integration first.
Existing one-handoff-per-rank typed costs cannot score repeated visits or relays;
keep those candidates capacity-first and explicitly unmeasured until covered.
Unit/native/model checks and test-fix work start only after full implementation;
physical distributed and performance acceptance remain deferred to the live cluster.


### Graph visit pricing

Price retained Prima graph visits in the owning placement allocator, including
rank-zero input/output, worker host storage and additional per-device visits.
Keep exact absolute assignments; excess host/device/shared-pool capacity must
report overflow. Carry the internal prima_graphs reservation flag through typed
resolved settings and the immutable execution qualification scope; require it
before admitting a noncanonical layout from a segmented-capable runtime. A
conservative scratch estimate is not measured backend allocation or acceptance.
Local single-rank graphs retain their shared estimate; row/tensor/expert paths
need their own implemented pricing/execution before being enabled.


### Recipe48 — segmented_placement admission

Recipe48 advertises segmented_placement for implemented Llama/Qwen2/Qwen3 layer
graphs after adding manifest admission. Freeze the planner's prima_graph_counts
alongside prima_graphs, and independently compare rank visit counts against exact
absolute ownership in both Kluster and native startup. Count head input/output
separately; merge adjacent ownership when counting local visits. Missing or altered
reservations must refuse noncanonical startup. Negotiate segmentation before using
absolute layer-count launch translation or adding candidate profile ring edges.
Keep unsupported architectures, row/tensor/expert work and one-handoff timing
limitations explicit. A capability report grants no qualification; execute the
correctness/test phase only after full implementation completion, and leave
physical distributed/performance acceptance to the live three-computer cluster.


### Segmented planner costs

For segmented planner costs, derive contiguous absolute ownership visits and
count completed GPU uploads/downloads per GPU-containing visit, keeping the head
output upload/logits download independent. Route each activation through actual
physical ring hops, including relays and repeated visits, and include full-ring
settlement plus the unchanged batch metadata chain. Bound native marker/JSON
payload sizes using the largest wave counter. Keep transport framing, buffering
and relay CPU work outside an explicitly labelled application-link proxy; these
estimates are not generation timings or performance acceptance. Require exact
planned graph counts and the current published segmented capability with fresh
signed model/source/settings/hardware/storage/link evidence. Keep cold logical
weight reads separate from resident decode and canonical legacy wire estimates.
Missing evidence must retain capacity-first selection; preview must not measure
or mutate assets/caches. Validate routes, visit costs and mixed candidate sets
only in the later test phase after full implementation completion.


### Placement candidate generation

For placement candidate generation, honor required nodes and explicit
priority, enumerate bounded optional-worker subsets with at least one worker
in a network-bound run, and include participant identity when deduplicating
capacity-equivalent plans. A common capacity shape can still have different
network edges and profiling costs. Keep candidate generation separate from
measured selection and do not infer a performance-gate pass from estimates.


### Distributed qualification binding

- Bind distributed qualification to signed node-local platform/CPU/core/RAM,
  source/recipe/build/artifact and selected Vulkan UUID/driver/capacity observations.
  Recheck them before any spawn and during owned renewal; membership labels and
  inventory discovery alone cannot prove inference. Preserve legacy manifest
  canonical digests when adding optional metadata. Test the resulting manifest
  with published native ranks, including an explicit zero-work CPU coordinator.


## Qualification and delivery

Use the source-controlled qualification policy, not writable runtime manifests.
Evaluate OS/backend, topology, exact model/feature/placement combinations and
device/driver evidence. Policy updates must not invalidate the build they qualify;
implementation changes invalidate affected evidence.

Scopes are `<windows|linux|darwin>-vulkan-<local|distributed>`, selected from each node's
own platform (`kllm.modules.primacpp.interactors.platforms` normalizes aliases such as
`macOS`). Defining a scope qualifies nothing: Linux/macOS entries ship with every gate
false and no receipts, models or devices, and must stay that way until a physical run
records evidence. Never copy another platform's evidence into a scope. In a mixed-OS run
each participant platform is evaluated against its own scope. All GPU ranks in a ring must
share one backend (`require_shared_ring_backend`); a CPU-only coordinator master is exempt.

Keep controlled tests, physical local Vulkan, local multi-rank and physical multi-node
results separate. Local Windows/Vulkan qualification cannot enable workers, other
platforms, untested models or features. Missing assets/hardware/checks are unavailable,
not passes. Match paired benchmarks on bytes/settings/device/offload, include warm-up
and repeated alternating pairs, and use the existing scope's performance thresholds.
The distributed improvement gate needs physical distributed measurements.

Update feature tasks, validation receipts and affected operational/API docs with
actual results and remaining blockers. Deliver both repository revisions, commands
and results, the new recipe identity, compatibility impact and any still-blocked
selection paths. Never describe build/device-discovery success as completed inference
or complete Prima qualification.

Qualification presets must bind effective launch settings, exact rank/offload/output
placement, complete named assets and host/device/driver identity. Persist the admitted
context and recheck it during serving; global model/feature lists must not grant a
different preset's request features. Empty or revoked preset evidence remains closed.

Check baseline flag semantics before calling a comparison matched. The pinned
llama.cpp `7fe450e19305b828c199d602c23a8337aaa1f03b` counts output in every positive
GPU-layer count; Prima counts transformer layers separately. Use the shared assessment
translation and long-prefill/decode placement probe, including tied embedding output
weights when CPU projection is requested. Keep placement tracing outside timed runs.
Preserve historical mismatched receipts, label their limits, and repeat affected
acceptance without lowering its criteria.


## Publish and consume an immutable change

Push validated Prima changes directly to `main` on `https://github.com/berkkarahan/prima.cpp` unless explicitly told otherwise:

1. Inspect the fork diff, run affected native checks and check whitespace. Commit
   only the relevant source/tests/CI/docs. Never include build trees or model assets.
2. Compare `origin/main` with the tested base. Fast-forward push directly to
   `main` on `https://github.com/berkkarahan/prima.cpp` (unless the user explicitly
   specified another branch). If `origin/main` advanced, integrate its changes and
   rerun affected checks; do not force-push away another change. Report unresolved
   conflicts or authentication failures instead of rewriting history.
3. Verify the published full SHA with the remote. Update Kluster's recipe to that
   exact commit, increment its build recipe when its contract changes, and preserve
   upstream/dependency provenance. The normal managed path applies no hidden local
   patches. Existing builds remain historical and require fresh resolution/build.
4. Resolve/rebuild from the published source in a fresh isolated runtime directory.
   Verify source cleanliness, artifact hashes, capabilities and relocation. Rebuild
   Kluster through `uv run python scripts/build.py` when packaging/native bindings
   change, and check the imported native extension.

If the user explicitly instructs not to push, leave a reviewable fork diff and report
the required publication step. Never relax qualification policy without physical evidence.


---

**Related:** the validation-schedule boundary (implementation-first, deferred gates) is stated in 02-build-lifecycle.md; ranked/profile evidence contracts live in 10-profile-costs.md.
