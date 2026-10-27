# Interface Contracts: Settings Feature Flags Admin View

## 1. HTTP Control API Contracts

### `GET /v2/node/flags` (Legacy alias: `GET /v1/flags`)

Returns all known feature flags and their current persisted/default boolean states on the local node.

- **Response (`200 OK`)**:
  ```json
  {
    "flags": {
      "private-network-setup": false,
      "prima-auto-deploy": false
    }
  }
  ```

### `PUT /v2/node/flags` (Legacy alias: `PUT /v1/flags`)

Updates a single feature flag's boolean state in the node's unified SQLite database (`~/.kllm/managed-network.sqlite`) and returns the updated feature flag map.

- **Request Body (`application/json`)**:
  ```json
  {
    "name": "prima-auto-deploy",
    "enabled": true
  }
  ```
- **Response (`200 OK`)**:
  ```json
  {
    "flags": {
      "private-network-setup": false,
      "prima-auto-deploy": true
    }
  }
  ```
- **Error Responses**:
  - `400 Bad Request`: When `name` is not a known feature flag in `FEATURE_FLAGS`:
    ```json
    {
      "detail": "unknown feature flag 'nope'; valid flags: private-network-setup, prima-auto-deploy"
    }
    ```
  - `422 Unprocessable Entity` / `400 Bad Request`: When `enabled` is not a strict boolean or extra fields are provided.

---

## 2. Dashboard UI Contract (`SettingsPage.svelte` & `FeatureFlags.svelte`)

- **Navigation Section**:
  - Section ID: `'flags'`, Label: `'Feature flags'`.
  - Deep-link URL query parameter: `/settings?section=flags`.
  - Header intro text when `section === 'flags'`: `'Feature flags toggle experimental workflows immediately without restarting the node.'`
- **FeatureFlags Admin Panel (`dashboard/src/lib/modules/node-settings/view/FeatureFlags.svelte`)**:
  - Displays a list of registered feature flags (`private-network-setup`, `prima-auto-deploy`) with:
    - Human-readable label and monospace flag key (`private-network-setup`, `prima-auto-deploy`).
    - Concise description of the gated capability.
    - Accessible toggle button/switch (`role="switch"`, `aria-checked={enabled}`, `disabled={pending}`) that flips the flag on click.
  - Displays an inline error banner if a toggle request fails while keeping the switch at its last confirmed state.
