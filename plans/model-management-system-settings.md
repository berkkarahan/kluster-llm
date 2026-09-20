# Model management & system settings (Unsloth-style UX)

> **Created:** 2026-09-20
> **Status:** Realized
> **Repo / scope:** kluster-llm — dashboard model load + Plan & Run system settings

## Conventions captured at creation

- Dashboard is Svelte 5 runes (`$state` / `$derived` / `$effect`) with a single `appStore` in `dashboard/src/lib/store.svelte.ts`.
- Backend is FastAPI under `python/kllm/agent/__init__.py`; model ops in `python/kllm/models.py`, load sampling in `python/kllm/load_progress.py`, per-model settings in `python/kllm/model_settings.py`.
- Progress is **poll-based** (not WebSocket): `PollLoop` in `dashboard/src/lib/poll-loop.ts` mirrors Unsloth Studio’s download-manager cadence (fast/slow tier, jitter, in-flight dedup, visibility pause). Store starts `loadLoop` + `downloadLoop` from `AppStore.start()`.
- Unsloth Studio references (upstream `unslothai/unsloth` `studio/`):
  - Download → load handoff: `studio/frontend/src/features/chat/hooks/use-chat-model-runtime.ts` polls download progress then `/api/inference/load-progress` (`mmap` → `ready`).
  - Hardware cache: `studio/frontend/src/hooks/use-hardware-info.ts` (`GET /api/system/hardware`, module-level cache).
  - Download manager poll loop: `studio/frontend/src/features/hub/download-manager/`.
- Existing UI already maps closely to Unsloth’s chat toast phases: `DownloadPanel.svelte`, `LoadProgressPanel.svelte`, `HardwareSettings.svelte`, Models page search modal, Plan & Run form.
- Visual language: dark grid + IBM Plex Mono/Sans in `dashboard/src/app.css` (range thumbs styled for dark theme).
- Auto vs edit pattern: `AutoEditField.svelte` + `AppStore.setAutoInt` / `setSpeculativeMode`. Sampling uses `null` = auto with GGUF defaults from `ModelInfo.defaults`.

## Objective

Replicate Unsloth Desktop/Studio’s seamless local model UX inside kllm:

1. **Model loading** — search HF GGUFs, non-blocking download bars, RAM/VRAM estimates / OOM chips, status chips (`Downloading…` → `Loading to VRAM…` → `Ready` / `OOM Warning`).
2. **System settings** — device + engine backend (CPU / Vulkan / CUDA / …), multi-GPU heterogeneous selection, advanced params with **sliders that seed defaults when first edited**, persist without hard reload.

## Component & state map

```
┌─────────────────────────────────────────────────────────────────┐
│  +layout.svelte                                                 │
│    appStore.start() → status 1s + PollLoop(download) + PollLoop │
│    header chips: dl% / load% / infer                            │
└─────────────────────────────────────────────────────────────────┘
         │
         ├─ /models  (+page.svelte)
         │    DownloadPanel ← downloadJob
         │    LoadProgressPanel ← loadProgress + planResult.fit
         │    Search form → POST /v1/models/search → modal
         │    Local table: Ready | Loading… | Load → /run
         │
         └─ /run  (+page.svelte)  “SYSTEM SETTINGS” surface
              Model select + Context slider
              HardwareSettings → PUT /v1/config (no reload)
              Placement weight sliders
              Advanced: AutoEditField / AutoSliderField
              Sampling AutoSliderFields (null ↔ seeded default)
              LoadProgressPanel + Plan / Run / Save
```

| Piece | Role | Backend |
|-------|------|---------|
| `appStore` | UI state + API orchestration | `/v1/*` |
| `PollLoop` | Responsive polling | — |
| `DownloadPanel` | Async download strip | `GET /v1/models/download-progress` |
| `LoadProgressPanel` | Load phases + OOM | `GET /v1/load-progress` |
| `HardwareSettings` | Device / backend / GPUs / budget | `GET\|PUT /v1/config`, `GET /v1/gpus` |
| `SliderField` | Range + number (always numeric) | local |
| `AutoSliderField` | Auto ↔ Edit; seeds default on edit | local → store |
| `AutoEditField` | Auto ↔ Edit for non-slider fields | local |
| Models modal | HF hits + fit/OOM chips | `POST /v1/models/search` |

### Data flow (user journey)

1. User searches → modal lists GGUFs with **fit / OOM risk** vs cluster VRAM.
2. Download → `POST /v1/models/download` returns immediately; `downloadLoop` polls; UI stays interactive; on `complete`, store auto-selects the new file.
3. Load → Plan & Run → `POST /v1/run` arms `load_progress.begin`; panel shows Starting → Loading to VRAM → Ready; overflow plan shows OOM Warning before Run is enabled.
4. Settings change → store mutates locally; hardware persists via `PUT /v1/config`; model params via Save → `PUT /v1/model-settings/{id}`; live VRAM estimate via debounced silent `POST /v1/plan`.

## Phased work

### Phase A — Plan + gaps (this file)
Document Unsloth mapping and remaining UI gaps vs current tree.

### Phase B — Slider primitives + default seeding
- Add `SliderField` and `AutoSliderField`.
- Sampling / placement / threads / auto-int fields use sliders.
- Switching Auto → Edit **seeds the model default (or fallback) into the control** so the first edit shows a real value, not an empty box.

### Phase C — Settings polish
- Dirty-state accent on **Save settings**.
- Reset clears sampling overrides.
- Placement weights as dual sliders.

### Phase D — Verify
- API tests in `tests/test_model_management_api.py` remain green.
- Manual: search → download progress → Load → engine/VRAM settings → Run load bar.

## Open questions

- WebSocket push for progress is optional; Unsloth itself polls — keep PollLoop unless latency becomes an issue.
- Whether “Load” on Models should call `/v1/run` directly vs navigate to Plan & Run (today: navigate — safer for settings review).
