# Performance Validation Contract

## Baseline identity

Every comparison records revision/tree state, OS/build, CPU and memory, Python/pytest/Node/
browser/Cargo versions, loaded native extension path, dependency lock state, command, build
mode, workload, cache reset/warm state, case outcomes, elapsed distribution and resource
measurements. Separate setup/collection, test/runtime work, teardown and whole-command wall
time. A benchmark result is invalid if the compared case list, opt-in skips, loaded binary or
workload changed without explanation.

The planning reference has a single initial run only:

| Suite | Initial observation | Required comparison |
|---|---|---|
| Python | 516 passed, 5 skipped, 80.53 s pytest time; `uv run --no-sync pytest -q --durations=30` | Five matched, uninstrumented complete runs per revision; same collected IDs and skip reasons. |
| Dashboard | 47 passed, 46.689 s; Node single concurrency; browser scenario 24.896 s | Five matched full runs, retain real Chrome/Edge checks and same views/assertions. |
| Rust | 34 passed, 0.23 s test bodies / 10.418 s command wall | Re-measure command and body separately; protect semantics and do not claim reduction without evidence. |

Repeat new baseline before implementation acceptance; the single readings above are not medians.

## Log sizing workload

- 500 distinct event IDs, each 1,024 ASCII message characters, same fixture/order/metadata.
- Call uncached live-envelope preparation 20 times per batch × 5 batches; include warmup outside
  timing, use `perf_counter`, do not include executor startup, network or feed creation.
- Compare exact rows, IDs, order, cursor/highwater, reset, continuity, gap/reason and complete
  result bytes. Include ASCII, Unicode, escaping, one oversized row, metadata overflow, existing
  retention/count gap and invalid source row cases.
- Measure the complete logs result at ≤256 KiB (browser boundary), not snapshot-only JSON.
- Report each batch/median and p95 over 100 measured operations. Acceptance: ≥50% median
  reduction and all equivalence/boundary checks passing.

## Complete suites and test controls

Keep these complete serial invocations available:

```powershell
uv run pytest -q
node --experimental-strip-types --test --test-concurrency=1 "dashboard/tests/*.test.mjs"
cargo test --manifest-path crates/kllm-native/Cargo.toml
npm --prefix dashboard run check
npm --prefix dashboard run build
```

During diagnosis only:

```powershell
uv run pytest -q --durations=0 --durations-min=0
```

Test category selections are additive local feedback commands. They never replace the complete
commands above. Preserve browser/hardware test collection and explain opt-in skips. Run
dashboard check, Node tests and build sequentially because they share generated SvelteKit state.
Do not execute browser tests while measuring a suite baseline.

Useful focused baseline commands after test markers/seams land:

```powershell
uv run pytest -q -m unit
uv run pytest -q -m integration
uv run pytest -q tests/test_dashboard_live.py tests/test_cluster_log.py tests/test_cp_proxy.py
node --experimental-strip-types --test --test-concurrency=1 dashboard/tests/logs.test.mjs dashboard/tests/live-updates.test.mjs
```

No new worker/concurrency dependency is selected. If separately approved after serial
optimization, compare serial and 2/4 workers with process/state/port isolation and three
repeat complete runs; keep serial complete validation.

## Lifecycle and boundary matrix

- Run complete tests 5× before and 5× after, same machine/idle policy, no simultaneous builds.
- Run affected focused tests in isolation, reverse order and 3× complete-run stability.
- Maintain identical test IDs/counts/skip reasons, no failures and no weakened outcomes.
- Prove no test-owned children, sockets, HTTP servers, Vite watchers, timer tails or helper
  processes remain. Test success is not inferred from forced process exit.
- Keep real HTTP/WS, subprocess, native-helper deadline/reaping, journal, authenticated
  worker forwarding and browser checks; controlled dependencies apply only to unit cases.
- Report medians/percent changes and distributions; do not sum microbenchmark gains.
- Affected Windows/Linux/macOS code gets target evidence. Unavailable hardware remains open.
- Any inference comparison uses identical model, prompt/token count, backend, topology,
  llama.cpp pin, launch settings, warm/cold state and hardware. This plan changes no such knob.
