# Feature Memory - 010-vram-ram-cpu-profiler

## Scope Notes
- The native crate already probes one-time *capacity* (total/available RAM, VRAM total) in
  `crates/kllm-native/src/hw.rs` via sysinfo plus backend enumeration (nvidia-smi, rocm-smi,
  Windows WMI/registry, macOS Metal fraction). This feature adds a *usage* profiler — live
  VRAM used/free, RAM used/free, and CPU utilization — served as on-demand probes.
- Platform scope is Windows and macOS only (per request). Linux paths must remain intact
  (do not delete the existing non-macOS branch), but are not the delivery target.

## Relevant Durable Memory
- Constitution II/III: new non-trivial logic needs a runnable behavior check; hardware-dependent
  checks must state their OS/backend prerequisites and not present skipped checks as passes.
- Constitution IV: measurements that are unavailable must be reported explicitly, never as
  fabricated zero; failures must not be reported as healthy.
- Architecture constitution: keep `cfg`-gated platform functions as live code; keep imports at
  module top; keep native exports on `crates/kllm-native/src/lib.rs`.

## Open Questions
- None blocking specification. User confirmed both host-wide and managed-process CPU/RAM.
- Live GPU usage must be an observed reading, not the existing budget fraction. Unavailable
  device-wide usage remains unavailable; a probe process's own allocation is not device usage.

## Watchlist
- On Apple Silicon, VRAM is unified memory — a "VRAM used" probe must not double-count RAM and
  VRAM, and must be labeled as shared.
- Verify Windows GPU support by vendor and memory scope rather than assuming a missing utility
  implies missing hardware. Declare unsupported counters explicitly.
- CPU requires a measurement interval; initial sampling and reused process IDs cannot report
  valid usage until a baseline for the correct process lifetime exists.
- Existing monitoring stops with no child and its validator rejects new fields: use a negotiated
  live resource snapshot beside unchanged process history. Do not create a fake lifetime.
- DXGI budget/current usage and Metal currentAllocatedSize describe application scope, not
  device-wide usage. Windows uses adapter-wide PDH counters; macOS unsupported counters stay null.
- New native sources run in two bounded agent-owned helper slots. Kill/reap before replacement;
  no abandoned thread-per-probe pattern or unbounded source queue.
- Launch registration, heartbeat and history must share one canonical start timestamp while
  retaining exact OS tokens separately. Existing Linux/capacity and old payloads stay compatible.
- Reported device capacity is the llama.cpp `--list-devices` total, not the DXGI or registry
  byte count. A unique normalized display name is the verified inventory link; ambiguous names
  stay unlinked. The OS dedicated total remains in the metric `detail`.

## Never Store Here
- permanent project decisions
- general bug patterns unless they are directly reused
- implementation history after the feature ships

## Capture review — proposals only

The mandatory capture command was reviewed against the implementation, tests and validation
record. The memory index contains only A1/D1 and no indexed duplicate of these candidate bugs.
Memory MCP search/register tools are unavailable; the documented Markdown fallback was used.
Governance's explicit approval requirement takes precedence over the extension's automatic
approval wording. No durable memory or index entry was written.

- **Windows venv redirector is not the actual helper/workload PID** — confidence 99%.
  Durable because this affects every future Python helper supervisor and process profiler.
  Prevents idle-launcher measurements, extra unmanaged processes and incomplete kill/reap.
  Evidence: the initial workload showed zero CPU/6 MB RSS; the real interpreter showed ~6.2%
  of 16 logical CPUs and ~78 MB RSS. Corrected Job Object and parent-death checks pass.
  Look next: `python/kllm/usage_probe.py:helper_command`, `helper_environment`, and hardware
  tests/validation.md. Proposed category/file: `bugs/2026-09-30-windows-real-helper-pids.md`.
- **A rebuilt wheel can be shadowed by an old in-tree native extension** — confidence 95%.
  Durable because repository pytest intentionally loads python/ and native packaging is mixed.
  Prevents reporting a rebuild as verified while tests/runtime load an older .pyd.
  Evidence: the supported wheel build succeeded but `_UsageCollector` was missing from the
  loaded source extension; the editable refresh failed with error 32 until the user-approved
  owning process was stopped. Rebuild/import and native boundary tests then passed.
  Look next: `docs/troubleshooting.md` native-resource recovery and validation.md.
  Proposed category/file: `bugs/2026-09-30-native-checkout-shadowing.md`.

These are feature-local review proposals awaiting user approval and an available registration
tool, not approved project-wide rules or automatically shared lessons.

Verification follow-up: the shared installer cache-path helper now handles Windows absolute
paths and quoted SDK paths with spaces. Existing/new regression checks pass (54 installer
tests); the full suite is green (508 passed, five explicit skips). The reviewed correction
does not introduce a new architecture policy; its evidence is retained in validation.md.
The user declined hosted verification and will perform Mac runtime/Metal checks independently.
No remote snapshot or workflow was published, and Mac/Linux runtime checks are not passed.
