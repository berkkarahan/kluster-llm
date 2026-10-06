# Configuration redesign implementation

Approved scope: guided deployment and expert controls based on the existing
OpenDesign layout; one saved configuration per model; saved CLI defaults.

- [x] Shared parameter catalog, validation, canonical argv, and runtime preflight.
- [x] Saved/default resolution, sparse CLI overrides, explicit persistence, and v1 compatibility.
- [x] Generation additions, launch mapping, active configuration, and protocol defaults.
- [x] Dashboard model isolation, saving/reverting, expert arguments, and chat overrides.
- [x] Select/configure/review flow, persistent real estimates, capability feedback, and exports.
- [x] Regression checks, integration checks, documentation, and final review.

Deferred as approved: KV offload, DIO/lazy loading, advanced sampler editors,
RoPE tuning, and named profiles. Hardware validation remains a separate check.

Validation: full Python suite 799 passed / 13 skipped; the subsequently added
independent RAM/VRAM cap regression also passed (21 configuration tests total).
Full dashboard suite: 57 regular tests and 6 Chrome tests passed. Svelte check
reported zero errors/warnings; production build, focused Ruff check and
`git diff --check` passed. Browser snapshots were inspected at mobile width.
An earlier full run exposed a pre-existing worker-expiry timing failure; its
isolated rerun and the final full run passed. GPU/model loading and physical
distributed-network acceptance were not exercised. Existing unrelated workspace
changes were preserved; no commit, push, or live-node reconfiguration performed.
