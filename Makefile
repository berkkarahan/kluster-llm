.PHONY: all setup-coordinator setup-worker calculate-split run-coordinator run-worker clean install install-cli build test-cli

# ── Defaults ──────────────────────────────────────────────────────────────────
MODEL ?=
WORKER_IP ?=
WORKER_PORT ?= 50052
CONTEXT ?= 4096
TIER ?= stable

# ── Full install (toolchains, kllm, llama.cpp, WireGuard, dashboard) ───────────
# Cross-platform: `python scripts/install.py`
install:
	python scripts/install.py

install-cli: install

build:
	python scripts/build.py

test-cli:
	cd crates/kllm-native && cargo test
	. .venv/bin/activate 2>/dev/null || .venv/Scripts/activate; pytest -q

# ── All-in-one setup ─────────────────────────────────────────────────────────
all: install

# ── Coordinator setup (macOS) — prefer `make install` ────────────────────────
setup-coordinator:
	@echo "=== Setting up macOS coordinator (delegates to scripts/install.py) ==="
	python scripts/install.py --backend metal

# ── Worker setup (Windows) — prefer `python scripts/install.py` ──────────────
setup-worker:
	@echo "=== Setting up Windows worker ==="
	@powershell -File worker-windows/setup_windows.ps1

# ── Calculate split ratio ────────────────────────────────────────────────────
calculate-split:
	@cd coordinator-macos && ./calculate_split.sh --mac-memory 24 --worker-vram 12

# ── Run coordinator ──────────────────────────────────────────────────────────
run-coordinator:
	@if [ -z "$(MODEL)" ]; then \
		echo "ERROR: MODEL must be set (e.g., make run-coordinator MODEL=/path/to/model.gguf)"; \
		exit 1; \
	fi
	@if [ -z "$(WORKER_IP)" ]; then \
		echo "ERROR: WORKER_IP must be set"; \
		exit 1; \
	fi
	@echo "=== Running coordinator ($(TIER) tier) ==="
	@cd coordinator-macos && ./run_coordinator.sh \
		--model $(MODEL) \
		--worker-ip $(WORKER_IP) \
		--worker-port $(WORKER_PORT) \
		--context $(CONTEXT)

# ── Run coordinator (experimental) ──────────────────────────────────────────
run-coordinator-prima:
	@if [ -z "$(MODEL)" ]; then \
		echo "ERROR: MODEL must be set"; \
		exit 1; \
	fi
	@echo "=== Running coordinator (Prima.cpp / Halda) ==="
	@cd coordinator-macos && ./run_coordinator_prima.sh \
		--model $(MODEL) \
		--worker-ip $(WORKER_IP) \
		--worker-port $(WORKER_PORT)

# ── Clean ────────────────────────────────────────────────────────────────────
clean:
	@echo "=== Cleaning build artifacts ==="
	@rm -rf coordinator-macos/../vendor/llama.cpp/build
	@rm -rf coordinator-macos/../vendor/prima.cpp/build
	@rm -f worker-windows/rpc-server.exe
	@rm -rf worker-windows/src
