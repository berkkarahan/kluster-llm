# Quickstart & Validation Guide: Settings Feature Flags Admin View

## Prerequisites

- Python environment managed via `uv` (`uv sync` / `uv run python scripts/install.py`)
- Node.js dependencies installed in `dashboard/` (`npm --prefix dashboard install`)

## 1. Automated Backend Verification

Run the Python test suites covering configuration persistence, settings HTTP endpoints, and architecture boundaries:

```powershell
uv run pytest -q tests/test_config.py tests/test_cli_flags.py tests/test_dashboard_api.py tests/test_architecture_boundaries.py
```

**Expected Outcome**:
- `GET /v2/node/flags` returns all known feature flags (`private-network-setup`, `prima-auto-deploy`) with their current boolean states.
- `PUT /v2/node/flags` with `{"name": "prima-auto-deploy", "enabled": true}` persists to `managed-network.sqlite` and returns the updated flags map; subsequent `GET /v2/cluster/status` (`feature_flags`) and `kllm flags` CLI reflect `prima-auto-deploy = enabled`.
- Invalid flag names or non-boolean values are rejected with HTTP 400/422 without modifying persisted state.
- `tests/test_architecture_boundaries.py` passes with zero boundary violations.

## 2. Automated Dashboard Verification

Run the dashboard typecheck, production build, and Node test runner:

```powershell
npm --prefix dashboard run check
npm --prefix dashboard run build
node dashboard/tests/run-tests.mjs settings-feature-flags.test.mjs
```

**Expected Outcome**:
- `svelte-check` reports 0 errors and 0 warnings.
- `SettingsPage` renders the **Feature flags** navigation item, supports `?section=flags`, displays both feature flags with their current state, toggles flags via `PUT /v2/node/flags`, updates `cluster.status.feature_flags` on the go so gated views (`PrimaRunPage`) switch immediately, and reverts with an error notice on failure.
