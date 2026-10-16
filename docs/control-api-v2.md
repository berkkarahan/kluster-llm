# Control API v2

The agent advertises its control protocol as `control_protocol: 2` from `GET /health`. A worker checks this value before registration; cluster nodes must be upgraded together. An incompatible master rejects registration with HTTP 426 and an actionable message.

Control requests use resource paths under `/v2`. Errors use `{ "error": { "code": "...", "message": "...", "details": ... } }`; `details` is omitted when there is no structured validation detail.

| Resource | Routes |
|---|---|
| Node | `GET /v2/node`, `/devices`, `/config`, `/home`; `PUT /v2/node/config`; `POST /v2/node/runtime` |
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

Inference clients keep using the OpenAI-compatible `POST /v1/chat/completions` and Anthropic-compatible `POST /v1/messages` routes. The old control paths remain temporarily available while external callers move to v2.

On first startup with an older cluster-state file, the agent stops tracked model/runtime child processes, clears transient membership, join keys, active plans, and process state, then starts idle. It retains the node identity, run settings, models, credentials, and usage history. Rejoin each node after the coordinated upgrade. RPC port 50052 remains restricted to a trusted private LAN or dedicated fast Ethernet network. Start/join reject obsolete `overlay` input rather than ignoring it; stop/leave a saved legacy WireGuard cluster on each node before using LAN-only clustering.

SQLite-backed telemetry and managed-network operations return the existing sanitized `503` unavailable response when bounded admission or checkout expires, or when the owner is closing. A usage-ingest `503` may follow a confirmed reading commit if a later retention unit failed; retries use the existing reading identity and do not create duplicate observations. A storage commit with an unknown outcome is never retried automatically.

### Optional Prima preparation resources

Runtime source requests accept `engine: "primacpp"` for the supported pinned snapshot.
Prima versions carry a `recipe_digest` and an engine-specific identity. Existing llama.cpp
identity hashes and default wire records are unchanged. The library reports
`engine_sources`, `inference_supported`, and `qualification_errors` additively.

`POST /v2/node/runtimes/{runtime_id}/prima-plan` provides a master-only, read-only
PlanBody topology preview. `/v2/node/prima-profiles` provides local explicit profiling
jobs, status, logs and cancellation, with the existing local-mutation authorization.
See [Prima contracts and limitations](prima-integration.md). Unqualified Prima runs return
`409 ENGINE_NOT_QUALIFIED`; target/local selection is refused before changing state.

Prima cluster preparation additionally exposes `POST /v2/cluster/prima-preparations`,
`GET /{job_id}`, `GET /{job_id}/logs` and `POST /{job_id}/cancel`. Creation uses
`{runtime_id, settings, plan_digest, request_id, include_profile}` and retains local-token,
same-origin and master-authority checks. Previews return a plan_digest bound to runtime,
model observations, settings, inventory and placement. Worker membership advertises
`prima_control_version: 1`; paired signed commands authorize bounded asset pushes.
Normal inference remains gated. See [Prima preparation](prima-integration.md).
