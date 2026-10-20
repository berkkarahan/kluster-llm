---
name: kllm-audit
description: "Audit the full Kluster LLM codebase (Python backend, Rust native crate, Svelte 5 dashboard) across bugs, performance, and maintainability. On-demand only; requires explicit human user permission before launching."
disable-model-invocation: true
---

# Audit Kluster LLM Codebase

Guide for conducting an on-demand, non-destructive audit of the `kluster-llm` repository. Covers Python application modules (`src/kllm/`), native Rust extensions (`crates/kllm-native/`), and the Svelte 5 dashboard (`dashboard/src/lib/`) across **Bugs & Correctness**, **Performance & Efficiency**, and **Maintainability & Architectural Boundaries**.

This skill is strictly **on-demand** and must **never** be invoked automatically.

---

## 1. Human Permission Required Before Audit Launch

**MANDATORY FIRST STEP**: Before inspecting any code, executing any CLI or diagnostic commands, or gathering review notes, you MUST prompt the human user and receive explicit, unambiguous authorization to proceed.

1. State clearly to the user:
   - Target scope: `kluster-llm` full codebase (`src/kllm/`, `crates/kllm-native/`, `dashboard/src/lib/`).
   - Audit dimensions: Bugs & Correctness, Performance, Maintainability.
   - Non-destructive guarantee: All operations are strictly read-only; no tracked files, uncommitted changes, or git state will be modified.
   - Output destination: Review artifacts will be stored in a separate designated audit workspace (e.g. `~/teamwork_projects/kluster_prima_review/`).
2. Ask: *"Do you give permission to launch the on-demand Kluster LLM codebase audit?"*
3. **HALT and wait** for the user's explicit confirmation. Do NOT proceed to Step 2 until permission is granted.

---

## 2. Environment Setup & Workspace Hygiene

Once explicit user permission is confirmed:

1. **Locate and Verify Repository**: Locate the Kluster checkout using `KLLM_REPO_ROOT`, current checkout, or `kllm.shared.state.repo_root()`, and verify it contains `src/kllm/`, `crates/kllm-native/`, `dashboard/`, and `pyproject.toml`.
2. **Strict Read-Only Verification**: Check `git status` to confirm working tree state. Never stage, commit, revert, or modify tracked repository files during an audit.
3. **Establish Architectural Governance**: Review normative architecture rules:
   - `AGENTS.md`: Bootstrapping rules, install boundary, platform support expectations.
   - `.specify/memory/architecture_constitution.md`: Module boundaries, gateway encapsulation, forbidden composition root imports.
4. **Symbol & Call-Path Exploration**:
   - If `.codegraph/` exists, use `codegraph explore "<query>"` before raw grep or ad-hoc file reading to trace symbol definitions and dynamic dispatch hops.
   - When CodeGraph is unavailable, use targeted grep and file viewing.

---

## 3. Tri-Dimensional Audit Methodology

Conduct the audit systematically across the three repository tiers using the [Audit Checklist](references/checklist.md):

### Scope A: Python Application Backend (`src/kllm/`)
- **Process & Lifecycle Management**: Inspect `agent/application_lifecycle.py`, `agent/runtime.py`, and `modules/models/interactors/download_transfer.py`. Look for uncancelled coroutines on timeout, blocking calls inside async event loops, and missing download resume headers.
- **State Concurrency & Shared State**: Review `shared/state.py` (`ClusterState.save()`). Check for missing re-entrant locks during dictionary serialization (`asdict(self)`) under concurrent FastAPI endpoint mutations.
- **Security & Protocol RFC Compliance**: Check `shared/auth.py` and `agent/runtime.py`. Verify constant-time comparison (`secrets.compare_digest`) for join keys, and case-insensitive Bearer token parsing (RFC 7235 / RFC 6750).
- **Subprocess Security**: Audit `modules/managed_network/interactors/identity.py` for unqualified Windows binary calls (e.g. `whoami`, `icacls`) vulnerable to untrusted PATH search (CWE-426).
- **Architectural Boundary Invariants**: Check `modules/*/endpoints.py` for forbidden inverted imports from composition roots (e.g. `_host()` importing `kllm.agent.runtime`), and check `agent/__init__.py` for dangerous `importlib.reload` side-effects.

### Scope B: Native Rust Extension (`crates/kllm-native/`)
- **Cross-Platform Telemetry**: Inspect `src/usage.rs`. Ensure Linux `/proc/<pid>/stat` and `statm` parsing is implemented and does not return `Err("unsupported_counter")`, which breaks Linux worker NetworkBridge relays.
- **Process Supervision & Tree Termination**: Audit `src/process.rs` (`stop_process`, `process_alive`). Verify graceful shutdown (`SIGTERM` before `kill()`), Windows Job Object assignment with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, and native Win32 `OpenProcess` polling instead of spawning `tasklist.exe`.
- **Event Bus & Lock Contention**: Review `src/network/address_events.rs` (`Shared::emit`). Ensure lock contention on event queues does not spuriously trigger permanent sticky `"gap"` health states that abort platform network barriers.
- **Hardware Enumeration**: Inspect `src/hw.rs` and `src/bin/network_helper/`. Verify GPU discovery avoids expensive PowerShell process spawning in favor of native registry or DXGI APIs.

### Scope C: Svelte 5 Dashboard (`dashboard/src/lib/`)
- **Live Feed Robustness**: Inspect `dashboard/src/lib/live/updates.ts`. Verify log tag classifiers (`tagMatchesSource`) do not terminate active WebSocket connections (code 1008) when encountering unclassified or new log sources.
- **Timer & Reconnection Lifecycles**: Review reconnection scheduling and snapshot generation counters. Ensure timers are cancelled on component unmount and snapshot arrival.
- **Memory Growth & Deduplication**: Inspect `dashboard/src/lib/modules/logs/interactor.svelte.ts`. Check that log deduplication sets (`seenIds`) have bounded capacity or eviction policies to prevent monotonic heap growth during long-lived sessions.

---

## 4. Evidence Standards & Finding Formulation

Every finding produced by this audit MUST meet strict evidentiary standards:

1. **Exact Disk Citation**: Must reference existing files and exact line numbers (e.g. `crates/kllm-native/src/usage.rs:48-62`).
2. **Reproducible Execution Trace**: Step-by-step code and dataflow walkthrough demonstrating how the defect manifests in real environments.
3. **Calibrated Severity**:
   - `Critical`: Causes crash, network bridge failure, unrecoverable hang, or security compromise in standard deployments.
   - `High`: Degrades core functionality, leaks OS processes/handles, causes false-positive failures, or introduces substantial latency.
   - `Medium`: Violates RFC specs, introduces edge-case races, creates moderate performance overhead, or breaches architecture rules.
   - `Low`: Minor inconsistency, cleanup oversight, or localized code hygiene debt.
4. **Concrete Remediation**: Actionable, minimal-diff fix recommendation that respects repository architecture conventions without gratuitous rewrites.

---

## 5. Adversarial Verification Rubric

Before incorporating any candidate finding into the final deliverable, independently verify it against this rubric:
- **Existence Check**: Re-read the physical file at the cited lines. Does the code exist verbatim as cited?
- **Reachability Check**: Is the execution path reachable under real operational scenarios?
- **Intentionality Check**: Is this documented as an intentional trade-off in `AGENTS.md` or `.specify/memory/`? If intentional and documented, filter out or downgrade.
- **No Speculative Nitpicks**: Reject stylistic preferences, hypothetical micro-optimizations without profiling evidence, or false positives based on assumed APIs.

---

## 6. Audit Report Structure

Deliver findings formatted under:
1. Executive Summary & Verification Statistics.
2. High-Risk Findings Table (ID, Title, Scope, Severity, File:Line, Status).
3. Detailed Verified Findings with Disk Citations, Dataflow Trace, Severity Justification, and Remediation.
4. Adversarial Critique & Refutations Log.
5. Repository State Attestation confirming zero modifications to tracked code.
