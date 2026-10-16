"""Controlled policy replies; real private subprocess pipes for DirectClient tests."""
import json
from pathlib import Path
import struct
import sys
import time

config = Path(sys.argv[1])
grant = json.loads(config.read_text())
source, output = sys.stdin.buffer, sys.stdout.buffer
while True:
    header = source.read(4)
    if not header:
        break
    if len(header) != 4:
        raise ValueError("Partial request header")
    request = json.loads(source.read(struct.unpack("!I", header)[0]))
    with config.with_suffix(".requests").open("a") as log:
        log.write(json.dumps(request) + "\n")
    action = request["action"]
    if action == "status":
        value = {"ready": True, "grant": grant, "prima_direct_version": 1,
                 "prima_ports_version": 2, "prima_ports_watchdog_version": 1,
                 "prima_ports_recovery_version": 1, "prima_ports_owned_runs": [],
                 "prima_direct_active_scopes": []}
        override = config.with_suffix(".status")
        if override.exists():
            value = json.loads(override.read_text())
    elif action in {"delay", "partial"}:
        if action == "partial":
            response = json.dumps({"request_id": request["request_id"], "result": {"phase":"admitted","from":"old-request"}}).encode()
            output.write(struct.pack("!I", len(response)) + response[:2])
            output.flush()
            time.sleep(0.2)
            output.write(response[2:])
            output.flush()
            continue
        time.sleep(0.2)
        value = {"phase": "admitted", "from": "old-request"}
    elif action == "prima_ports_release":
        value = {"phase": "released"}
    elif action == "malformed":
        output.write(struct.pack("!I", 2) + b"[]")
        output.flush()
        continue
    else:
        value = {"ok": True}
    response = json.dumps({"request_id": request["request_id"], "result": value}).encode()
    output.write(struct.pack("!I", len(response)) + response)
    output.flush()
