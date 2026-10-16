# Plan: Advisor (dual-model verdict)

## Goal / Requirement

Add an **advisor** capability to Kluster LLM so that when a decision is required, the main
chat (a coding agent connected through `kllm connect`, or any client on the OpenAI/Anthropic
interface) can ask a *superior* model for direction. The advisor uses two models: one "main"
and one "advisor" that is at least as strong. Each is supplied explicitly in state and may be
the same model file run at different effort levels (sampling parameters).

When the chat needs advisory input it sends a **small summary** of the situation plus the
specific *direction/verdict question*, and the superior advisor model returns a short
natural-language recommendation. The result is reported back to the main chat; the chat then
decides how to use it. This stays entirely offline — no external API, no credentials, RPC-only
to local llama-server processes.

The request has already been refined with the requester:
- Trigger: **a sub-agent that runs on the master and reports back to the main chat.**
- Models: **both explicitly configurable**; may be the same model file at different effort levels.
- Verdict shape: **a short natural-language recommendation / judgment.**
- Workflow: spec-kit memory-first (plan + PENDING.md, then implement with tests).

## Requirements / Acceptance Criteria

1. `custom` state holds optional `advisor.model`, `advisor.main`, `advisor.temperature`,
   `advisor.top_p`. Both models are explicit; omitting either disables the advisor cleanly.
2. If either configured file is missing on disk (and not a direct path), return an actionable
   400 error — never crash or fall back silently to the other model.
3. When run without any configuration, returns `{"disabled": true}` so callers always get a
   usable answer; never blocks the chat.
4. The advisor talks to each local model over the existing OpenAI interface, reusing proven
   gateway patterns (no new network primitives).
5. The strong/weak distinction is real: the superior model's text is what feeds back; if it
   fails while the weaker still answers, the weaker answer is reported, not silently dropped.
6. The response format is stable and documented so chat agents can depend on it.

## Design / Implementation Plan

New module `src/kllm/advisor.py` (pure Python, no new dependencies) holding:

- `advisor_state(state)` — read the four documented keys from `custom` plus the model
  directory, returning a normalized dict or `{"disabled": True}` when config is absent.
- `_resolve_model(name_or_path, models_dir)` — expand an explicit model name/file to a
  path, reusing `models.store.resolve_local_model`. Returns `(key, path)` or raises a typed
  `ValidationError` ("advisor model <name> not found"). A direct path (exists on disk) is used as-is.
- `run_advisor(state, *, chat_client=...)` — build two `RequestPayload`s:

      task = "Give a brief direction to this question and answer it.\n{question}"
      main    = {"model": main_path,  "messages": [main_system + task],   ..., weak=True}
      advisor = {"model": advisor_path,"messages": [adv_system + task],   ..., weak=False}
      main_system    = "You are the strong local model. Answer directly and briefly."
      adv_system     = "You are a superior judge. Read the question, decide, then give a short recommendation with your reasoning."

  Calls `chat_client("POST", url, json=payload)` for **weak first**, then (unless it returned a
  usable body) for **strong**. Uses the caller's `httpx`-style client + `infer_base(port)` URL
  so tests inject a stub. Strong text wins; on strong failure but weak success, return the weak
  summary; if both fail, raise an actionable error including port and both messages (never secrets).

RequestPayload is a thin dataclass validating: model file exists locally, temperature/top_p in
[0,2]/[0,1], stream disabled. Mirrors `run_settings.RunBody` shape at the HTTP boundary.

HTTP surface on the master agent server (`src/kllm/agent/__init__.py`):

- New handler `advisor(request)` guarded by `Depends(_require_api_token)`.
- Registered via a new tuple entry in `src/kllm/agent/routes/v2.py`: `("/node/advisor", "POST", "advisor")`.
  The master's control-port HTTP server (`build_router`) is the boundary to test per the contract.

The `state` module already has `put_state`, `load_state`, and a free-form `custom` dict — **no
changes needed there**. Config is persisted as valid JSON under state dir; documented in `cli.md`.

### Tests (pytest, deterministic — real process/httpx only)

- `test/test_advisor.py`:
  - **unit** (`run_advisor` with a stubbed chat client): weak+strong both succeed → strong wins;
    strong returns `{"error":...}` or empty → fall back to weak summary; neither has text → error;
    disabled when either config key missing.
  - **resolve unit**: explicit name/file → path; missing name → typed `ValidationError`.
  - **boundary** HTTP: mount the FastAPI app via `agent.build_router`; POST `/node/advisor`
    without token → 401; with a mocked state + mocked client wiring the master to the endpoint →
    returns strong summary and proxies `custom` into the payload (integration check per contract).

## Migration / Rollback plan

No migration. Adding config keys is additive (state schema already supports arbitrary `custom`).
Feature can be removed by deleting `advisor.py`, the handler, and the route tuple; existing endpoints unaffected.

## Verification against acceptance criteria

- Criteria 1–3: covered by unit tests (`run_advisor` disabled/no-config paths) + boundary test.
- Criteria 4: gateway pattern reused exactly; only an injected-client call is new.
- Criteria 5: strong-first-failure → weak fallback asserted in the stubbed-client test.
- Criteria 6: documented response shape and CLI usage in `cli.md`.

Run: `uv run python scripts/build.py`, then `uv run pytest -q tests/test_advisor.py -k advisor` and full suite.
