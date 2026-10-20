# Kluster LLM agent skills

This is the canonical, versioned library. `operations/` teaches agents to use
Kluster; `development/` teaches them to change this repository. Skills load on
demand from their descriptions or can be invoked by name through the agent's
native skill interface.

| Category | Skill | Use |
|---|---|---|
| Operations | `kllm-install` | Bootstrap, rebuild and repair a node |
| Operations | `kllm-cluster` | Master/worker membership and lifecycle |
| Operations | `kllm-10gbe` | Guided Windows/macOS/Linux private Ethernet setup (10GbE preferred) and recovery |
| Operations | `kllm-models` | Model download, configuration, planning and inference |
| Operations | `kllm-connect` | Agent endpoints, authentication and readiness |
| Operations | `kllm-diagnose` | Evidence-led runtime/network troubleshooting |
| Development | `kllm-develop` | Architecture-aware implementation and bug fixes |
| Development | `kllm-prima` | Prima fork features, improvements and bugs through managed Kluster builds and qualification |
| Development | `kllm-dashboard` | Live Svelte dashboard changes |
| Development | `kllm-test` | Regression checks and validation boundaries |
| Development | `kllm-audit` | On-demand full `kluster-llm` codebase audit (bugs, performance, maintainability; requires explicit human permission) |
| Development | `kllm-prima-delta-audit` | On-demand `berkkarahan/prima.cpp` fork delta and Kluster integration boundary audit (requires explicit human permission) |
| Development | `kllm-prima-core-audit` | On-demand `prima.cpp` native distributed runtime audit (requires explicit human permission) |

Example requests: “Use kllm-10gbe to guide setup between my Windows worker and
Mac master”; “Use kllm-develop and kllm-test to fix this CLI failure.” The 10GbE
skill carries separate platform references and defaults to Kluster's managed
workflow. Loading a skill does not change networking or grant administrator
access.

## Install

Run from any directory; the script finds sources relative to its own checkout:

```bash
python3 scripts/install_skills.py --list
python3 scripts/install_skills.py --dry-run
python3 scripts/install_skills.py
python3 scripts/install_skills.py --agent codex --agent claude --category operations
python3 scripts/install_skills.py --agent hermes --scope user
python3 scripts/install_skills.py --agent pi --project-root /path/to/another/checkout
```

Use `python` or `py -3` on Windows, or `uv run python` in an already bootstrapped
checkout. The standalone installer uses the standard library and does not need
the application, native extension, agent binaries, or administrator access. It
also works with the macOS system Python 3.9; application bootstrap still follows
the requirements in `docs/agent-install.md`.

Defaults are `--agent all --scope project --category all`. Repeat `--agent` to
select clients. `--project-root` changes only the destination, not the canonical
source. Categories are flattened at installation so each native skill folder
contains its own `SKILL.md` and complete supporting resources.

| Agent | Project skill root | Default user skill root |
|---|---|---|
| Codex | `.agents/skills` | `~/.agents/skills` |
| Claude Code | `.claude/skills` | `~/.claude/skills` |
| Cursor | `.cursor/skills` | `~/.cursor/skills` |
| OpenCode | `.opencode/skills` | `~/.config/opencode/skills` |
| Hermes | `.hermes/skills` | `~/.hermes/skills` |
| OpenClaw | `.agents/skills` | `~/.openclaw/skills` |
| Pi | `.pi/skills` | `~/.pi/agent/skills` |

User installs respect `CODEX_HOME` (legacy/native home skill root when supplied),
`CLAUDE_CONFIG_DIR`, `OPENCODE_CONFIG_DIR`, `XDG_CONFIG_HOME`, `HERMES_HOME`,
`HERMES_DATA_DIR_SUFFIX`, `OPENCLAW_STATE_DIR`, and `PI_CODING_AGENT_DIR` where
applicable. Shared/resolved destination roots are installed once. An OpenClaw
project is its selected workspace; select the intended checkout as that workspace
for project discovery. Hermes may need `hermes skills trust` locally; the
installer never grants trust. Restart/reload the agent to refresh discovery.

Copies are intentional: OpenClaw can reject workspace symlinks escaping its
skill root, and Windows link privileges vary. Rerun the installer after library
updates. `.kllm-skills.json` records each copy's source and content hashes.
Unchanged owned copies update; unrelated skills remain untouched. Selecting a
category installs/updates that category without uninstalling previously installed
categories. A conflicting or locally edited selected skill stops preflight.
`--force` backs up the selected conflict outside the discovery root before
replacement and prints its backup path. It never follows a conflicting skill
symlink into its external target.

All destinations are preflighted before writes. Replacement failure rolls back
the current destination; earlier completed destinations remain installed and a
rerun is safe. An interrupted process can leave a `.kllm-skills.lock` and sibling
`.kllm-skills-*` recovery folder. Check the recorded process and inspect preserved
copies before restoring an interrupted swap or removing a stale lock. Do not
delete recovery copies blindly. Listing and dry runs create no files.

## CONNECT sessions

```bash
uv run kllm connect hermes --kllm-skills operations
uv run kllm connect codex --kllm-skills development --persist
uv run kllm connect opencode --kllm-skills none --no-launch
```

`--kllm-skills all|operations|development|none` defaults to `all`. CONNECT finds
the checkout with existing repository discovery, including `KLLM_REPO_ROOT`.
It copies the selected pack into the isolated Codex/Hermes/OpenClaw/Pi home,
a temporary Claude additional directory, or a temporary OpenCode skill source.
Normal agent profiles are not rewritten. OpenCode V1 uses `skills.paths`; V2
uses a sources array and its private standalone service. Existing skill sources
retain native discovery/precedence.

Selection controls the injected Kluster pack, not skills already installed in
native project/user folders. `none` removes unchanged session-owned copies from
a persistent isolated home; user-installed or locally edited skills are retained
(edited owned copies require review before selection changes). `--persist`
retains isolated managed copies; ordinary temporary resources are cleaned after
exit, launch failure, or `--no-launch`. A missing checkout library leaves
connection behavior intact with an actionable note; invalid discovered content
fails clearly. Canonical skills are checkout assets, not bundled wheel resources.

`--no-launch` still checks authenticated model readiness and can prepare
inference. Use `--no-serve` when inference must already be running. Cursor has
skill installation support but is not a `kllm connect` harness.

## Maintain the library

Add a unique lowercase kebab-case skill directory directly under its category.
Use a single-line `name` and `description` frontmatter field; JSON-style quoted
descriptions are portable and supported by the dependency-free installer.
Descriptions should distinguish product operation from repository development.
Keep supporting references inside the skill folder and avoid fixed machine
paths. Resolve repository documentation through the identified Kluster checkout.
Existing Spec Kit skills are separate and are not migrated by this installer.

Validate metadata with the available skill-creator validator and run
`uv run python -m pytest -q tests/test_agent_skills.py tests/test_connect_skills.py`.
Native discovery paths follow [Codex](https://learn.chatgpt.com/docs/build-skills),
[Claude](https://code.claude.com/docs/en/skills),
[Cursor](https://cursor.com/docs/skills),
[OpenCode](https://opencode.ai/docs/skills/),
[Hermes](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/),
[OpenClaw](https://docs.openclaw.ai/tools/skills), and
[Pi](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md).
