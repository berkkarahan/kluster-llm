# Memory-MD Skill

Auto-loaded skill for Spec Kit Memory Hub. Tells the agent **when** and **how** to use memory-md commands.

## Trigger Map

Use this map to determine which command to execute based on the current workflow phase:

| Phase | Command | Auto-Execute? |
|-------|---------|---------------|
| Project setup | `/speckit.memory-md.init` | Once |
| Before `/specify` | `/speckit.memory-md.specify` | Yes |
| Before `/plan` | `/speckit.memory-md.plan-with-memory` | Yes |
| During implementation | `/speckit.memory-md.prepare-context` | Yes |
| After verify | `/speckit.memory-md.capture` | Yes (>50% confidence) |
| After bug fix | `/speckit.memory-md.capture-from-diff` | Yes (>50% confidence) |
| New feature start | `/speckit.memory-md.sync-shared` | Yes (>50% confidence) |
| Lesson validated | `/speckit.memory-md.share-lesson` | Yes (>50% confidence) |
| Memory noisy | `/speckit.memory-md.audit` | On-demand |
| Index rows missing | `/speckit.memory-md.repair-index` | On-demand |
| Stack changed | `/speckit.memory-md.init-project` | On-demand |
| Build doc cache | `/speckit.memory-md.index-docs` | Once/session start |
| Audit finding | `/speckit.memory-md.log-finding` | After audit |
| Evaluate ROI | `/speckit.memory-md.token-report` | On-demand |

## Auto-Execute Rules

**Confidence-based auto-approval:**

- **> 50% confidence**: Auto-execute without asking the user
- **<= 50% confidence**: Surface the proposal in conversation and ask: *"I've identified a potential lesson worth capturing: [X]. Shall I record it?"*
- **User override**: User can always trigger any command manually regardless of confidence

**When to evaluate for capture:**
- After completing a complex feature
- After fixing a recurring bug
- After discovering a non-obvious architectural constraint
- After resolving a conflict between spec and existing memory

## MCP vs CLI Fallback

**Preference order:**

1. **MCP tools** (preferred) — Use when `speckit-memory-hub` MCP server is configured:
   - `speckit_memory_refresh_cache` — sync `.md` files to SQLite
   - `speckit_memory_search` — search the SQLite cache
   - `speckit_memory_synthesize` — generate `memory-synthesis.md`
   - `speckit_memory_synthesize_docs` — generate `doc-synthesis.md`
   - `speckit_memory_register` — write durable memory entries
   - `speckit_memory_share_lesson` — publish to global shared memory
   - `speckit_memory_sync_shared` — pull from global shared memory
   - `speckit_memory_init_project` — profile tech stack
   - `speckit_memory_audit_cache` — validate cache integrity
   - `speckit_memory_repair_index` — recover missing index rows
   - `speckit_memory_token_report` — compare token usage

2. **CLI fallback** — Use `npx speckit-memory <command>` when MCP is unavailable

3. **Markdown-first fallback** — Read `docs/memory/INDEX.md` and follow `workflow.md` manually if SQLite fails

## Quick Reference

| Command | What It Does |
|---------|--------------|
| `/speckit.memory-md.init` | Bootstraps folder structure, templates, and config |
| `/speckit.memory-md.init-project` | Profiles tech stack for cross-project sync |
| `/speckit.memory-md.specify` | Prepares memory context before writing spec |
| `/speckit.memory-md.plan-with-memory` | Synthesizes constraints and gates planning |
| `/speckit.memory-md.prepare-context` | Refreshes cache and generates synthesis |
| `/speckit.memory-md.capture` | Proposes durable lessons from completed work |
| `/speckit.memory-md.capture-from-diff` | Extracts lessons from git diffs |
| `/speckit.memory-md.share-lesson` | Promotes local lesson to global shared memory |
| `/speckit.memory-md.sync-shared` | Pulls external lessons from global memory |
| `/speckit.memory-md.audit` | Audits memory quality and integrity |
| `/speckit.memory-md.log-finding` | Converts audit finding to tracker issue |
| `/speckit.memory-md.index-docs` | Builds doc cache for low-token retrieval |
| `/speckit.memory-md.repair-index` | Recovers missing INDEX.md rows |
| `/speckit.memory-md.token-report` | Compares token usage (full vs optimized) |

## Memory Layers

| Layer | Location | Purpose |
|-------|----------|---------|
| Governance | `.specify/memory/` | Stable rules, constitution, standards |
| Durable | `docs/memory/` | Historical decisions, patterns, bugs |
| Active | `specs/<feature>/memory.md` | Feature-local constraints |
| Ephemeral | (not committed) | Temporary prompt/terminal state |

## Key Rules

- **Never write directly to `docs/memory/` files** — always use `capture` flow or `speckit_memory_register`
- **Do NOT read `.md` memory files directly** — use MCP search or SQLite cache as source of truth
- **Keep synthesis under 900 words** — respect `retrieval.max_synthesis_words`
- **Human-in-the-loop** — AI proposes, human disposes (except >50% confidence auto-approval)
- **Date-based files** — Use `category/YYYY-MM-DD-short-title.md` format, never monolithic files
