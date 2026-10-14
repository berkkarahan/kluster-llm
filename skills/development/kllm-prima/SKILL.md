---
name: kllm-prima
description: "Add features, improvements or bug fixes to berkkarahan/prima.cpp while integrating and validating them through Kluster LLM's managed Prima runtime. Use for Prima native changes, source/recipe updates, Windows/Vulkan assessment and engine qualification; use kllm-develop for unrelated Kluster changes."
---

# Develop Prima through Kluster LLM

Work across Kluster and `https://github.com/berkkarahan/prima.cpp`. Kluster owns
membership, final placement, authenticated preparation and process lifecycle;
Prima owns native model/backend execution, profiling and its server behavior.
Prima is an optional engine. Inspect current qualification before treating it as
selectable or production-ready.

This skill is split into topic files next to this index. Read this index, then
read **only the topic files your task touches**. Every topic file starts with
condensed Quick rules and keeps the full normative text verbatim beneath them.

> **Runtime copy note.** The discovery copy under `.agents/skills/kllm-prima/`
> is generated from this canonical directory by `scripts/install_skills.py`.
> After editing these files, rerun that installer (`--force`; it backs up the
> stale copy) so the installed copy contains this index and all topic files.

## Lifecycle spine

1. **Establish both sources** — 01-sources-setup.md: read Kluster's
   `AGENTS.md`/memory workflow/`kllm-develop`, the Prima docs and the spec 017
   set; treat `recipe.json` pins as authoritative and compute the digest through
   `recipe_digest` (never hardcode it, never read a moving HEAD); use an
   isolated pinned-commit checkout; fix the failing behavior's owner without
   swapping engines.
2. **Implement and measure** — 02-build-lifecycle.md for the validation
   schedule: implementation first, tests deferred until the implementation is
   complete, compilation is not acceptance, reproduction evidence is retained,
   and all worktree evidence is labelled developmental. Windows builds/ISA →
   03-windows-native.md; Vulkan devices → 04-gpu-vulkan.md.
3. **Publish only when authorized** — 12-placement-qualification.md: fork diff
   → fast-forward push to `codex/kluster-integration` → recipe bump to the
   exact SHA → fresh isolated rebuild. Without authorization, leave a
   reviewable diff and report the required step.
4. **Qualification stays off without evidence** — performance and physical
   distributed acceptance remain deferred to the user's live three-computer
   cluster; controlled/local/physical results stay labelled and separate
   (02-build-lifecycle.md, 12-placement-qualification.md). Build or
   device-discovery success is never described as completed inference.

## Topic index

| File | Read it for | Keywords |
|---|---|---|
| 01-sources-setup.md | Both sources: docs to read, recipe pins/digests, checkout hygiene, failure-ownership boundaries | recipe.json, digest, pin, checkout, owner |
| 02-build-lifecycle.md | Validation schedule, defect reproduction/evidence, managed install builds, worktree labeling, deferred-test phase | validation, reproduce, evidence, install.py, from-source |
| 03-windows-native.md | Windows Release builds, ISA/CPUID/AVX-512 detection, MSVC /EHsc C boundaries, CPU graph scheduling & cancellation | MSVC, EHsc, CPUID, AVX-512, relocation, cancellation |
| 04-gpu-vulkan.md | Vulkan logical→physical mapping, explicit devices/visibility, driver-unavailable startup, output-only GPU coordinators | Vulkan, ordinal, UUID, visibility mask, explicit_devices_v1, output_only_gpu_v1 |
| 05-lora.md | All LoRA: managed loading, assets, native selection/sync, admission, embedding adapters, per-request, activated invocation | LoRA, adapter, managed_lora_v1, request_lora_v1, activated_lora_v1, scales |
| 06-reasoning-tools.md | Reasoning controls/budgets, manual control, reasoning wire, tool history/streams, automatic & forced tools, GBNF grammars | reasoning, thinking, tool, GBNF, grammar, streaming_tools, reasoning_wire_v1 |
| 07-server-apis.md | HTTP surface: templates & keyword defaults, tokenizer/apply-template, completions, infill, embeddings, rerank, split GGUF assets | apply-template, /v1/completions, /infill, /embeddings, rerank, split GGUF, template_kwargs_defaults_v1 |
| 08-models-architectures.md | Adding/fixing architectures, attention sinks, expert biases, quantized formats, cost models, per-family notes | architecture, GPT-OSS, Gemma, Granite, OLMo, OpenELM, quantization, LongRoPE, shared expert |
| 09-cache-replay.md | KV caches (compact SWA), prompt cache, checkpoints, drafts, CLI/server replay, self-extend, head KV ceiling | KV, cache, checkpoint, SWA, rewind, draft, replay, self-extend, prompt_cache_v1 |
| 10-profile-costs.md | Kernel/full-model profiling, I/O probes, signed profile protocol, private links, transfer grants, Vulkan perf builds | profiling, GFLOPS, roofline, profile_edges, logits, GGML_VULKAN_PERF |
| 11-run-lifecycle-network.md | Readiness/launch journaling, signed ownership, network reservations, privileged firewall helper, admission & watchdogs | readiness, journal, firewall, helper, watchdog, admission, lease, recovery |
| 12-placement-qualification.md | Manifest/placement mode, segmented execution, candidate generation, qualification presets, publication & delivery | placement, manifest, segmented_placement, qualification, publish, recipe bump |
