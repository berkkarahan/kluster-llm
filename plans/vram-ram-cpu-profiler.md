# VRAM / RAM / CPU resource profiler plan

> **Created:** 2026-09-29
> **Status:** Planned
> **Repo / scope:** kluster-llm — Rust Windows/macOS probes and existing monitoring

## Conventions captured at creation

- `crates/kllm-native/src/hw.rs:probe_capacity` and `planner.rs:effective_vram_bytes`
  describe capacity/budget, including the legacy 80% Metal policy; do not replace them with usage.
- `python/kllm/usage.py:Sampler.sample` collects psutil/subprocess telemetry only with a
  managed child. `UsageStore` stores process-lifetime JSON; `usage.validate` is strict.
- `python/kllm/agent/__init__.py:_usage_loop`, `usage_ingest`, `usage_live`, and
  `_tracked_process_start` are existing integration/facade boundaries.
- PyO3 0.23, sysinfo 0.33.1, Rust edition 2021 and Python 3.12 are present. New collection
  needs checked native bindings for truthful failures, precise process identity and GPU scope.
- Constitution 1.1.0 requires scoped edits, runnable behavior/integration checks, explicit
  failures and real platform evidence. All plan gates pass at design level; code is not built.

## Intended sequence

1. Implement typed Rust host/process readings and scoped GPU memory observations.
2. Add two bounded agent-owned helper slots for host/process and GPU blocking sources.
3. Adapt existing sampling and negotiate optional live resources over existing routes.
4. Preserve process history and capacity, expose host-only live data and truthful memory labels.
5. Rebuild the wheel and validate Windows, macOS, Linux compatibility and failure scenarios.

Windows uses PDH adapter-memory counters plus compatible DXGI capacity. Public macOS Metal
allocation/budget fields are not device-wide GPU usage; shared pool metadata is available,
while unsupported usage remains unavailable. No new monitoring service or dashboard redesign.

## Detailed handoff

- [Specification](../specs/010-vram-ram-cpu-profiler/spec.md)
- [Implementation plan](../specs/010-vram-ram-cpu-profiler/plan.md)
- [Research](../specs/010-vram-ram-cpu-profiler/research.md)
- [Data model](../specs/010-vram-ram-cpu-profiler/data-model.md)
- [Native contract](../specs/010-vram-ram-cpu-profiler/contracts/native-probes.md)
- [Transport contract](../specs/010-vram-ram-cpu-profiler/contracts/usage-transport.md)
- [Validation guide](../specs/010-vram-ram-cpu-profiler/quickstart.md)

Phases 0–1 complete; tasks, implementation and hardware acceptance remain future work.
Freeze this point-in-time intent after realization; record actual behavior in operational docs.
