# Tasks: Structural Coding Standards

- [x] Extract join, live-log, and run helpers while leaving patched names on `kllm.agent`.
- [x] Extract installer HTTP, release scoring, and llama.cpp source helpers while leaving patched names on `scripts/install.py`.
- [x] Split `master_from_pydict` argv assembly and the `plan_split` preamble in the native crate.
- [x] Extract `kllm plan` and `kllm run` printing helpers without changing Typer options.
- [x] Add `.specify/memory/architecture_constitution.md` and amend the project constitution to 1.1.0.
- [x] Register durable architecture A1 and decision D1, and point `docs/memory/INDEX.md` at them.
- [x] Run the affected pytest modules and `cargo test` for `kllm-native`.
