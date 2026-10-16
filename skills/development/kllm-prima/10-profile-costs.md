# 10 · Profiling, measurements & link contracts

**Use when:** You run or interpret kernel/model profiling, full-model kernel jobs, native I/O probes, signed profile start/status/cancel flows, private-link measurements and transfer grants, device-memory handoff/logits measurements, node-work reservations and resource guards, or GGML_VULKAN_PERF diagnostic builds.

**Keywords:** profiling, kernel, GFLOPS, roofline, MoE, indexed, expert offsets, I/O probe, signed profile, profile protocol capability, private link, ring, directed edges, profile_edges, protocol2, peer keys, receipts, transfer grants, renewals, authority, logits, embedding handoff, storage device, resource guards, supervisor journals, GGML_VULKAN_PERF, diagnostic_vulkan_timing_v1, application-link proxy, warm-up, paired timing.

**Related:** 02-build-lifecycle.md (validation schedule), 04-gpu-vulkan.md (device selection), 12-placement-qualification.md (capacity-first selection, evidence gating).

## Quick rules

- Kernel profiling: encode operands into their actual tensor type, check compute status and completed output against decoded operands, and refuse unsupported GPU operations without CPU substitution; verify every affected CPU format and the selected physical GPU separately; GFLOPS from malformed storage are not evidence, and successful synthetic matrices do not prove the full-model profiler or planner; invalidate affected cached measurements through immutable source/recipe identity.
- Indexed MoE kernels: verify input and down projections, distinct token/expert offsets, padded ID views, one/multiple selected experts, odd output widths and the small-batch/general dispatch boundary on CPU and the requested physical GPU; compare against decoded stored operands, accounting explicitly for CPU dot-input quantization and any existing F16 decoding with a numerical rounding bound; keep model correctness/performance gates unchanged.
- Paired timing & graph optimizations: run paired timing after contract processes/builds settle — identical text can still have different generated token sequences, which must remain visible rather than being retokenized as usage; compare graph optimizations independently on CPU and the selected GPU (fewer graph nodes can still regress host execution); preserve host-weight paths when measurements show regression and select device-memory optimizations by actual buffer ownership; check signed/odd-width/multi-token expert reductions, neighboring tensor preservation and completed alternating operation timings — operation microbenchmarks do not replace full-model paired acceptance, and a final matched model receipt that regresses rejects the optimization even if a short operation sample improves; expert sums must include cancellation-sensitive F32 values (tree and serial additions can differ and change greedy generation).
- Vulkan strides & shaders: direct-stride paths must retain host/other-backend handling, check view offsets/stride widths/dispatch bounds and refuse unsupported layouts; adding shaders must update incremental generation dependencies as well as pass a clean managed build — an old generated header is not current-source evidence.
- Full-model kernel jobs: extract complete GGUF metadata without replacing live model tensors; count actual attention dimensions, head norms and active MoE experts, retain full storage costs and refuse missing/unknown roles; validate typed per-layer and input/output estimates against their rate tables and exact context/threads/backend; bind cache identity to observed host/device hardware and recheck before publication; keep roofline estimates distinct from generation timings and qualification.
- Native I/O probes: use an exclusive owned file on the model filesystem with explicit cache/alignment settings, checked reads, bounded work and handle-owned cleanup on child death; record unavailable storage separately — do not substitute paged/RAM throughput.
- Signed profile protocol: start/status/cancel must bind the exact approved preparation, operation, manifest, sender/authority, model/runtime/rank and effective settings; advertise/require the profile protocol capability before transferring assets — generic signed-channel capability is insufficient; private link measurements follow actual directed rank edges on the approved private listener with checked bytes, signed sample receipts and exact source/assignment revisions; delegate worker peer keys only through ephemeral master-approved preparation/manifest/authority scopes, recheck ownership during measurement and preserve pinned pairing; derive extra profile_edges only from representable feasible candidates among already prepared participants, bind them to the immutable signed manifest, and require link protocol2 before asset transfer when extras exist (preserving legacy canonical digests when absent); measurement approval never widens native listener rules or changes execution ranks; reject unapproved/duplicate receipts — missing any candidate link keeps capacity-first planning; check a four-agent removal case over real signed transport, distinguishing fixture rates/adapters from physical distributed acceptance.
- Stream & ownership discipline: bound stream work, concurrency and cancellation; use a high-resolution clock for elapsed transfer timing on Windows; label authentication/receipt latency and acknowledged application throughput explicitly (they do not prove raw NIC speed, native generation or qualification); require link protocol capability before assets; long jobs recheck current authority and the progress-renewed preparation lease rather than extending the original wire command deadline; persist owners, refuse foreign status/cancel and request-ID adoption, and keep cancellation atomic with cache publication; import peer results only after matching current signed hardware evidence and complete typed context — normal previews remain read-only until link/context evidence exists.
- Memory handoffs: distinguish device memory reads/KV copies from host uploads/downloads; measure completed exact model-width F32 handoffs on the requested GPU, warm once, retain checked sample arrays, and refuse CPU substitution or advertised capability downgrade; bind profile storage to the OS filesystem device identifier and owned directory, rechecking before publication and through preparation-owned signed fresh context; measure and byte-check completed F32 downloads at actual model vocabulary width, retain exact positive samples, refuse missing advertised logits capability before publication, and bind the cost to GGUF token embedding dimensions; account for connected local versus separate distributed transformer/output graphs — GPU output avoids the local intermediate embedding download while the distributed output graph uploads its host embedding again; missing evidence preserves capacity-first selection, and previews must not run measurements, restage layouts or write caches.
- Node-work reservation & guards: reserve node work before model resolution or native listener/rank startup using the same registry guard for Prima and llama.cpp/RPC; bind sharing to exact operation, authority and contract — owned preparation may profile its own assets while overlapping measurements or foreign work refuse; keep worker transfer claims through complete verified bytes and listener claims through durable cleanup; cancellation/terminal labels cannot release unfinished futures or live owned children; retain failed generation cleanup identity and cleanup_pending ownership for retry; tie receive grants to a real bounded timer and settle source/reader tasks before releasing their token; resource guards do not replace durable supervisor journals or leases — negotiate resource capability before distributed transfer/admission and test all-node refusal before spawn, unrelated-owner preservation and startup across both engines.
- Transfer grants: exercise transfers beyond their initial approval lifetime with complete bytes and hashes; issue source grants just before each asset, extend them only on exact forward acknowledgements and reject source drift; use fresh operation/sender/authority-bound preparation progress renewals for already-complete participants — duplicate, stalled, expired or cancelled work must not revive approval; keep cache verification and chunk I/O off the command event loop and verify signed transport separately from simulated clocks and physical multi-machine acceptance.
- GGML_VULKAN_PERF builds: diagnostic artifacts only — require their actual `diagnostic_vulkan_timing_v1` capability and refuse them as acceptance inputs before starting model or placement processes; capture development binary capabilities with a bounded owned probe and verify its hash is unchanged (never reuse the published runtime's capability record); retain owned server logs through failure and cleanup; GPU timestamp queries must preserve normal batching, check queue support and result availability after completion, handle counter wrap and units, bound query allocation, and never wait for unused slots or invent missing samples; restore ordinary non-instrumented builds for normal correctness validation.

## Full text (normative)

### Kernel profiling & indexed MoE kernels

- For kernel profiling, encode operands into their actual tensor type, check compute
  status and completed output against decoded operands, and refuse unsupported GPU
  operations without CPU substitution. Verify every affected CPU format and the
  selected physical GPU separately. GFLOPS from malformed storage are not evidence;
  successful synthetic matrices do not prove the full-model profiler or planner.
  Invalidate affected cached measurements through immutable source/recipe identity.
  For indexed MoE kernels, verify input and down projections, distinct token/expert
  offsets, padded ID views, one/multiple selected experts, odd output widths and the
  small-batch/general dispatch boundary on CPU and the requested physical GPU.
  Compare against decoded stored operands; account explicitly for CPU dot-input
  quantization and any existing F16 decoding with a numerical rounding bound. Keep
  model correctness/performance gates unchanged. Run paired timing after contract
  processes/builds settle; identical text can still have different generated token
  sequences, which must remain visible rather than being retokenized as usage.
  Compare graph optimizations independently on CPU and the selected GPU. Fewer
  graph nodes can still regress host execution. Preserve host-weight paths when
  measurements show that regression and select device-memory optimizations by
  actual buffer ownership. Check signed/odd-width/multi-token expert reductions,
  neighboring tensor preservation and completed alternating operation timings;
  operation microbenchmarks do not replace full-model paired acceptance.
  Reject a graph optimization when the final matched model receipt regresses,
  even if a short operation sample improves. For expert sums, include cancellation-
  sensitive F32 values: tree and serial additions can differ and change greedy
  generation. Vulkan direct-stride paths must retain host/other-backend handling,
  check view offsets/stride widths/dispatch bounds and refuse unsupported layouts.
  Adding shaders must update incremental generation dependencies as well as pass
  a clean managed build; an old generated header is not current-source evidence.


### Full-model kernel jobs & native I/O probes

  For full-model kernel jobs, extract complete GGUF metadata without replacing live
  model tensors. Count actual attention dimensions, head norms and active MoE experts,
  retain full storage costs and refuse missing/unknown roles. Validate typed per-layer
  and input/output estimates against their rate tables and exact context/threads/backend.
  Bind cache identity to observed host/device hardware and recheck before publication.
  Keep roofline estimates distinct from generation timings and qualification. Native
  I/O probes must use an exclusive owned file on the model filesystem, explicit cache/
  alignment settings, checked reads, bounded work and handle-owned cleanup on child
  death. Record unavailable storage separately; do not substitute paged/RAM throughput.


### Signed profile protocol & private links

  Signed profile start/status/cancel must bind the exact approved preparation,
  operation, manifest, sender/authority, model/runtime/rank and effective settings.
  Advertise/require the profile protocol capability before transferring assets;
  generic signed-channel capability is insufficient. Private link measurements must
  follow actual directed rank edges on the approved private listener, with checked
  bytes, signed sample receipts and exact source/assignment revisions. Delegate
  worker peer keys only through ephemeral master-approved preparation/manifest/
  authority scopes, recheck ownership during measurement and preserve pinned pairing.
  Candidate exclusion may create new directed ring links. Derive extra profile_edges
  only from representable feasible candidates among already prepared participants,
  bind them to the immutable signed manifest, and require link protocol2 before
  asset transfer when extras exist. Preserve legacy canonical digests when absent.
  Measurement approval never widens native listener rules or changes execution ranks.
  Reject unapproved/duplicate receipts; missing any candidate link keeps capacity-first
  planning. Check a four-agent removal case over real signed transport and distinguish
  fixture rates/adapters from physical distributed acceptance.


### Stream work, renewals & peer imports

  Bound stream work, concurrency and cancellation; use a high-resolution clock for
  elapsed transfer timing on Windows. Label authentication/receipt latency and
  acknowledged application throughput explicitly; they do not prove raw NIC speed,
  native generation or qualification. Require link protocol capability before assets. Long jobs recheck current
  authority and the progress-renewed preparation lease rather than extending the
  original wire command deadline. Persist owners, refuse foreign status/cancel and
  request-ID adoption, and keep cancellation atomic with cache publication. Import
  peer results only after matching current signed hardware evidence and complete
  typed context; normal previews remain read-only until link/context evidence exists.


### Device handoffs, logits & graph topology

  Distinguish device memory reads/KV copies from host uploads/downloads. Measure
  completed exact model-width F32 handoffs on the requested GPU, warm once, retain
  checked sample arrays and refuse CPU substitution or advertised capability downgrade.
  Bind profile storage to the OS filesystem device identifier and owned directory,
  recheck before publication and through preparation-owned signed fresh context.
  Score absolute assigned CPU/GPU indices and explicit input/output ownership only
  when every feasible candidate has matching source/model/settings/hardware/storage
  and fresh exact directed links. Keep application link proxies and logical weight-read
  estimates labelled; do not add cold loading to steady-state decode. Embedding handoffs
  cannot supply GPU logits download costs. Measure and byte-check completed F32
  downloads at actual model vocabulary width, retain exact positive samples and refuse
  missing advertised logits capability before publication. Bind the cost to GGUF
  token embedding dimensions. Account for connected local versus separate distributed
  transformer/output graphs: GPU output avoids the local intermediate embedding
  download, while the distributed output graph uploads its host embedding again. Missing evidence preserves capacity-first
  selection, and previews must not run measurements, restage layouts or write caches.


### Node-work reservation & resource guards

  Reserve node work before model resolution or native listener/rank startup, using
  the same registry guard for Prima and llama.cpp/RPC. Bind sharing to exact operation,
  authority and contract; owned preparation may profile its own assets, while overlapping
  measurements or foreign work refuse. Keep worker transfer claims through complete
  verified bytes and listener claims through durable cleanup. Cancellation/terminal
  labels cannot release unfinished futures or live owned children. Retain failed
  generation cleanup identity and cleanup_pending ownership for retry. Tie receive
  grants to a real bounded timer and settle source/reader tasks before releasing their
  token. Resource guards do not replace durable supervisor journals or leases; negotiate
  resource capability before distributed transfer/admission and test all-node refusal
  before spawn, unrelated-owner preservation and startup across both engines.


### Transfer grants beyond initial approval

- Exercise transfers beyond their initial approval lifetime with complete bytes and
  hashes. Issue source grants just before each asset, extend them only on exact
  forward acknowledgements and reject source drift. Use fresh operation/sender/
  authority-bound preparation progress renewals for already-complete participants;
  duplicate, stalled, expired or cancelled work must not revive approval. Keep cache
  verification and chunk I/O off the command event loop and verify signed transport
  separately from simulated clocks and physical multi-machine acceptance.


### GGML_VULKAN_PERF diagnostic builds

Optional GGML_VULKAN_PERF builds are diagnostic artifacts. Require their actual
diagnostic_vulkan_timing_v1 capability and refuse them as acceptance inputs before
starting model or placement processes. Capture development binary capabilities
with a bounded owned probe and verify its hash is unchanged; do not reuse the
published runtime's capability record. Retain owned server logs through failure
and cleanup. GPU timestamp queries must preserve normal batching, check queue
support and result availability after completion, handle counter wrap and units,
bound query allocation, and never wait for unused slots or invent missing samples.
Restore ordinary non-instrumented builds for normal correctness validation.


---

**Related:** scoring preconditions and capacity-first selection when evidence is missing live in 12-placement-qualification.md; the deferred performance gates are stated in 02-build-lifecycle.md.
