---
name: kllm-install
description: "Install, rebuild, upgrade, or repair a Kluster LLM node on Windows, macOS, or Linux. Use for bootstrap and missing toolchain/runtime failures."
---

# Install a Kluster node

Locate the checkout through `KLLM_REPO_ROOT`, the current checkout, or `kllm.shared.state.repo_root()`. Resolve repository paths below against that root. If it is unavailable, obtain the checkout location before offering installation commands.

1. Read `docs/agent-install.md`, including its recovery tree, before choosing commands. Check OS, Python, backend, checkout, available permissions, and whether this node is already running inference.
2. Use `python scripts/install.py` (`python3` on macOS/Linux when appropriate, `py -3` on Windows). Use its documented flags for an intentional partial install. Do not replace this with system pip or a hand-built CMake flow unless recovering a specific documented failed stage.
3. After application/native packaging changes use `uv run python scripts/build.py` or the supported installer. `pip install -e .` alone does not refresh the native extension.
4. Keep `llama-release` as the stable bootstrap pin. macOS GPU bootstrap must produce Metal + Vulkan; do not silently install a Metal-only pack. Managed runtime selections use isolated builds and exact repository/commit identities shared by run participants; do not overwrite `vendor/bin` to switch a managed runtime.
5. Follow the guide's import, native-extension, dashboard, binary and backend checks. Use `python scripts/install.py --check` for the bootstrap pin and inspect managed runtime preparation separately.
6. Run setup on each physical node that will participate. Keep the ordinary agent under its existing account. The installer stages the administrator network helper; it does not authorize that helper or configure host networking.

For dedicated network setup use `kllm-10gbe`; for joining and operating the cluster use `kllm-cluster`. Preserve credentials, models, runtime state, and unrelated changes during repair. Do not reset state merely because startup failed.

Report the commands and their exit status, which guide checks passed, the selected source/backend, and unavailable physical-node checks. A local build does not prove Windows/Linux/macOS GPU or mixed-node readiness.
