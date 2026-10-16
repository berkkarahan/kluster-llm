#!/usr/bin/env python3
"""Cross-platform build for kllm: Rust extension (maturin) + Svelte dashboard.

For a full install (llama-server, rpc-server, toolchains) use:

  python scripts/install.py

Usage:
  python scripts/build.py
  python scripts/build.py --skip-ui
  python scripts/build.py --skip-rust
  python scripts/build.py --release
"""

from __future__ import annotations

import sys
from pathlib import Path

# Reuse the full installer with llama/toolchain auto-install skipped so
# `build.py` stays a fast CLI+UI rebuild entrypoint.
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import install as kllm_install  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    kllm_install.parse_args(argv)
    # build.py never installs llama / system toolchains by default
    argv_full = list(argv or sys.argv[1:])
    extras = []
    if "--skip-llama" not in argv_full:
        extras.append("--skip-llama")
    if "--skip-toolchains" not in argv_full:
        extras.append("--skip-toolchains")
    return kllm_install.main([*argv_full, *extras])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130) from None
