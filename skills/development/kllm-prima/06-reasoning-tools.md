# 06 · Reasoning controls & tool protocols

**Use when:** You work on reasoning/thinking controls, reasoning budgets, manual reasoning control, the reasoning wire protocol (Responses/Messages thinking semantics), or tool history/streams, automatic/forced/parallel tool calls, GBNF grammars and sampler behavior.

**Keywords:** reasoning, thinking, budget, manual control, reasoning_wire_v1, reasoning_controls_v1, tool history, tool streams, Responses, Messages, tool_use, tool_result, function_call, automatic tools, tools:auto, parallel_tools, streaming_tools, GBNF, grammar cycle, nullability, sampler, prefill, reset, logprobs.

**Related:** 07-server-apis.md (rendering, apply-template, protocol translations), 08-models-architectures.md (vocabulary/metadata interplay).

## Quick rules

- Tool history: preserve returned call IDs and group consecutive historical calls; normalize each public protocol before Jinja rendering — Chat assistant/tool messages, Responses function_call/function_call_output items, Messages tool_use/tool_result blocks; match the pinned baseline's text joining and template argument representation; reject malformed IDs/arguments, unsupported non-text blocks and templates that lose calls; discover tool_turns without misclassifying text tool blocks as vision or changing native HTTP error boundaries; nested image results still require vision evidence.
- Tool streams: preserve one call/item identity through start, argument deltas and completion in Chat, Responses and Messages; keep arguments in the protocol's tool channel; retain terminal usage and native logprobs even when framing/UTF-8 buffering produces no visible text; ignore aggregate terminal content as a second delta; cover partial JSON, length limits, empty completion, repeated terminal records and errors after start; test actual model-backed streams through the authenticated gateway; named forced calls do not prove automatic choice, parallel tool parsing or complete tool-history support — those stay gated separately.
- Automatic-tool development: use the pinned baseline chat-runtime bridge rather than a new format heuristic, keeping license/provenance and API adaptations visible; wire serialized preparation, lazy triggers and the baseline parser through actual generation before advertising an HTTP capability; repeated parser elements must consume input (optional calls plus nullable whitespace can create a grammar cycle — compute transitive nullability before recursion checks and reject such cycles safely); test generated grammars, split/mid-token triggers, Unicode, clone, reset and vocabulary-backed sampler acceptance; Prima's initial model allocation does not load its vocabulary — call its checked metadata loader and free it for vocabulary-only assets; do not change the recipe while a model lifecycle assessment uses the previous pin.
- Automatic HTTP tools: use the same baseline serialized preparation for generation, apply-template and incremental parsing; preserve plain/unmarked JSON as text; compare an unexpected model choice with the pinned baseline before changing parser semantics; check explicit tagged activation and ordinary tool decline separately; required and parallel calls need actual schema-bound model output and continuations from their emitted call IDs; reserve internal preparation parameters on raw completion/infill as well as chat routes; invalid generated GBNF must refuse instead of silently disabling grammar; keep tools:auto, parallel_tools and streaming_tools qualification distinct and require the actual native automatic/streaming capability markers.
- Regular tool grammar prefill: generated regular tool grammars must accept the template's existing generation prefix before sampling, matching the pinned baseline — tokenize it with special tokens, skip only an inserted leading-space token, and reapply it after sampler reset; never prefill a user-provided grammar (missing prefill forced a duplicate assistant header and diverted reasoning into text); check real-vocabulary repeated reset plus thinking-on calls/streams across all three APIs, not only thinking-off calls; preserve failing published receipts and advance the immutable recipe when this sampler behavior changes.
- Reasoning controls: share the pinned baseline chat preparation/parser for plain, named and automatic tool responses and structured schemas; preserve effort/none, thinking toggle, output format and protocol-specific history/delta semantics (plaintext reasoning history is not vision or opaque stored state); use the baseline reasoning budget state machine with explicit model/token/sampler API adaptations and license provenance; enforce budgets in actual token selection, initialize from the generation prefix, preserve UTF-8 completion/re-arming, and suppress lazy grammar while thinking — replay completed end tags before grammar resumes; own grammar/chain/budget allocation through failure and preserve reset/clone state; bind deployment controls to the manifest, honor request aliases/overrides, and require native `reasoning_controls_v1` plus explicit request/history qualification.
- Manual reasoning control: bind the server-generated returned completion ID to its task before queue publication and reject caller-supplied internal fields; use the serialized inference loop to act only on the matching live, opted-in sampler — slot numbers and later slot reuse cannot authorize a transition; preserve baseline success/message semantics outside active counting and retain a successful control across the initial prompt sampler reset; expose the same authenticated inference proxy without generation defaults or stream flags; separately qualify control and custom delimiter features and negotiate their native capabilities; legacy rendering has the pinned baseline's pure-content/no-inferred-delimiter semantics; compose Jinja schemas with reasoning/tool grammar once — no duplicate schema or fence constraints.
- Reasoning wire: derive baseline API requirements from the pinned implementation before adding a service; the current baseline Responses converter rejects nonempty previous IDs and implements no persistence or opaque reasoning decryption — earlier notes claiming those features were inferences, now superseded; match its reasoning content array/type, empty summary/encrypted placeholders and actual stream events; replay from reasoning_text content and preserve assistant/tool turn grouping; Messages thinking uses an empty signature and signature_delta before block closure with all delta targets open until closure; require `reasoning_wire_v1` for the corrected protocol contract, separately from parser/control capabilities.

## Full text (normative)

### Tool history normalization

For tool history, preserve returned call IDs and group consecutive historical
calls. Normalize each public protocol before Jinja rendering: Chat assistant/tool
messages, Responses function_call/function_call_output items, and Messages
tool_use/tool_result blocks. Match the pinned baseline's text joining and template
argument representation. Reject malformed IDs/arguments, unsupported non-text
blocks and templates that lose calls. Discover tool_turns in each protocol without
misclassifying text tool blocks as vision or changing native HTTP error boundaries;
nested image results still require vision evidence. Check real generated call
follow-ups, streaming continuation and exact baseline prompts/token IDs separately
from automatic choice and stored-response support.


### Tool streams

For tool streams, preserve one call/item identity through start, argument deltas
and completion in Chat, Responses and Messages. Keep arguments in the protocol's
tool channel, retain terminal usage and native logprobs even when framing or UTF-8
buffering produces no visible text, and ignore aggregate terminal content as a
second delta. Cover partial JSON/length limits, empty completion, repeated terminal
records and errors after start. Test actual model-backed streams through the
authenticated gateway. Named forced calls do not prove automatic choice, parallel
tool parsing or complete tool-history support; keep those features gated separately.


### Automatic-tool development

For automatic-tool development, use the pinned baseline chat-runtime bridge rather
than a new format heuristic. Keep its license/provenance and API adaptations
visible. Wire serialized preparation, lazy triggers and the baseline parser through
actual generation before advertising an HTTP capability. Repeated parser elements
must consume input: optional calls plus nullable whitespace can create a grammar
cycle. Compute transitive nullability before recursion checks and reject such
cycles safely. Test generated grammars, split/mid-token triggers, Unicode, clone,
reset and vocabulary-backed sampler acceptance. Prima's initial model allocation
does not load its vocabulary; call its checked metadata loader and free that
loader when testing vocabulary-only assets. A parser/sampler contract does not
prove automatic/required HTTP calls or parallel streaming. Do not change the
recipe while a model lifecycle assessment is using the previous pin: restart
admission rechecks that immutable identity.


### Automatic HTTP tools

Automatic HTTP tools use the same baseline serialized preparation for generation,
apply-template and incremental parsing. Preserve plain/unmarked JSON as text;
compare an unexpected model choice with the pinned baseline before changing parser
semantics. Check explicit tagged activation and ordinary tool decline separately.
Required and parallel calls need actual schema-bound model output and continuations
from their emitted call IDs. Streams must keep text/reasoning/arguments separate,
retain stable per-call/item identities and once-only terminal/error records, with
length termination and owned disconnect settlement. Reserve internal preparation
parameters on raw completion/infill as well as chat routes. Invalid generated
GBNF must refuse instead of silently disabling grammar. Keep tools:auto,
parallel_tools and streaming_tools qualification distinct, and require the actual
native automatic/streaming capability markers. Library capabilities never replace
model/template/placement-specific evidence or the deferred performance gates.


### Regular tool grammar prefill

Generated regular tool grammars must accept the template's existing generation
prefix before sampling, matching the pinned baseline. Tokenize it with special
tokens, skip only an inserted leading-space token, and reapply it after sampler
reset. Never prefill a user-provided grammar. Missing prefill forced a duplicate
assistant header and diverted reasoning into text. Check real-vocabulary repeated
reset plus thinking-on calls/streams across all three APIs, not only thinking-off
calls. Preserve failing published receipts and advance the immutable recipe when
this sampler behavior changes.


### Reasoning controls

For reasoning controls, share the pinned baseline chat preparation/parser for plain,
named and automatic tool responses and structured schemas. Preserve effort/none,
thinking toggle, output format and protocol-specific history/delta semantics;
plaintext reasoning history is not vision or opaque stored state. Use the baseline
reasoning budget state machine with explicit model/token/sampler API adaptations and
license provenance. Enforce budgets in actual token selection, initialize from the
generation prefix, preserve UTF-8 completion/re-arming and suppress lazy grammar
while thinking; replay completed end tags before grammar resumes. Own grammar/chain/
budget allocation through failure and preserve reset/clone state. Bind deployment
controls to the manifest, honor request aliases/overrides, and require native
reasoning_controls_v1 plus explicit request/history qualification. An implemented
Jinja path does not prove legacy/manual/opaque-state parity. Test all parser, budget,
stream/history, sampler/draft and admission behavior only after full implementation
completion; physical cluster/performance gates stay deferred and unpassed.


### Manual reasoning control

For manual reasoning control, bind the server-generated returned completion ID to
its task before queue publication and reject caller-supplied internal fields. Use
the serialized inference loop to act only on the matching live, opted-in sampler;
slot numbers and later slot reuse cannot authorize a transition. Preserve baseline
success/message semantics outside active counting, and retain a successful control
across the initial prompt sampler reset. Expose the same authenticated inference
proxy without generation defaults or stream flags; separately qualify control and
custom delimiter features and negotiate their native capabilities. Legacy rendering
has the pinned baseline's pure-content/no-inferred-delimiter semantics. Compose
Jinja schemas with reasoning/tool grammar once; do not install duplicate schema or
fence constraints. Source/compile evidence does not prove prompt races, concurrency,
draft, auth or API parity. Run those checks only after full implementation finishes;
physical cluster and performance acceptance stays deferred and unpassed.


### Reasoning wire protocol (Responses / Messages)

Derive baseline API requirements from the pinned implementation before adding a
new service. The current baseline Responses converter rejects nonempty previous
IDs and implements no persistence or opaque reasoning decryption; earlier notes
calling those missing integration features were inferences, now superseded. Match
its reasoning content array/type, empty summary/encrypted placeholders and actual
stream events. Replay from reasoning_text content and preserve assistant/tool
turn grouping. Messages thinking uses an empty signature and signature_delta before
block closure; keep all delta targets open until closure. Require reasoning_wire_v1
for the corrected protocol contract, separately from parser/control capabilities.
Source/compile evidence remains distinct from full API/model acceptance. Complete
all required implementation before tests or test fixes; physical cluster/performance
gates remain deferred to the user's live three-computer cluster and unpassed.


---

**Related:** template rendering rules (parser pinning, frozen bytes, execution contexts) live in 07-server-apis.md.
