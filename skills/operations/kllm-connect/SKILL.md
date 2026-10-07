---
name: kllm-connect
description: "Connect Claude Code, Codex, OpenCode, Hermes, OpenClaw, or Pi to Kluster LLM inference. Use for endpoint authentication, served model IDs, isolated profiles, and connection readiness."
---

# Connect an agent to inference

Locate the checkout through `KLLM_REPO_ROOT`, current checkout, or `kllm.state.repo_root()`. Read the Connect section in `docs/cli.md`; check `uv run kllm connect --help` and whether the requested client is installed.

1. Identify the endpoint, master, requested model, agent, and whether a model is already ready. Connecting can start a model when serving is enabled. Use `--no-serve` when the task must not start inference.
2. Use `uv run kllm connect <agent> --model '<model>'` or specify the control endpoint with `--url`. A worker endpoint resolves to its master. Explicit remote endpoints require the master's token through `--api-key` or `KLLM_API_KEY`; a worker's local token cannot authenticate master inference.
3. Loopback endpoints can reuse the local token. Never paste tokens into reports. An explicit key overrides the environment key and local key. An already running different model is rejected; an idle master needs an explicit model.
4. Readiness uses the authenticated served-model catalog and preserves the exact upstream ID. Increase `--timeout` only after examining an actual readiness delay; do not mask authentication or old-protocol failures with retries.
5. Use `--kllm-skills operations`, `development`, `all` (default), or `none` to select the provisioned Kluster pack. Other native agent skills remain independently discoverable. `--persist` retains isolated configuration; ordinary profiles are not rewritten by CONNECT.
6. Extra harness arguments are passed through. Use `--` when disambiguating a harness option. OpenCode V2 requires the private standalone service to inherit session configuration; do not connect through another background server.
7. `--no-launch` still performs readiness checks and can prepare inference, then prints a redacted plan and removes temporary configuration. It is not an offline dry run. Avoid `--yolo` unless the user requests its permission behavior.

Codex uses Responses, Claude uses Messages, and the other supported clients use Chat Completions. Cursor supports skill installation but is not a CONNECT harness.

Success means the requested client uses the identified served model. Separate launch-plan checks from actual installed-client and real-generation acceptance. On failure inspect the gateway and agent logs with `kllm-diagnose`.
