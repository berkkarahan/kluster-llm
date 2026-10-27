# Feature 022 Memory: Settings Feature Flags Admin View

## Context & Constraints
- Feature flags (`private-network-setup`, `prima-auto-deploy`) are persisted in the unified SQLite database (`~/.kllm/managed-network.sqlite`) via `kllm.shared.config` (`feature_flags()`, `get_feature_flag()`, `set_feature_flag()`).
- Dashboard receives `feature_flags: Record<string, boolean>` via the cluster/dashboard status payload (`cluster.status.feature_flags`).
- Currently, operators can only toggle feature flags from the CLI (`kllm flags <name> --enable|--disable`).
- The feature adds a compact Feature Flags admin panel inside Settings (`dashboard/src/lib/modules/node-settings/`) and the corresponding HTTP control endpoint(s) so operators can view and toggle feature flags on the go from the dashboard UI, with immediate reactivity across gated dashboard views.
- Architecture boundaries (`.specify/memory/architecture_constitution.md`) require clean separation between `src/kllm/modules/settings/` (or `node-settings` in the dashboard) and `kllm.agent`, and behavior-focused tests for both backend and dashboard.
