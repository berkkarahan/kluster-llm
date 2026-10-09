# Memory-First Workflow

This file defines how agents prepare context before planning or implementing a change.
It implements the requirement in `AGENTS.md` and the quality gates in `constitution.md`.

## Before planning

1. Prepare project memory using the best available path, in this order:
   - configured Spec Kit memory MCP tools, when present;
   - the `/speckit.memory-md.prepare-context` command, when installed;
   - otherwise the markdown fallback in `docs/memory/`.
2. Review the prepared synthesis together with:
   - `docs/memory/INDEX.md` (routing map for durable memory);
   - relevant files under `docs/memory/architecture/` and `docs/memory/decisions/`;
   - governance under `.specify/memory/` (`constitution.md`,
     `architecture_constitution.md`).
3. In a read-only planning or review task, use the existing synthesis and the markdown
   fallback. Do not invent missing memory files; report them instead.
4. Memory preparation may write synthesis files. Do not capture or publish durable memory
   without the user's authorization.

## Feature cycle

Feature work uses the Spec Kit lifecycle:

`/speckit.specify` → `/speckit.clarify` → `/speckit.plan` → `/speckit.tasks` →
`/speckit.implement`, with `/speckit.analyze` and `/speckit.converge` for checks and gaps.

- Feature folders are `specs/NNN-slug/` with sequential numbering, containing at minimum
  `spec.md`, `plan.md`, `tasks.md`, and feature-local `memory.md`.
- Feature `memory.md` is scratch context for one feature; it is not durable project memory.
- Hard-won, reusable knowledge belongs in `docs/memory/` with an `INDEX.md` entry.

## Boundaries

- `.specify/memory/` is governance and workflow. Change it only through the amendment
  process in `constitution.md`.
- `docs/memory/` is durable project memory. Add entries only with user authorization.
- `specs/` is per-feature working state. Keep it scoped to the feature.
