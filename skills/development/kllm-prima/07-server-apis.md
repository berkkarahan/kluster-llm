# 07 · Server APIs, templates & assets

**Use when:** You expose or change the HTTP surface — template rendering and keyword defaults, `/tokenize`/`/detokenize`/`/apply-template`, `/v1/completions`, raw `/completion`/`/completions`, `/infill`, `/embedding`/`/embeddings`, reranking, embedding projection/roles — or handle verified asset manifests and split GGUF/companion assets.

**Keywords:** apply-template, apply_template_v1, tokenize, detokenize, /v1/completions, /completion, /completions, /infill, FIM, /embeddings, rerank, TEI, texts, return_text, Jinja, template_kwargs_defaults_v1, decode_metadata_v2, embedding_role_v1, split GGUF, shards, basenames, asset manifests, capability.

**Related:** 05-lora.md, 06-reasoning-tools.md, 08-models-architectures.md, 12-placement-qualification.md.

## Quick rules

- Asset manifests: keep model shards, drafts, projectors and templates in verified asset manifests; when chat generation differs between engines, compare each engine's actual rendered prompt token IDs through its own /apply-template and tokenizer before attributing the failure to model execution; share native rendering with generation, exercise history and malformed flags at the HTTP boundary, and negotiate `apply_template_v1`; equal prompts or output bytes do not establish equal generated tokens or API parity; keep native usage counts and logprob token pieces — never replace them with output retokenization; development capability reports must come from the tested binary, and development overrides remain explicitly unqualified until published/rebuilt.
- Template rendering: use the parser/runtime pinned to Kluster's baseline and retain source/license provenance; compare every bundled preset against an independent Jinja reference, then check actual model prompts/token IDs and serving behavior; freeze selected source bytes into the signed manifest before preparation, bind those bytes to qualification, and require the native Jinja capability; rank zero renders prompts and workers consume its token stream; keep execution contexts separate under concurrent requests, bound file/source size, reject malformed templates before readiness, and share generation/apply-template rendering; do not append legacy thinking suffixes after a Jinja template already rendered them; keep input-origin token marking and automatic tool parsing distinct from plain template rendering — a renderer pass cannot qualify those broader features.
- Deployment template keyword defaults: merge request values over the owned deployment object in the same renderer used by chat, Responses, Messages, apply-template, input-token counting and automatic tool preparation; negotiate `template_kwargs_defaults_v1` before preparation; validate bounded finite JSON, preserve dictionary insertion order, stage an owned content-addressed JSON file on rank zero and verify existing bytes without following symlinks; native startup must match parsed defaults against the signed execution manifest; the file path avoids Windows argv limits and workers consume rendered tokens; named legacy aliases must not silently ignore keyword defaults.
- Embedding projection: separate input/transformer width from output vector width across metadata, graph pooling, allocation, reordering, C getters, snapshots, server JSON and managed budgets; aggregate pooled inputs before any quantized projection (averaging projected microbatch means changes operation order); use sequence-ceiling columns for sparse/shared IDs and preserve complete token output selection; a final head-only graph must use the assigned output backend, settled worker wave and correct scheduler for evidence, with reset before metadata cleanup on success or failure; worker counts use received batch metadata; negotiate `decode_metadata_v2` before distributed preparation; keep native matrix and OpenAI pooled response contracts distinct with real token usage, normalization and encoding.
- Roles & launch help: inspect launch help by process role — the server owns embedding/pooling switches; CLI workers receive selected output rows in batch metadata and must not be given unsupported server flags; preserve those options in immutable extras, validate choices and capability, and distinguish embedding from chat qualification scopes; wire new native embedding features through authenticated route registration and qualification as well as the binary; resolve the served model ID, preserve native input/format fields and keep generation defaults out of embedding requests; parse frozen mode extras by arity in native manifests so JSON/adapter values cannot become flags; compare the head/worker role and reject environment/argv mode overrides; negotiate `embedding_role_v1` before execution; unsupported classifier pooling must refuse before graph construction.
- Reranking: bind `--reranking`/`--rerank` to the head only and reject an embedding-mode combination or non-rank pooling; give reranking its own serving qualification feature (an embedding-qualified or chat-qualified run does not authorize rerank requests); route all native rerank aliases through Kluster's authenticated gateway, resolve the single served model ID and require the native rerank API capability; preserve the source document index when native completion order varies, sort by descending relevance score, apply the bounded `top_n` count and report evaluated token usage; match the pinned baseline's `texts` request variant (TEI score array, source text only when `return_text` is true) with a separate native capability and request feature; keep optional `top_n` and returned text within request-scoped qualification.
- Completion routes: preserve the OpenAI text-completion contract of `/v1/completions` separately from the raw `/completion` and `/completions` endpoints — indexed `choices[].text`, finish reasons, usage and SSE `[DONE]`, bounded multiple-prompt/choice and logprob semantics; `/infill` requires model FIM prefix/suffix/middle tokens, validates string prefix/suffix plus optional prompt and structured extra context, supplies an empty default prompt (the native task creator still requires the field) and keeps FIM formatting in the native tokenizer with literal special-token text; raw `/completion`-`/completions` keep their raw response/stream format with a separate capability and request feature; `/embedding` and `/embeddings` retain the native embedding response format and `content` input behavior rather than converting to OpenAI `/v1/embeddings`; bind every alias through the authenticated gateway to the exact served model with per-family capability and request-feature checks at the gateway.
- Split GGUF and companion assets: collect every declared basename before preparation, require the first shard as the selected native model, transfer each signed digest, and verify the complete run-owned basename layout before native launch; a digest-addressed cache file is not a usable split-model layout by itself; refuse missing shards, symlinks, changed existing layout files and case-folding basename collisions; publish new names through a temporary file so an interrupted copy cannot look complete.

## Full text (normative)

### Asset manifests & template comparison

- Keep model shards, drafts, projectors and templates in verified asset manifests.
  When chat generation differs, compare each engine's actual rendered prompt token
  IDs through its own /apply-template and tokenizer before attributing the failure
  to model execution. Share native rendering with generation, exercise history and
  malformed flags at the HTTP boundary, and negotiate apply_template_v1. Equal
  prompts or output bytes do not establish equal generated tokens or API parity.
  Keep native usage counts and logprob token pieces; never replace them with output
  retokenization. Development capability reports must come from the tested binary,
  and development overrides remain explicitly unqualified until published/rebuilt.


### Template rendering & provenance

For template work, use the parser/runtime pinned to Kluster's baseline and retain
source/license provenance. Compare every bundled preset against an independent
Jinja reference, then check actual model prompts/token IDs and serving behavior.
Freeze selected source bytes into the signed manifest before preparation; bind
those bytes to qualification and require the native Jinja capability. Rank zero
renders prompts and workers consume its token stream. Keep execution contexts
separate under concurrent requests, bound file/source size, reject malformed
templates before readiness, and share generation/apply-template rendering. Do not
append legacy thinking suffixes after a Jinja template already rendered them.
Keep input-origin token marking and automatic tool parsing distinct from plain
template rendering; a renderer pass cannot qualify these broader features.


### Deployment template keyword defaults

For deployment template keyword defaults, merge request values over the owned
deployment object in the same renderer used by chat, Responses, Messages,
apply-template, input-token counting and automatic tool preparation. Negotiate
template_kwargs_defaults_v1 before preparation. Validate bounded finite JSON,
preserve dictionary insertion order, stage an owned content-addressed JSON file
on rank zero and verify existing bytes without following symlinks. Native startup
must match parsed defaults against the signed execution manifest. This file path
avoids Windows argv limits; workers consume rendered tokens. Named legacy aliases
must not silently ignore keyword defaults. Keep precedence, malformed input,
manifest/file drift and model/protocol checks in the deferred test phase until the
full implementation is finished.


### Embedding projection integration

Embedding projection integration must separate input/transformer width from output
vector width across metadata, graph pooling, allocation, reordering, C getters,
snapshots, server JSON and managed budgets. Aggregate pooled inputs before any
quantized projection; averaging projected microbatch means changes operation order.
Use sequence-ceiling columns for sparse/shared IDs and preserve complete token
output selection. A final head-only graph must use the assigned output backend,
settled worker wave and correct scheduler for evidence, with reset before metadata
cleanup on success or failure. Worker counts must use received batch metadata;
owned bounded vectors and complete shared-sequence/all-output flags avoid leaks
and graph-shape disagreement. Negotiate decode_metadata_v2 before distributed
preparation. Keep native matrix and OpenAI pooled response contracts distinct,
real token usage, normalization and encoding; source/build metadata is not API or
model acceptance. Continue the user's full-implementation-first test schedule and
live three-computer physical/performance deferral.


### Embedding roles & launch help

Inspect launch help by process role. The server owns embedding/pooling switches;
CLI workers receive selected output rows in batch metadata and must not be given
unsupported server flags. Preserve those options in immutable extras, validate
choices and capability, and distinguish embedding from chat qualification scopes.
Role-specific help is metadata evidence only, not a test or acceptance result.


Wire new native embedding features through authenticated route registration and
qualification as well as the binary. Resolve the served model ID; preserve native
input/format fields and keep generation defaults out of embedding requests.
Qualify encoding, requested dimensions and normalization explicitly. Parse frozen
mode extras by arity in native manifests so JSON/adapter values cannot become
flags; compare the head/worker role and reject environment/argv mode overrides.
Negotiate embedding_role_v1 before execution. Unsupported classifier pooling
must refuse before graph construction. Source wiring and capability/help/build
metadata do not prove API, model, backend or hardware acceptance. Keep the user's
implementation-first tests and live-cluster physical/performance deferrals.


### Reranking

For reranking, bind `--reranking`/`--rerank` to the head only and reject an
embedding-mode combination or non-rank pooling. Give reranking its own serving
qualification feature; an embedding-qualified or chat-qualified run does not
authorize rerank requests. Route all native rerank aliases through Kluster's
authenticated gateway, resolve the single served model ID and require the
native rerank API capability. Preserve the source document index when native
completion order varies, sort by descending relevance score, apply the bounded
`top_n` count and report evaluated token usage. A successful build or advertised
capability does not establish rerank model, API, backend or distributed parity.
Match the pinned baseline's `texts` request variant: return a TEI score array,
include source text only when `return_text` is true, and require a separate
native capability and request feature for that variant. Keep optional `top_n`
and returned text within request-scoped qualification rather than treating all
rerank calls as identical.


### Tokenizer & template API

For tokenizer and template API work, compare the pinned llama.cpp request and
response contracts with Prima's handlers before exposing them through Kluster.
Bind `/tokenize`, `/detokenize` and `/apply-template` to the authenticated
gateway and the exact served model. Scope mixed token input, special-token
parsing, returned pieces and template tool/reasoning options separately where
they change behavior. Keep the selected template and defaults in the signed
run context; gateway exposure and native capability metadata are not model/API
acceptance. Follow the current user test deferral before any model-backed or
native contract check.


### /v1/completions

For `/v1/completions`, preserve the OpenAI text-completion contract separately
from the raw `/completion` and `/completions` endpoints. Bind the served model
through Kluster's authenticated gateway, emit indexed `choices[].text`, finish
reasons, usage and SSE `[DONE]`, and retain bounded multiple-prompt/choice and
logprob request semantics. Put source and request-scoped capability checks at
the gateway; compilation and JSON shape alone do not establish full baseline
API or model-backed streaming parity.


### /infill

For `/infill`, require model FIM prefix/suffix/middle tokens and validate string
prefix/suffix, optional string prompt, and structured extra context before
task creation. The native task creator still requires a prompt field even when
the API makes it optional, so supply an empty default and keep FIM formatting
in the native tokenizer. Bind the route through Kluster's authenticated gateway
to the exact served model and require a distinct infill capability and request
feature. Keep literal special-token text literal in user-supplied FIM input.
Native compilation and advertised support do not establish FIM model/API
acceptance; follow the user's test deferral and live-cluster gate schedule.


### Raw /completion and /completions

For native `/completion` and `/completions`, preserve their raw response and
stream format separately from `/v1/completions`. Bind each alias through the
authenticated gateway to the exact served model. Require a separate raw
completion capability and request feature, validate prompt/stream/probability
options at the boundary, and convert native malformed requests to clear
errors. Source/build evidence alone does not qualify these API paths.


### /embedding and /embeddings

For `/embedding` and `/embeddings`, retain the native embedding response format
and `content` input behavior rather than converting them to OpenAI
`/v1/embeddings`. Route both aliases through the authenticated gateway with
the exact served-model check and a separate raw-embedding request feature.


### Split GGUF & companion assets

For split GGUF and companion assets, collect every declared basename before
preparation, require the first shard as the selected native model, transfer
each signed digest, and verify the complete run-owned
basename layout before native launch. A digest-addressed cache file is not a
usable split-model layout by itself. Refuse missing shards, symlinks and
changed existing layout files and case-folding basename collisions; publish
new names through a temporary file so
an interrupted copy cannot look complete. Keep regression checks written for
these boundaries, but follow the user's current instruction to defer running
unit/native/model tests until the full implementation is finished.


---

**Related:** reranking's classifier-output plumbing lives in 08-models-architectures.md; run/wire ownership continues in 11-run-lifecycle-network.md.
