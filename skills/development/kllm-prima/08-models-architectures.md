# 08 · Model architectures, quantization & costs

**Use when:** You add or fix a model architecture, connect a loader to segmented execution, add a quantized format, extend full-model costs, or need family-specific notes (GPT-OSS, Gemma2/3, Granite dense/MoE/Switch/SWA, OLMo/OLMoE/OLMo2, OpenELM, BERT-type embedders, classifiers, shared-expert MoE, fused SwiGLU, LongRoPE).

**Keywords:** architecture, tensors, graph, GGUF metadata, attention sinks, softmax, src[2], src[4], expert biases, top_k, fused activation, quantization, packed block, type traits, costs, roofline, Gemma, Gemma2, Gemma3, Granite, OLMo, OLMoE, OpenELM, BERT, bidirectional, classifier, labels, shared expert, SwiGLU, Phi-3, LongRoPE, sliding window, RoPE, K-shift, Harmony, EOG.

**Related:** 09-cache-replay.md (compact KV consumers), 10-profile-costs.md (profiler validation), 07-server-apis.md (embedding/classifier APIs).

## Quick rules

- Adding an architecture: follow the pinned baseline's metadata, loader and graph together — registry names alone do not establish support; keep absolute rank ownership, fused/separate QKV shapes, per-layer head counts, token versus raw embedding scaling, head/post norms, activation and logit transforms explicit; SWA pattern defaults and its RoPE frequency/scale are architecture-specific; extend compact masks, shifts, snapshots and managed/profiler byte estimates along with the graph and negotiate the general compact storage capability before launch; price optional resident projections even when generation does not use them; retain explicit unfinished embedding/vision API contracts in feature status.
- Model modernization: derive required tensors, graph operations and quantization from the pinned baseline model implementation before adding an architecture name; attention sinks contribute one unscaled logit per query head to the softmax normalizer without a value vector — regular softmax uses src[2], Flash Attention uses src[4]; preserve per-head indexing across outer batches, optional operands, masked/unmasked input, numerical stabilization and padded GPU dispatch bounds; advertise backend support only where the extra operand is actually computed — unsupported accelerators must not silently ignore it; optional graph-builder wiring and production compilation are groundwork, not complete GPT-OSS/model support.
- Expert biases: preserve the selected-expert/token ordering when gathering rows; compact padded/strided top_k ID views before flattening and use an integer GPU copy — never floating-point reinterpretation or an unreported CPU transfer; match whether the baseline normalizes all logits or only selected logits; a model-specific fused activation must register CPU work planning and all GPU preparation/dispatch/shader dependencies, not just a source function; keep optional new graph behavior absent from existing callers unless required.
- Loader ↔ segmented execution: preserve absolute layer metadata while storing only the rank's local tensors; scalar/per-layer expert widths and SWA patterns must match real GGUF metadata including array types; use the same per-layer rotary frequencies in generation and cached K-shift; KV defragmentation also maps owned absolute layers to rank-local tensors; a zero-transformer coordinator has sequence positions without K tensors; fused QKV weights can have a combined bias or separate Q/K/V biases — make sliced views contiguous before reshaping; port tokenizer splitting and actual EOG semantics with the graph (Harmony intermediate end markers must permit analysis to continue into final text); keep cached sequence isolation in noncausal masks; record compact SWA cache storage, quantization, profiler and accelerator gaps explicitly.
- Adding a quantized format: preserve its GGUF numeric type, packed block size, exponent/nibble layout and actual stored bytes; register type traits, reference encode/decode, row validation, CPU dot-input type and every applicable generic CPU dispatch — do not alias a neighboring quantization; GPU support must include matrix/vector/indexed-expert/gather/decode shaders, F32 selection where required, pipeline slots, generation dependencies and honest backend admission; copy reference behavior from the pinned primary source with license/provenance; check file-type policy and explicit overrides through their actual caller flow; inspect format-neutral file-offset tensor-span accounting before adding fixed-byte estimates.
- Extending full-model costs: derive roles and dimensions from the actual loader/graph; fused QKV accounts for attention and rotary/KV work once; expert bias arithmetic and active bytes use selected experts while resident storage includes all experts; refuse missing/unknown roles and invalid/ambiguous shapes; keep non-matrix logical-work proxies labelled; match KV costs to the actual views/computation rather than an unimplemented compact cache; type-based matrix rates alone do not prove a model's GPU path — check required sink/activation/other operations before publishing a requested-backend profile; inspect existing typed decoders' rate/byte recomputation before changing schema; bind all new costs to a new immutable source/recipe.
- Shared-expert MoE: load only absolute manifest-owned layers with their local buffer/context indices; keep routed and always-active shared FFN branches separate until addition; preserve router normalization and per-layer expert widths; use direct sigmoid rather than silu(x)/x for scalar gates; profiler and planner costs must include full shared storage, selected routed arithmetic and simultaneous branch scratch.
- Fused SwiGLU: price the combined up projection and its actual halves, not an absent separate gate; keep long/short RoPE factors on each owning rank's layer buffers; logical context slots must exclude head-only reserved sequence IDs while KV/pooling metadata still reserves those IDs (negotiate `logical_context_sequences_v1`); follow the pinned baseline for attention semantics including disabled Phi-3 sliding attention — never infer attention behavior solely from historical GGUF window metadata.
- Sliding-attention enablement: update loading, graph admission, owned dense/sliding masks, KV allocation, shifts, snapshots/replay and storage estimates together; default/pattern metadata and independent sliding RoPE must match the pinned baseline; Gemma2 uses tied output weights, post-attention/FFN norms, GELU, query scaling and attention/output soft-capping without Q/K head norms, and its baseline scales supplied input vectors as well as token lookups while Gemma3's token-only scaling keeps its own semantics; price simultaneous mask pools and soft-capping logical work without claiming measured rates.
- Scaffolds & Granite dense: distinguish Gemma's tied-output/GELU path from Gemma2/3 post norms and Granite's optional biases and scale controls; preserve Granite's rotary-disable switch in both attention and KV shifts; do not label dense text groundwork as shared-expert or vision/deepstack support; LongRoPE requires source enum/metadata parsing and CLI selection as well as factor loading — price factor storage on owning ranks and omit disabled rotary arithmetic from logical costs.
- Granite experts: retain optional gate tensors — without a gate, apply SILU to up and pass that result to down without multiplying by up again; keep selected full-router probabilities normalized and add the always-active shared gated FFN without introducing Qwen2's sigmoid scalar gate; preserve dense Granite's rotary switch separately from Granite-MoE's rotary semantics; load expert/shared tensors only for manifest-owned layers; optional gate callbacks must not report an absent gate matrix as executed work.
- Granite Switch / SWA: check the exact architecture before assigning tensor semantics — Switch uses stacked token-selected LoRA and a persistent attention router; SWA uses fused routed gate/up and fused shared FFNs with explicit bounded per-layer sliding/RoPE flags, actual selected-expert counts, sinks and all compact KV consumers; slice fused routed outputs with their token/expert strides before activation; treat gate_up_exps weights as expert storage in GGUF planning and reserve combined/contiguous halves plus simultaneous masks; ambiguous fused/separate role sets must refuse; keep Switch, Hybrid and vision/deepstack work open until their own paths exist.
- OLMo family: do not infer normalization from the model name — OLMo uses unweighted LayerNorm with optional Q/K/V clamping and optional/tied output; OLMoE uses learned pre-norms, projected-column Q/K RMS norms and unnormalized selected full-router probabilities with required gated experts/output weights; OLMo2 has no pre-norms, uses post norms with optional sliding attention (plain rotary settings in both attention and KV shifts, four-layer sliding pattern carried through compact allocation/masks/snapshots/estimates/budgets); price unweighted norms, clamping and selected routed work explicitly and reserve OLMoE live scratch.
- OpenELM: head counts and FFN widths vary by absolute layer — use each layer's actual fused-QKV/output dimensions with head-width RMS norms and tied output on the assigned output buffer; preserve those dimensions in typed costs and KV estimates; reserve wider projection/slice/norm scratch separately from the embedding-width proxy; missing key/value width metadata uses the native first-layer head-width default, not a changing per-layer ratio.
- Bidirectional embedding models: load input embeddings/norms and classifier weights only on the head and transformer weights only on their absolute owners; preserve full-token attention within each batch and select raw output rows only after the final attention layer and before its final FFN handoff; optional projection and classifier biases stay optional; head-only coordinators still initialize position and pooling inputs; keep legacy input graph names compatible with the ring executor; price materialized F32 attention scores even when the requested FA flag does not change that graph and retain allocated KV storage in budgets until allocation changes; typed costs omit rotary work and a generative vocabulary head for BERT.
- Classifiers: derive the class count from GGUF string output-label arrays (scalar default for empty/absent); size the classifier output tensor and bias to the count while keeping classifier projection and bias optional independently; expose count and labels through the C API and carry the actual class-vector width through pooled rank extraction rather than truncating to one float; the rerank HTTP score uses the first element while the native embedding API retains all class values; reserve the full pooled class vector in Kluster's managed memory budget.

## Full text (normative)

### Model modernization & attention sinks

For model modernization, derive required tensors, graph operations and quantization
from the pinned baseline model implementation before adding an architecture name.
Attention sinks contribute one unscaled logit per query head to the softmax
normalizer without a value vector; regular softmax uses src[2] and Flash Attention
uses src[4]. Preserve per-head indexing across outer batches, optional operands,
masked/unmasked input, numerical stabilization and padded GPU dispatch bounds.
Advertise backend support only where the extra operand is actually computed;
unsupported accelerators must not silently ignore it. Optional graph-builder
wiring and production compilation are groundwork, not complete GPT-OSS/model
support. Keep loader, expert routing/biases/activation, sliding-window/RoPE,
quantization, exact placement and profiling requirements open until implemented.
Run numerical/native/model checks and fix tests only after the full required
implementation is complete; live cluster/performance gates remain deferred.


### Expert biases & fused activations

For expert biases, preserve the selected-expert/token ordering when gathering
rows. top_k IDs may be a padded/strided view: compact before flattening and use
an integer GPU copy, not floating-point reinterpretation or an unreported CPU
transfer. Match whether the baseline normalizes all logits or only selected
logits. A model-specific fused activation must register CPU work planning and
all GPU preparation/dispatch/shader dependencies, not just a source function.
Keep optional new graph behavior absent from existing callers unless required.
Compile evidence is not numerical/model acceptance; defer those checks until
full required implementation completion under the current user schedule.


### Loader ↔ segmented execution

When connecting a model loader to segmented execution, preserve absolute layer
metadata while storing only the rank's local tensors. Scalar/per-layer expert
widths and SWA patterns must match real GGUF metadata, including array types.
Use the same per-layer rotary frequencies in generation and cached K-shift;
KV defragmentation also maps owned absolute layers to rank-local tensors.
A zero-transformer coordinator has sequence positions without K tensors.
Fused QKV weights can have a combined bias or separate Q/K/V biases; make sliced
views contiguous before reshaping. Port tokenizer splitting and actual EOG
semantics with the graph: Harmony intermediate end markers must permit analysis
to continue into final text. Keep cached sequence isolation in noncausal masks.
Record compact SWA cache storage, quantization, profiler and accelerator gaps
explicitly; full-context KV plus masking does not prove the compact cache
contract. Registry/loader/graph compilation never establishes model acceptance.


### Adding a quantized format

When adding a quantized format, preserve its GGUF numeric type, packed block
size, exponent/nibble layout and actual stored bytes. Register type traits,
reference encode/decode, row validation, CPU dot-input type and every applicable
generic CPU dispatch; do not alias a neighboring quantization. GPU support must
include matrix/vector/indexed-expert/gather/decode shaders, F32 selection where
required, pipeline slots, generation dependencies and honest backend admission.
Copy reference behavior from the pinned primary source with license/provenance.
Check file-type policy and explicit overrides through their actual caller flow.
File-offset tensor-span accounting can already be format-neutral: inspect it
before adding fixed-byte estimates. Source/build evidence never replaces checks
against decoded original operands or full original model assets. Run those only
after full required implementation completion under the current schedule; physical
cluster/performance acceptance remains deferred and unpassed.


### Extending full-model costs

When extending full-model costs, derive roles and dimensions from the actual
loader/graph. Fused QKV must account for attention and rotary/KV work once; expert
bias arithmetic and active bytes use selected experts, while resident storage
includes all experts. Refuse missing/unknown roles and invalid/ambiguous shapes.
Keep non-matrix logical-work proxies labelled, and match KV costs to the actual
views/computation rather than a compact cache that has not been implemented.
Type-based matrix rates alone do not prove a model's GPU path: check required
sink/activation/other operations before publishing a requested-backend profile.
Existing typed decoders may already accept new format keys; inspect their rate/
byte recomputation before changing schema. Bind all new costs to a new immutable
source/recipe. Compilation is not measurement, generation quality or hardware
acceptance; run checks only after the full required implementation is finished
under the current schedule, with cluster/performance gates separately deferred.


### Adding an architecture

When adding an architecture, follow the pinned baseline's metadata, loader and
graph together. Registry names alone do not establish support. Keep absolute
rank ownership, fused/separate QKV shapes, per-layer head counts, token versus raw
embedding scaling, head/post norms, activation and logit transforms explicit.
SWA pattern defaults and its RoPE frequency/scale are architecture-specific.
Extend compact masks, shifts, snapshots and managed/profiler byte estimates along
with the graph; negotiate the general compact storage capability before launch.
Price optional resident projections even when generation does not use them, and
retain explicit unfinished embedding/vision API contracts in the feature status.
Gemma3 generation/compact/profile paths are implemented development work; its
retrieval embedding-output and vision paths remain open. Source inventories and
production builds are not model/backend acceptance. Keep all tests deferred
until full required implementation finishes, and physical distributed/performance
gates pending for the user's live three-computer cluster.


### Shared-expert MoE

For shared-expert MoE architectures, load only absolute manifest-owned layers and
use their local buffer/context indices. Keep routed and always-active shared FFN
branches separate until addition; preserve router normalization and per-layer
expert widths from the pinned baseline. Use direct sigmoid rather than silu(x)/x
for scalar gates. Profiler and planner costs must include full shared storage,
selected routed arithmetic and simultaneous branch scratch. Registration or
segmented capability metadata does not establish model or placement acceptance.


### Fused SwiGLU & logical context slots

For fused SwiGLU models, price the combined up projection and its actual halves,
not an absent separate gate. Keep long/short RoPE factors on each owning rank's
layer buffers. Logical context slots must exclude head-only reserved sequence IDs
while KV/pooling metadata still reserves those IDs. Negotiate
logical_context_sequences_v1 before managed execution. Follow the pinned baseline
for attention semantics, including disabled Phi-3 sliding attention; do not infer
attention behavior solely from historical GGUF window metadata.


### Sliding-attention architectures (Gemma2/3)

When enabling a sliding-attention architecture, update loading, graph admission,
owned dense/sliding masks, KV allocation, shifts, snapshots/replay and storage
estimates together. Default/pattern metadata and independent sliding RoPE must
match the pinned baseline. Gemma2 uses tied output weights, post-attention/FFN
norms, GELU, query scaling and attention/output soft-capping without Q/K head
norms. Its baseline scales supplied input vectors as well as token lookups;
Gemma3's token-only scaling must retain its own semantics. Price simultaneous
mask pools and soft-capping logical work without claiming measured rates.


### Decoder scaffolds & Granite dense / LongRoPE

When reusing decoder scaffolds, distinguish Gemma's tied-output/GELU path from
Gemma2/3 post norms and Granite's optional biases and scale controls. Preserve
Granite's rotary-disable switch in both attention and KV shifts; do not label
dense text groundwork as shared-expert or vision/deepstack support. LongRoPE
requires source enum/metadata parsing and CLI selection as well as factor loading.
Price factor storage on owning ranks and omit disabled rotary arithmetic from
logical costs. Follow the implementation-first test schedule and retain all
remaining architecture/backend/API/exact-placement requirements.


### Granite routed/shared experts

For Granite routed/shared experts, retain optional gate tensors: without a gate,
apply SILU to up and pass that result to down without multiplying by up again.
Keep selected full-router probabilities normalized and add the always-active
shared gated FFN without introducing Qwen2's sigmoid scalar gate. Preserve dense
Granite's rotary switch separately from Granite-MoE's rotary semantics. Load
expert/shared tensors only for manifest-owned layers; price all expert storage,
selected arithmetic, ungated activations, shared work and simultaneous scratch.
Optional gate callbacks must not report an absent gate matrix as executed work.
Source capability metadata does not qualify models, experts, hardware or timing.


### Granite Switch & Granite SWA

Check the exact architecture before assigning new tensor semantics: Granite Switch
uses stacked token-selected LoRA and a persistent attention router; Granite SWA
uses fused routed gate/up and fused shared FFNs. Granite SWA needs explicit bounded
per-layer sliding/RoPE flags, actual selected-expert counts, sinks, and all compact
KV consumers. Slice fused routed outputs with their token/expert strides before
activation; price full storage and selected arithmetic once. Treat gate_up_exps
weights as expert storage in GGUF planning and reserve combined/contiguous halves
as well as simultaneous masks. Ambiguous fused/separate role sets must refuse.
Keep Switch, Hybrid and vision/deepstack work open until their own paths exist.


### OLMo2 normalization

Preserve OLMo2's normalization axes and order: normalize projected Q/K columns
before head reshaping, omit attention/FFN pre-norms, and retain post-attention/FFN
norms. Sliding layers use plain rotary settings (frequency scale 1, extrapolation
0, attention factor 1) in both attention and KV shifts. Carry the optional
four-layer sliding pattern through compact allocation, masks, snapshots/replay,
native estimates and Kluster budgets. Typed norm costs must distinguish columns
from per-head normalization; source/build proof is not model or backend acceptance.


### OLMo family

For the OLMo family, do not infer normalization from the model name alone. OLMo
uses unweighted LayerNorm, optional Q/K/V clamping and optional/tied output.
OLMoE uses learned pre-norms, projected-column Q/K RMS norms and unnormalized
selected full-router probabilities with required gated experts/output weights.
OLMo2 has no pre-norms and uses post norms with optional sliding attention.
Preserve these orders in the rank-owned graph; price unweighted norms, clamping
and selected routed work explicitly, classify OLMoE expert storage and reserve
its live scratch. Build/source metadata remains distinct from acceptance.


### OpenELM

OpenELM varies head counts and FFN widths by absolute layer. Use each layer's
actual fused-QKV/output dimensions with head-width RMS norms, and keep tied
output on the assigned output buffer. Preserve those dimensions in typed costs
and KV estimates. Reserve wider projection/slice/norm scratch separately from
the embedding-width proxy. Missing key/value width metadata uses the native
first-layer head-width default, not a changing per-layer embedding/head ratio.
Treat registry/admission/build evidence separately from model/backend acceptance.


### Bidirectional embedding models

For bidirectional embedding models, load input embeddings/norms and classifier
weights only on the head, and transformer weights only on their absolute owners.
Preserve full-token attention within each batch; select raw output rows only after
the final attention layer and before its final FFN handoff. Optional projection
and classifier biases must stay optional. Head-only coordinators still initialize
position and pooling inputs; skip only unallocated transformer inputs. Keep
legacy input graph names compatible with the ring executor. Price materialized
F32 attention scores even when the requested FA flag does not change that graph;
retain allocated KV storage in budgets until allocation itself changes. Typed
costs must omit rotary work and a generative vocabulary head for BERT. Scalar
reranking requires the corresponding loaded classifier output; do not mistake
embedding paths for full multi-class classifier or embedding-architecture parity.
Build/source evidence remains separate from model/API/backend acceptance.


### Classifier models & labels

Classifier models may declare GGUF string output labels. Derive the class count
from that array, retaining the scalar default for an empty or absent array;
size the classifier output tensor and its bias to the count. Keep classifier
projection and bias optional independently, so a direct classifier projection
works without the intermediate tanh. Make the C API expose the count and labels
and carry the actual class-vector width through pooled rank extraction rather
than truncating it to one float. The rerank HTTP score uses its first element,
matching the pinned baseline, while the native embedding API retains all class
values. Reserve the full pooled class vector in Kluster's managed memory budget.
Do not mark classifier model/API acceptance from metadata, compilation or a
capability string alone.


---

**Related:** reranking's HTTP/qualification surface lives in 07-server-apis.md; compact sliding-window KV consumers live in 09-cache-replay.md; requested-backend profile publication gates live in 10-profile-costs.md.
