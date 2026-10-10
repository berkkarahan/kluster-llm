# Verification — 2026-10-10

## Captured results

| Check | Result |
| --- | --- |
| `git merge-base --is-ancestor origin/main HEAD` | Passed; remote main d668eb7 merged as 1195bfe. |
| `uv run python -m pytest -q` | 1,216 passed, 14 skipped, 8 existing deprecation warnings; 25.06 s. |
| Latest `uv run python -m pytest -q` | 1,228 passed, 14 skipped, 8 existing deprecation warnings; 25.04 s. |
| `uv run python -m pytest -q tests/test_prima_integration.py` | 23 passed, including a real local controlled profiler subprocess. |
| Final worker/Prima boundary selection | 50 passed after adding refusal before worker firewall/process changes. |
| `npm --prefix dashboard test` | 63 module tests and 8 serial browser tests passed. |
| `npm --prefix dashboard run check` | 0 errors, 0 warnings. |
| `PYO3_PYTHON=<uv Python> cargo test --manifest-path crates/kllm-native/Cargo.toml` | 45 passed; no native source changes in this feature. |
| `uv run python scripts/build.py --skip-path-shim` | Passed; native extension/wheel and production dashboard rebuilt. |
| Final package rebuild with `--skip-ui --skip-path-shim` | Passed after the final worker gate change. |
| New and affected Python modules, `ruff check` | Passed. |
| Feature diff whitespace checks | Passed; original unrelated `src/kllm/__init__.py` trailing blank line was preserved and excluded. Literal unified patch context has scoped Git whitespace attributes. |

The final managed Prima installer was run with the pinned repository/commit, resolved
recipe digest, `--engine primacpp`, `--backend cpu`, `--from-source`, and isolated
`--runtime-dir /tmp/kllm-prima-managed-final`. It exited 0 and built/staged
`llama-server`, `llama-cli` and `profile-tool`. RuntimeRegistry verified those artifact
digests; the recorded recipe matched the recipe at that verification. This was a real macOS CPU
compile with downloaded, pinned ZeroMQ sources, not a mocked build.

The earlier Kluster wheel included all Prima Python modules, recipe JSON and source
patches. The native extension imports successfully. Existing uncommitted binding
stubs and unrelated whitespace were preserved; neither is used by the new feature.

## Limits

- No Vulkan model inference, Windows/Linux, mixed-node or distributed Prima
  generation result is claimed. The controlled subprocess test is not a real model
  benchmark.
- No before/after inference performance or model quality measurements were made.
- Current Prima help/source lacks baseline options including `--fit`, `--kv-unified`,
  `--ctx-checkpoints` and `--cache-ram`. Distributed execution assumptions also need
  modernization.
- The topology adapter and profiling/staging primitives do not constitute signed
  distributed rank execution or an integrated profile-driven planner. Those tasks
  remain open in tasks.md.
- The recipe's qualification gates remain false. No gate was marked successful
  based on a fixture, build, browser pass or fabricated benchmark.
- No live cluster network or inference process was changed. Kluster integration edits
  remain uncommitted; the Prima native changes were separately committed and published
  to the fork as recorded below.

## Mandatory Vulkan requirement

Vulkan GPU inference is now an independent mandatory qualification gate on Windows,
Linux and macOS (MoltenVK). The policy change changes the recipe identity, so previous
evaluation builds require a new resolution/build. Their CPU compile result remains
historical evidence and does not qualify Vulkan support.

Profiling now selects the matching accelerator metric instead of taking the faster
Metal/CUDA value. Missing, zero or nonfinite Vulkan measurements fail explicitly.
The pinned source now has an initial Vulkan Q4K/copy/bandwidth profiler path, but it
still needs model-backed profile runs, scheduler validation and real GPU offload and
distributed validation. Device enumeration alone is not an inference result.

The pinned source patch now builds a Vulkan-only Q4K kernel measurement, Vulkan
memory-copy delay and Vulkan read-bandwidth metric. The Vulkan kernel path verifies
the selected graph backend and returns no value when Vulkan cannot execute the graph;
it does not use CPU fallback. Profile output exposes these measurements, while
Kluster still requires a nonzero Vulkan Q4K result for Vulkan profiles.

Latest managed Vulkan source-build evidence (2026-10-10): recipe digest
`01a66905c394416f3db98b653aa08fbec017724e716e0271066b16c593e23f72` built the
pinned source and staged server, CLI, profile tool, Vulkan loader, MoltenVK library
and relative ICD manifest. The report included the `vulkan_profile` feature and
found `Vulkan0: Apple M1 Pro (MoltenVK)`. Running the staged binary with
`--world 2 --rank 0 --layer-window 0,8 --list-devices` exited 0, confirming the
zero-layer rank parser. A copy of the packaged runtime at a different path also
enumerated MoltenVK successfully. `otool -L` showed `@rpath/libvulkan.1.dylib`
instead of the build machine's absolute Homebrew path, and `codesign --verify`
passed for the staged server.

This verifies macOS build, packaging and device discovery only. No GGUF model was
run through Vulkan, and no Vulkan profile value, offloaded layer, distributed
generation or performance result was collected. Windows/Linux builds and device
checks, physical mixed-backend runs, API parity, rank lifecycle, authenticated
transfers and the performance gate remain open. All source-controlled qualification
gates remain false. No gate was closed from device enumeration or compilation.

## Published Prima fork and pin

Public fork: `https://github.com/berkkarahan/prima.cpp`, branch
`codex/kluster-integration`, commit
`a4a7e4d0fe11ef8275a17fa409974393c38f21fc`. The commit descends from upstream
`288df6a2cc2120387e12d6f40798f30cc525c9ea`; its 11-file diff matches the verified
temporary checkout byte for byte. Public Git access and remote/local SHA agreement
passed. The durable fork checkout at `/Users/berk/projects/prima.cpp` is clean and
tracks the published branch.

Kluster recipe schema 2 / build recipe 6 pins that fork commit, retaining the original
upstream source as provenance. Its patch list is empty; the superseded patch is removed.
Recipe digest: `17a4f918bb9447442cb94117d65b70f722dfa274c54279afb431bb6ad6b761bb`.

| Check | Result |
| --- | --- |
| `uv run python -m pytest -q tests/test_prima_integration.py tests/test_runtime_versions.py` | 100 passed, 1 skipped, 8 existing deprecation warnings; 3.65 s. Includes upstream source rejection, stale recipe rejection before preparation, retained stale records requiring rebuild, and fork metadata. |
| Affected Python modules and Prima tests, `ruff check` | Passed. |
| Managed installer from public fork, `--backend vulkan --from-source` | Passed in `/tmp/kllm-prima-fork-verification`; built Metal+Vulkan server, CLI and profiler from the exact SHA. Source checkout is clean; no local patches were applied. |
| Manifest and artifact verification | Source, upstream provenance, recipe digest and artifact hashes passed; relocated artifacts passed RuntimeRegistry capture. |
| Staged and relocated `--world 2 --rank 0 --layer-window 0,8 --list-devices` | Both exited 0 and reported the Vulkan profiling feature and Apple M1 Pro through MoltenVK. |
| Loader, ICD and signatures | Both locations use `@rpath/libvulkan.1.dylib` and a relative MoltenVK ICD; signatures passed for server, CLI, profiler, loader and driver. |
| `uv run python scripts/build.py --skip-ui --skip-path-shim` | Passed; wheel contains the fork pin and no superseded patch, and native extension loading passed. |
| Scoped whitespace check | Passed, excluding the preserved unrelated `src/kllm/__init__.py` blank line. |

All six qualification gates remain false. This receipt establishes fork publication,
reproducible macOS compilation, packaging and device discovery; Vulkan model inference,
Windows/Linux builds, distributed generation, API parity and performance remain open.

## Manifest/preparation and Windows assessment receipt — 2026-10-11

This section supersedes earlier source/build receipts for the current handoff. It does
not mark T012–T022 complete. The complete accepted compatibility/lifecycle/planner
plan remains unfinished: required native API/model/launch backports, arbitrary exact
placement, production coordinator/network admission/engine dispatch, and remote
end-to-end profiling/planner scoring are still implementation work.

Prima fork branch `codex/kluster-integration` now publishes
`aeb3afdd44b58c93e70e53d47ed4ac7e29452ff8`, descended from the requested starting
commit `a4a7e4d0fe11ef8275a17fa409974393c38f21fc`. Remote SHA agreement passed.
Kluster pins recipe schema 2 / build recipe 7 with digest
`084e6674c89d9e454e004df6e6f2a8b926cb285b1dca3b1679afbffd90c485bb` and no local patches.

| Check | Result |
| --- | --- |
| Native [CI run 38085012210](https://github.com/berkkarahan/prima.cpp/actions/runs/38085012210) | Passed Windows, Linux and macOS CPU compilation of server, CLI, profiler and SHA/manifest contract test; CTest passed on each. Vulkan is disabled in this CI build. |
| Managed installer from the published pin, macOS `--backend vulkan --from-source` | Passed in `/tmp/kllm-prima-assessment-final`; source/recipe and staged artifact hashes verified. |
| Staged and relocated device discovery | Both report Apple M1 Pro through MoltenVK and `execution_manifest_v1`; server/CLI/profiler signatures verify. Enumeration is not GPU execution. |
| `make test` | Passed: 1,244 Python tests, 15 skips; 63 dashboard module checks, 8 browser checks; 45 Rust tests; Svelte check 0 errors/0 warnings. |
| Final Python check after stricter preparation request validation | 1,245 passed, 15 skipped, 8 existing deprecation warnings. |
| Focused Prima/runtime checks after review fixes | 114 passed, 2 skipped without a native assessment directory. |
| Standalone lifecycle assessment with the staged native validator | 14 passed, no skips; controlled real children, signed WebSocket pushes, resume/integrity, ownership/readiness/lease checks and native manifest validation. |
| Standalone Vulkan assessment without supplied models | Exit 2, dense and MoE explicitly unavailable. No model execution result is claimed. |
| Dashboard production build | Passed. |
| Supported package rebuild and native loading | Passed; current fork pin, recipe, new modules and typed rank gateway verified. |
| Affected Ruff and scoped whitespace checks | Passed; unrelated `src/kllm/__init__.py` blank line and `src/kllm/primacpp/` stubs excluded. |

The versioned [compatibility inventory](compatibility.json) covers current proxy/control
routes, 46 parameters, template selections, recognized MoE architectures, launch
requirements and behavioral parity checks. Parser presence does not establish behavior.
The [machine-readable receipt](assessment-macos-controlled.json) records the current
source/recipe, baseline executable identity, OS/device observations, controlled results
and unavailable checks. It contains no model, physical distributed or performance pass.

Review fixes include process identity checks during readiness/renewal, serialized
controller construction, strict preparation envelopes, baseline binary identification,
and rejection of matching HTTP errors as successful observations. GPU execution proof
remains explicitly unavailable. The signed preparation implementation binds asset
chunks to sender, run and authority; its controlled transfer check launches no inference.

All six source-controlled qualification gates remain false. No existing live cluster,
adapter configuration, inference process or production runtime selection was changed.

Kluster implementation revision: `42be21fbc0df5e9e022635de86e879478bdceaa1`,
branch `feat/prima-integration`. The remote branch/main histories were rewritten
during the session. An isolated checkout merged the existing module refactor from
its equivalent published revision and reapplied the feature commit. Both the merge
result and the final implementation tree were verified byte-for-byte against the
locally validated trees. Publication uses a normal fast-forward push; no remote
history is overwritten. The original local history is retained in a private Git ref.
Only this handoff receipt is added after the implementation revision.
