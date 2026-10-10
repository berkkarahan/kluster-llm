# Kluster LLM Constitution

## Core Principles

### I. Simple, Consistent Code

- Changes MUST follow the naming, structure, and idioms of the surrounding module.
- Contributors MUST inspect the affected flow and its callers before editing. Bug fixes MUST
  address the shared root cause when multiple callers are affected.
- Existing helpers, standard libraries, and native platform features MUST be considered before
  adding code or dependencies. New abstractions and dependencies MUST solve a current requirement
  and include a concrete justification in the change description.
- Changes MUST stay scoped to the requirement; unrelated refactors and speculative extension
  points MUST be excluded.

Rationale: less duplicated logic and fewer moving parts reduce review and maintenance costs.

### II. Behavior-Focused Regression Tests

- New or changed non-trivial logic MUST have a runnable automated check of observable behavior,
  using the existing test framework. Existing tests MAY satisfy this requirement when they cover
  the changed behavior; trivial edits and documentation-only changes need no new tests.
- Bug fixes MUST include a regression check that fails for the original defect and passes after
  the fix. Relevant boundary inputs and failure paths MUST be covered, not just the happy path.
- Tests MUST be deterministic and isolated: use temporary state and controlled external
  dependencies rather than real credentials, public services, GPUs, or live clusters by default.
- Tests MUST assert outcomes or public contracts rather than reproduce implementation logic.
  A coverage percentage alone MUST NOT substitute for meaningful assertions.

Rationale: small, reliable checks catch regressions without making refactoring unnecessarily costly.

### III. Verified Integration and Portability

- Changes to CLI/API contracts, Python/Rust bindings, process supervision, or node communication
  MUST include a check at the affected boundary; mocked unit tests alone do not prove integration.
- Platform-specific behavior MUST be isolated and checked for affected supported platforms
  (Windows, Linux, and macOS). Hardware-dependent checks MUST state their OS/backend prerequisites.
- GPU, native-runtime, or multi-node behavior MUST be verified on the relevant hardware before
  being reported as validated. When unavailable, the change record MUST identify the unverified
  behavior and the exact follow-up check; skipped checks MUST NOT be presented as passes.
- Performance claims MUST include a reproducible workload and before/after results with the
  model, backend, and hardware identified.

Rationale: distributed inference can fail at process, platform, and device boundaries despite
passing isolated tests.

### IV. Explicit Contracts and Failures

- User input, configuration, network payloads, and subprocess results MUST be validated at their
  trust boundaries. Invalid input MUST produce an actionable error rather than partial success.
- Public CLI options, API payloads, configuration formats, and native interfaces MUST remain
  compatible unless an intentional breaking change documents the impact and migration steps.
- Errors MUST retain diagnostic context without exposing tokens, join keys, or other secrets.
  Fallbacks MUST be explicit; failures MUST NOT be swallowed or reported as healthy state.
- Resources acquired by a change, including processes, sockets, and temporary files, MUST have
  defined cleanup on success, failure, and cancellation. State updates MUST preserve recoverability
  when interrupted.

Rationale: explicit failure behavior makes cluster problems diagnosable and prevents hidden damage.

### V. Maintainable, Reproducible Changes

- Behavior, configuration, installation, or operational changes MUST update the relevant existing
  documentation in the same change. Commands in documentation MUST match the implemented interface.
- Comments MUST explain non-obvious constraints or decisions rather than restate code. Deliberate
  shortcuts MUST document their known limit and the condition that warrants replacing them.
- Dependency changes MUST update applicable manifests and lockfiles together. Packaging changes
  MUST be rebuilt through the repository's supported tooling and checked by importing the package
  and loading the native extension where affected.
- Change descriptions MUST record the reason for the change, verification commands and results,
  and any remaining limitations. New tooling MUST have a demonstrated need unmet by existing tools.

Rationale: synchronized documentation, reproducible builds, and recorded evidence let the next
maintainer reproduce both expected behavior and failures.

## Repository Constraints

- Keep responsibilities in the established modules: Python CLI/agent code in `python/kllm/`,
  native runtime code in `crates/kllm-native/`, and UI code in `dashboard/`.
- Python environments MUST use `uv` and the committed Python/dependency configuration. Follow
  `docs/agent-install.md` for setup and recovery; after packaging changes, use
  `uv run python scripts/install.py` or `uv run python scripts/build.py` to rebuild.
- The committed stable `llama-release` tag is the bootstrap default. Explicit managed runtime
  selections MAY use public forks, releases, tags, branches, or commits, resolved to immutable
  repository/commit identities. Every run participant MUST use the same source identity with
  verified node-local artifacts and compatible RPC transport. Bootstrap pin changes retain
  `python scripts/install.py --llama-only` and `--check` verification.
- Upstream RPC is unauthenticated. Port **50052** MUST remain on a private LAN or WireGuard overlay
  and MUST NOT be exposed publicly.
- Specifications, plans, tasks, and reviews MUST comply with
  `.specify/memory/architecture_constitution.md`. That file is the structural coding
  standard: how modules, imports, and test patch surfaces are shaped. It does not add
  product behavior. Amended 2026-10-09: each feature owns its module layout and its
  test patch surface; facades remain only for the external entry points named in that
  file. Reason: internal moves were retaining a facade for every historical patch path.
  Impact: later specs patch the owning module, and the listed entry points stay
  importable.

## Development Workflow and Quality Gates

1. Before implementation, identify the affected callers, public contracts, and acceptance checks.
   Specifications and plans MUST account for these principles and record any proposed exception.
2. Implement the smallest complete change, add or update the required checks, and update affected
   documentation. Use the existing verification commands appropriate to the changed surface:

   | Surface | Verification from repository root |
   |---------|-----------------------------------|
   | Python CLI, agent, or scripts | `uv run pytest -q` |
   | Rust native runtime | `cargo test --manifest-path crates/kllm-native/Cargo.toml` |
   | Dashboard types and diagnostics | `npm --prefix dashboard run check` |
   | Dashboard production build | `npm --prefix dashboard run build` |

   Targeted test selections MAY be used when they cover the affected behavior and callers. Shared
   contracts or broad changes MUST also run the relevant integration checks and wider suites.
   Documentation-only changes MUST verify referenced paths, commands, and internal consistency.
3. Before merge, review the diff for correctness, duplication, compatibility, failure handling,
   and documentation accuracy. Record commands and results; changes MUST NOT introduce failing
   checks. Pre-existing failures or unavailable environments MUST be identified with evidence
   and follow-up rather than silently omitted.

## Governance

This constitution defines the project's development quality baseline. Specifications, plans,
implementation tasks, and reviews MUST comply with it. `AGENTS.md` and `docs/agent-install.md`
provide operational guidance; conflicts MUST be resolved explicitly rather than silently bypassed.

Amendments MUST update this file, explain the reason and impact, and receive maintainer approval
through the normal review process. The amendment review MUST check dependent templates, commands,
and guidance for conflicts and identify any required follow-up. Update the version and last-amended
date on every amendment; preserve the original ratification date.

Constitution versions use semantic versioning: **MAJOR** for incompatible principle removals or
redefinitions, **MINOR** for new principles or materially expanded obligations, and **PATCH** for
clarifications that do not change obligations. Initial adoption is **1.0.0**.

Every change review MUST check compliance with the applicable principles and quality gates.
Any temporary exception MUST record its rationale, bounded scope, maintainer approval, and a
follow-up action with a review date in the change record. Exceptions MUST NOT permit public RPC
exposure, secret disclosure, or removal of safeguards against data loss.

**Version**: 1.3.0 | **Ratified**: 2026-09-28 | **Last Amended**: 2026-10-09
