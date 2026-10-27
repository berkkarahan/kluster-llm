# Phase 0 Research: Settings Feature Flags Admin View

## 1. Settings Navigation & Deep-Linking Pattern

- **Decision**: Add `{ id: 'flags', label: 'Feature flags' }` to the `sections` array in `dashboard/src/lib/modules/node-settings/view/SettingsPage.svelte`, support `?section=flags` in `onMount`, and render a dedicated `FeatureFlags.svelte` panel inside `.settings-body`.
- **Rationale**: Matches the user clarification (Option A) and follows the exact pattern used by Hardware, Network, Tokens, Software, llama.cpp/Prima, and Paths in `SettingsPage.svelte`.
- **Alternatives considered**:
  - Always-visible footer card below `.settings-layout`: Rejected per user clarification in favor of a dedicated Settings navigation item.
  - Embedding inside `Software.svelte`: Rejected because `Software.svelte` is focused on git/release identity and cluster software updates, which are disabled or locked in certain cluster states, whereas feature flags must be toggleable on the go at any time.

## 2. Backend HTTP Endpoint Placement & Contract

- **Decision**: Extend `SettingsHandlers` in `src/kllm/modules/settings/router_http.py` and `src/kllm/modules/settings/endpoints.py` with `GET /v2/node/flags` and `PUT /v2/node/flags` (plus `/v1/flags` legacy alias), accepting a Pydantic `FeatureFlagBody(name: str, enabled: bool)` in `src/kllm/shared/schemas.py` and returning `{"flags": dict[str, bool]}`.
- **Rationale**: `SettingsHandlers` already receives `config=kllm_config` via dependency injection from `kllm.agent.runtime`. Delegating to `config.feature_flags()` and `config.set_feature_flag(body.name, body.enabled)` reuses the existing SQLite-backed persistence and validation in `kllm.shared.config` with zero duplication.
- **Alternatives considered**:
  - Overloading `PUT /v2/node/config`: Rejected because `PUT /v2/node/config` requires full hardware config fields (`device`, `vram_percent`, `ram_percent`, `split_mode`) and triggers GPU re-probing and "applies on next worker restart" semantics, whereas feature flag toggles are atomic, lightweight, and take effect immediately.

## 3. Immediate Live UI Synchronization & In-Flight Guarding

- **Decision**: Implement `setFeatureFlag(name: string, enabled: boolean)` on `NodeSettingsInteractor` (`dashboard/src/lib/modules/node-settings/interactor.svelte.ts`) with per-flag in-flight state (`pendingFlags: Record<string, boolean>`) and inline error tracking (`flagError: string | null`). On `PUT /v2/node/flags` success, immediately update `this.cluster.status.feature_flags` with the returned `data.flags` map and call `await this.refresh()`.
- **Rationale**: Updating `this.cluster.status.feature_flags` immediately ensures reactive consumers (`PrimaRunPage.svelte`, network setup views, and `FeatureFlags.svelte` itself) reflect the new state within milliseconds while `this.refresh()` reconciles the full live status snapshot. Per-flag `pendingFlags[name]` prevents rapid double-clicks from racing while allowing other flags to remain interactive.
- **Alternatives considered**:
  - Waiting solely for the next periodic WebSocket/poll tick: Rejected because it introduces unnecessary visual lag between the HTTP response and the toggle state update.
