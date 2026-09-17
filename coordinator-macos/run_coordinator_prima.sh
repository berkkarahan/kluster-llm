#!/bin/bash
# run_coordinator_prima.sh - Launch Prima.cpp with Halda scheduler for experimental distributed inference
# Usage: ./run_coordinator_prima.sh --model <path> --worker-ip <ip> [--worker-port 50052]

set -euo pipefail

echo "=== Kluster LLM: Coordinator (Experimental Tier - Prima.cpp) ==="
echo ""

# ── Defaults ──────────────────────────────────────────────────────────────────
MODEL=""
WORKER_IP=""
WORKER_PORT=50052
CONTEXT=4096
THREADS=$(sysctl -n hw.perflevel_sustained.performance 2>/dev/null || echo 8)
MICRO_BATCH=2
MMAP_THRESHOLD=0.10  # Keep memory pressure below 10%

# ── Parse arguments ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model) MODEL="$2"; shift 2 ;;
        --worker-ip) WORKER_IP="$2"; shift 2 ;;
        --worker-port) WORKER_PORT="$2"; shift 2 ;;
        --context) CONTEXT="$2"; shift 2 ;;
        --threads) THREADS="$2"; shift 2 ;;
        --micro-batch) MICRO_BATCH="$2"; shift 2 ;;
        --mmap-threshold) MMAP_THRESHOLD="$2"; shift 2 ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
done

# ── Validation ────────────────────────────────────────────────────────────────
if [ -z "$MODEL" ]; then
    echo "ERROR: --model <path> is required"
    echo "Usage: $0 --model /path/to/model.gguf --worker-ip <ip>"
    exit 1
fi

if [ -z "$WORKER_IP" ]; then
    echo "ERROR: --worker-ip <ip> is required"
    exit 1
fi

if [ ! -f "$MODEL" ]; then
    echo "ERROR: Model file not found: $MODEL"
    exit 1
fi

# ── Hardware detection ──────────────────────────────────────────────────────
TOTAL_MEM_MB=$(sysctl -n hw.memsize | awk '{printf "%d", $1/1024/1024}')
CPU_CORES=$(sysctl -n hw.ncpu)

echo "System: macOS $(sw_vers -productVersion)"
echo "Memory: ${TOTAL_MEM_MB} MB unified"
echo "CPU cores: $CPU_CORES"
echo ""

# ── Check for Prima.cpp binary ──────────────────────────────────────────────
PRIMA_SERVER="${PRIMA_SERVER:-$(pwd)/../vendor/prima.cpp/build/release/bin/prima-server}"

if [ ! -f "$PRIMA_SERVER" ]; then
    echo "ERROR: prima-server not found at $PRIMA_SERVER"
    echo "Prima.cpp is required for the Experimental Tier."
    echo "Clone prima.cpp from https://github.com/your-org/prima.cpp and compile."
    exit 1
fi

echo "[INFO] Launching coordinator with Halda scheduler..."
echo "  Worker: ${WORKER_IP}:${WORKER_PORT}"
echo "  Model: ${MODEL}"
echo "  Context: ${CONTEXT} tokens"
echo "  Scheduler: Halda (automatic workload distribution)"
echo "  MMap threshold: ${MMAP_THRESHOLD} (10% memory pressure limit)"
echo "  Micro-batch: ${MICRO_BATCH}"
echo ""

exec "$PRIMA_SERVER" \
    --model "$MODEL" \
    --host 0.0.0.0 \
    --port 8080 \
    --ncmd 1 \
    --ntp 1 \
    --cb-type-verbose 1 \
    --verbose-eval \
    --metrics \
    --flash-attn \
    --scheduler halda \
    --mmap-threshold $MMAP_THRESHOLD \
    --threads $THREADS \
    --threads-batch $THREADS \
    --ctx-size $CONTEXT \
    --batch-size $MICRO_BATCH \
    --ubatch-size $MICRO_BATCH \
    --log-disable \
    --verbose
