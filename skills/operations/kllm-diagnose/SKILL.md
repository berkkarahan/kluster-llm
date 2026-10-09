---
name: kllm-diagnose
description: "Diagnose Kluster LLM startup, inference, membership, logging, runtime, or private-network failures. Use for evidence-led recovery without discarding state or claiming unmeasured hardware results."
---

# Diagnose a Kluster failure

Find the checkout via `KLLM_REPO_ROOT`, current checkout, or `kllm.state.repo_root()`. Start with `docs/troubleshooting.md`, current CLI help, and the failing operation's logs. Read `docs/agent-install.md` for installer/runtime recovery.

1. Record the exact trigger, error/status code, affected computers, OS/backends, endpoint, time, and whether inference or networking is still active. Keep tokens and join keys out of evidence.
2. Distinguish layers: local agent/control reachability, accepted membership heartbeat, dedicated network readiness, runtime source/backend readiness, RPC allocation, model load, and client protocol. A ping, cached node, or historical reading proves none of the later layers.
3. Inspect `uv run kllm status` when starting a local agent is acceptable. For passive checks use the existing endpoint/logs. Use current `kllm logs --help` for source/node filtering; there is no invented `--tag` option. Label worker-local or cached log history accurately.
4. Apply the relevant documented recovery. Do not remove `cluster.json`, credentials, all adapter addresses, or runtime stores as a generic fix. Do not downgrade source identity or switch a managed stream silently to LAN/WireGuard.
5. For 10GbE adapter, administrator helper, subnet, or restoration problems load `kllm-10gbe`. Unknown speed, ambiguous configuration, ownership conflicts, and cleanup pending need evidence and local review rather than forced network changes.
6. `RPC_BUSY` needs the owning run to drain, `STALE_REVISION` needs fresh assessment, and `RPC_PROTOCOL_UNSUPPORTED` needs verified compatible TCP-only runtime installation. Stop inference through its existing controls before rebuilding or changing its network.
7. Re-run the original failing operation after the targeted recovery. For a code defect use `kllm-develop` and a behavior-focused regression check.

Report the supported cause, changed action, original-trigger verification, and unresolved evidence. State when hardware, physical authorization, throughput, reboot, mixed OS, or real generation were not tested.
