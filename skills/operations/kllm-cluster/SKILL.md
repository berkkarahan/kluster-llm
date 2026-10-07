---
name: kllm-cluster
description: "Start, join, inspect, update, or stop a Kluster LLM master/worker cluster. Use for membership and runtime lifecycle tasks; route dedicated 10GbE creation to kllm-10gbe."
---

# Operate a Kluster cluster

Find the Kluster checkout using `KLLM_REPO_ROOT`, the current checkout, or `kllm.state.repo_root()`. Read `docs/cli.md` and `docs/agent-install.md` for current commands; inspect command help when options matter.

1. Identify the intended master, worker computers, control endpoints, existing inference, and management connectivity. Preserve the running cluster when the request is only inspection.
2. Use `uv run kllm status` to inspect local state. It can start a local agent; use existing status APIs for strictly passive inspection. Start the master with `uv run kllm start` and join each worker with `uv run kllm join --key '<join-key>'`; supply `--master` only when discovery needs an explicit LAN address.
3. Keep join keys out of shared logs and reports. For WireGuard, verify tools on every participant and use matching overlay options on master and workers. RPC TCP 50052 is unauthenticated upstream and must remain on a private LAN or WireGuard network.
4. For a dedicated 10GbE link or switched network, load `kllm-10gbe`. Prefer its master-coordinated setup after local administrator enablement; do not manually rewrite every worker's settings as a default.
5. Check accepted heartbeats and live status, not just cached membership. A joined worker is idle until the master allocates it to a model run. Joining, preparing runtimes, and agent restart do not independently start RPC.
6. Before inference, verify that selected participants have matching runtime source identities and compatible transport. Offline, unused nodes are not proof that a selected run must fail. Use `kllm-models` for capacity planning and launch.
7. For requested worker updates use the documented `uv run kllm cluster update` flow after inspecting clean worker checkout requirements. Do not discard uncommitted worker work to make an update succeed.
8. Distinguish stopping a model from leaving a cluster or stopping a private network. Stop inference before network reconfiguration. Retain uncertain ownership and cleanup-pending states; use `kllm-diagnose` for recovery.

Completion means the requested lifecycle action is observed on the named nodes. Report master/worker roles, live connectivity, runtime preparation, active inference, and any unresolved cleanup without exposing keys.
