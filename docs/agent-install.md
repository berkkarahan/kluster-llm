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
| 6 | `rpc-server` found | same locations and/or `worker-windows/rpc-server.exe` |
| 7 | WireGuard CLI present (for overlay) | `wg --version` or `C:\Program Files\WireGuard\wg.exe` |
| 8 | Agent starts | `kllm status` returns JSON-ish status (role `idle` is OK) |

**Cluster smoke (needs 2+ machines):** master `kllm start` prints join key; worker `kllm join --key …` succeeds; master `kllm status` lists the worker; optional `kllm run --model …` after model distribute.

---

## 1. Preconditions (agent must confirm before installing)

### Required already on the machine

| Item | Notes |
|------|--------|
| **Python ≥ 3.10** | `python --version` or `python3 --version`. On Windows prefer `python`, not the Store stub. |
| **Repo root** | Directory containing `pyproject.toml`, `scripts/install.py`, `python/kllm/`. |
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
| Windows | `vulkan` (auto) | Prefer `--prefer-prebuilt`. CUDA if `nvcc` / CUDA toolkit present → auto may pick `cuda`. |
| macOS | `metal` | Prefer source build with Metal. |
| Linux | `vulkan` or `cuda` if toolkit present | Source build preferred when cmake works. |

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

**macOS Metal explicit:**

```bash
python3 scripts/install.py --backend metal
```

**Windows Vulkan prebuilt explicit:**

```powershell
python scripts\install.py --backend vulkan --prefer-prebuilt
```

**Linux CUDA:**

```bash
python3 scripts/install.py --backend cuda --from-source
```

### 2.3 What the installer does (in order)

1. Ensures **git** (and other toolchains unless `--skip-toolchains`).  
2. Ensures **uv**, creates/repairs **`.venv`** (Python from **`.python-version`**), runs **`uv sync`**, installs **Rust** if missing, builds **`kllm`** with maturin, installs wheel + pytest.  
3. Ensures **Node/npm**, runs `dashboard` `npm install` + `npm run build`.  
4. Installs **llama.cpp** binaries (`llama-server` + `rpc-server`): prebuilt GitHub `b*` assets and/or cmake source with `-DGGML_RPC=ON`; stages to `vendor/bin/` and `worker-windows/` as needed.  
5. Installs **WireGuard** (winget / brew / apt/dnf/pacman). Warns but does not abort the whole install if WG needs admin.

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
Get-ChildItem vendor\bin\llama-server.exe, vendor\bin\rpc-server.exe, worker-windows\rpc-server.exe -ErrorAction SilentlyContinue
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

| Flag | When to use |
|------|-------------|
| `--backend auto\|metal\|cuda\|vulkan\|cpu` | Force accelerator; default `auto`. |
| `--prefer-prebuilt` | Prefer GitHub release zips (default-ish on Windows). |
| `--from-source` | Prefer cmake build of `vendor/llama.cpp`. |
| `--release` | Release-mode maturin build. |
| `--skip-ui` | No dashboard (CLI-only node). |
| `--skip-rust` | Skip kllm package build (already built). |
| `--skip-llama` | Skip llama binaries (already present). |
| `--skip-wireguard` | Skip WG (LAN-only RPC; no overlay). |
| `--skip-toolchains` | Do not auto-install Rust/Node/git/cmake. |
| `--skip-npm-install` | Dashboard `npm run build` only. |

**CLI/UI rebuild only** (no llama/WG/toolchains):

```bash
python scripts/build.py
```

**Legacy wrappers** (they call `install.py`):

- `coordinator-macos/setup_mac.sh` → `install.py --backend metal`  
- `worker-windows/setup_windows.ps1` → partial install (`--skip-rust --skip-ui --skip-wireguard --prefer-prebuilt --backend vulkan`)  
- Prefer `scripts/install.py` over these.

---

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

### 4.3 Model + inference (master)

```bash
kllm model download 'org/repo:Q4_K_M'
# or 'org/repo:file.Q4_K_M.gguf' / a direct URL
kllm model distribute <name>
kllm run --model <name>
kllm ui
```

Ports: control **18765**, RPC **50052**, inference **8080**. RPC must stay private (LAN or WireGuard). Upstream llama.cpp RPC has **no auth**.

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
| `llama-server` missing | Prebuilt miss or cmake fail | Retry `--prefer-prebuilt` **or** `--from-source` with cmake; set `KLLM_LLAMA_SERVER` if you have a binary |
| `rpc-server` missing | Some prebuilt packs omit it | Build from source with `-DGGML_RPC=ON` targeting `rpc-server`; copy to `vendor/bin/` and `worker-windows/` |
| WireGuard warning | No admin / no package manager | Install WG manually from https://www.wireguard.com/install/ ; do **not** use `--overlay wireguard` until `wg` works |
| `kllm` command not found | Venv not activated | Activate `.venv` (§2.4) |
| `native_available()` False | Extension not installed | Re-run `python scripts/install.py` without `--skip-rust` |
| Join key 401 | Wrong key / master stopped | Re-`kllm start` on master; copy new key |
| Worker join OK but `run` fails | RPC unreachable / wrong bind | `kllm status` → check `rpc_bind`, firewall 50052, same L2/VPN; see troubleshooting.md |
| Overlay start/join 400 | `wg` missing or overlay mismatch | Install WG; match `--overlay` on all nodes |

**Do not:** download random third-party llama builds; leave `rpc-server` on `0.0.0.0` on untrusted networks; expose 50052 to the internet; skip venv activation and install into system Python.

---

## 6. Minimal command cheat sheet (copy-paste)

```bash
# Full install
python scripts/install.py

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
kllm model distribute file.gguf
kllm run --model file.gguf
```

---

## 7. File map (where things land)

| Path | Role |
|------|------|
| `scripts/install.py` | Full installer |
| `scripts/build.py` | CLI + dashboard rebuild only |
| `.venv/` | Project virtualenv |
| `dashboard/build/` | Static UI served by agent |
| `vendor/bin/` | Staged `llama-server` / `rpc-server` |
| `vendor/llama.cpp/` | Source + cmake build tree |
| `worker-windows/rpc-server.exe` | Windows worker binary location |
| `~/.kllm/` | Runtime state (`cluster.json`, models, WG keys, API token, `hf_token`) |

---

## 8. Agent behavioral rules

1. **Prefer** `python scripts/install.py` over manual pip/maturin/cmake unless debugging a specific failed step.  
2. **Run install on each physical node** that will be master or worker.  
3. **Activate `.venv`** before every `kllm` command in a new shell.  
4. **Match** `--backend` to the GPU on that node; mixed backends across nodes are supported (layer split).  
5. **Treat RPC as insecure** at the app layer; rely on LAN isolation or WireGuard overlay.  
6. After changes to Rust/Python packaging, re-run install or `python scripts/build.py` (not only `pip install -e .` unless you know maturin is required).  
7. When reporting status to the user, cite which §0 checks passed/failed.

---

## 9. Quick links

- Installer: [`scripts/install.py`](../scripts/install.py)  
- CLI reference: [cli.md](cli.md)  
- Troubleshooting: [troubleshooting.md](troubleshooting.md)  
- Engine / RPC policy: [engine-decision.md](engine-decision.md)  
- Network: [`network-config/`](../network-config/)
