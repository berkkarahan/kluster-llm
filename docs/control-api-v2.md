# Control API v2

The agent advertises its control protocol as `control_protocol: 2` from `GET /health`. A worker checks this value before registration; cluster nodes must be upgraded together. An incompatible master rejects registration with HTTP 426 and an actionable message.

Control requests use resource paths under `/v2`. Errors use `{ "error": { "code": "...", "message": "...", "details": ... } }`; `details` is omitted when there is no structured validation detail.

| Resource | Routes |
|---|---|
| Node | `GET /v2/node`, `/devices`, `/config`, `/home`; `PUT /v2/node/config`; `POST /v2/node/runtime` |
| Cluster | `GET`, `POST`, and `DELETE /v2/cluster`; `POST` and `DELETE /v2/cluster/membership` |
| Members | `GET /v2/cluster/nodes`; `POST /v2/cluster/nodes/register`, `/unregister`, and `/heartbeat`; `GET /v2/cluster/discovery` |
| Updates | `POST /v2/cluster/updates` and `/progress` |
| Models | `GET /v2/models`; `POST /v2/models/search`, `/downloads`, `/downloads/cancel`, `/deletions`, and `/chat-template/validation`; `GET /v2/models/downloads/current` |
| Inference | `GET /v2/inference/capabilities`, `/load`, and `/model-settings/{model}`; `PUT /v2/inference/model-settings/{model}`; `POST /v2/inference/plans`, `/runs`, and `/runs/stop` |
| Telemetry and logs | `GET /v2/telemetry`, `/lifetimes`, `/series`, and `/readings`; `POST /v2/telemetry/ingest`; `GET` and `POST /v2/logs` |
| Events and credentials | HTTP and WebSocket `GET /v2/events`; `GET /v2/credentials/api-token` and `/huggingface`; `PUT /v2/credentials/huggingface` |

Inference clients keep using the OpenAI-compatible `POST /v1/chat/completions` and Anthropic-compatible `POST /v1/messages` routes. The old control paths remain temporarily available while external callers move to v2.

On first startup with an older cluster-state file, the agent stops tracked model/runtime child processes, clears transient membership, join keys, active plans, and process state, then starts idle. It retains the node identity, run settings, models, credentials, and usage history. Rejoin each node after the coordinated upgrade. RPC port 50052 remains restricted to a private LAN or WireGuard overlay.
