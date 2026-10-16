# Quickstart: Cross-Platform Prima Planning Verification

## Overview

Feature `018-cross-platform-planning` enables platform-aware Prima planning, qualification scope resolution (`windows-vulkan-*`, `linux-vulkan-*`, `darwin-vulkan-*`), homogeneous Vulkan ring validation, and cross-platform participant evidence checks without requiring non-Windows physical hardware to run the automated test suite.

## Prerequisites

Ensure the development environment is synced via `uv`:

```powershell
uv sync
```

## Running Verification Tests

1. **Run the Prima Windows & Cross-Platform Planning Suite**:
   ```powershell
   uv run pytest tests/test_prima_windows.py -q
   ```

2. **Run the Prima Lifecycle & Distributed Coordinator Suite**:
   ```powershell
   uv run pytest tests/test_prima_lifecycle.py -q
   ```

3. **Run Dashboard Runtime Settings Tests**:
   ```powershell
   npm --prefix dashboard test
   ```

## Key Scenarios Exercised

- **Single-Node Cross-Platform Planning**: Preview and materialize local Prima plans for `Windows`, `Linux`, and `Darwin` nodes; verify `platform` and `architecture` are preserved in `RankAllocation` and `ExecutionManifest` with deterministic digests.
- **Heterogeneous OS Distributed Ring Planning**: Preview and materialize a mixed-OS ring (`Windows` + `Linux` + `Darwin` Vulkan workers, and coordinator-only CPU master on any OS); verify deterministic rank order and manifest digest.
- **Homogeneous Backend Enforcement**: Verify that a mixed-backend ring (e.g., a `Darwin` node with `backend="metal"` alongside a `Windows` node with `backend="vulkan"`) is rejected at plan time with an error naming the conflicting node and backend.
- **Platform-Derived Qualification Scopes**: Verify `PrimaQualifiers.evaluate_context` resolves `linux-vulkan-local`, `darwin-vulkan-local`, `linux-vulkan-distributed`, and `darwin-vulkan-distributed`, and reports missing verification gates rather than `unsupported_platform_or_backend` while keeping non-Windows scopes unqualified by default.
- **Cross-Platform Participant Evidence Validation**: Verify that distributed preparation accepts signed participant evidence from `Windows`, `Linux`, and `Darwin` nodes sharing `runtime_id`, `source_commit`, and `recipe_digest` with distinct per-OS `build_id` and `artifacts_digest`, and rejects a mismatched node with an error naming that `node_id`.
