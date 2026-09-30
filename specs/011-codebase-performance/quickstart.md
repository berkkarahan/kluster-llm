# Quickstart: Performance Feature Validation

These commands are validation instructions for implementation; they do not claim the feature
is implemented or that targets have passed. Run from repository root. Follow
[`docs/agent-install.md`](../../docs/agent-install.md) if toolchains/environment are missing.

## Prerequisites

- Windows reference: uv Python environment and rebuilt current project extension as needed;
  Node 22, locked dashboard dependencies, Chrome/Edge for the retained browser tests.
- Same revision, dependency locks, power state and idle host conditions for matched baselines.
- No user cluster, credentials, network service, model or GPU is needed for synthetic log and
  ordinary controlled unit checks. Relevant platform/hardware integration cases keep explicit
  existing prerequisites.
- Inspect the active extension path for native boundary runs so an old wheel does not shadow
  the rebuilt code.

## Complete regression commands

```powershell
uv run pytest -q
node --experimental-strip-types --test --test-concurrency=1 "dashboard/tests/*.test.mjs"
cargo test --manifest-path crates/kllm-native/Cargo.toml
npm --prefix dashboard run check
npm --prefix dashboard run build
```

Run dashboard typecheck, Node tests and build sequentially. Every complete run keeps all
existing non-opt-in test cases and original explicit skip reasons. `-m unit` and `-m
integration` are convenience selections, not complete validation.

## Matched suite timing

1. Record revision, dirty tree, OS/CPU/RAM, runtimes, browser, loaded native module, cache
   state and case/skip lists. Do not run concurrent builds/benchmarks.
2. Capture five uninstrumented complete baseline runs and five optimized runs. Record whole
   command elapsed, runner elapsed, outcome list, phase timings from diagnostic runs, and
   leaks/cleanup. Use diagnosis separately:

   ```powershell
   uv run pytest -q --durations=0 --durations-min=0
   ```

3. Acceptance requires unchanged cases/assertions, no new unexplained skip/failure, Python
   median at least 30% lower and dashboard median at least 20% lower. Re-run the changed
  selection in isolation and reverse order; 3 consecutive complete runs must pass.

Controlled category examples after markers exist:

```powershell
uv run pytest -q -m unit
uv run pytest -q -m integration
```

## Oversized log preparation

Build the 500 identified-message fixture and time 100 uncached log preparations (five batches
of 20, warmup excluded). Use the workload and exact baseline generator in
[`research.md`](research.md). Start/end timer around `_log_result`/its replacement, not feed or
browser startup. Assert the complete `logs` result including wrapper is ≤256 KiB. For every
sample assert exact selected newest suffix, event IDs/order, highwater cursor, reset, continuity,
gap reason and no mutation of the source rows. Report each batch, median and p95; gain must be
at least 50%. Test Unicode/escaping, one oversized row, metadata-only overflow, invalid omitted
data, 500/501 count, retention and existing aggregate frame sizing.

## Deterministic and real test lanes

- Unit lane: external socket/HTTP/GPU failures are controlled at the dependency boundary;
  assert the same route/helper behavior and diagnostics.
- Integration lane: real local HTTP/WS, private worker ingestion/forwarding, process-owned
  helper timeout/reaping and production deadline behavior remain exercised.
- Browser lane: real existing Chrome/Edge browser tests retain layout, keyboard, rendering,
  update and cleanup assertions; replace only blind scheduling sleeps with a successful
  completion/render signal.
- Identity protocol: test legacy and explicitly negotiated modes separately. Run producer
  admission → journal/reload → worker mirror → HTTP/WS → dashboard, including retries, duplicate
  conflict, holes/frontier, slot exhaustion, Clear, resets, legacy downgrade and bounded memory.
  An unsupported durable authority must surface unsupported; it must not be counted as a pass
  for negotiated mode.

## Platform/inference boundary

Run relevant test/build commands on Windows, Linux and macOS for changed code paths. Hardware
checks remain opt-in with their declared device/OS setup. Do not claim tokens/s gains from suite
or synthetic planner timings. If a separate inference performance claim is later made, use
[`scripts/compare_inference.py`](../../scripts/compare_inference.py) with the same real model,
input/output, backend, topology, engine pin, settings and cache state; record machine identity
and supplement its five-request median/min/max with enough observations for the claim.

## Stop conditions

- Any missing/changed test identity, new unexplained skip, real-boundary regression or leak.
- A >5% repeatable regression in a protected workload after paired remeasurement.
- Log result >256 KiB, altered cursor/gap/order/IDs, partial commit or source mutation.
- Negotiated dedup/window overflow that silently evicts, skips holes, reuses retired positions
  or accepts an old ID at a new position.
- No proof of durable slot/generation recovery: leave negotiated mode explicitly unsupported.
- Missing target hardware/platform evidence: report the affected acceptance as open.
