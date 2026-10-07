---
name: kllm-test
description: "Choose and run meaningful Kluster LLM Python, Svelte/dashboard, Rust, CLI, process, or networking checks. Use for validation and regression testing while distinguishing fixtures from hardware acceptance."
---

# Verify a Kluster change

Verify the Kluster checkout via `KLLM_REPO_ROOT`, current checkout, or `kllm.state.repo_root()`. Inspect `pyproject.toml`, `Makefile`, `dashboard/package.json`, and the current dashboard test runner before choosing commands.

1. Start with the changed public behavior and its affected callers. Select existing focused tests and add an isolated regression case for uncovered non-trivial logic. Use temporary directories/state and fake endpoints/binaries rather than real credentials or a production cluster by default.
2. Python: `uv run python -m pytest -q tests/<affected-file>.py`; then `uv run python -m pytest -q` when full Python validation is required. Do not use `uv run pytest` when its executable entry point is unavailable. The configured default is four workers with `--dist=loadfile`; use `-n 0` for a justified serial comparison or shared external-resource check.
3. Dashboard: use `npm --prefix dashboard test`. The current runner sends non-browser files through four workers and Chrome/shared-state files through a serial second phase. Read its current browser-file set rather than maintaining another stale list. `npm --prefix dashboard run test:serial` is the matched serial comparison.
4. Test-created Vite servers need disabled HMR/WebSockets/watchers and explicit closure when the test uses middleware mode. Do not run competing SvelteKit metadata generation, browser suites, checks or builds concurrently.
5. For the complete existing gate, `make test` runs Python, dashboard, Rust and dashboard check sequentially. Keep that sequencing. For focused native testing use the Makefile's `PYO3_PYTHON` interpreter convention. Build the standalone network helper with `--no-default-features --features network-helper --bin kllm-network-helper`, not the Python extension feature set.
6. Run checks appropriate to CLI/API, filesystem replacement, process cleanup or native boundaries. Mocked logic alone does not prove those integration contracts. After packaging changes use the supported build/install flow and check imports/native loading.
7. Capture commands, exit codes and case/skip counts. Characterize a failure against an unchanged baseline or isolated repeat before calling it a regression. Once relevant checks pass, repeat only when new changes or unresolved concerns justify it.

State separately what was verified through unit fixtures, real local transport/process/filesystem boundaries, installed clients, browser sessions, physical OS authorization, GPUs, mixed-node networking, reboot/restoration, generation, or measured performance. Skipped or unavailable acceptance is not a pass; throughput claims need matched workloads and identified hardware/model/backend.
