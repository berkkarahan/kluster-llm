#!/usr/bin/env python3
"""Install the llama.cpp release pinned in ``llama-release`` on this machine.

Keeps master and worker RPC protocol versions in lockstep. Does not build
kllm, the dashboard, or WireGuard — use ``scripts/install.py`` for that.

Usage:
  python scripts/setup-llama.py
  python scripts/setup-llama.py --backend vulkan
  python scripts/setup-llama.py --check
  python scripts/setup-llama.py --force
  python scripts/setup-llama.py --from-source
  python scripts/setup-llama.py --tag b11026
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import urllib.error
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_INSTALL_PATH = _SCRIPTS / "install.py"


def _load_install():
    spec = importlib.util.spec_from_file_location("kllm_install_script", _INSTALL_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"error: cannot load {_INSTALL_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kllm_install_script"] = mod
    spec.loader.exec_module(mod)
    return mod


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Install the llama.cpp release pinned in llama-release.",
    )
    p.add_argument(
        "--backend",
        default="auto",
        choices=["auto", "metal", "cuda", "vulkan", "cpu"],
        help="llama.cpp accelerator backend (default: auto-detect)",
    )
    p.add_argument(
        "--tag",
        default=None,
        help="Override the tag in llama-release (e.g. b11026)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-download / rebuild even when the local stamp already matches",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Print pin vs installed stamp and exit 0 only when they match",
    )
    p.add_argument(
        "--from-source",
        action="store_true",
        help="Compile the pinned tag instead of using GitHub prebuilt binaries",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    install = _load_install()
    tag = install.read_pinned_release(args.tag)

    if args.check:
        status = install.llama_install_status(tag)
        print(f"pin:          {status['pin']}")
        print(f"installed:    {status['stamp'] or '(no stamp)'}")
        print(f"llama-server: {status['llama_server'] or 'MISSING'}")
        print(f"rpc-server:   {status['rpc_server'] or 'MISSING'}")
        if status["ok"]:
            print("status:       ok")
            return 0
        print("status:       mismatch - run python scripts/setup-llama.py")
        return 1

    print(f"Repo root: {install.ROOT}")
    print(f"Pin file:  {install.PIN_FILE} ({tag})")
    install.install_llama(
        args.backend,
        prefer_prebuilt=not args.from_source,
        force=args.force,
        tag=tag,
    )
    status = install.llama_install_status(tag)
    if not status["ok"]:
        raise SystemExit(
            f"error: llama.cpp {tag} is not installed (stamp={status['stamp']!r}). "
            "Retry with --force or --from-source."
        )
    print(f"llama.cpp {tag} ready.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as exc:
        print(f"\nerror: network failure: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except subprocess.CalledProcessError as exc:
        print(f"\nerror: command failed with exit code {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode) from None
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130) from None
