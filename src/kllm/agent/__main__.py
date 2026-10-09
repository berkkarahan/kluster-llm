from __future__ import annotations

import argparse
import copy
import os
import sys

import uvicorn
from uvicorn.config import LOGGING_CONFIG

from kllm import runtime
from kllm.cluster_log import agent_log_path
from kllm.state import DEFAULT_CONTROL_PORT, ensure_home


def _log_config() -> dict:
    """Tee uvicorn logs to ~/.kllm/logs/agent.log when running in a terminal.

    CLI-spawned agents already redirect stdout/stderr to that file (non-TTY).
    """
    ensure_home()
    config = copy.deepcopy(LOGGING_CONFIG)
    if not sys.stderr.isatty():
        return config
    path = agent_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    config["handlers"]["kllm_file"] = {
        "class": "kllm.log_sink.PolicyFileHandler",
        "filename": str(path),
        "formatter": "default",
        "encoding": "utf-8",
    }
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = config.get("loggers", {}).get(name)
        if not logger:
            continue
        handlers = list(logger.get("handlers") or [])
        if "kllm_file" not in handlers:
            handlers.append("kllm_file")
            logger["handlers"] = handlers
    return config


def main() -> None:
    ensure_home()
    parser = argparse.ArgumentParser(prog="kllm-agent")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_CONTROL_PORT)
    args = parser.parse_args()
    runtime.register_agent(os.getpid(), args.port, os.getpid() if sys.stderr.isatty() else None)
    uvicorn.run(
        "kllm.agent:app",
        host=args.host,
        port=args.port,
        log_level="info",
        log_config=_log_config(),
    )


if __name__ == "__main__":
    main()
