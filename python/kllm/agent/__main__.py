from __future__ import annotations

import argparse
import os

import uvicorn

from kllm import runtime
from kllm.state import DEFAULT_CONTROL_PORT, ensure_home


def main() -> None:
    ensure_home()
    parser = argparse.ArgumentParser(prog="kllm-agent")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_CONTROL_PORT)
    args = parser.parse_args()
    runtime.register_agent(os.getpid(), args.port)
    uvicorn.run(
        "kllm.agent:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
