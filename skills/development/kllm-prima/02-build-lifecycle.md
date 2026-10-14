# 02 · Build, install & validation lifecycle

**Use when:** You implement, build or install Prima changes, decide what runs when, reproduce defects, or need the current validation-schedule boundary.

**Keywords:** validation schedule, deferred tests, reproduction, evidence, hashes, install.py, --llama-only, --engine primacpp, --from-source, worktree, acceptance.

**Related:** 01-sources-setup.md, 03-windows-native.md, 10-profile-costs.md, 12-placement-qualification.md.

## Quick rules

- Implementation first: finish all required implementation before running any unit tests or starting test-fix work; production compilation may check implementation but does not count as acceptance. Once implementation is complete, run relevant tests and fix failures before claiming the integration is finished.
- Performance acceptance and physical distributed validation stay deferred to the user's live three-computer cluster; keep qualification disabled until evidence exists.
- Reproduce defects on an isolated managed build with supplied, complete GGUF assets; record source/recipe/artifact/model hashes, effective settings, physical device UUID, driver and OS — capture the original failure before changing source.
- Native changes go in the Prima Git checkout with runnable C++ contract or model-backed regression checks; Kluster changes stay in their owning feature modules and run relevant Python/Rust/dashboard checks through `kllm-test`.
- Managed builds go through `scripts/install.py --llama-only --engine primacpp --from-source` with the resolved `--source-repo`, `--revision`, `--recipe-digest`, `--runtime-dir` and explicit backend; follow `docs/agent-install.md` for toolchain recovery; managed builds must not overwrite bootstrap `vendor/bin`.
- During development, build the edited fork with its own CMake targets; retain the diff plus real test results and label worktree evidence as developmental — modified files and manually restamped artifacts never qualify a published pin.

## Full text (normative)

## Implement and measure

- Follow the user's validation schedule. For the current Prima integration,
  finish the required implementation before running any unit tests or starting
  test-fix work. Do not substitute native contracts or model assessments for the
  deferred test phase. Production compilation may check implementation, but it
  does not count as acceptance. After implementation is complete, run relevant
  tests and fix failures before claiming the integration is finished. Performance
  and physical distributed acceptance remain deferred to the user's live
  three-computer cluster; keep qualification disabled until evidence exists.
- Reproduce defects using an isolated managed build and supplied, complete GGUF
  assets. Record source/recipe/artifact/model hashes, effective settings, physical
  device UUID, driver and OS. Capture the original failure before changing source.
- Native changes go in the Prima Git checkout, with runnable C++ contract or
  model-backed regression checks. Kluster changes stay in its owning feature
  modules; run relevant Python/Rust/dashboard checks through `kllm-test`.
- Use `scripts/install.py --llama-only --engine primacpp --from-source` with the
  resolved `--source-repo`, `--revision`, `--recipe-digest`, `--runtime-dir` and
  explicit backend. Follow `docs/agent-install.md` for toolchain recovery.
  Managed builds must not overwrite bootstrap `vendor/bin`.
- During development, build the edited fork using its CMake targets and retain
  its diff plus real test results. Label worktree evidence as developmental;
  modified files and manually restamped artifacts do not qualify a published pin.


## Deferred acceptance boundary

Honor the current task's validation boundary. For this integration, the user has
deferred performance acceptance and physical distributed validation to a live
Kluster cluster of three computers. Do not run further local paired acceptance
or performance-gate diagnostic experiments. Continue implementation and correctness
checks, preserve the existing thresholds, and keep deferred gates unpassed.


---

**Related:** 12-placement-qualification.md holds the qualification-policy, evidence-separation and delivery rules; 10-profile-costs.md holds the paired-benchmark and diagnostic-build rules.
