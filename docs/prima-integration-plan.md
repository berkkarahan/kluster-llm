# Prima.cpp CLI and dashboard integration

## Goal

Implement working Prima.cpp installation, distributed inference, CLI controls, and dashboard integration in Kluster LLM on a dedicated `feat/prima-integration` branch.

Do not commit to `main` or `master`. The checkout was on `main` during inspection. Preserve all existing uncommitted work before making integration changes.

## Status and version-control handoff

- This plan was prepared in Plan mode; implementation has not started.
- The integration branch has not been created.
- Plan mode permits this document only under `/Users/berk/.opencode/plan`.
- After switching to a build-capable agent, create the feature branch, copy this plan to `docs/prima-integration-plan.md`, and commit only the plan file first, as requested by the user.
- Inspect the current working-tree diff before branching or staging. Existing edits include planner, dashboard, run settings, and tests, plus a deleted plan file; these are potential user work and must be preserved.

## Verified upstream interface

Official upstream: https://github.com/OpenCPIL/prima.cpp

Revision inspected: `288df6a2cc2120387e12d6f40798f30cc525c9ea`. Use a tested, explicitly pinned revision for the integration.

- Rank 0 runs Prima's `llama-server`; other ranks run Prima's `llama-cli`.
- Distributed communication uses a ZeroMQ ring, configured with `--world`, `--rank`, `--master`, and `--next`.
- Each participating node needs a local copy of the model.
- Data and signal ports are configured using `--data-port` and `--signal-port`; upstream documents defaults of 9000 and 10000.
- Automatic workload distribution requires HiGHS on rank 0. Dependencies also include ZeroMQ and fio for profiling.
- Upstream documents a Make-based build, including `USE_HIGHS=1` on rank 0 and backend-specific build options.
- Server mode exposes an OpenAI-compatible chat-completions API.
- Native Windows and Vulkan/AMD support are documented as pending. Initial integration targets macOS/Linux, with capabilities checked against the pinned build.
- The existing experimental wrapper's `prima-server`, `--scheduler halda`, and `--mmap-threshold` assumptions do not match the inspected upstream interface.

## Implementation

### 1. Branch and preserve existing work

- Create `feat/prima-integration` before editing repository files.
- Review existing local changes and retain them without overwriting or accidentally staging them.
- Save this plan in the repository and commit that file on the feature branch.

### 2. Reproducible installation

- Extend `scripts/install.py` with explicit Prima engine selection.
- Pin the upstream source revision and install/check its build and profiling dependencies.
- Build the actual server, CLI worker, and required profiling tools using the verified upstream build path.
- Stage Prima binaries and associated runtime assets in a separate directory to distinguish them from the project's llama.cpp binaries.
- Record the installed revision/backend and provide installation verification.
- Detect unsupported platform/backend selections and report actionable errors.

### 3. Distributed runtime

- Add engine-specific launch configuration in the agent and native process supervisor.
- Advertise installed engine versions and supported capabilities through membership/status data.
- Assign deterministic ranks with the coordinator at rank 0 and generate the complete ring topology.
- Verify model availability and compatible engine revisions on participating nodes before launch.
- Use existing authenticated control-plane communication to prepare and start Prima workers.
- Account for per-node control addresses, inference addresses, configurable ports, and the existing optional network overlay.
- Track process identity, startup/profiling progress, readiness, logs, and terminal errors.
- Clean up partially started runs and handle Stop, engine changes, worker departure, and failed processes across the ring.
- Make membership-driven recovery engine-aware; a Prima ring change requires coordinated topology handling.
- Keep engine-managed topology flags out of arbitrary extra-argument overrides.

### 4. Planning, CLI, and persisted settings

- Add shared engine selection and engine-specific settings to the API, CLI payloads, and persisted run/model settings.
- Make cluster startup/join, plan/run, status, and stop behavior engine-aware.
- Proposed run interfaces:

  ```bash
  kllm plan --engine prima --model model.gguf
  kllm run --engine prima --model model.gguf
  ```

- Expose supported Prima options such as prefetch behavior, GPU memory limits, and ring ports, based on the pinned upstream parser.
- Model Prima planning separately from llama.cpp's static memory-fit/layer-split assumptions. Runtime profiling determines automatic placement, and mmap allows disk-backed execution.
- Distinguish a topology/preflight plan from measured runtime placement; do not present guessed allocations as final scheduler output.
- Reuse model download/distribution to supply every participating node with the required files.

### 5. Dashboard

- Add engine selection to the existing System Settings/run screen.
- Persist engine selection and settings through the shared API.
- Display installation/version information, node compatibility, ranks, and ring membership.
- Show Prima-specific settings supported by the pinned build, with capability-aware handling of other controls.
- Connect Plan, model distribution, Run, Stop, loading/profiling progress, logs, and chat to the selected engine's lifecycle.
- Surface failed launches and incompatible nodes clearly.

### 6. Documentation and legacy wrappers

- Replace placeholder upstream URLs and invalid Prima setup/run instructions.
- Update `docs/prima-cpp-guide.md`, `docs/cli.md`, installer documentation, and README engine descriptions.
- Update or replace `coordinator-macos/run_coordinator_prima.sh` and its Makefile target to use the implemented integration.
- Document supported platforms/backends, local model requirements, dependencies, ports, and recovery behavior.

## Integration points identified

- Installer: `scripts/install.py`.
- Shared settings and CLI: `python/kllm/run_settings.py`, `python/kllm/cli_payload.py`, `python/kllm/cli.py`.
- Planning and orchestration: `python/kllm/run_planning.py`, `python/kllm/agent/__init__.py`, `python/kllm/membership.py`.
- Process management: `python/kllm/native.py`, `python/kllm/runtime.py`, `crates/kllm-native/src/process.rs`.
- State, networking, logs, and readiness: relevant existing modules under `python/kllm/`.
- Dashboard: `dashboard/src/lib/types.ts`, `dashboard/src/lib/store.svelte.ts`, `dashboard/src/routes/run/+page.svelte`, and relevant status/progress components.

## Verification and acceptance criteria

- Installer checks validate the pinned Prima build and distinguish its binaries from llama.cpp.
- Tests cover topology/rank assignment, correct coordinator/worker arguments, capability validation, and settings persistence.
- Orchestration tests cover missing models, incompatible revisions, failed worker startup, timeout/cleanup, stopping, and membership changes.
- Existing llama.cpp lifecycle tests remain passing.
- Run appropriate Python and Rust tests, rebuild the native package using the repository's uv/maturin build flow, and run dashboard `npm run check` and `npm run build`.
- Perform a real single-node Prima inference smoke test and a supported two-node inference test when hardware is available, including a chat-completion request and clean shutdown.
- Exercise engine selection, settings persistence, progress/errors, Run, Stop, and chat through the browser UI.
- Report which real-runtime/platform checks were actually completed and which require additional hardware.
- All version-controlled work is on `feat/prima-integration`; no commits are made to `main` or `master`.
