# Memory Synthesis

## Current Scope
- Feature: 012-app-10gbe-network
- Spec: Feature Specification: App-Managed Private 10GbE Network
- Feature folder: specs\012-app-10gbe-network
- Active notes: # Active Feature Context ## Request and Scope - Continue the interrupted speckit-specify request for master-triggered private 10GbE setup through socket signals , with workers configuring themselves . - Specification and Phase 1 planning are complete ; task generation and...
- Spec context: # Feature Specification : App-Managed Private 10GbE Network **Feature Branch **: `main` (existing branch ; no feature branch created ) **Feature Directory **: `specs/012-app-10gbe-network` **Created**: 2026-10-01 **Status**: Ready for planning **Input**: User...

## Relevant Project Context
- [none]

## Relevant Decisions
- [D1] Keep responsibilities in the established modules: Python CLI/agent code in python/kllm/ , native runtime code in crates/kllm-native/ , and UI code in dashboard/ . Python environments MUST use uv and the committed Python/dependency configuration. Follow docs/agent-install.md for setup and recovery; after packaging changes, use uv run python scripts/install.py or uv run python scripts/build.py to rebuild. (Source: `.specify/memory/constitution.md`)
- [D2] User input, configuration, network payloads, and subprocess results MUST be validated at their trust boundaries. Invalid input MUST produce an actionable error rather than partial success. Public CLI options, API payloads, configuration formats, and native interfaces MUST remain compatible unless an intentional breaking change documents the impact and migration steps. (Source: `.specify/memory/constitution.md`)

## Active Architecture Constraints
- [A1] Structural standard for specifications, plans, tasks, and reviews. Product behavior and trust boundaries stay in the project constitution. This file says how code is shaped so a later change does not invent a second style. (Source: `.specify/memory/architecture_constitution.md`)

## Accepted Deviations
- [none]

## Relevant Security Constraints
- [none]

## Related Historical Lessons
- [none]

## Conflict Warnings
- [none]

## Retrieval Notes
- Index entries considered: 3
- Source sections read: 3
- Budget status: within limit
