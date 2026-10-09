# Agent install guide — Kluster LLM (`hetero-llm-cluster`)

> **Audience:** LLM coding agents (and humans following the same checklist).  
> **Goal:** Bring a clean machine from repo checkout → working `kllm` cluster node (master and/or worker).  
> **Do not invent alternate install paths** unless this guide’s primary path fails after the listed recoveries.

Related human docs: [cli.md](cli.md), [troubleshooting.md](troubleshooting.md), [engine-decision.md](engine-decision.md), [../README.md](../README.md).  
Installer source of truth: [`scripts/install.py`](../scripts/install.py).

---

## 0. Success criteria (done when all true)

After install on **this** machine:

| # | Check | How to verify |
|---|--------|----------------|
| 1 | Project venv exists | `.venv/` present; activate works |
| 2 | `kllm` importable | `python -c "import kllm; print('ok')"` inside venv |
| 3 | Native extension loads | `python -c "from kllm import native; print(native.native_available())"` → `True` |
| 4 | Dashboard built | `dashboard/build/index.html` exists |
| 5 | `llama-server` found | file under `vendor/bin/` or `vendor/llama.cpp/build/bin/` (`.exe` on Windows) |
| 6 | `rpc-server` found | file under `vendor/bin/` or `vendor/llama.cpp/build/bin/` (`.exe` on Windows) |
| 6b | llama.cpp pin matches | `python scripts/install.py --check` exits 0; stamp `vendor/bin/llama-release.txt` equals committed [`llama-release`](../llama-release) |
| 7 | WireGuard CLI present (for overlay) | `wg --version` or `C:\Program Files\WireGuard\wg.exe` |
| 8 | Agent starts | `kllm status` returns JSON-ish status (role `idle` is OK). Install adds a global `~/.local/bin/kllm` shim (`uv run kllm` for this checkout); open a **new terminal** if PATH was just updated. Skip with `python scripts/install.py --skip-path-shim`. |

**Cluster smoke (needs 2+ machines):** master `kllm start` prints join key; worker `kllm join --key …` succeeds; master `kllm status` lists the worker; optional `kllm run --model …` after master-local model download (no GGUF copy to workers).

---

## 1. Preconditions (agent must confirm before installing)

### Required already on the machine

| Item | Notes |
|------|--------|
| **Python ≥ 3.10** | `python --version` or `python3 --version`. On Windows prefer `python`, not the Store stub. |
| **Repo root** | Directory containing `pyproject.toml`, `scripts/install.py`, `llama-release`, `src/kllm/`. |
| **Network** | Outbound HTTPS to GitHub, PyPI, npm registry, rustup (or offline mirrors). |
| **Admin/sudo** (often) | WireGuard / apt / winget package installs may need elevation. If denied, continue and report; overlay will be unavailable until WG is installed. |

### Not required up front (installer installs these)

- Rust (`cargo` / `rustc`) via rustup  
- Node.js + npm  
- git, cmake (when building llama from source)  
- WireGuard tools  
- `llama-server` / `rpc-server` binaries  

### Platform matrix (pick backend)

| Host OS | Default `--backend` | Notes |
|---------|---------------------|--------|
| Windows | `vulkan` (auto) | Prebuilt GitHub zip for the pin in `llama-release`. CUDA if `nvcc` / CUDA toolkit present → auto may pick `cuda`. |
| macOS Apple Silicon | `metal` (auto) | Default install is a **source** build with **Metal + Vulkan** (MoltenVK via Homebrew). Stamp `backend=metal+vulkan`. Runtime preference defaults to Metal; set `KLLM_ACCEL_BACKEND=vulkan` for Vulkan-master clusters. The default **aborts** if it cannot produce both backends; `--prefer-prebuilt` is rejected on macOS, and `--backend cpu` is the only CPU-only opt-out. |
| Linux | `cuda` with NVIDIA tooling, `rocm` with AMD HIP tooling, otherwise `vulkan` | Use `--backend rocm --from-source` on supported Linux AMD systems. Hardware validation remains required. |

---

## 2. Primary install procedure (do this first)

### 2.1 Working directory

```text
cd <repo-root>   # must contain scripts/install.py
```

### 2.2 Run the full installer

**Default (recommended on every node):**

```bash
python scripts/install.py
```

**Windows (if `python` ambiguous):**

```powershell
py -3 scripts\install.py
```

**macOS Metal+Vulkan (default source multi-backend):**

```bash
python3 scripts/install.py
# or explicit:
python3 scripts/install.py --backend metal
```

**macOS CPU-only:**

```bash
python3 scripts/install.py --backend cpu --from-source
```

> `--prefer-prebuilt` is rejected on macOS: GitHub macOS packs are Metal-only, and macOS installs must provide Metal + Vulkan in one binary. `--backend cpu` is the only opt-out.

**Windows Vulkan prebuilt explicit:**

```powershell
python scripts\install.py --backend vulkan --prefer-prebuilt
```

**Linux CUDA:**

```bash
python3 scripts/install.py --backend cuda --from-source
```

### 2.3 What the installer does (in order)

1. Ensures required toolchains (including **git**, **uv**, **Rust**, and **Node/npm**, unless `--skip-toolchains`), creates/repairs **`.venv`** (Python from **`.python-version`**).
2. Installs **WireGuard** when requested. This runs outside the parallel build phase to avoid concurrent system package-manager operations.
3. Runs the independent **kllm** build (`uv sync`, maturin, wheel install), **dashboard** build (`npm install` + `npm run build`), and **llama.cpp** install concurrently, subject to the skip flags. The installer waits for all stages and reports each failure before returning non-zero.
4. The llama.cpp stage installs binaries (`llama-server` + `rpc-server`) for the tag in committed [`llama-release`](../llama-release): on **macOS** (non-CPU), compiles a Metal+Vulkan source build by default; otherwise prefers GitHub prebuilt assets, with cmake source fallback. Stages to `vendor/bin/` and writes `vendor/bin/llama-release.txt`. Re-runs when the stamp does not match the pin.
   The macOS Homebrew dependencies are `molten-vk vulkan-headers vulkan-loader shaderc spirv-headers`. The Vulkan gate checks `glslc` (provided by `shaderc`) and the `SPIRV-Headers` CMake config alongside Vulkan headers and the MoltenVK ICD manifest.
   The reusable `vendor/llama.cpp/build` tree is installer-owned: it is discarded when the pin, backend flags or a recorded OpenSSL package path changes, so a toolchain upgrade (`brew upgrade openssl@3`) cannot leave a cache that half-resolves a package. A dangling `-L` in an exported `LDFLAGS` is ignored rather than treated as stale, so it cannot force a rebuild every run.
   On macOS the default install **aborts** when the Metal+Vulkan build fails; it never downgrades to a Metal-only pack, and `--prefer-prebuilt` is rejected outright. Verification probes the binary the runtime actually launches (`vendor/bin/llama-server`, which `kllm-native` resolves first), not the CMake build output, and `--check` exits non-zero on a backend mismatch.
   llama-server's embedded HTTPS server is the expendable part: if `cmake` configure fails, the installer clears the cached OpenSSL entries and dangling linker flags and retries **with HTTPS enabled** (the real fix); only if that fails does it fall back to `-DLLAMA_OPENSSL=OFF`, recorded as `https=off` in the stamp. The fallback is never latched, so the next run restores HTTPS.
   Runtime setup can reuse a pack already in `~/.kllm/models` without changing which backends are installed: it skips when the install already satisfies the contract and refuses a pack that cannot. Joining only enrolls the node and never extracts a runtime pack or starts RPC.
5. Installs **WireGuard** (winget / brew / apt/dnf/pacman). Warns but does not abort the whole install if WG needs admin.
6. Installs a global **`kllm`** launcher under `~/.local/bin` (Windows: `kllm.cmd`) that runs `uv run kllm` for this checkout, and idempotently adds `~/.local/bin` to the user PATH when needed (`--skip-path-shim` to skip).

Expected wall time: several minutes to 30+ minutes (llama compile / large downloads).

### 2.4 Run the CLI

Prefer **uv** (no shell activation):

```bash
uv run kllm status
uvx --from . kllm start
```

Or activate **`.venv`**:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

### 2.5 Verify (run all)

```bash
python -c "import kllm; from kllm import native; assert native.native_available(); print('kllm ok')"
kllm status
```

Binary presence (pick OS syntax):

```bash
# Unix
ls vendor/bin/llama-server vendor/bin/rpc-server 2>/dev/null
ls vendor/llama.cpp/build/bin/llama-server vendor/llama.cpp/build/bin/rpc-server 2>/dev/null

# Windows PowerShell
Get-ChildItem vendor\bin\llama-server.exe, vendor\bin\rpc-server.exe -ErrorAction SilentlyContinue
```

WireGuard:

```bash
wg version
# or Windows:
& "C:\Program Files\WireGuard\wg.exe" --version
```

If steps 1–6 of §0 pass → **install phase complete**. Proceed to §4 for cluster bring-up.

---

## 3. Installer flags (use only when needed)

### Managed runtime library

Settings → **llama.cpp** stores multiple upstream versions and public HTTPS Git forks.
Choose a release/tag, branch, or full commit; the selecting node resolves it to an immutable
repository/commit identity. **Set target & sync** persists the master's target and starts
isolated downloads/builds on the master; children receive it over the paired signed control
channel, and a per-node **Sync** (or Retry) forces that worker immediately. Joining and
reconnecting children prepare that target automatically. A worker whose pinned pairing diverges
from the master (for example after a restored `cluster.json`) re-asserts it automatically; a
master label or cluster-binding rotation that kept the signing key is adopted even while a
managed network is live, replacing the superseded pin and clearing stale authority; a changed
signing key needs explicit local re-pairing. After adoption the master re-stamps a saved target
whose master label changed and re-sends it; a changed signing binding is reported as needing
re-application. Transient build failures retry automatically with a bounded backoff. A worker
that still has no paired channel shows *Runtime control channel not connected … re-pair it*
rather than waiting silently, and forcing it returns an actionable `CHANNEL_UNAVAILABLE`.
Failed or cancelled builds remain visible until Retry or a new target.
The local library stays usable when signed cluster control is unavailable; setting and enforcing
the cluster target requires the recovered agent.

The library lives in `KLLM_HOME/runtimes` (normally `~/.kllm/runtimes`). Preparation
prefers a matching **prebuilt GitHub release pack**: a pack is eligible only when a published
release's tag resolves to the pinned commit, and only if it provides both `llama-server` and
`rpc-server` and satisfies this node's backend contract. Otherwise, and whenever a source build
is required for verified TCP-only RPC evidence, preparation compiles the exact commit. Builds
contain both runtime binaries, libraries, source provenance, digests, and (for source builds)
TCP-only RPC evidence. They use existing backend presets, including Metal+Vulkan on macOS, never
replace `vendor/bin`, and preparation does not start RPC or interrupt active inference. New runs
require verified matching source identities on the master and selected workers; unused/offline
children do not block them. Active runs and recovery retain their original runtime until stopped.
Unknown or incompatible RPC protocols remain unsupported, and a prebuilt pack never carries the
TCP-only evidence the managed private network gate requires — prepare that version from source.

Existing installations appear as legacy entries. Prepare a managed version before an enforced
run; a tag stamp alone is not proof of a fork's exact revision. Upgrade/rebuild participating
agents for runtime-management and native path-selection support. `python scripts/build.py`
also refreshes the source-layout native module imported by `uv run`.

The agent invokes the primary installer in isolated managed mode, trying a prebuilt pack first
and falling back to a source build when no pack is eligible:

```bash
uv run python scripts/install.py --llama-only --prebuilt \
  --source-repo https://github.com/ggml-org/llama.cpp.git \
  --revision <full-40-character-commit> --runtime-dir <isolated-build-directory> \
  --backend auto
# fallback (verified TCP-only RPC; required for the managed 10GbE private network)
uv run python scripts/install.py --llama-only --from-source --rpc-tcp-only \
  --source-repo https://github.com/ggml-org/llama.cpp.git \
  --revision <full-40-character-commit> --runtime-dir <isolated-build-directory> \
  --backend auto
```

All three managed source arguments are required together. The committed stable `llama-release`
remains the bootstrap default for existing installer commands and `--check`.

| Flag | When to use |
|------|-------------|
| `--backend auto\|metal\|cuda\|rocm\|vulkan\|cpu` | Force accelerator; default `auto`. |
| `--prefer-prebuilt` | Prefer GitHub release zips (default on Windows/Linux; rejected on macOS, which must build Metal+Vulkan). |
| `--from-source` | Compile the pinned llama.cpp tag in `vendor/llama.cpp` (macOS default for Metal+Vulkan). |
| `--prebuilt` | Managed mode: install a matching release pack; exit with a marker for the caller to fall back to source. |
| `--rpc-tcp-only` | Source-build RPC with RDMA disabled (verified evidence for managed private networks; rejected with `--prebuilt`). |
| `--tag TAG` | Override `llama-release` (keep every node on the same tag). |
| `--force` | Reinstall llama.cpp even when `vendor/bin/llama-release.txt` matches. |
| `--release` | Release-mode maturin build. |
| `--skip-ui` | No dashboard (CLI-only node). |
| `--skip-rust` | Skip kllm package build (already built). |
| `--skip-llama` | Skip llama binaries (already present). |
| `--skip-wireguard` | Skip WG (LAN-only RPC; no overlay). |
| `--skip-toolchains` | Do not auto-install Rust/Node/git/cmake. |
| `--skip-npm-install` | Dashboard `npm run build` only. |

**llama.cpp only** (pin in `llama-release`; run on every node after a pin bump or RPC mismatch):

```bash
python scripts/install.py --llama-only
python scripts/install.py --check
python scripts/install.py --llama-only --backend metal    # macOS: Metal+Vulkan multi-backend (default)
# Vulkan-master cluster: keep the multi binary; prefer Vulkan at runtime:
#   export KLLM_ACCEL_BACKEND=vulkan
```

**CLI/UI rebuild only** (no llama/WG/toolchains):

```bash
python scripts/build.py
```

### Local SQLite storage

Usage history and managed-network records keep their existing SQLite files and
schemas. The owner reads these optional settings when it opens a store; restart
the agent after changing them:

| Variable | Default | Accepted values |
|---|---:|---|
| `KLLM_SQLITE_POOL_SIZE` | `1` | Integer `1`–`4` connections per file |
| `KLLM_SQLITE_WAIT_TIMEOUT` | `5` seconds | Positive finite number |
| `KLLM_SQLITE_SHUTDOWN_TIMEOUT` | `10` seconds | Positive finite number |

No overflow connections are created. Invalid settings prevent the owner from
opening and the affected history/network feature reports unavailable; existing
database files remain available for recovery. Install or rebuild through the
normal `uv sync` and `python scripts/build.py` flow above.

## 4. Post-install: bring up a cluster

Run install on **every** node. Then:

### 4.1 Master

```bash
source .venv/bin/activate   # or Windows Activate.ps1
kllm start
# Optional overlay (requires WireGuard on all nodes):
# kllm start --overlay wireguard
```

Record the printed **join key**. Control port default **18765**.

### 4.2 Worker(s)

```bash
source .venv/bin/activate
kllm join --key '<join-key>'
# if mDNS fails: kllm join --key '<join-key>' --master <master-lan-ip>
# If master used overlay:
# kllm join --key '<join-key>' --overlay wireguard
```

Join uses the join key fingerprint over mDNS to find the master; `--master` is only needed when discovery fails. Overlay must match master (`none` vs `wireguard`). Control plane stays on LAN; RPC may move to `10.66.87.0/24` when overlay is on.

A joined worker is idle until the master selects it for a model run and commands RPC startup. Joining, heartbeats, runtime installation, and restarting the agent never independently start RPC. Stopping the model stops its workers' RPC processes and preserves cluster membership and networking. Managed private-network setup can verify connectivity before llama binaries are installed; the master checks runtime prerequisites at inference startup.

### 4.3 Model + inference (master)

```bash
kllm model download 'org/repo:Q4_K_M'
# or 'org/repo:file.Q4_K_M.gguf' / a direct URL
# GGUF stays on the master; workers receive tensors over RPC
kllm run --model <name>
kllm ui
```

Ports: control **18765**, RPC **50052**, inference **8080**. RPC must stay private (LAN or WireGuard). Upstream llama.cpp RPC has **no auth**.

### 4.4 Updating software

Update any node's own checkout in one step — stop the local node, `git pull --ff-only`, full rebuild:

```bash
# On master (requires a clean checkout; honors KLLM_REPO_ROOT)
kllm update
# Node is left stopped; start or join again when the rebuild finishes:
kllm start
```

Then fan out the same pull + smart rebuild to joined workers:

```bash
# On master (workers must have a clean git checkout of this repo)
kllm cluster update
# Dashboard: Cluster → Update workers
```

Each worker runs `git pull --ff-only`, then `scripts/install.py` if `llama-release` changed or `scripts/build.py` otherwise, restarts its agent, and rejoins with the same key. Set `KLLM_REPO_ROOT` on workers if the checkout is not discoverable from the agent process.

### 4.4 Useful env overrides

| Env | Purpose |
|-----|---------|
| `KLLM_HOME` | State dir (default `~/.kllm`) |
| `KLLM_REPO_ROOT` | Repo root for binary resolution |
| `KLLM_LLAMA_SERVER` | Absolute path to `llama-server` |
| `KLLM_RPC_SERVER` | Absolute path to `rpc-server` |
| `KLLM_WG` | Absolute path to `wg` / `wg.exe` |
| `KLLM_API_KEY` | Inference API token override for harnesses |
| `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` | Hugging Face Hub token (overridden by `~/.kllm/hf_token`) |

---

## 5. Failure recovery (agent decision tree)

| Symptom | Likely cause | Agent action |
|---------|--------------|--------------|
| `python` / `python3` not found | No Python | Install Python 3.10+; re-run §2 |
| `cargo` missing after install | PATH not refreshed | New shell; ensure `~/.cargo/bin` or `%USERPROFILE%\.cargo\bin` on PATH; re-run |
| `npm` missing after install | PATH / winget needs new shell | New shell; re-run with `--skip-rust` if kllm already built |
| maturin / Rust compile fails | Broken toolchain | Re-run rustup; `rustup default stable`; re-run install |
| CMake cache points to a removed SDK/OpenSSL directory | Stale cached package path | Re-run the installer: it discards its stale build tree for missing package paths and resets dangling linker flags. Windows drive/UNC paths and quoted SDK paths with spaces are recognized; a dangling linker flag alone does not discard the tree. |
| `llama-server` missing | Prebuilt miss or cmake fail | `python scripts/install.py --llama-only --force`; or `--from-source`; set `KLLM_LLAMA_SERVER` if you have a binary |
| `rpc-server` missing | Some prebuilt packs omit it | `python scripts/install.py --llama-only --from-source` (`-DGGML_RPC=ON`); verify it was staged in `vendor/bin/` |
| `Could NOT find Vulkan` / `missing: glslc` on Windows | Vulkan node without the LunarG SDK | Managed/source builds provision `KhronosGroup.VulkanSDK` via winget when missing; if winget or elevation is unavailable, install it from https://vulkan.lunarg.com/sdk/home#windows and retry in Settings → llama.cpp |
| `install.py --check` mismatch | Mixed llama.cpp tags across nodes | Run `python scripts/install.py --llama-only` on **every** node; do not mix HEAD source with a pinned nightly |
| WireGuard warning | No admin / no package manager | Install WG manually from https://www.wireguard.com/install/ ; do **not** use `--overlay wireguard` until `wg` works |
| `kllm` command not found | Venv not activated | Activate `.venv` (§2.4) |
| `native_available()` False | Extension not installed | Re-run `python scripts/install.py` without `--skip-rust` |
| Join key 401 | Wrong key / master stopped | Re-`kllm start` on master; copy new key |
| Worker join OK but `run` fails | RPC unreachable, wrong bind, or **llama.cpp tag mismatch** | `python scripts/install.py --check` on every node; `kllm status` → `rpc_bind`, firewall 50052; see troubleshooting.md |
| Overlay start/join 400 | `wg` missing or overlay mismatch | Install WG; match `--overlay` on all nodes |

**Do not:** download random third-party llama builds; mix llama.cpp tags across nodes; leave `rpc-server` on `0.0.0.0` on untrusted networks; expose 50052 to the internet; skip venv activation and install into system Python.

---

## 6. Minimal command cheat sheet (copy-paste)

```bash
# Full install
python scripts/install.py

# llama.cpp pin only (same tag on every node)
python scripts/install.py --llama-only
python scripts/install.py --check

# Activate
source .venv/bin/activate          # Unix
# .\.venv\Scripts\Activate.ps1     # Windows

# Verify
python -c "from kllm import native; assert native.native_available()"
kllm status

# Cluster
kllm start
kllm join --key KEY
# if needed: kllm join --key KEY --master MASTER_IP
kllm model download 'ORG/REPO:Q4_K_M'
kllm run --model file.gguf
```

---

## 7. File map (where things land)

| Path | Role |
|------|------|
| `scripts/install.py` | Full installer |
| `scripts/install.py --llama-only` | llama.cpp only, pinned by `llama-release` |
| `llama-release` | Committed ggml-org/llama.cpp **stable** GitHub tag (e.g. `v0.5.0`; not `bNNNN` nightlies) |
| `scripts/build.py` | CLI + dashboard rebuild only |
| `.venv/` | Project virtualenv |
| `dashboard/build/` | Static UI served by agent |
| `vendor/bin/` | Staged `llama-server` / `rpc-server` |
| `vendor/bin/llama-release.txt` | Local stamp of the installed tag (gitignored under `vendor/`) |
| `vendor/llama.cpp/` | Source + cmake build tree |
| `~/.kllm/` | Runtime state (`cluster.json`, models, WG keys, API token, `hf_token`) |

---

## 8. Agent behavioral rules

1. **Prefer** `python scripts/install.py` over manual pip/maturin/cmake unless debugging a specific failed step.  
2. **Run install on each physical node** that will be master or worker.  
3. **Activate `.venv`** before every `kllm` command in a new shell.  
4. **Match** `--backend` to the GPU on that node; mixed backends across nodes are supported (layer split).  
5. **Treat RPC as insecure** at the app layer; rely on LAN isolation or WireGuard overlay.  
6. **Keep every node on the same llama.cpp tag** (`llama-release` + `python scripts/install.py --llama-only`). Mixed tags break RPC HELLO.  
7. After changes to Rust/Python packaging, re-run install or `python scripts/build.py` (not only `pip install -e .` unless you know maturin is required).  
8. When reporting status to the user, cite which §0 checks passed/failed.

---

## 9. Quick links

- Installer: [`scripts/install.py`](../scripts/install.py)  
- llama.cpp pin: [`llama-release`](../llama-release); sync with `python scripts/install.py --llama-only`  
- CLI reference: [cli.md](cli.md)  
- Troubleshooting: [troubleshooting.md](troubleshooting.md)  
- Engine / RPC policy: [engine-decision.md](engine-decision.md)  
- Network: [`network-config/`](../network-config/)

## Install agent skills

The canonical [skill library](../skills/README.md) separates operations from
development and includes `kllm-10gbe` with Windows, macOS and Linux references.
`python3 scripts/install_skills.py` installs project-local discovery copies for
Codex, Claude Code, Cursor, OpenCode, Hermes, OpenClaw and Pi. Use `python`/`py -3`
on Windows, or `uv run python` in the project environment. This standalone
standard-library installer needs neither the native extension nor elevation.

Use `--dry-run` before installation, repeat `--agent` to select clients,
`--category operations|development|all` to select content, or `--scope user` for
user-wide copies. Reruns update unchanged owned copies; `--force` backs up selected
conflicts outside discovery roots. Existing skills and trust settings remain
independent. CONNECT provisions copies automatically from this checkout through
`--kllm-skills`; installed CLIs without a discoverable checkout can set
`KLLM_REPO_ROOT`. Skills do not themselves authorize host networking changes.

## Guided private-network administrator helper

The supported installer builds and copies `vendor/bin/kllm-network-helper` (`.exe` on Windows) independently of Python. No helper runs with elevated privileges during install/build. The Home → Private 10GbE network panel is hidden until `kllm flags private-network-setup --enable`; once shown it displays the administrator notice, and the local enable button requests OS approval and installs the protected helper for the original user's account and pinned cluster master.

macOS uses a protected `/Library/PrivilegedHelperTools/com.kllm.network` binary and system LaunchDaemon; Linux uses `/usr/local/libexec/kllm-network-helper`, `/etc/kllm-network` grants/journals and a systemd service, with `pkexec` for local authorization; Windows uses a protected Program Files helper and SYSTEM scheduled task. IPC is an account-restricted local socket or named pipe. The helper never imports Python or receives arbitrary executable paths or commands. Keep the ordinary agent under its existing account and credential directory.

Updating the installed helper requires settled networking and another local OS approval. Stop the private network and resolve pending restoration first. Physical OS authorization, DHCP restoration, ARP/DAD, firewall and reboot behavior must be validated on each target deployment; compilation and controlled tests alone do not prove those checks.
