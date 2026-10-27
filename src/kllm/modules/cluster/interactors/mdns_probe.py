"""Dependency-free mDNS master browse for helper interpreters.

Run as ``python -m kllm.modules.cluster.interactors.mdns_probe`` with only the
package root on ``PYTHONPATH``. It imports nothing outside the standard library
so the platform interpreter (for example macOS ``/usr/bin/python3`` 3.9) can
browse even when the virtualenv has no ``zeroconf`` and macOS Local Network
privacy only exempts platform binaries.

The process prints exactly one JSON object on stdout::

    {"ok": true, "masters": [{"name", "host", "port", "key_id"}], "reason": null}

and exits 0 when a browse was actually sent. Transport failures print
``"ok": false`` with a machine-readable ``reason`` and exit 1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import struct
import sys
import time

MCAST_ADDR = "224.0.0.251"
MCAST_PORT = 5353
DEFAULT_SERVICE = "_kllm._tcp.local."
DEFAULT_TIMEOUT = 2.0

TYPE_A = 1
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_SRV = 33
CLASS_IN = 1
QU_BIT = 0x8000
MAX_PACKET = 9000


def join_key_id(key: str) -> str:
    """Non-secret fingerprint of a join key (mirrors the package helper)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _encode_name(name: str) -> bytes:
    out = bytearray()
    for label in name.rstrip(".").split("."):
        raw = label.encode("utf-8")
        if not raw or len(raw) > 63:
            raise ValueError("invalid mDNS name")
        out.append(len(raw))
        out.extend(raw)
    out.append(0)
    return bytes(out)


def _question(name: str, qtype: int) -> bytes:
    # The QU bit asks responders for a unicast reply on this ephemeral port,
    # which avoids competing for the shared 5353 socket used by the OS resolver.
    return _encode_name(name) + struct.pack("!HH", qtype, CLASS_IN | QU_BIT)


def _build_query(questions: list[tuple[str, int]]) -> bytes:
    header = struct.pack("!HHHHHH", 0, 0, len(questions), 0, 0, 0)
    return header + b"".join(_question(name, qtype) for name, qtype in questions)


def _read_name(data: bytes, offset: int) -> tuple[str, int]:
    """Decode a (possibly compressed) DNS name; returns name and next offset."""
    labels: list[str] = []
    end = offset
    jumped = False
    seen: set[int] = set()
    while True:
        if offset >= len(data):
            raise ValueError("truncated mDNS name")
        length = data[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise ValueError("truncated mDNS pointer")
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if not jumped:
                end = offset + 2
            jumped = True
            if pointer in seen or pointer >= len(data):
                raise ValueError("invalid mDNS pointer")
            seen.add(pointer)
            offset = pointer
            continue
        offset += 1
        if length == 0:
            if not jumped:
                end = offset
            break
        if offset + length > len(data):
            raise ValueError("truncated mDNS label")
        labels.append(data[offset : offset + length].decode("utf-8", "replace"))
        offset += length
    return (".".join(labels) + ".") if labels else ".", end


class BrowseState:
    def __init__(self, service: str = DEFAULT_SERVICE) -> None:
        self.service = service.lower().rstrip(".") + "."
        self.instances: set[str] = set()
        self.srv: dict[str, tuple[str, int]] = {}
        self.txt: dict[str, dict[str, str]] = {}
        self.addresses: dict[str, str] = {}

    def feed(self, data: bytes) -> None:
        if len(data) < 12:
            return
        _, _, questions, answers, authority, additional = struct.unpack("!HHHHHH", data[:12])
        offset = 12
        for _ in range(questions):
            _, offset = _read_name(data, offset)
            offset += 4
        for count in (answers, authority, additional):
            for _ in range(count):
                if offset >= len(data):
                    return
                try:
                    name, offset = _read_name(data, offset)
                    if offset + 10 > len(data):
                        return
                    rtype, _, _, rdlength = struct.unpack("!HHIH", data[offset : offset + 10])
                    offset += 10
                    rdata = offset
                    offset += rdlength
                    if offset > len(data):
                        return
                    self._record(name.lower(), rtype, data, rdata, rdlength)
                except (ValueError, OSError, UnicodeError):
                    return

    def _record(self, name: str, rtype: int, data: bytes, offset: int, length: int) -> None:
        if rtype == TYPE_PTR:
            target, _ = _read_name(data, offset)
            target = target.lower()
            if target.endswith(self.service):
                self.instances.add(target)
        elif rtype == TYPE_SRV:
            if length < 7:
                return
            port = struct.unpack("!H", data[offset + 4 : offset + 6])[0]
            target, _ = _read_name(data, offset + 6)
            self.srv[name] = (target.lower(), int(port))
        elif rtype == TYPE_TXT:
            props: dict[str, str] = {}
            cursor = offset
            end = offset + length
            while cursor < end:
                size = data[cursor]
                cursor += 1
                if size == 0:
                    break
                item = data[cursor : cursor + size].decode("utf-8", "replace")
                cursor += size
                key, sep, value = item.partition("=")
                if key:
                    props[key] = value if sep else ""
            self.txt[name] = props
        elif rtype == TYPE_A and length == 4:
            self.addresses[name] = socket.inet_ntoa(data[offset : offset + 4])

    def masters(self, key: str | None) -> list[dict[str, object]]:
        want = join_key_id(key) if key else None
        found: list[dict[str, object]] = []
        seen: set[tuple[str, int]] = set()
        for instance in sorted(self.instances):
            srv = self.srv.get(instance)
            if not srv:
                continue
            target, port = srv
            props = self.txt.get(instance, {})
            if props.get("role") != "master":
                continue
            host = self.addresses.get(target)
            if not host:
                continue
            kid = props.get("kid") or None
            if want is not None and kid != want:
                continue
            marker = (host, port)
            if marker in seen:
                continue
            seen.add(marker)
            found.append({"name": instance, "host": host, "port": port, "key_id": kid})
        return found


def _pending_questions(state: BrowseState, asked: set[tuple[str, int]]) -> list[tuple[str, int]]:
    questions: list[tuple[str, int]] = []
    for instance in state.instances:
        if instance not in state.srv:
            key = (instance, TYPE_SRV)
            if key not in asked:
                asked.add(key)
                questions.append(key)
        if instance not in state.txt:
            key = (instance, TYPE_TXT)
            if key not in asked:
                asked.add(key)
                questions.append(key)
        srv = state.srv.get(instance)
        if srv and state.txt.get(instance, {}).get("role") == "master":
            target = srv[0]
            key = (target, TYPE_A)
            if key not in asked and target not in state.addresses:
                asked.add(key)
                questions.append(key)
    return questions


def _send(sock: socket.socket, packet: bytes, interface: str | None) -> None:
    if interface:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))
    sock.sendto(packet, (MCAST_ADDR, MCAST_PORT))


def browse(timeout: float, key: str | None, service: str, interfaces: list[str]) -> dict[str, object]:
    state = BrowseState(service)
    asked: set[tuple[str, int]] = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    targets: list[str | None] = list(interfaces) if interfaces else [None]
    sent = 0
    last_error: OSError | None = None
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        sock.bind(("", 0))
        sock.settimeout(0.2)
        initial = _build_query([(service, TYPE_PTR)])
        for interface in targets:
            try:
                _send(sock, initial, interface)
                sent += 1
            except OSError as exc:
                last_error = exc
        if not sent:
            reason = "MDNS_BLOCKED" if last_error else "NO_INTERFACE"
            return {
                "ok": False,
                "masters": [],
                "reason": reason,
                "detail": str(last_error) if last_error else None,
            }

        deadline = time.monotonic() + max(0.05, float(timeout))
        while time.monotonic() < deadline:
            try:
                data, _ = sock.recvfrom(MAX_PACKET)
            except socket.timeout:
                continue
            except OSError:
                continue
            state.feed(data)
            follow_ups = _pending_questions(state, asked)
            if follow_ups:
                packet = _build_query(follow_ups)
                for interface in targets:
                    try:
                        _send(sock, packet, interface)
                    except OSError:
                        continue
    finally:
        sock.close()
    return {"ok": True, "masters": state.masters(key), "reason": None, "detail": None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Browse Kluster masters via mDNS")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--key", default="")
    parser.add_argument("--service", default=DEFAULT_SERVICE)
    parser.add_argument("--interface", action="append", default=[])
    args = parser.parse_args(argv)
    result = browse(
        timeout=args.timeout,
        key=args.key or None,
        service=args.service,
        interfaces=[item for item in args.interface if item],
    )
    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
