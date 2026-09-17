#!/bin/bash
# run_coordinator.sh - Launch llama.cpp RPC coordinator for distributed inference
# Usage: ./run_coordinator.sh --model <path> --worker-ip <ip> [--worker-port 50052]

set -euo pipefail

echo "=== Kluster LLM: Coordinator (macOS) ==="
echo ""

# ── Defaults ──────────────────────────────────────────────────────────────────
MODEL=""
WORKER_IP=""
WORKER_PORT=50052
CONTEXT=4096
THREADS=$(sysctl -n hw.perflevel_sustained.performance 2>/dev/null || echo 8)
MICRO_BATCH=2
SPLIT_MODE="layer"
TENSOR_SPLIT=""
NGI=0  # CPU threads for model loading
NGT=0  # GPU threads (auto)

# ── Parse arguments ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model) MODEL="$2"; shift 2 ;;
        --worker-ip) WORKER_IP="$2"; shift 2 ;;
        --worker-port) WORKER_PORT="$2"; shift 2 ;;
        --context) CONTEXT="$2"; shift 2 ;;
        --threads) THREADS="$2"; shift 2 ;;
        --micro-batch) MICRO_BATCH="$2"; shift 2 ;;
        --tensor-split) TENSOR_SPLIT="$2"; shift 2 ;;
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
METAL_AVAILABLE=0
if [ -f /usr/lib/libmetal*.dylib ] || sysctl -n hw.agx 2>/dev/null | grep -q "^[1-9]"; then
    METAL_AVAILABLE=1
fi

echo "System: macOS $(sw_vers -productVersion)"
echo "Memory: ${TOTAL_MEM_MB} MB unified"
echo "CPU cores: $CPU_CORES"
echo "Metal: $( [ $METAL_AVAILABLE -eq 1 ] && echo 'available' || echo 'unavailable')"
echo ""

# ── Auto-calculate layer split ──────────────────────────────────────────────
calculate_tensor_split() {
    # Estimate worker VRAM (default 8GB if not detected)
    local worker_vram_mb=8192
    if command -v dxdiag > /dev/null 2>&1; then
        # Try to detect via WMI from network (requires worker cooperation)
        echo "  Attempting worker VRAM detection..."
        # Fallback: ask user or use default
        worker_vram_mb=8192
    fi

    # Model size estimate from file size (rough: 1 byte ≈ 1 quantized parameter)
    local model_size_mb
    model_size_mb=$(du -m "$MODEL" | awk '{print $1}')

    # Budget: keep 20% headroom on Mac, use rest for split
    local mac_budget_mb=$(( TOTAL_MEM_MB * 80 / 100 ))
    local total_budget_mb=$(( mac_budget_mb + worker_vram_mb ))

    # Calculate split ratio (Mac : Worker)
    local split_ratio
    split_ratio=$(awk "BEGIN {printf \"%.3f\", $mac_budget_mb / $total_budget_mb}")

    echo "Model size: ~${model_size_mb} MB"
    echo "Mac budget: ~${mac_budget_mb} MB"
    echo "Worker VRAM: ~${worker_vram_mb} MB"
    echo "Calculated split: Mac ${split_ratio} : Worker $(awk "BEGIN {printf \"%.3f\", 1 - $split_ratio}")"

    # Pass as tensor-split (fraction on Mac side)
    TENSOR_SPLIT="$split_ratio"
}

if [ -z "$TENSOR_SPLIT" ]; then
    echo "[INFO] No --tensor-split provided, auto-calculating..."
    calculate_tensor_split
    echo ""
fi

# ── Calculate -ngl (GPU layers) ──────────────────────────────────────────────
if [ -z "$NGI" ]; then
    # Estimate layers that fit in Mac unified memory + worker VRAM
    mac_budget_mb=$(( TOTAL_MEM_MB * 80 / 100 ))
    worker_vram_mb=8192  # Default; override if detected
    total_vram_mb=$(( mac_budget_mb + worker_vram_mb ))

    # Model size in MB
    model_size_mb=$(du -m "$MODEL" | awk '{print $1}')

    # Estimate layer count (rough: 7B model ≈ 32 layers, 70B ≈ 80 layers)
    # Use model size as proxy
    if [ "$model_size_mb" -lt 6000 ]; then
        total_layers=32  # 7B class
    elif [ "$model_size_mb" -lt 12000 ]; then
        total_layers=48  # 13B class
    else
        total_layers=80  # 70B class
    fi

    # Calculate layers for Mac (based on split ratio)
    mac_layers=$(awk "BEGIN {printf \"%d\", $total_layers * $TENSOR_SPLIT}")

    # Calculate layers for worker (remainder)
    worker_layers=$(( total_layers - mac_layers ))

    # Set -ngl to total GPU layers (mac + worker)
    NGI=$(( mac_layers + worker_layers ))

    # If model is too large, cap at total layers and let remainder spill to RAM
    if [ "$NGI" -gt "$total_layers" ]; then
        NGI=$total_layers
    fi

    echo "[INFO] Calculated -ngl: $NGI (Mac: $mac_layers, Worker: $worker_layers)"
fi

# ── Build command ─────────────────────────────────────────────────────────────
LLAMA_SERVER="${LLAMA_SERVER:-$(pwd)/../vendor/llama.cpp/build/release/bin/llama-server}"

if [ ! -f "$LLAMA_SERVER" ]; then
    echo "ERROR: llama-server not found at $LLAMA_SERVER"
    echo "Run ./setup_mac.sh first to compile."
    exit 1
fi

echo "[INFO] Launching coordinator..."
echo "  Worker: ${WORKER_IP}:${WORKER_PORT}"
echo "  Model: ${MODEL}"
echo "  Context: ${CONTEXT} tokens"
echo "  Split mode: ${SPLIT_MODE}"
echo "  Tensor split: ${TENSOR_SPLIT}"
echo "  KV cache: q8_0 (8-bit quantized)"
echo "  Micro-batch: ${MICRO_BATCH}"
echo "  GPU layers (-ngl): ${NGI}"
echo ""

exec "$LLAMA_SERVER" \
    --model "$MODEL" \
    --host 0.0.0.0 \
    --port 8080 \
    --ncmd 1 \
    --ntp 1 \
    --cb-type-verbose 1 \
    --verbose-eval \
    --metrics \
    --flash-attn \
    --split-mode "$SPLIT_MODE" \
    --tensor-split "$TENSOR_SPLIT" \
    --cache-type-k q8_0 \
    --cache-type-v q8_0 \
    --ngl $NGI \
    --threads $THREADS \
    --threads-batch $THREADS \
    --ctx-size $CONTEXT \
    --batch-size $MICRO_BATCH \
    --ubatch-size $MICRO_BATCH \
    --log-disable \
    --verbose
