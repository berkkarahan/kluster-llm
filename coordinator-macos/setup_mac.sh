#!/bin/bash
# setup_mac.sh - Legacy helper; prefer: python scripts/install.py
set -euo pipefail

echo "=== Kluster LLM: macOS Coordinator Setup ==="
echo "Delegating to scripts/install.py (Metal + full stack)…"
echo ""

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 "$ROOT/scripts/install.py" --backend metal
