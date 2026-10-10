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

## Status

Preparation and contract implementation is in progress. Prima is not qualified for
normal inference selection. A successful source build is not API/platform parity.
See tasks.md for completed work and the remaining implementation, and validation.md
for actual evidence. Existing local binding stubs are preserved and are not used.
