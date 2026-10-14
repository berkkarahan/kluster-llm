# 09 · KV caches, checkpoints, drafts & replay

**Use when:** You work on KV/cache memory — prompt caching, context checkpoints, compact sliding-window (SWA) KV storage, head KV ceilings, cache activation — or on draft (speculative) decoding, CLI session reuse, server replay, self-extend and context-shift handling.

**Keywords:** KV, cache, prompt_cache_v1, context_checkpoints_v1, checkpoint, compact, SWA, sliding window, rewind, defrag, defragmentation, draft, speculative, session, replay, self-extend, context shift, n_past, cache_prompt, KV ceiling, budget, GPT-OSS, recurrent, --swa-full.

**Related:** 05-lora.md (adapter-weight phases in caches), 08-models-architectures.md (per-architecture KV needs), 12-placement-qualification.md (budgets in qualification scopes).

## Quick rules

- Draft-simple decoding: clear target manifests/ranks/network setup in each local draft context and suppress autonomous profiling; respect independent K/V cache types and explicit CPU selection; do not force mlock or inherit the target GPU layout; verify the actual modern flag aliases before managed preflight; resolve active draft files through the existing model resolver, stage complete shards under verified basenames, and bind role/hash/size plus CPU placement and context count in the signed scope — an inactive draft must not require its file; price every independent HTTP-slot draft context on the master while preserving baseline shared-context estimation; audit worker sequence-ID offsets before changing rollback IDs; free samplers/contexts before models, handle null speculators, refuse failed slot initialization before readiness; capture accepted-token work counters, not performance claims.
- Prompt caching: reserve the entire configured host RAM ceiling on every selected participant (including GPU workers and a zero-layer coordinator), bind the budget to signed settings and require `prompt_cache_v1` before preparation; keep serialized KV state on its owning rank with bounded all-rank transactions and one eviction order for save/restore/remove; remove partial saves everywhere and invalidate partially restored sequences before recomputing their prompt; missing or foreign acknowledgements make the context unusable; serialize actual owned layers with absolute indices and handle zero-cell state safely while preserving the full-model state format; clear snapshots when system prompts/adapters change; cache operation evidence establishes no throughput benefit.
- Context checkpoints: keep a separate pool/ceiling from prompt-cache RAM and independent histories per HTTP slot; capture actual completed prefill positions including on a zero-layer head (optimistic n_past can include undecoded sub-batches); recurrent snapshots require an entire matching prefix and a remaining token to rebuild logits; rebuild sampling and draft prompt state after restoration; freeze count, token spacing and the planned byte ceiling in the execution contract and negotiate `context_checkpoints_v1`; price Prima's full sequence state (rather than the baseline's partial recurrent-only state), including metadata, on every selected rank without charging the master twice; clear histories on system/adapter changes and explicit slot erase.
- Compact sliding-window KV: separate immutable owned-layer storage layout from mutable sequence/cell state; size the unified pool for each sequence's window plus the incoming microbatch with the pinned baseline's padding and checked arithmetic — a simple window-only ring can overwrite history required by the earliest token in a prefill batch; activation must connect allocation, separate cell reclaim/sequence operations, attention/mask views, shifts/defrag, snapshot serialization and capacity/profile accounting together; keep full-size mode until those consumers are complete — never advertise compact execution from sizing metadata alone or score hypothetical savings.
- Compact reservations & views: keep sequence membership authoritative in the full cache and map physical compact rows to those global cells; prepare row reservations without mutation before full-cache admission and commit only after success; bind tickets to owner identity and generation, validate restored maps atomically, and preserve compact row bytes when global metadata is defragmented; shared sequences and earliest-token prefill history constrain reclamation; compact-cache views, attention masks and K shifts must share physical row order and per-layer capacity; serialize compact layout/version/ownership/window and sparse row ordinals alongside dense cell metadata, validate before map publication, and clean up the destination on failed/truncated restore; allocate reservation commit storage before full-cache admission.
- Distributed prefix rewind: check every physical rank before any cache mutation, including a transformerless coordinator or a head without SWA layers; settle commit/abort with manifest, revision, sequence/range, phase, world and hop identity — missing or foreign settlement makes the context unusable; keep workers from accepting decode or unrelated controls while a rewind preflight is pending; while compact activation is unfinished, explicitly use and price `--swa-full` on every rank and do not treat that temporary full-layout accounting as finished compact integration.
- CLI session reuse: bind the declared local session-token prefix on each transformer rank to a domain-separated digest of count and little-endian uint32 IDs; require a materialized predecessor at the earliest actual decode boundary including logits reconstruction; empty relay ranks need no KV tensor state; replay when a worker session is missing/mismatched or required history was evicted; invalidate declarations on checked mutation or actual decode; ordinary context-shift replay requires exact evaluated token history even when session saving is disabled; positional add/divide preflight must simulate resultant positions, signed delta bounds and window availability for every shared sequence (RNN transforms operate on their tail state) with operands bound to all-rank settlement.
- Server replay: per-slot completed token history is needed even when cache_prompt is false — populate it only after successful decode views, trim speculative futures after accepted-prefix settlement, and exclude optimistic unevaluated entries from later prefix reuse; preserve the complete system prefix and aLoRA base/active weight-phase identity; replayed segments use their stored phase and restore the live request selection afterward while preserving sampler/output state; failed replay clears the affected sequence through owned request recovery.
- Self-extend: preflight grouped self-extend operations as one bounded transform program — check intermediate signed position/delta bounds but evaluate required SWA history only after the final composed result, binding every operand and order to all-rank phase/revision/manifest settlement; replay needs actual evaluated positions as well as token IDs (a session file without the transform schedule is not an ordinary reusable prefix); do not accumulate transformer replay history for recurrent self-extend; server self-extend rebases pending rows after the composed transform and advances counters only for that slot's successfully evaluated rows; division relative to a fixed system origin preserves prefix coordinates without changing the original step layout/wire contract; evaluate system prefixes on real request sequences rather than assuming head/worker sequence zero maps identically or RNN state can be shared.
- Head KV ceiling: bind Prima's head KV ceiling (HTTP slots plus its reserved sequence) into immutable settings, memory planning and qualification scopes; price local drafts separately with one sequence each; mirror the actual unified pool while per-stream support is unfinished and preserve baseline wire mappings when Prima-specific fields are absent; count completed-history metadata, compact forward/reverse map copies and snapshot row ordinals in addition to tensor storage; quantized native KV estimates must use block-aware storage and per-layer widths, including recurrent state cells and checked products/sums; keep full mode active until the final compact consumer/launch review completes.
- Compact activation: reserve graphs from immutable capacity rather than live occupancy — worst-case writes must fit the compact pool even after shifts or defragmentation; thread the reservation flag through masks, storage and attention without modifying live cache metadata; bind explicit SWA mode and the head sequence ceiling to native manifests and negotiate the advertised capability; account for the server's already-added reserved sequence during model startup while CLI workers and standalone manifest checks use the original slot count; GPT-OSS compact storage is now an implemented default — other architectures' compact paths and runtime acceptance remain open, and the historical activation guards describe earlier prerequisites rather than a current requirement to keep GPT-OSS full storage active.

## Full text (normative)

### Draft-simple decoding

For draft-simple decoding, clear target manifests/ranks/network setup in each
local draft context and suppress autonomous profiling. Respect independent K/V
cache types and explicit CPU selection; do not force mlock or inherit the target
GPU layout. Verify the actual modern flag aliases before managed preflight.
Resolve active draft files through the existing model resolver, stage complete
shards under verified basenames, and bind role/hash/size plus CPU placement and
context count in the signed scope. An inactive draft must not require its file.
Price every independent HTTP-slot draft context on the master; preserve baseline
shared-context estimation. Check bounded prefix batches, decode failure, sampler
grammar state, all accepted-token logprobs/usage, EOS/stop/output limits, invalid
request budgets and out-of-target-vocabulary proposals. Worker sequence IDs are
intentionally offset from the head's reserved sequence zero; audit the actual
metadata mapping before changing rollback IDs. Free samplers/contexts before
models, handle null speculators, and refuse failed slot initialization before
readiness. Capture accepted-token work counters, not performance claims. Check
real dense/MoE gateway paths and controlled zero-work-head native ranks, while
physical cluster/performance acceptance stays deferred and unqualified.


### Prompt caching

For prompt caching, reserve the entire configured host RAM ceiling on every
selected participant, including GPU workers and a zero-layer coordinator. Bind
the budget to signed settings and require prompt_cache_v1 before preparation.
Keep serialized KV state on its owning rank; use bounded all-rank transactions
and one eviction order for save/restore/remove. Remove partial saves everywhere;
invalidate partially restored sequences before recomputing their prompt. Missing
or foreign acknowledgements make the context unusable. Serialize actual owned
layers with absolute indices and handle zero-cell state safely; preserve existing
full-model state format. Clear snapshots when system prompts/adapters change.
Cache operation evidence establishes no throughput benefit. Defer cache contracts,
model processes and CI test execution with the rest of the test phase until the
implementation is finished. When pushing during this phase, skip CI tests and
record that they still require execution afterward.


### Context checkpoints

For context checkpoints, keep a separate pool/ceiling from prompt-cache RAM and
independent histories per HTTP slot. Capture actual completed prefill positions,
including on a zero-layer head; optimistic n_past can include undecoded sub-batches.
Recurrent snapshots require an entire matching prefix and a remaining token to
rebuild logits. Rebuild sampling and draft prompt state after restoration. Freeze
count, token spacing and the planned byte ceiling in the execution contract and
negotiate context_checkpoints_v1. Price Prima's full sequence state rather than
the baseline's partial recurrent-only state, including metadata, on every selected
rank without charging the master twice. Clear histories on system/adapter changes
and explicit slot erase. Keep checkpoint contracts, model branches and failure/
cancellation checks deferred with the rest of the test phase until implementation
completion; compilation grants no acceptance or qualification.


### Compact sliding-window KV — storage & activation prerequisites

For compact sliding-window KV, separate immutable owned-layer storage layout
from mutable sequence/cell state. Size the unified pool for each sequence's
window plus the incoming microbatch, with the pinned baseline's padding and
checked arithmetic. A simple window-only ring can overwrite history required
by the earliest token in a prefill batch. Activation must connect allocation,
separate cell reclaim/sequence operations, attention/mask views, shifts/defrag,
snapshot serialization and capacity/profile accounting together. Keep full-size
mode until those consumers are complete; do not advertise compact execution
from sizing metadata alone or score hypothetical memory/work savings. Exercise
shared prefixes, rewinds and zero-transformer ranks only in the post-implementation
test phase; physical cluster/performance gates remain separately deferred.


### Compact caches — reservations & sequence membership

For compact sliding-window caches, keep sequence membership authoritative in the
full cache and map physical compact rows to those global cells. Prepare row
reservations without mutation before full-cache admission; commit only after
success. Bind tickets to owner identity and generation, validate restored maps
atomically, and preserve compact row bytes when global metadata is defragmented.
Shared sequences and earliest-token prefill history constrain reclamation. Keep
compact execution disabled until attention views/masks, sequence rewind/replay,
shifts, snapshots and managed budgets are wired together. Compile during this
phase; defer all unit/native/model tests and test fixes until full implementation.


### Compact views, attention masks & K shifts

Compact-cache views, attention masks and K shifts must share physical row order
and per-layer capacity. Defragment global metadata without copying compact row
bytes. Serialize compact layout/version/ownership/window and sparse row ordinals
alongside dense cell metadata; validate before map publication and clean up the
destination on failed/truncated restore. Allocate reservation commit storage
before full-cache admission. Prefix reuse must replay missing evicted history,
and distributed ranks must agree on that decision before mutating their caches.
Do not activate compact mode while managed budgets or replay coordination are
incomplete. Production compilation is source/build evidence only; all tests and
performance acceptance remain on the user's deferred schedule.


### Distributed prefix rewind

Distributed prefix rewind must check every physical rank before any cache
mutation, including a transformerless coordinator or a head without SWA layers.
Settle commit/abort with manifest, revision, sequence/range, phase, world and hop
identity; missing or foreign settlement makes the context unusable. Keep workers
from accepting decode or unrelated controls while a rewind preflight is pending.
Match resolved launch settings and KV/checkpoint estimates to actual storage:
while compact activation is unfinished, explicitly use/price --swa-full on every
rank. Do not treat this temporary full-layout accounting as finished compact
integration. Keep all tests deferred until full required implementation finishes;
physical distributed and performance acceptance stay pending for the live cluster.


### CLI session reuse

For CLI session reuse, bind the declared local session-token prefix on each
transformer rank to a domain-separated digest of count and little-endian uint32
IDs. Also require a materialized predecessor at the earliest actual decode
boundary, including logits reconstruction. Empty relay ranks need no KV tensor
state. Replay when a worker session is missing/mismatched or required history was
evicted; invalidate declarations on checked mutation or actual decode. Ordinary
context-shift replay requires exact evaluated token history, even when session
saving is disabled. Positional add/divide preflight must simulate resultant
positions, signed delta bounds and window availability for every shared sequence;
RNN transforms operate on their tail state. Bind operands to all-rank settlement.
Keep compact activation disabled while server/self-extend replay or compact
managed budgets are unfinished. Preserve the user's deferred test schedule.


### Server replay

Server replay needs per-slot completed token history even when cache_prompt is
false. Populate it only after successful decode views, trim speculative futures
after accepted-prefix settlement, and exclude optimistic unevaluated entries
from later prefix reuse. Preserve the complete system prefix and aLoRA base/active
weight-phase identity. Replayed segments use their stored phase and restore the
live request selection afterward; preserve sampler/output state. Failed replay
clears the affected sequence through owned request recovery. Ordinary context
shift handling does not complete self-extend replay or compact managed budgets;
keep compact activation disabled until those consumers are complete. All tests
remain deferred until full required implementation finishes, with physical
cluster/performance acceptance pending for the user's live cluster.


### Self-extend preflight program

Preflight grouped self-extend operations as one bounded transform program. Check
intermediate signed position/delta bounds, but evaluate required SWA history only
after the final composed result. Bind every operand and order to all-rank phase/
revision/manifest settlement. Replay needs actual evaluated positions as well as
token IDs; a session file without the transform schedule cannot safely be treated
as an ordinary reusable prefix. Do not accumulate transformer replay history for
recurrent self-extend. CLI replay does not complete server replay or compact
managed budgets; retain the activation and qualification guards until complete.
Continue honoring the user's implementation-first and live-cluster test schedule.


### Server self-extend

Server self-extend needs evaluated positions, weight phases and per-slot completed
counts. Rebase pending rows after the composed transform; advance counters only
for that slot's successfully evaluated rows. Division relative to a fixed system
origin preserves prefix coordinates; extend the program API without changing the
original step layout/wire contract. Evaluate system prefixes on real request
sequences rather than assuming head/worker sequence zero maps identically or RNN
state can be shared. Retry from complete history and restore the frozen batch
selection; refuse incomplete recurrent self-extend history. Keep compact mode
guarded until managed budgets and activation review finish. All tests remain on
the user's deferred implementation-first schedule; hardware cluster/performance
acceptance remains pending and unpassed.


### Head KV ceiling

Bind Prima's head KV ceiling (HTTP slots plus its reserved sequence) into immutable
settings, memory planning and qualification scopes. Price local drafts separately
with one sequence each. Mirror the actual unified pool while per-stream support is
unfinished, and preserve baseline wire mappings when Prima-specific fields are
absent. Count completed-history metadata, compact forward/reverse map copies and
snapshot row ordinals in addition to tensor storage. Quantized native KV estimates
must use block-aware storage and per-layer widths; include recurrent state cells
and checked products/sums. Estimates and successful production builds do not prove
model correctness or hardware allocation. Keep full mode active until the final
compact consumer/launch review completes. Run no unit/native/model tests or test
fixes until full required implementation finishes; defer physical distributed and
performance acceptance to the user's live three-computer cluster.


### Compact cache activation status

For compact cache activation, reserve graphs from immutable capacity rather than
live occupancy. Worst-case writes must fit the compact pool even after shifts or
defragmentation; thread the reservation flag through masks, storage and attention
without modifying live cache metadata. Bind explicit SWA mode and the head
sequence ceiling to native manifests and negotiate its advertised capability.
Account for the server's already-added reserved sequence during model startup,
while CLI workers and standalone manifest checks use the original slot count.
GPT-OSS compact storage is now an implemented default; other architectures'
compact paths and runtime acceptance remain open. Historical activation guards
above describe earlier prerequisites, not a current requirement to keep GPT-OSS
full storage active. Keep qualification off and follow the user's deferred test
and physical cluster/performance schedule.


---

**Related:** aLoRA weight phases in replay and caches are specified in 05-lora.md; per-architecture SWA patterns and norm orders live in 08-models-architectures.md.
