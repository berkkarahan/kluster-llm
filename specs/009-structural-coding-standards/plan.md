# Plan: Structural Coding Standards

The code change is a behavior-preserving extraction. Standards live in governance so the next specification reads them during planning.

## Facade rule

`python/kllm/agent/__init__.py` and `scripts/install.py` keep every name tests patch. Siblings take explicit arguments:

- `python/kllm/agent/join_flow.py`, `live_ws.py`, `run_route.py`
- `scripts/install_http.py`, `scripts/install_llama_release.py`, `scripts/install_llama_source.py`

`kllm plan` / `kllm run` printing lives in private helpers in `python/kllm/cli.py`. Typer options stay on the commands.

Rust: `push_load_and_template` and `push_sampling_flags` in `crates/kllm-native/src/process.rs` append the same argv. `prepare_split` in `crates/kllm-native/src/planner.rs` returns the same locals `plan_split` used before scoring.

## Governance

- `.specify/memory/architecture_constitution.md` holds the rules.
- `.specify/memory/constitution.md` is 1.1.0 and requires that file.
- Durable memory: `docs/memory/architecture/2026-09-29-structural-standards.md` (A1) and `docs/memory/decisions/2026-09-29-facade-patch-surface.md` (D1).

`.specify/feature.json` stays on `specs/008-add-10gbe-link`.
