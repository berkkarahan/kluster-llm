# 03 · Windows native build, ISA detection & error boundaries

**Use when:** You build on Windows (Release targets, staged executables, relocation), deal with ISA/CPUID detection and AVX-512 gating, CPU graph scheduling and cancellation, or MSVC `/EHsc` exception boundaries in exported C entry points (model/context/backend/grammar/socket setup, sampler ownership).

**Keywords:** Windows, MSVC, /EHsc, C4297, CPUID, OSXSAVE, XCR0, AVX-512, GGML_NATIVE, system_info, relocation, graph scheduling, cancellation, OpenMP, pthread, sampler, grammar, socket, exception, exported C.

**Related:** 02-build-lifecycle.md, 04-gpu-vulkan.md, 10-profile-costs.md.

## Quick rules

- Build Release server, CLI, profiler and native contract tests; check staged executables and relocation to a path containing spaces outside the compiler shell; preserve only actual required runtime dependencies.
- Compare the effective compiler ISA and the serving binary's `system_info` with the baseline — cached CMake option values can differ from native-detected flags.
- MSVC native instruction detection must check CPUID, OSXSAVE/XCR0 and runnable compiler intrinsics rather than optimized-away vectors; enable AVX-512 extensions independently only when supported, refresh probes on reconfiguration, and retain GGML_NATIVE=OFF/manual ISA handling.
- Missing CPU/OS prerequisites are checked through deterministic vectors and an actual portable build — that does not prove host throughput or permit distributing a host-specific artifact to another CPU.
- CPU graph scheduling changes preserve real-operation dependencies and final worker settlement while skipping only metadata-only work; cancellation must stop all workers at the same node boundary — a shared boolean can strand a late worker.
- Cancellation regression battery: prior cancellation leaves outputs untouched, cancellation after real work, repeated pool reuse, alias/empty graphs, both OpenMP/pthread implementations with one/multiple workers; bound regression processes and retain timed-out owned-process evidence — termination is cleanup, not a passing test or performance result.
- MSVC `/EHsc` assumes exported C calls cannot throw: keep exceptions inside exported C entry points and return nullptr/status from model/context/backend/grammar/socket setup, refusing readiness on failure.
- Ownership details (full text): backend setup borrows its context and callers free it once; close only distinct owned socket handles with zero linger; refuse reinitialization without closing existing live handles; use checked sampler operations and thread-local error text for managed C++ callers; a null grammar must never become unconstrained generation; preserve allocation ownership on grammar reset/clone failures; sampler failure fails the owning HTTP request only, retaining other requests and valid snapshots.

## Full text (normative)

### Windows build, ISA detection & CPU graph scheduling

- On Windows, build Release server, CLI, profiler and native contract tests;
  check staged executables and relocation to a path containing spaces outside
  the compiler shell. Preserve only actual required runtime dependencies.
  Compare effective compiler ISA and the serving binary's system_info with the
  baseline; cached CMake option values may differ from native-detected flags.
  MSVC native instruction detection must check CPUID, OSXSAVE/XCR0 and runnable
  compiler intrinsics rather than optimized-away vectors. Enable AVX-512 extensions
  independently only when supported, refresh probes on reconfiguration, and retain
  GGML_NATIVE=OFF/manual ISA handling. Check missing CPU/OS prerequisites through
  deterministic vectors and an actual portable build; this does not prove host
  throughput or permit distributing a host-specific artifact to another CPU.
  For CPU graph scheduling changes, preserve real-operation dependencies and final
  worker settlement while skipping metadata-only work. Cancellation must stop all
  workers at the same node boundary; a shared boolean can strand a late worker.
  Check prior cancellation leaves outputs untouched, cancellation after real work,
  repeated pool reuse, alias/empty graphs and both OpenMP/pthread implementations
  with one/multiple workers. Bound regression processes and retain timed-out owned
  process evidence; termination is cleanup, not a passing test or performance result.


### MSVC /EHsc exception boundary, sampler & socket ownership

For Windows native error handling, keep exceptions inside exported C entry
points: MSVC /EHsc assumes those calls cannot throw. Return nullptr/status from
model/context/backend/grammar/socket setup and refuse readiness on failure.
Backend setup borrows its context; callers free it once. Close only distinct owned
socket handles with zero linger, accounting for send/master aliases and partial
initialization. Refuse reinitialization without closing existing live handles.
Use checked sampler operations and thread-local error text for managed C++ callers;
keep nested chain callback failures visible at that boundary. A null grammar must
never become unconstrained generation. Preserve allocation ownership on grammar
reset/clone failures and bind cloned candidate pointers to copied storage. Fail
the owning HTTP request and clear its head/worker sequence on sampler failure,
while retaining other requests and valid snapshots. Keep invalid input, callback,
fully masked candidate, allocation/port/backend failure and model cleanup checks
in the deferred test phase until implementation completion. Compiler success or
removal of C4297 warnings is not runtime correctness or qualification evidence.


---

**Related:** Vulkan initialization also sits inside the `/EHsc` boundary — see 04-gpu-vulkan.md (driver-unavailable startup).
