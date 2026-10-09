---
name: kllm-models
description: "Download, configure, capacity-plan, run, or remove models with Kluster LLM. Use for GGUF sources, deployment settings, managed runtime readiness, and inference startup."
---

# Manage and run models

Locate the Kluster checkout through `KLLM_REPO_ROOT`, current checkout, or `kllm.state.repo_root()`. Read `docs/cli.md` and use current `kllm model`, `kllm plan`, and `kllm run` help for supported flags.

1. Inspect the master, model source/quantization, available storage, participating nodes, desired context, and active model. An existing active run keeps its effective configuration until stopped; changing deployment controls affects the next run.
2. Download on the master with `uv run kllm model download 'org/repo:Q4_K_M'`, an exact GGUF filename source, or a supported direct URL. Keep GGUF files on the master; workers receive tensors over RPC. Use the existing credential mechanism for gated models without printing tokens.
3. Inspect the model and supported runtime parameters rather than guessing flags. The installed `llama-server --help` is authoritative for its capabilities. Check vision projector and chat-template compatibility when relevant.
4. Prefer saved model configuration unless the user requests defaults. Explicit flags override saved values. Keep node hardware safety caps distinct from deployment limits; unified GPU memory is shared RAM, not extra VRAM.
5. Use `uv run kllm plan --model '<model>'` with the intended settings before `uv run kllm run --model '<model>'`. Planning may prepare the local agent/runtime; it is a capacity estimate, not proof of load success. Resolve incomplete estimates, missing prerequisites, and memory warnings before asserting fit.
6. Check selected workers' verified managed source identities and transport support. Do not mix fork revisions because tag labels match, or silently switch an active stream to another network after link failure.
7. Observe readiness and the authenticated served-model catalog after launch. Preserve the exact served model ID when connecting clients; use `kllm-connect`.
8. Remove a model only within the user's requested scope, with current CLI help and running-model constraints checked. Do not infer authorization to delete other models or runtime versions.

Report the source/quantization, effective settings, selected nodes, estimated versus observed readiness, and remaining limits. Performance claims require a reproducible workload and identified model/backend/hardware.
