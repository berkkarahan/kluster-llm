# Prima.cpp integration

## Accepted objective

Integrate Prima as an optional managed engine without breaking existing Kluster APIs,
platforms, model features, or deployment settings. Modernize Prima upstream, rather
than porting its distributed implementation into current llama.cpp. Kluster owns
membership, master authority, final placement and process lifecycle. Prima profiling
informs Kluster's planner. llama.cpp remains the default.

## Acceptance

- Windows, Linux and macOS, with the same backend contracts as the merged baseline.
- Vulkan GPU computation is mandatory on Windows, Linux and macOS (MoltenVK).
  Qualify real GPU offload, selected-device execution, profiling, placement and
  distributed generation through Vulkan. CPU, Metal or CUDA success cannot satisfy
  this requirement or substitute for a requested Vulkan run.
- Existing API request/response, streaming, authentication, tools, templates, model
  identifiers, supported model formats and deployment features retain their behavior.
- Exact layer/device placement and per-rank GPU offload; no rounded approximation of
  a placement Prima cannot express, no silent engine switch or feature loss.
- Signed run ownership, private networking, readiness barriers, verified model
  preparation, cancellation and recovery across selected ranks.
- At least 10% improvement on a representative distributed workload, no more than
  5% median TTFT/throughput regression elsewhere in the qualification matrix,
  unchanged model bytes/settings/quality, no OOMs and bounded memory.
- Physical backend, mixed-node and performance qualification remains distinct from
  isolated unit/process/browser checks.

## Current delivery boundary (2026-10-12)

Implementation-first scheduling: the user has deferred all unit/native/model tests
and test-fix work until the full required implementation is finished. Production
compilation and source/artifact identity checks may continue. Earlier references
to continuing correctness checks below are subject to this newer schedule.

The current continuation finishes Windows/Vulkan implementation and correctness
checks. At the user's request, performance-gate validation is deferred to their
live Kluster cluster of three computers; do not run further local paired performance
acceptance or performance-gate diagnostic experiments. Preserve the acceptance
thresholds above and record deferred performance gates as unpassed.

Physical distributed cluster gates and validations will also be tested later.
This covers physical multi-node generation, mixed-backend/platform cluster
acceptance and private-network failure/recovery exercises. Implementation,
functional profiler checks and controlled correctness tests continue. Later live
results must be added to validation.md before the corresponding qualification is
enabled. Deferral does not grant a performance pass or silently enable a release scope.

## Status

Preparation and contract implementation is in progress. Prima is not qualified for
normal inference selection. A successful source build is not API/platform parity.
See tasks.md for completed work and the remaining implementation, and validation.md
for actual evidence. Existing local binding stubs are preserved and are not used.
