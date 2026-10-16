# Control API v2

The agent advertises its control protocol as `control_protocol: 2` from `GET /health`. A worker checks this value before registration; cluster nodes must be upgraded together. An incompatible master rejects registration with HTTP 426 and an actionable message.

Control requests use resource paths under `/v2`. Errors use `{ "error": { "code": "...", "message": "...", "details": ... } }`; `details` is omitted when there is no structured validation detail.

| Resource | Routes |
|---|---|
| Node | `GET /v2/node`, `/devices`, `/prima-devices`, `/config`, `/home`; `PUT /v2/node/config` |
| llama.cpp library | `GET/POST /v2/node/runtimes`; `GET /sources`, `POST /resolve`; `DELETE /{runtime_id}` |
| Runtime builds | `POST /v2/node/runtime-builds`; `GET /{job_id}` and `/{job_id}/logs`; `POST /{job_id}/cancel` |
| Runtime selection | `PUT /v2/node/runtime-selection` (idle nodes); `GET/PUT /v2/cluster/runtime-target` |
| Cluster | `GET`, `POST`, and `DELETE /v2/cluster`; `POST` and `DELETE /v2/cluster/membership` |
| Members | `GET /v2/cluster/nodes`; `POST /v2/cluster/nodes/register`, `/unregister`, and `/heartbeat`; `GET /v2/cluster/discovery` |
| Updates | `POST /v2/cluster/updates` and `/progress` |
| Fast subnet | `GET /v2/cluster/fast-subnet/reachability`; `POST /v2/cluster/fast-subnet/reconnect` |
| Models | `GET /v2/models`; `POST /v2/models/search`, `/downloads`, `/downloads/cancel`, `/deletions`, and `/chat-template/validation`; `GET /v2/models/downloads/current` |
| Inference | `GET /v2/inference/capabilities`, `/load`, and `/model-settings/{model}`; `PUT /v2/inference/model-settings/{model}`; `POST /v2/inference/plans`, `/runs`, and `/runs/stop` |
| Telemetry and logs | `GET /v2/telemetry`, `/lifetimes`, `/series`, and `/readings`; `POST /v2/telemetry/ingest`; `GET` and `POST /v2/logs` |
| Events and credentials | HTTP and WebSocket `GET /v2/events`; `GET /v2/credentials/api-token` and `/huggingface`; `PUT /v2/credentials/huggingface` |

`GET /v2/node/runtimes` and `GET /v2/cluster/runtime-target` report a `control: {available, message}` marker. With `available: false` the local version library is still returned so saving, building and local selection keep working; `PUT /v2/cluster/runtime-target` returns `503` with `RUNTIME_CONTROL_UNAVAILABLE` until signed node control recovers.

Runtime status carries the shared node identity: each node row includes `name` (hostname) and `address` (dedicated private-network address, else RPC bind or LAN host), plus `channel: {connected, error}` on the master and a library-level `target_state` (`current`, `label_stale`, `binding_stale`, `absent`) with `target_error`. `GET /v2/status` and the live status feed include `network_directory`: the durable per-node identity from `managed-network.sqlite` (`node_id`, `hostname`, `role`, `lan_host`, `rpc_bind`, `private_address`, `assignment_state`, `link_speed_mbps`, `updated_at`). Workers mirror the master's directory through the register/heartbeat response (`node_directory`) and persist it locally, so cluster and Settings → llama.cpp render the same names and addresses.

`GET /v2/inference/connection` returns a no-store Connect snapshot (`role`, `node_id`, master `url`, exact served `model`, and `api_token`). The dashboard authenticates with the local node token. On a worker, the agent relays to its paired master with the cluster key; the master permits its API token or that paired cluster key. The route returns 503 if the master cannot serve a unique model.

`GET /v2/cluster/fast-subnet/reachability` is master-only. When a managed network is `ready`, the master opens one TCP connection from its own private address to each joined worker's private address on that worker's control port and returns `reachable` per worker. No ready network returns an empty worker list. `POST /v2/cluster/fast-subnet/reconnect` on the master names a joined worker that is still reaching the master outside that subnet; the master forwards the command to the worker's current management address with `X-Kllm-Key`. The worker checks the same key and re-runs join against the master's private address, which moves later heartbeats and RPC onto the fast subnet. A worker already inside the subnet, a network that is not ready, or a worker that does not answer returns an error and leaves the current join in place.

Inference clients use OpenAI-compatible `POST /v1/chat/completions`, `POST /v1/embeddings`, and Anthropic-compatible `POST /v1/messages`. Embeddings use the same API-token authentication, resolve the exact served model ID, and preserve native input/encoding fields without adding generation defaults. Prima additionally requires an embedding-qualified execution scope; advertised native capability alone does not enable it. The old control paths remain temporarily available while external callers move to v2.

On first startup with an older cluster-state file, the agent stops tracked model/runtime child processes, clears transient membership, join keys, active plans, and process state, then starts idle. It retains the node identity, run settings, models, credentials, and usage history. Rejoin each node after the coordinated upgrade. RPC port 50052 remains restricted to a private LAN or WireGuard overlay.

SQLite-backed telemetry and managed-network operations return the existing sanitized `503` unavailable response when bounded admission or checkout expires, or when the owner is closing. A usage-ingest `503` may follow a confirmed reading commit if a later retention unit failed; retries use the existing reading identity and do not create duplicate observations. A storage commit with an unknown outcome is never retried automatically.

### Optional Prima preparation resources

Runtime source requests accept `engine: "primacpp"` for the supported pinned snapshot.
Prima versions carry a `recipe_digest` and an engine-specific identity. Existing llama.cpp
identity hashes and default wire records are unchanged. The library reports
`engine_sources`, `inference_supported`, and `qualification_errors` additively.

`resolve` accepts a Prima source through two routes that converge on one immutable
identity: the pinned `source_commit`, or the pinned `source_release` tag when it
resolves to exactly `source_release_commit` (a moved or deleted release tag refuses
until the recipe is advanced). `engine_sources` publishes the release as
`release` / `release_commit` next to the commit ref, driving the add-dialog prefill
and the Prima section's install shortcut. Save/build/target contracts are unchanged:
the release carries no build assets, so preparation still compiles the pinned source.

The node status payload carries the configured engine mode (`engine`:
`llamacpp | primacpp`, written by `kllm start/join --engine`); the dashboard uses it to
swap the Settings runtime section to the Prima settings. `GET /v2/node/prima-devices`
returns the selected Prima build's own physical Vulkan device report
(`available`, and when prepared, `devices` with `name`/`description`/`device_identity`
(UUID)/`driver_identity`/`physical_index`, plus the build's probed `features` and
`backends`); `available: false` carries a `reason` when no Prima runtime is prepared or
its probe fails. The build manifest's probed `capabilities` also reach the library via
`versions[].build.capabilities`. Run-device assignment stays on Hardware's GPU
selection plus the Deploy plan; the planner assigns layers and derives the tensor split,
so this report is an identity probe, not a selection control.

`POST /v2/node/runtimes/{runtime_id}/prima-plan` provides a master-only, read-only
PlanBody topology preview. `/v2/node/prima-profiles` provides local explicit profiling
jobs, status, logs and cancellation, with the existing local-mutation authorization.
Generation timings and typed kernel roofline estimates remain separate. Native kernel
schema2 binds exact context/threads/backend and complete per-type/layer/input/output
costs, uses checked GPU/I/O measurements and advertises typed_kernel_profile_v2.
Kluster validates rate/estimate consistency, ownership and observed host/device
identity before caching. Missing, malformed, downgraded or changed-hardware results
fail without publishing a profile. Cache vectors remain estimates; they do not grant
qualification or extrapolate full-model generation timings to partial ranks.

Workers advertise prima_profile_version2. Signed prima_profile uses
`{preparation_job_id, manifest_digest, profile: ProfileRequest}` and requires the
operation's complete, unexpired approved assets plus exact effective model/runtime/
rank/backend/settings. Status/cancel use `{profile_job_id}` and the same operation/
sender/authority; unrelated operations cannot read or cancel another profile.
Long jobs recheck authority and the600s progress-renewed preparation approval.
Durable owners prevent request-ID adoption, including local-profile IDs. Backend
selection is explicit so CPU ranks on a Vulkan build can produce CPU costs. Master
imports compare complete typed results to current signed participant evidence;
missing/foreign/stale/incomplete results never become planning inputs.
See [Prima contracts and limitations](prima-integration.md). Unqualified Prima runs return
`409 ENGINE_NOT_QUALIFIED`; target/local selection is refused before changing state.

Prima cluster preparation additionally exposes `POST /v2/cluster/prima-preparations`,
`GET /{job_id}`, `GET /{job_id}/logs` and `POST /{job_id}/cancel`. Creation uses
`{runtime_id, settings, plan_digest, request_id, include_profile}` and retains local-token,
same-origin and master-authority checks. Previews return a plan_digest bound to runtime,
model observations, settings, inventory and placement. Worker membership advertises
`prima_control_version: 1`, `prima_inventory_version: 1` and `prima_ports_version: 2`; paired signed commands
authorize bounded asset pushes. Distributed preparation requires the latter
capability and uses read-only `prima_inventory` commands with `{runtime_id, rank,
backend}` to observe each node's platform/host/build and selected physical Vulkan
identities. Reports bind the exact rank allocation and are rechecked before startup
and during owned renewal. Inventory is not inference qualification. Workers missing
the capability receive an upgrade-required preparation error before new commands.
Normal inference remains gated. See [Prima preparation](prima-integration.md).

Workers also advertise strict `prima_asset_lease_version:1`. Signed
`prima_prepare_renew` uses the original operation run_id and `{job_id, progress}`;
progress must be a new nonnegative safe integer and the same sender/authority must
own an unexpired preparing/prepared job. A successful chunk or authorized progress
renewal extends its600s approval. Status/duplicate progress cannot renew it, and
cancelled/expired approvals require fresh preparation. The master validates exact
chunk-range acknowledgements before extending source grants and renewing other
enrolled participants. These changes add no arbitrary source-path or public
unauthenticated transfer endpoint.

The internal signed runtime protocol also accepts `prima_admit_ports` with
`{contract: {network_id, manifest_digest, address, assignment_revision, rules:
[{port, peers}]}}` and `prima_release_ports` with `{network_id}`; both use the
signed command's run_id. The worker validates the immutable preparation and
manifest-derived rules before forwarding the original signed envelope to the local
privileged helper. Cleanup refuses a still-owned rank for that run. Read-only
`prima_port_capabilities` with `{}` reports `{version, available, cleanup_pending}`;
availability requires the updated helper's empty owned-rule ledger. Prepared Run
negotiates it before mutation, journals each attempted node and admits every rank
before any spawn. Node-local pre-spawn/running leases and reconstructed cleanup
retain port ownership until matching rank stop plus native-rule release. Other
OS/admin paths, helper absence/review/IPC recovery and physical acceptance remain required. This
adds no public arbitrary firewall or subprocess endpoint and enables no qualification.

Port capability2 adds signed `prima_renew_ports` with the same exact `{contract}`
shape. Rank startup and ongoing renewal forward fresh master signatures to the
helper; live rank ownership, private assignments, manifest and authority must match.
The independent helper watchdog uses600s admission and90s running leases. Agent
rank leases stay60s. Missing watchdog support, old wire capability, replayed or
expired/changed renewal is refused. Status does not extend a lease. The helper only
drains its verified rules; process stop and cluster-release proofs remain distinct.

Token-authenticated loopback-only `GET/POST
/v2/node/network-setup/recovery/{network_id}/prima/{run_id}` provides local listener
recovery. GET returns per-rule original/current snapshots and exact/changed/unknown/
absent ownership plus `snapshot_digest`; POST accepts only `{decision,
snapshot_digest}`. `restore_owned` rechecks/removes only exact created rules.
`keep_current` explicitly preserves current OS rules and relinquishes this node's
listener ledger; preserved rules require manual management. It does not adopt
changed rules, resume inference or release a cluster reservation. A stopped owned
rank and pending/expired admission are required. Fresh snapshot binding, local
token/origin/loopback checks and durable retry fences reject stale, foreign or live
recovery. Setup status exposes `prima_listener_recovery` resources. Helpers advertise
optional `prima_ports_recovery_version:1`; older helpers require upgrading for this
API without changing admission capability2. Physical OS/service acceptance remains
required, and physical distributed validation will be tested later.

The Private 10GbE network dashboard exposes these local resources in master and
worker views. Rule review must match the selected run/manifest and contain complete
ownership/digest fields before decisions appear. Changed/unknown ownership disables
restoration. Keeping current rules requires explicit acknowledgement of manual
management; each decision attempt, refresh and close consumes the displayed review.
Failed or invalid acknowledgements require a new review. Local resolution does not
optimistically release cluster ownership; the user retries Stop on the master.

Helper IPC uses one queue/connection/I/O deadline (query8s, mutation/recovery45s).
TIMEOUT remains distinct from ADMIN_REQUIRED in local setup status; an unresponsive
helper does not authorize another grant or discard pending ownership. Invalid or
oversized helper envelopes return HELPER_REFUSED. On Windows pending synchronous
I/O is cancelled and settled before the client lock opens, while server replies
are flushed under their deadline before disconnect. A timeout or closed pipe is
not a rank-stop, mutation-rollback or listener-release acknowledgement. The wire
schema and signed-envelope bytes are unchanged. Isolated pipe tests do not qualify
the protected service, its ACL/deployment/restart or physical firewall behavior;
physical distributed validation will be tested later.

Already elevated Windows manual adapters can negotiate Prima listener capability2
through an owned native `--stdio-prima` child (direct capability1). This is an
internal private-pipe backend; it adds no remote arbitrary process/firewall endpoint.
Direct scope initialization reuses signed prepare/apply, native address observation
and signed runtime listener commands. UUID-correlated framed replies distinguish
late requests, including partial responses after timeout. EOF or broken framing
retains native cleanup ownership; a timeout/child exit cannot acknowledge rank stop.
Remembered direct and protected-service ledgers are not interchangeable. Local
grant retirement requires matching direct status with explicit typed empty owned-run
and active-scope lists; the replacement owner is negotiated before signed commands.
Signed network reconciliation now updates native scope peers for an unchanged assignment
only after its listeners drain. Direct address/DHCP operations remain on the existing
manual adapter. Elevated physical acceptance remains required and distributed cluster
validation will be tested later.


Signed Prima private-link profiling advertises prima_link_profile_version1.
prima_link_profile takes {preparation_job_id, scope, mode}, where mode is receive
or send. StreamScope binds a fresh probe UUID, the preparation operation UUID,
contract_digest (execution manifest), network UUID, master authority, exact source/
target NodeIdentity values and their assignment revisions. The approved master first
requests receive on the target, then send on the source. Replies are respectively
{phase: ready, grant_id} and {phase: measured, grant_id, measurement}; grant_id is
the canonical scope digest. Both commands require their owner's current complete
preparation. Master-local collection uses its current preparation in progress.

The version2 stream protocol reuses the selected private diagnostic port; existing
version1 connectivity echo remains compatible. It fixes payload_bytes1048576,
samples3 and ttl_ms10000, authenticates before bulk reads and signs each complete
sample acknowledgement. A temporary12s grant delegates worker peer keys without
changing pinned pairing. Revoked/expired preparations, stale assignments, absent
addresses, changed authority/keys, replay and foreign operations are refused.
Collected receipts record directed addresses/revisions, application handshake latency,
three elapsed samples and median acknowledged payload throughput. They do not alter
ordinary previews or qualify inference. Physical distributed validation will be
tested later.


Workers advertise prima_profile_context_version1 for read-only, preparation-owned
fresh context. Signed prima_profile_context takes {profile_job_id} under its original
operation/sender/authority. The complete owned profile and current unexpired asset
preparation are required. Replies contain manifest_digest, participant_evidence and
storage_identity {filesystem_device, directory}; the latter is the current OS file
system device identifier and owned model directory, not a physical media guarantee.
Changed hardware, filesystem location or missing model layouts cannot supply matching
planner evidence. The master performs its equivalent local read without restaging.
Explicit include_profile on distributed preparations requires this capability before
asset transfer, alongside profile2 and private-link1. Ordinary previews may obtain
fresh signed context but start no measurements or cache publication.

host_transfer_profile_v1 extends native kernel schema2 with gpu_host_transfer, using
method completed_f32_embedding_v1, exact bytes and five positive upload_ms/download_ms
samples after warmup. Every transfer compares complete returned bytes on the requested
backend. Kluster retains median gpu_upload_ms/gpu_download_ms and gpu_transfer_bytes,
refuses malformed/missing advertised GPU evidence and binds model filesystem identity
before/after profiling. CPU profiles cannot claim GPU transfers. These measurements
and the new typed planner estimates do not grant qualification. Physical distributed
validation will be tested later.


prima_resources_version1 advertises atomic node work reservations. Selected workers
must provide it for distributed Prima preparation and Run. Resource ownership is
internal and bound to existing signed operations/manifests; it adds no remote arbitrary
reservation endpoint. Conflicts refuse preparation/profile/rank/listener startup.
Normal llama.cpp master Run returns409 NODE_RESOURCE_RESERVED for a reserved node;
RPC startup returnsRPC_BUSY before firewall or spawn. Matching native listener claims
remain until durable cleanup acknowledgement. Profiling and preparation may briefly
report settling after result publication while final owned work drains. cleanup_pending
retains owned profile PID/start identity after failed cleanup; Cancel retries cleanup
and does not free live work merely by changing phase. Preparation Cancel revokes its
approval; running ranks retain their distinct Run/Stop ownership.

Private stream scopes now own bounded node claims and a12s expiry timer. Close/expiry
settles the owned reader/source transfer before releasing its token. Resource guards
serialize work within the running agent; durable process/listener/preparation journals
and existing leases still govern restart/reconciliation. Physical distributed validation
will be tested later; no qualification policy is enabled by these controlled checks.


Recipe25's logits_transfer_profile_v1 adds gpu_logits_transfer to schema2: method
completed_f32_logits_v1, exact bytes and five positive completed download_ms samples
after warmup. The probe initializes/checks the requested backend's F32 buffer at the
actual model vocabulary width. CPU reports null; absent advertised GPU evidence is
refused before cache publication. Median gpu_logits_download_ms and gpu_logits_bytes
remain distinct from embedding uploads/downloads and GPU-to-GPU KV copies.

Candidate scoring verifies logits bytes against the complete GGUF token embedding
tensor vocabulary dimension. Missing/wrong widths retain capacity-first planning.
GPU output uses its own projection estimate and completed logits download. Connected
local graphs avoid an intermediate embedding download for GPU output; distributed
rank zero's separate output graph includes its additional host embedding upload.
These are typed one-token/slot f16/off resident estimates, not generation timing or
qualification. Additional feasible candidate profiling/contracts/edges and remaining
settings/layouts stay open. Physical distributed cluster gates and validations will
be tested later; all qualification remains disabled.


Candidate-edge profiling now advertises prima_link_profile_version2. Explicit
include_profile derives additional directed profile_edges from representable feasible
candidates among the selected preparation's existing participants and private addresses.
These source/target pairs are immutable manifest content; empty profile_edges is omitted
to preserve existing canonical digests. Version2 is required before asset transfer when
extra pairs exist; version1 remains sufficient for execution-edge-only profiling.
The collector measures the union of execution/profile pairs once per directed pair
on the existing diagnostic listener. Profile approval creates no native listener rule
and changes no execution rank, layer, port or transfer edge. User node-priority and
required-node constraints still govern candidate generation. Missing candidate participants,
unsupported layouts/settings, stale evidence or missing links preserve capacity-first
planning. Preview rejects unapproved/duplicate directed receipts and performs no
measurements or cache writes. Physical distributed gates/validations will be tested
later; controlled four-agent transport coverage does not qualify physical inference.


### Performance validation deferred by user - 2026-10-12

The user explicitly requests no further performance-gate validation here and will
check performance on a live Kluster cluster of three computers. Local paired
acceptance and further performance-gate diagnostic experiments are stopped.
Implementation and correctness checks, including functional profiler contracts,
continue. Existing benchmark failures remain historical evidence, not passes.
Preserve the no more than 5% regression and 10% representative distributed
improvement criteria until the user's live measurements can evaluate them. Do not
relax thresholds, retokenize usage, infer a pass or enable an unverified release scope.

Physical distributed cluster gates and validations will be tested later on the
user's three-computer setup. Performance and distributed qualification remain
unpassed. Their deferral is explicit and is not an implementation blocker for
finishing remaining API/model/assets/settings/placement/planner work.
The bounded timing diagnostic already performed is not a performance gate result:
its summary is corrected to insufficient_decode_samples because mixed token-count
prefill graphs include a final single-token projection. No new timing experiment
will extend that sample collection here. The optional diagnostic-mode correctness
repair and its benchmark-rejection checks can be finished independently.

### Prima template continuation - 2026-10-12

Published feature commit cfca7ee (recipe33) supports full Jinja embedded/inline/file templates and
advertises jinja_template_v1. Kluster resolves custom/preset choices before
preparation and freezes exact source bytes as prima_chat_template_source in the
signed manifest and qualification execution settings. Rank zero renders;
workers receive its tokens. Kluster now pins and builds this immutable source. Large templates use owned
content-named files at launch; changed files refuse without overwrite. This does
not enable qualification. Broader input-origin token marking
and automatic native tool parsing remain open. Performance and physical cluster
acceptance will be tested later on the user's live three-computer cluster.

### Named tool streams - 2026-10-12

Published Prima3e41d47 (recipe34) adds streaming named forced calls to Chat,
Responses and Messages, with stable call/item IDs, correct argument delta channels,
usage, native token logprobs and explicit partial termination. The native server
advertises streaming_named_tools_v1. This requires a new immutable managed build
and separate qualification feature evidence. Automatic tool choice/parallel parsing
and complete tool-history support remain open. Performance and physical cluster
acceptance will be tested later on the user's live three-computer cluster.

### Text tool history - 2026-10-12

Published Prima3707d0a (recipe35) adds complete supplied text tool history for
Chat assistant/tool messages, Responses function_call/function_call_output items
and Messages tool_use/tool_result blocks. Call IDs and historical call groups are
preserved. Jinja template capabilities govern argument representation; unsupported
history/content refuses instead of being silently discarded. The server advertises
tool_history_text_v1. Kluster requires tool_turns evidence for each protocol and
retains separate nested vision requirements and native invalid-input boundaries.
Automatic tool choice, multimodal history and stored Responses state remain open.
Performance and physical cluster acceptance will be tested later on the user's
live three-computer cluster; all release qualification remains unpassed.

### Automatic function tools - recipe38
Prima37828feb serves automatic/required and parallel text function calls through
Chat/Responses/Messages, including supplied histories and streams. Capability
markers are automatic_tools_v1 and streaming_automatic_tools_v1; Kluster also
requires tools:auto, parallel_tools and streaming_tools evidence in the exact
active execution scope. Model output must follow its template's declared format;
unmarked JSON remains text. Reasoning, text and function arguments use separate
channels with stable call/item IDs and terminal usage. Generated regular grammars
consume the existing assistant prefix before sampling and after reset. Internal
parameters are reserved on completion/infill and all three protocol routes.
Multimodal, stored Responses and broader API/launch parity remain open.
Performance and physical distributed gates/validations will be tested later on
the user's live cluster of three computers; source qualification stays unpassed.


Prima rank allocations keep `ngl` as a transformer-only offload count. A
rank-zero master with `output_gpu=true` can have `ngl=0`, one selected GPU and
one positive tensor proportion, including when all transformers are on workers.
Native support requires `output_only_gpu_v1`; older runtimes refuse this layout.
Preparation and rank startup retain selected-device inventory and backend checks.
Qualification remains disabled until its exact execution scope is validated.


### Manual reasoning control (implementation, acceptance deferred)

POST /v1/chat/completions/control uses the same API authentication as Chat.
Send {"id":"<returned completion id>","action":"reasoning_end"} for a live
completion launched with reasoning_control=true. The native inference loop owns
the sampler transition. Unknown/finished IDs and unarmed requests return success
false with a message; an armed live request follows the baseline success contract.
Kluster does not inject generation defaults into controls. Prima additionally
requires explicit feature qualification and reasoning_control_v1. All tests remain
deferred until full implementation completion; physical cluster/performance gates
will be tested later on the user's live three-computer cluster and are unpassed.

Recipe 93 adds authenticated `/completion` and `/completions` aliases for the
native completion format. These remain distinct from OpenAI
`/v1/completions`; the gateway checks the one served model and a separate
raw-completion request feature and native capability. API behavior and model
acceptance still require the deferred test phase. Physical distributed and
performance gates remain for the user's live three-computer cluster.

Kluster also exposes authenticated native-format `/embedding` and `/embeddings` separately from OpenAI `/v1/embeddings`. The aliases retain native input/output semantics and require embedding_raw qualification; source/build metadata is not API acceptance.
