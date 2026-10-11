# Security

Kluster LLM is a trusted-LAN cluster tool. It is not a multi-tenant service, and it is not safe to expose to the internet.

## Network boundary

Run every node on a private network you control: a dedicated Ethernet or Thunderbolt link, a lab LAN, or the optional WireGuard overlay.

Do not publish these ports:

- **50052**, llama.cpp `rpc-server`. Upstream RPC has no authentication or TLS. Anyone who can reach it can take part in inference. The inference API token does not protect this port.
- **18765**, the control plane (`kllm-agent`). The agent listens on all interfaces. Cluster status returns the master join key. `GET /v2/credentials/api-token` and `GET /v1/api-token` return the local API token with no separate check. Start, stop, and join are unauthenticated as well.

The join key and the API token are secrets for that private network. Do not paste them into issues, logs, or shared chats.

Inference routes (`/v1/chat/completions`, `/v1/messages`, and the `/v2/inference` proxy) require the API token. That check stops at the HTTP gateway. It does not authenticate RPC on port 50052.

## Out of scope

- Exposing the control plane or RPC port to the public internet
- Sharing one cluster across untrusted users or tenants
- Adding authentication to upstream llama.cpp RPC, which has none

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/berkkarahan/kluster-llm/security/advisories/new) for issues that are dangerous to disclose in public. Include the version or commit, the port and route involved, and enough detail to reproduce the problem on a private network.

Do not open a public issue for an unpatched exposure of the join key, API token, or RPC port.
