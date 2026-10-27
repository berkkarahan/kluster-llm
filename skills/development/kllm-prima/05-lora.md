# 05 · LoRA — loading, assets, selection, admission, per-request & activated

**Use when:** Any LoRA work: managed adapter loading, LoRA assets, native selection/synchronization, admission checks, embedding adapters, per-request selection, or activated-LoRA invocation and cache semantics.

**Keywords:** LoRA, adapter, A/B pairs, alpha, rank, managed_lora_v1, scales, basenames, request_lora_v1, activated_lora_v1, invocation, neutral prefix, weight phases, batch selection, tied output, embedding adapter, recipe50, recipe51, recipe53.

**Related:** 07-server-apis.md (HTTP slot semantics, protocol translations), 09-cache-replay.md (cache/checkpoint invalidation, prompt state).

## Quick rules

- Loading: validate adapter metadata and complete A/B pairs on all ranks, then allocate only the immutable owner's transformer tensors (head input/output weights stay on rank zero); unknown targets must fail their owning rank before the readiness barrier; keep metadata/device contexts/buffers/model registration under exception-safe ownership and contain initializer failures at the exported C boundary. Recipe50 is a loading foundation only — do not advertise managed LoRA from it.
- Assets: preserve ordered declarations, normalize finite F32 scales, allow template keyword defaults alongside adapters; give each unique regular complete local adapter an explicit verified asset role with original portable basenames (no model-shard filename rules); reject conflicting names and symlinks; freeze ordered basename/scale bindings and planned storage/per-token scratch bounds through typed settings and the qualification scope; pre-spawn, recheck staged size/SHA and metadata bounds, then remap every adapter under the owned model layout on all ranks.
- Selection: use model-local stable adapter identities with ordered application; validate/prepare a complete finite selection before mutation; managed heads bind replacement commands to exact manifest, monotonic revision and physical ring hop order and wait for all-rank acknowledgement before committing; foreign, stale, missing or failed replies make the context unusable; no worker-local managed changes; settle backend work before/after KV and snapshot invalidation. Recipe51 implements synchronization code only.
- Admission: independently check ordered native file basenames/scales and approved size/SHA against the execution manifest, rejecting unreferenced assets and changed initial selection; stream hashes with bounded buffers and GGML's UTF-8 file opener; negotiate worker adapter asset roles before transfer and interpret master Windows/POSIX path separators consistently; freeze target metadata and recheck it from verified staged bytes; verification must reject stale/foreign selections, wrong scales/targets/backends and incomplete A/B execution; do not enable managed_lora_v1 until input embedding and tied output buffer paths are implemented.
- Embedding adapters: preserve the GGUF A(rank,vocabulary)/B(rank,embedding) orientation and apply the delta to gathered token embeddings; tied output needs its own pair on the actual output buffer (decode/transpose B into F32, retain A, never rewrite supplied assets); resolve input through the input pair even when CPU tied weights share a tensor pointer; preserve original component identities in execution traces including GET_ROWS for input A; load/apply adapters before graph reservation and include all input/tied-output nodes; validate finite effective F32 scales before selection; do not reject Flash Attention merely because an adapter is selected when baseline selection permits it. Recipe53 advertises managed_lora_v1 as an implementation capability — it grants no qualification.
- Per-request: keep deployment defaults separate from slot selections; preserve empty-array baseline semantics and validate all IDs/scales before use; group only equal selections in a native batch with bounded round-robin scheduling; settle batch weights on all ranks through a manifest/revision/mode-bound acknowledged command before execution; a dedicated batch-selection API may preserve sequence KV/snapshots only when the server tracks adapter identity; bind prompt caches/checkpoints to exact scales and charge that metadata to their budgets; preserve selections in all public text protocol translations and effective generation settings; require `request_lora_v1` and explicit request feature qualification.
- Activated: parse bounded UINT32 invocation metadata and check native token IDs against the base vocabulary; freeze count/hash identities from the supplied adapter bytes, recheck owned staged metadata and negotiate `activated_lora_v1` before preparation; find the final occurrence with a bounded linear-time scan handling overlapping sequences; preserve the pinned baseline's activation boundary and absent/multiple/mixed selection semantics; select base and active weight phases through the same acknowledged all-rank batch API; system prefixes use the appropriate sequence weights without altering another slot's state; track neutral live-cache prefixes and neutral snapshot lineage — cross-adapter reuse may restore only captured base state within the new boundary, and full recurrent state after activation must never be relabelled as a neutral prefix; capture at completed positions, charge metadata to byte ceilings, and invalidate neutral reuse on shifts/truncation with speculative accepted-token accounting.

## Full text (normative)

### Managed LoRA loading (recipe50)

For managed LoRA, validate adapter metadata and complete A/B pairs on all ranks,
then allocate only the immutable owner's transformer tensors; head input/output
weights stay on rank zero. Unknown targets must fail their owning rank before the
readiness barrier. Keep metadata/device contexts/buffers/model registration under
exception-safe ownership and contain initializer failures at the exported C boundary.
Validate metadata types, finite alpha, positive rank and matching dense/expert
shapes. Keep target LoRA files out of independently loaded draft models.
Recipe50 provides this loading foundation only. Do not advertise managed LoRA until
verified adapter roles/transfers, ordered file/scale bindings, per-rank weight/scratch
pricing, owned launch paths and native rank-wide selection/scale synchronization are
implemented. Head-only HTTP adapter changes are insufficient: settle all ranks
before decode and preserve cache/checkpoint invalidation. Keep adapter and failure
checks in the later test phase after full implementation completion; compilation
and staging alone do not qualify adapter execution or distributed performance.


### Managed LoRA assets

For managed LoRA assets, preserve ordered declarations and normalize finite F32
scales; allow template keyword defaults alongside adapters. Give each unique
regular complete local adapter an explicit verified asset role, retaining original
portable basenames without applying model-shard filename rules. Reject conflicting
names and symlinks. Freeze ordered basename/scale bindings and planned storage/
per-token scratch bounds through typed settings and the qualification scope.
Reserve storage on host/selected allocation pools and scratch for effective batches
and retained graph visits; full-file/all-pair bounds are conservative estimates,
not measured rank allocation. Before spawn, recheck staged size/SHA and metadata
bounds, then remap every adapter under the owned model layout on all ranks.
Do not advertise managed_lora_v1 until native binding checks, rank-wide selection/
scale settlement, worker asset-role negotiation, snapshot invalidation and completed
GPU LoRA/output evidence are implemented. Tests remain deferred until full
implementation completion; physical distributed/performance acceptance stays with
the user's live three-computer cluster.


### Native LoRA selection & synchronization (recipe51)

For native LoRA selection, use model-local stable adapter identities and ordered
application. Validate/prepare a complete finite selection before mutation; managed
heads must bind replacement commands to exact manifest, monotonic revision and
physical ring hop order, then wait for all-rank acknowledgement before committing.
Bound selection/frame counts and I/O; foreign, stale, missing or failed replies
must make the context unusable. Do not allow worker-local managed changes.
Settle backend work before/after KV and snapshot invalidation. Check startup status
and retain exception-safe cleanup. HTTP updates must validate before enqueueing,
wait for idle slots, keep reported scales unchanged until success, clear draft KV
and rebuild system prefixes. Register result waiting before publishing the task.
Recipe51 implements synchronization code only; manifest/file/scale admission,
worker adapter-role negotiation and completed GPU LoRA/output evidence still precede
managed_lora_v1 enablement. Run synchronization/error/model checks only after full
implementation completion; compile success is not protocol or cluster acceptance.


### LoRA admission

For LoRA admission, independently check ordered native file basenames/scales and
approved size/SHA against the execution manifest, rejecting unreferenced assets and
changed initial selection. Stream hashes with bounded buffers and GGML's UTF-8 file
opener; retain legacy no-adapter manifest behavior. Negotiate worker adapter asset
roles before transfer and interpret master Windows/POSIX path separators consistently.
Freeze target metadata and recheck it from verified staged bytes. Tag actual LoRA
operation nodes with stable adapter/target/component/role identity, then report only
completed nonempty work with selection/revision context. Accumulate observations
per phase/epoch so partial batches do not hide later required work. Verification
must reject stale/foreign selections, wrong scales/targets/backends and incomplete
A/B execution. Do not enable managed_lora_v1 until input embedding and tied output
buffer paths are implemented. Native SHA vectors, file drift, role negotiation,
operation evidence and model checks run only after full implementation completion;
tracing/compilation is not performance or physical cluster acceptance.


### Embedding LoRA (recipe53)

For embedding LoRA, preserve the GGUF A(rank,vocabulary)/B(rank,embedding)
orientation and apply its delta to gathered token embeddings. Tied output needs
its own pair on the actual output buffer: decode/transpose B into F32 and retain A,
without rewriting supplied assets. Resolve input through the input pair even when
CPU tied weights share a tensor pointer. Preserve original component identities
in execution traces, including GET_ROWS for input A. Distinguish embedding ranks
in planner metadata. Load/apply adapters before graph reservation and include all
input/tied-output nodes; validate finite effective F32 scales before selection.
Do not reject Flash Attention merely because an adapter is selected when baseline
selection permits it. Recipe53 advertises managed_lora_v1 as an implementation
capability; it grants no qualification. Keep shape/transposition, quantized data,
GPU ownership, graph capacity, selection and Flash Attention checks in the deferred
test phase after the full implementation is finished. Physical distributed and
performance acceptance stays deferred to the live three-computer cluster.


### Per-request LoRA

For per-request LoRA, keep deployment defaults separate from slot selections.
Preserve empty-array baseline semantics and validate all IDs/scales before use.
Group only equal selections in a native batch and use bounded round-robin scheduling
so another configuration can progress. Settle batch weights on all ranks through
a manifest/revision/mode-bound acknowledged command before execution. A dedicated
batch-selection API may preserve sequence KV/snapshots only when the server tracks
their adapter identity; ordinary replacement must retain full invalidation. Bind
prompt caches/checkpoints to exact scales and charge that metadata to their budgets.
Clear changed slot/draft state and prepare system prefixes with each sequence's
weights, using independent prefix batches. Preserve selections in all public text
protocol translations and effective generation settings. Require request_lora_v1
and explicit request feature qualification; development capability is not acceptance.
Run concurrency, fairness, cache/prefix/draft, malformed selection and lost-reply
checks only after full implementation completion; cluster/performance gates remain
deferred to the live three-computer cluster. Activated LoRA invocation behavior is
a separate baseline requirement and cannot be inferred from ordinary LoRA support.


### Activated LoRA

For activated LoRA, parse bounded UINT32 invocation metadata and check native token
IDs against the base vocabulary. Freeze count/hash identities from the supplied
adapter bytes, recheck owned staged metadata and negotiate activated_lora_v1 before
preparation. Find the final occurrence with a bounded linear-time scan that handles
overlapping sequences. Preserve the pinned baseline's activation boundary and
absent/multiple/mixed selection semantics. Select base and active weight phases
through the same acknowledged all-rank batch API before execution. System prefixes
must use the appropriate sequence weights without altering another slot's state.
Track neutral live-cache prefixes and neutral snapshot lineage. Cross-adapter reuse
may restore only captured base state within the new boundary; full recurrent state
after activation must never be relabelled as a neutral prefix. Capture at completed
positions, charge metadata to byte ceilings, and invalidate neutral reuse on shifts/
truncation while accounting for speculative accepted tokens. Compilation cannot
prove invocation/cache correctness or broad noncausal API/model behavior. Keep all
metadata, boundary, concurrency, recurrent/cache/draft and protocol checks deferred
until full required implementation completion; cluster/performance gates stay
unpassed until the user's live three-computer validation.


---

**Related:** ordinary replacement retains full invalidation; only the identity-tracking batch API may preserve sequence KV/snapshots (09-cache-replay.md). HTTP update semantics and protocol translations live in 07-server-apis.md.
