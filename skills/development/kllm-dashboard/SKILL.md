---
name: kllm-dashboard
description: "Implement or refine Kluster LLM's Svelte dashboard while preserving live backend data, responsive controls, connection state, and deployment settings. Use for this repository's UI work."
---

# Change the live Kluster dashboard

Locate and verify the Kluster checkout through `KLLM_REPO_ROOT`, current checkout, or `kllm.shared.state.repo_root()`. Follow `kllm-develop` for repository governance and CodeGraph-first discovery.

1. Inspect the requested screen in `dashboard/src/lib/modules/<feature>/` (entities, interactor, presenter, router, and `view/`) before changing it. Composition is `createApp()` in `dashboard/src/lib/app.svelte.ts`. The live feed is `dashboard/src/lib/live/`. There is no `store.svelte.ts`. Read the current `dashboard/DESIGN.md` and styles without overwriting unrelated edits. When the user supplies an OpenDesign artifact, inspect that artifact and the live screen before translating its visuals.
2. Keep the module interactors, backend calls, and live WebSocket feed. Visual work must use live data; do not replace topology, health or metrics with sample values. Distinguish measured, unmeasured, unsupported, cached, disconnected, reconnecting and worker-local views.
3. Persisted membership is not liveness. An unavailable master or failed heartbeat must not produce a connected topology merely because nodes/history remain in state.
4. Keep guided controls and effective values first, organized advanced options second. Node hardware safety settings and next-deployment settings have different ownership. Active runs retain their own effective snapshots; model edits affect the next deployment.
5. Follow current API contracts and shared payload builders. Handle structured and non-JSON errors with endpoint/status context. Preserve secret redaction and distinguish a worker's token from the master's inference token.
6. Test controls on narrow and wide viewports. Keep tables scrolling inside their panels, prevent desktop/mobile menu controls from leaking through generic button styles, and verify interaction as well as layout.
7. Use `kllm-test` for the repository's parallel/serial test runner. Run appropriate dashboard tests, `npm --prefix dashboard run check`, then `npm --prefix dashboard run build` sequentially when UI or build behavior changed.

Report which screen interactions, widths, backend states and live flows were checked. A component/fixture pass is separate from browser, remote cluster, and performance acceptance.
