# Phase 1 Data Model: Settings Feature Flags Admin View

## Entities

### 1. Persisted Feature Flag Record (Existing Unified SQLite Storage)
- **Storage**: `feature_flags` table in `~/.kllm/managed-network.sqlite` (managed via `kllm.shared.persistence.feature_flags` and `kllm.shared.config`).
- **Fields**:
  - `name` (`TEXT PRIMARY KEY NOT NULL`): Canonical flag identifier (`"private-network-setup"` | `"prima-auto-deploy"`).
  - `enabled` (`INTEGER NOT NULL`): `1` when enabled, `0` when disabled.
  - `updated_at` (`FLOAT NOT NULL`): Unix epoch timestamp of last update.
- **Default Resolution**: Any registered flag in `FEATURE_FLAGS` without a persisted row resolves to `False` via `default_flags()`.

### 2. Feature Flag Update Request (`FeatureFlagBody`)
- **Location**: `src/kllm/shared/schemas.py`
- **Fields**:
  - `name` (`str`): Target feature flag identifier. Must be a member of `FEATURE_FLAGS`.
  - `enabled` (`StrictBool`): Desired boolean state (`true` or `false`). Non-boolean values (e.g., strings or integers) are rejected.
- **Validation Rules**:
  - Extra fields forbidden (`extra="forbid"`).
  - If `name` is not in `FEATURE_FLAGS`, returns HTTP `400 Bad Request` with detail listing valid flags.

### 3. Feature Flags Response Payload
- **Shape**:
  ```json
  {
    "flags": {
      "private-network-setup": false,
      "prima-auto-deploy": true
    }
  }
  ```

### 4. Dashboard Feature Flag Metadata (`FeatureFlagDescriptor`)
- **Location**: `dashboard/src/lib/modules/node-settings/entities.ts`
- **Fields**:
  - `id` (`string`): Canonical flag key (e.g., `'private-network-setup'`, `'prima-auto-deploy'`).
  - `label` (`string`): Human-readable title (e.g., `'Guided private network setup'`, `'Prima one-click auto-deploy'`).
  - `description` (`string`): Concise explanation of the workflow unlocked when enabled.
- **State Transitions**:
  - `Idle (disabled | enabled)` → user clicks toggle → `Saving (pendingFlags[id] = true, control disabled)`
  - `Saving` → HTTP 200 OK → `Idle` with updated `enabled` state reflected in `cluster.status.feature_flags`
  - `Saving` → HTTP/network error → `Idle` with prior confirmed `enabled` state preserved and `flagError` displayed
