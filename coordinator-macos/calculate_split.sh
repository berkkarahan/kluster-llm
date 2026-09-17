#!/bin/bash
# calculate_split.sh - Calculate optimal tensor-split ratio for distributed inference
# Usage: ./calculate_split.sh [--mac-memory <GB>] [--worker-vram <GB>] [--model-size <GB>]

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
MAC_MEMORY_GB=""
WORKER_VRAM_GB=""
MODEL_SIZE_GB=""
HEADROOM=0.80  # Keep 20% headroom on Mac

# ── Parse arguments ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mac-memory) MAC_MEMORY_GB="$2"; shift 2 ;;
        --worker-vram) WORKER_VRAM_GB="$2"; shift 2 ;;
        --model-size) MODEL_SIZE_GB="$2"; shift 2 ;;
        --headroom) HEADROOM="$2"; shift 2 ;;
        --help)
            echo "Usage: $0 [--mac-memory <GB>] [--worker-vram <GB>] [--model-size <GB>]"
            echo ""
            echo "  --mac-memory    Mac unified memory in GB (auto-detected if omitted)"
            echo "  --worker-vram   Windows GPU VRAM in GB (default: 8)"
            echo "  --model-size    Model size in GB (optional, for verification)"
            echo "  --headroom      Memory headroom ratio (default: 0.80)"
            exit 0
            ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
done

# ── Auto-detect Mac memory ──────────────────────────────────────────────────
if [ -z "$MAC_MEMORY_GB" ]; then
    MAC_MEMORY_GB=$(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%.0f", $1/1024/1024/1024}' || echo "24")
    echo "[INFO] Auto-detected Mac memory: ${MAC_MEMORY_GB} GB"
fi

# ── Default worker VRAM ──────────────────────────────────────────────────────
if [ -z "$WORKER_VRAM_GB" ]; then
    WORKER_VRAM_GB=8
    echo "[INFO] Using default worker VRAM: ${WORKER_VRAM_GB} GB"
fi

# ── Calculate budget ────────────────────────────────────────────────────────
MAC_BUDGET_GB=$(awk "BEGIN {printf \"%.2f\", $MAC_MEMORY_GB * $HEADROOM}")
TOTAL_BUDGET_GB=$(awk "BEGIN {printf \"%.2f\", $MAC_BUDGET_GB + $WORKER_VRAM_GB}")

echo ""
echo "=== Tensor Split Calculator ==="
echo ""
echo "Mac unified memory: ${MAC_MEMORY_GB} GB"
echo "Mac budget (${HEADROOM} headroom): ${MAC_BUDGET_GB} GB"
echo "Worker VRAM: ${WORKER_VRAM_GB} GB"
echo "Total budget: ${TOTAL_BUDGET_GB} GB"
echo ""

# ── Calculate split ratio ───────────────────────────────────────────────────
SPLIT_RATIO=$(awk "BEGIN {printf \"%.4f\", $MAC_BUDGET_GB / $TOTAL_BUDGET_GB}")
WORKER_RATIO=$(awk "BEGIN {printf \"%.4f\", 1 - $SPLIT_RATIO}")

echo "Calculated split ratio:"
echo "  Mac: ${SPLIT_RATIO} (${awk "BEGIN {printf \"%.1f\", $SPLIT_RATIO * 100}")%)"
echo "  Worker: ${WORKER_RATIO} (${awk "BEGIN {printf \"%.1f\", $WORKER_RATIO * 100}")%)"
echo ""

# ── Model size verification ─────────────────────────────────────────────────
if [ -n "$MODEL_SIZE_GB" ]; then
    echo "Model size: ${MODEL_SIZE_GB} GB"

    # Check if model fits in total budget
    FITS=$(awk "BEGIN {print ($MODEL_SIZE_GB <= $TOTAL_BUDGET_GB) ? 1 : 0}")

    if [ "$FITS" -eq 1 ]; then
        echo "✓ Model fits in total budget (${MODEL_SIZE_GB} GB ≤ ${TOTAL_BUDGET_GB} GB)"

        # Check if model fits on Mac side
        MAC_CAN_HOLD=$(awk "BEGIN {print ($MODEL_SIZE_GB * $SPLIT_RATIO <= $MAC_BUDGET_GB) ? 1 : 0}")
        if [ "$MAC_CAN_HOLD" -eq 1 ]; then
            echo "✓ Mac can hold its share (${awk "BEGIN {printf \"%.2f\", $MODEL_SIZE_GB * $SPLIT_RATIO}") GB ≤ ${MAC_BUDGET_GB} GB)"
        else
            echo "✗ Mac cannot hold its share - model too large for Mac budget"
        fi

        # Check if model fits on worker side
        WORKER_CAN_HOLD=$(awk "BEGIN {print ($MODEL_SIZE_GB * $WORKER_RATIO <= $WORKER_VRAM_GB) ? 1 : 0}")
        if [ "$WORKER_CAN_HOLD" -eq 1 ]; then
            echo "✓ Worker can hold its share (${awk "BEGIN {printf \"%.2f\", $MODEL_SIZE_GB * $WORKER_RATIO}") GB ≤ ${WORKER_VRAM_GB} GB)"
        else
            echo "✗ Worker cannot hold its share - model too large for worker VRAM"
            echo "  Note: Excess layers will spill to system RAM + CPU"
        fi
    else
        echo "✗ Model does NOT fit in total budget (${MODEL_SIZE_GB} GB > ${TOTAL_BUDGET_GB} GB)"
        echo "  Consider: smaller quantization, smaller model, or more VRAM"
    fi
fi

echo ""
echo "=== Usage ==="
echo ""
echo "Pass to coordinator:"
echo "  ./run_coordinator.sh --model model.gguf --worker-ip <ip> --tensor-split ${SPLIT_RATIO}"
echo ""
echo "Or use in run_coordinator.sh (auto-calculates if --tensor-split omitted)"
