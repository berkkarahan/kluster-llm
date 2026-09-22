"""Experimental MPI collectives for heterogeneous kluster-llm ranks.

Production inference stays on llama.cpp RPC with ``--split-mode layer``
(see docs/engine-decision.md). This module does not replace that path.

What moves on the network
    Token ids and activations only. GGML weight tensors stay in the owning
    rank's VRAM. Quantized weight types are rejected by the packer.

How unequal shards meet MPI
    ``MPI_Bcast`` and ``MPI_Allreduce`` require the same count on every rank.
    Variable column shards are packed into a fixed envelope (bcast) or placed
    into a dense buffer with zeros outside the owned span (allreduce sum).
    The MPI count is homogeneous. The non-zero span is weighted by capacity.

Where a real MPI library fits
    OpenMPI, MPICH, and MS-MPI do not share one communicator across macOS,
    Linux, and Windows. ``Mpi4pyTransport`` is for a single MPI implementation
    (typically a Linux subgroup). The mixed cluster uses the same counts over
    a private-LAN TCP star. Port 50053 is unauthenticated. Bind private IPv4
    only; never publish it.

Graph split
    Scores blend VRAM headroom, compute weight, and latency. Mixed backends
    (Metal / CUDA / ROCm / Vulkan / CPU) take pipeline layer ranges, because
    tensor splits across GGML backends drift. Tensor mode, with unequal
    column shards of every layer, is selected only when every rank reports
    the same backend.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import struct
import sys
from array import array
from dataclasses import dataclass
from typing import Sequence

DEFAULT_MPI_PORT = 50053
MAX_ACTIVATION_BYTES = 512 * 1024 * 1024
HEADER_BYTES = 64
MAGIC = b"KACT"
VERSION = 1

# Subset of ggml_type. Weight quant types are intentionally absent.
GGML_TYPE_F32 = 0
GGML_TYPE_I8 = 24
GGML_TYPE_I32 = 26
_TYPE_SIZE = {
    GGML_TYPE_F32: 4,
    GGML_TYPE_I8: 1,
    GGML_TYPE_I32: 4,
}

OP_BCAST = 1
OP_ALLREDUCE_SUM = 2
OP_ALLGATHER = 3
OP_HANDOFF = 4
OP_PLACEMENT = 5

FLAG_PADDED = 1
SPLIT_NONE = 0xFF

_HEADER = struct.Struct("<4sHBBIBBHHH11I")
_OS_LABEL = {
    "darwin": "macOS",
    "linux": "Linux",
    "windows": "Windows",
}


class MpiLayerError(ValueError):
    """Invalid plan, wire payload, or collective count."""


@dataclass(frozen=True)
class RankSpec:
    """One cluster process that will own a stationary weight slice."""

    rank: int
    node_id: str
    os_name: str
    backend: str
    vram_bytes: int
    compute_weight: float
    latency_ms: float
    host: str
    port: int = DEFAULT_MPI_PORT
    label: str = ""

    def display(self) -> str:
        if self.label:
            return self.label
        os_label = _OS_LABEL.get(self.os_name, self.os_name)
        return f"{os_label}/{self.backend}"


@dataclass(frozen=True)
class LayerSpan:
    rank: int
    start: int
    end: int

    @property
    def count(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class ColumnShard:
    rank: int
    offset: int
    length: int


@dataclass(frozen=True)
class StationaryWeight:
    """GGML weight kept on the node. There is no payload field to serialize."""

    name: str
    ggml_type: int
    ne: tuple[int, int, int, int]
    file_offset: int
    nbytes: int

    def meta(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ggml_type": self.ggml_type,
            "ne": list(self.ne),
            "file_offset": self.file_offset,
            "nbytes": self.nbytes,
        }


@dataclass(frozen=True)
class GraphSplit:
    mode: str
    reason: str
    n_layers: int
    hidden: int
    n_ff: int
    scores: tuple[float, ...]
    fractions: tuple[float, ...]
    layers: tuple[LayerSpan, ...]
    column_preview: tuple[ColumnShard, ...]
    columns_active: bool
    weight_elems: tuple[int, ...]
    ranks: tuple[RankSpec, ...]

    @property
    def n_ranks(self) -> int:
        return len(self.ranks)

    def column_lengths(self) -> tuple[int, ...]:
        return tuple(shard.length for shard in self.column_preview)


@dataclass(frozen=True)
class MpiCall:
    """One collective or point-to-point as the MPI library would see it."""

    primitive: str
    count_bytes: int
    root: int
    note: str
    nonzero_bytes: tuple[int, ...] = ()


@dataclass(frozen=True)
class GgmlView:
    """Activation or token tensor. ``data`` is the dense little-endian payload."""

    ggml_type: int
    ne: tuple[int, int, int, int]
    data: bytes
    op: int
    split_axis: int = SPLIT_NONE
    shard_offset: int = 0
    shard_length: int = 0
    rank: int = 0
    n_ranks: int = 1
    root: int = 0
    flags: int = 0
    chunk_index: int = 0
    chunk_count: int = 1


def assert_private_mpi_host(host: str) -> None:
    """Refuse any MPI/TCP bind that is not loopback or RFC1918/ULA-style IPv4 private.

    The collective carries raw activations and has no authentication. Reachability
    on a public address is the same failure mode as publishing rpc-server.
    """
    try:
        addr = ipaddress.ip_address(str(host).strip())
    except ValueError as exc:
        raise MpiLayerError(f"invalid MPI host {host!r}") from exc
    if addr.version != 4:
        raise MpiLayerError("MPI collectives bind IPv4 only")
    # CPython treats 0.0.0.0/8, link-local, and the limited broadcast as private.
    # Those addresses either publish the socket on every NIC or are not a LAN peer.
    if addr.is_unspecified or addr.is_multicast or addr.is_link_local or addr.is_reserved:
        raise MpiLayerError(
            f"refusing MPI bind on {host}; use a private LAN address (port {DEFAULT_MPI_PORT} stays unpublished)"
        )
    if addr.is_loopback or addr.is_private:
        return
    raise MpiLayerError(
        f"refusing MPI bind on {host}; use a private LAN address (port {DEFAULT_MPI_PORT} stays unpublished)"
    )


def demo_mixed_cluster() -> list[RankSpec]:
    """Theoretical 3-node cluster: macOS Metal, Ubuntu ROCm, Windows CUDA.

    Capacities differ on purpose so layer ranges and column previews are unequal.
    Hosts sit on the lab 192.168.10.0/24 link used by the rest of kllm.
    """
    specs = [
        RankSpec(
            rank=0,
            node_id="mac-master",
            os_name="darwin",
            backend="metal",
            vram_bytes=32 * 1024**3,
            compute_weight=1.0,
            latency_ms=0.4,
            host="192.168.10.1",
            label="macOS/Metal",
        ),
        RankSpec(
            rank=1,
            node_id="ubuntu-rocm",
            os_name="linux",
            backend="rocm",
            vram_bytes=16 * 1024**3,
            compute_weight=1.35,
            latency_ms=0.6,
            host="192.168.10.2",
            label="Ubuntu/ROCm",
        ),
        RankSpec(
            rank=2,
            node_id="windows-cuda",
            os_name="windows",
            backend="cuda",
            vram_bytes=12 * 1024**3,
            compute_weight=1.55,
            latency_ms=0.9,
            host="192.168.10.3",
            label="Windows/CUDA",
        ),
    ]
    for spec in specs:
        assert_private_mpi_host(spec.host)
    return specs


def ranks_from_members(members: Sequence[dict[str, object]]) -> list[RankSpec]:
    """Map cluster member dicts to MPI ranks. Master is rank 0, then workers.

    llama.cpp RPC device order is workers-then-master. Pipeline stage 0 has to
    sit on the process that holds the prompt, so this order is the reverse.
    """
    master: list[dict[str, object]] = []
    workers: list[dict[str, object]] = []
    for raw in members:
        if str(raw.get("role") or "") == "master":
            master.append(raw)
        else:
            workers.append(raw)
    ordered = [*master, *workers]
    if not ordered:
        raise MpiLayerError("at least one rank is required")
    specs: list[RankSpec] = []
    for rank, raw in enumerate(ordered):
        backend = str(raw.get("backend") or "cpu")
        os_name = str(raw.get("os_name") or raw.get("platform") or _os_hint(backend))
        vram = raw.get("vram_bytes")
        vram_bytes = int(vram) if isinstance(vram, (int, float)) and not isinstance(vram, bool) else 0
        if vram_bytes <= 0:
            ram = raw.get("available_ram_bytes") or raw.get("total_ram_bytes") or 0
            vram_bytes = int(ram)
        latency = raw.get("latency_ms")
        latency_ms = float(latency) if isinstance(latency, (int, float)) and not isinstance(latency, bool) else 0.0
        compute = raw.get("compute_weight")
        compute_weight = float(compute) if isinstance(compute, (int, float)) and not isinstance(compute, bool) else 1.0
        host = str(raw.get("host") or "127.0.0.1")
        specs.append(
            RankSpec(
                rank=rank,
                node_id=str(raw.get("node_id") or f"rank-{rank}"),
                os_name=os_name,
                backend=backend,
                vram_bytes=vram_bytes,
                compute_weight=compute_weight,
                latency_ms=latency_ms,
                host=host,
                port=int(raw.get("mpi_port") or DEFAULT_MPI_PORT),
                label=str(raw.get("label") or ""),
            )
        )
    return specs


def plan_graph_split(
    ranks: Sequence[RankSpec],
    *,
    n_layers: int,
    hidden: int,
    n_ff: int,
    parallelism: str = "auto",
    allow_unsafe_mixed_backend_tp: bool = False,
    headroom: float = 0.8,
    vram_weight: float = 0.6,
    latency_weight: float = 0.4,
) -> GraphSplit:
    """Assign unequal layer ranges or column shards from capacity scores.

    ``parallelism`` is ``auto``, ``layer``, or ``tensor``. ``auto`` uses tensor
    mode only when every backend string matches.
    """
    if not ranks:
        raise MpiLayerError("at least one rank is required")
    if n_layers < 1 or hidden < 1 or n_ff < 1:
        raise MpiLayerError("n_layers, hidden, and n_ff must be positive")
    if not 0.1 <= headroom <= 1.0:
        raise MpiLayerError("headroom must be between 0.1 and 1.0")
    mode_req = parallelism.strip().lower()
    if mode_req not in ("auto", "layer", "tensor"):
        raise MpiLayerError("parallelism must be auto, layer, or tensor")
    _check_rank_index(ranks)
    for spec in ranks:
        assert_private_mpi_host(spec.host)
        if spec.vram_bytes <= 0:
            raise MpiLayerError(f"rank {spec.rank} has no memory budget")
        if spec.compute_weight <= 0:
            raise MpiLayerError(f"rank {spec.rank} compute_weight must be positive")
        if spec.latency_ms < 0:
            raise MpiLayerError(f"rank {spec.rank} latency_ms must be >= 0")

    vw, lw = _normalize_weights(vram_weight, latency_weight)
    scores = tuple(_capacity_score(spec, headroom=headroom, vram_weight=vw, latency_weight=lw) for spec in ranks)
    total = sum(scores)
    fractions = tuple(score / total for score in scores)
    columns = _column_shards(n_ff, scores)
    backends = {spec.backend.strip().lower() for spec in ranks}
    mixed = len(backends) > 1
    mode, reason = _select_mode(
        mode_req,
        mixed=mixed,
        backends=backends,
        allow_unsafe=allow_unsafe_mixed_backend_tp,
    )
    if mode == "pipeline":
        if n_layers < len(ranks):
            raise MpiLayerError(
                f"pipeline split needs at least one layer per rank ({n_layers} layers, {len(ranks)} ranks)"
            )
        counts = largest_remainder(n_layers, scores)
        if any(count <= 0 for count in counts):
            raise MpiLayerError("capacity scores left a rank with zero layers")
        layers = _contiguous_spans(counts)
        active = False
    else:
        layers = tuple(LayerSpan(rank=spec.rank, start=0, end=n_layers) for spec in ranks)
        active = True
    weight_elems = tuple(
        _weight_elems(span, columns[span.rank], hidden=hidden, n_ff=n_ff, column_sharded=active)
        for span in layers
    )
    return GraphSplit(
        mode=mode,
        reason=reason,
        n_layers=n_layers,
        hidden=hidden,
        n_ff=n_ff,
        scores=scores,
        fractions=fractions,
        layers=layers,
        column_preview=columns,
        columns_active=active,
        weight_elems=weight_elems,
        ranks=tuple(ranks),
    )


def largest_remainder(total: int, weights: Sequence[float]) -> list[int]:
    """Integer shares of ``total`` proportional to ``weights``.

    Fractional parts are handed out largest-first, ties broken by rank index,
    so equal weights reproduce an even split when ``total`` divides evenly and
    a stable near-even split otherwise.
    """
    if total < 0:
        raise MpiLayerError("total must be >= 0")
    if not weights or any(weight <= 0 for weight in weights):
        raise MpiLayerError("weights must be positive")
    scale = sum(weights)
    raw = [total * weight / scale for weight in weights]
    floors = [int(value) for value in raw]
    leftover = total - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - floors[i]), i))
    for step in range(leftover):
        floors[order[step % len(order)]] += 1
    return floors


def render_flow(plan: GraphSplit, *, n_tokens: int = 1) -> str:
    """Text diagram of memory, tensor motion, and the private-LAN boundary.

    The figure is the graph-split visualization for this placement. Numbers
    come from ``plan`` so the document and the tests cannot drift apart.
    """
    if n_tokens < 1:
        raise MpiLayerError("n_tokens must be positive")
    act_bytes = _checked_mul(n_tokens, plan.hidden, 4)
    token_bytes = _checked_mul(n_tokens, 4)
    lines: list[str] = [
        f"graph-split mode={plan.mode} layers={plan.n_layers} hidden={plan.hidden} n_ff={plan.n_ff}",
        f"reason: {plan.reason}",
        (
            f"network boundary: private IPv4 only, TCP {DEFAULT_MPI_PORT}, "
            "no auth — do not publish (same posture as rpc-server :50052)"
        ),
        "weights: stationary GGML tensors; quantized bytes are not an MPI payload",
        "",
        "memory map before the first token",
    ]
    for spec in plan.ranks:
        span = plan.layers[spec.rank]
        shard = plan.column_preview[spec.rank]
        lines.append(f"  [{spec.rank}] {spec.display()} {spec.node_id} @ {spec.host}:{spec.port}")
        lines.append(
            f"      score={plan.scores[spec.rank]:.6e} fraction={plan.fractions[spec.rank]:.4f} "
            f"vram={spec.vram_bytes} compute={spec.compute_weight} latency_ms={spec.latency_ms}"
        )
        lines.append(f"      layers [{span.start}, {span.end}) resident on device")
        lines.append(
            f"      column shard offset={shard.offset} length={shard.length} "
            f"active={str(plan.columns_active).lower()}"
        )
        lines.append(
            f"      weight_elems~{plan.weight_elems[spec.rank]} "
            f"activation_scratch={act_bytes} bytes (empty until step 2)"
        )
        lines.append("      wire_weight_bytes=0")
    lines.extend(
        [
            "",
            "step 0  placement metadata",
            "  memory: GGUF slices stay mapped; activation scratch is empty",
            "  network: MPI_Bcast of the placement JSON (spans, column offsets, weight_bytes_on_wire=0)",
            "  boundary: rank 0 -> every worker, inside the private subnet",
            "",
            "step 1  token broadcast",
            f"  memory: I32 token ids replicated on every rank ({token_bytes} bytes)",
            f"  network: MPI_Bcast count={token_bytes} root=0",
            "  boundary: equal count; tokens are not capacity-weighted",
            "  weights: untouched",
        ]
    )
    if plan.mode == "pipeline":
        lines.extend(_pipeline_steps(plan, n_tokens=n_tokens, act_bytes=act_bytes))
    else:
        lines.extend(_tensor_steps(plan, n_tokens=n_tokens, act_bytes=act_bytes))
    lines.extend(_reduction_reference(plan, n_tokens=n_tokens))
    return "\n".join(lines) + "\n"


def placement_message(plan: GraphSplit) -> bytes:
    """Small JSON manifest. Weight file bytes are not included."""
    body = {
        "mode": plan.mode,
        "weights": "stationary",
        "ranks": [
            {
                "rank": spec.rank,
                "node_id": spec.node_id,
                "backend": spec.backend,
                "layers": [plan.layers[spec.rank].start, plan.layers[spec.rank].end],
                "column_offset": plan.column_preview[spec.rank].offset,
                "column_length": plan.column_preview[spec.rank].length if plan.columns_active else plan.n_ff,
                "weight_bytes_on_wire": 0,
            }
            for spec in plan.ranks
        ],
    }
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return pack_message(
        op=OP_PLACEMENT,
        ggml_type=GGML_TYPE_I8,
        ne=(len(raw), 1, 1, 1),
        data=raw,
        rank=0,
        n_ranks=plan.n_ranks,
        root=0,
    )


def pack_message(
    *,
    op: int,
    ggml_type: int,
    ne: tuple[int, int, int, int],
    data: bytes,
    rank: int = 0,
    n_ranks: int = 1,
    root: int = 0,
    split_axis: int = SPLIT_NONE,
    shard_offset: int = 0,
    shard_length: int = 0,
    flags: int = 0,
    chunk_index: int = 0,
    chunk_count: int = 1,
    mpi_count: int | None = None,
) -> bytes:
    """Serialize one activation, token vector, or placement blob.

    ``mpi_count`` is the byte count a later MPI call must use. It defaults to
    the logical payload size. Callers that pad set it explicitly and raise
    ``FLAG_PADDED``.
    """
    if ggml_type not in _TYPE_SIZE:
        raise MpiLayerError(
            "wire format accepts F32 activations, I32 tokens, and I8 placement metadata; "
            "GGML weight types stay on the node"
        )
    if len(ne) != 4 or any(dim < 0 for dim in ne):
        raise MpiLayerError("ne must be four non-negative dimensions")
    expect = _element_count(ne) * _TYPE_SIZE[ggml_type]
    if len(data) != expect:
        raise MpiLayerError(f"payload is {len(data)} bytes, ggml ne requires {expect}")
    if len(data) > MAX_ACTIVATION_BYTES:
        raise MpiLayerError(f"payload {len(data)} exceeds {MAX_ACTIVATION_BYTES} byte activation cap")
    count = len(data) if mpi_count is None else mpi_count
    if count < len(data):
        raise MpiLayerError("mpi_count shorter than logical payload")
    if count > MAX_ACTIVATION_BYTES:
        raise MpiLayerError("mpi_count exceeds activation cap")
    if count != len(data):
        flags |= FLAG_PADDED
    header = _HEADER.pack(
        MAGIC,
        VERSION,
        op,
        flags,
        ggml_type,
        _n_dims(ne),
        split_axis & 0xFF,
        rank,
        n_ranks,
        root,
        ne[0],
        ne[1],
        ne[2],
        ne[3],
        shard_offset,
        shard_length,
        len(data),
        count,
        chunk_index,
        chunk_count,
        0,
    )
    if len(header) != HEADER_BYTES:
        raise MpiLayerError("header is not 64 bytes")
    return header + data


def unpack_message(blob: bytes) -> GgmlView:
    if len(blob) < HEADER_BYTES:
        raise MpiLayerError("truncated MPI frame")
    (
        magic,
        version,
        op,
        flags,
        ggml_type,
        n_dims,
        split_axis,
        rank,
        n_ranks,
        root,
        ne0,
        ne1,
        ne2,
        ne3,
        shard_offset,
        shard_length,
        nbytes,
        mpi_count,
        chunk_index,
        chunk_count,
        _reserved,
    ) = _HEADER.unpack(blob[:HEADER_BYTES])
    del n_dims, _reserved
    if magic != MAGIC or version != VERSION:
        raise MpiLayerError("unrecognized kllm activation frame")
    if ggml_type not in _TYPE_SIZE:
        raise MpiLayerError("refusing a non-activation ggml type on the wire")
    if nbytes > MAX_ACTIVATION_BYTES or mpi_count > MAX_ACTIVATION_BYTES:
        raise MpiLayerError("frame declares an oversized activation")
    if len(blob) != HEADER_BYTES + nbytes:
        raise MpiLayerError("frame length does not match logical nbytes")
    if mpi_count < nbytes:
        raise MpiLayerError("mpi_count shorter than logical payload")
    return GgmlView(
        ggml_type=ggml_type,
        ne=(ne0, ne1, ne2, ne3),
        data=blob[HEADER_BYTES:],
        op=op,
        split_axis=split_axis,
        shard_offset=shard_offset,
        shard_length=shard_length,
        rank=rank,
        n_ranks=n_ranks,
        root=root,
        flags=flags,
        chunk_index=chunk_index,
        chunk_count=chunk_count,
    )


def tokens_message(tokens: Sequence[int], *, n_ranks: int, root: int = 0) -> bytes:
    data = struct.pack("<" + "i" * len(tokens), *[int(tok) for tok in tokens]) if tokens else b""
    return pack_message(
        op=OP_BCAST,
        ggml_type=GGML_TYPE_I32,
        ne=(len(tokens), 1, 1, 1),
        data=data,
        n_ranks=n_ranks,
        root=root,
        mpi_count=len(data),
    )


def activation_message(
    values: Sequence[float],
    *,
    hidden: int,
    n_tokens: int,
    n_ranks: int,
    rank: int,
    root: int = 0,
    op: int = OP_HANDOFF,
    shard_offset: int = 0,
    shard_length: int | None = None,
) -> bytes:
    if hidden < 1 or n_tokens < 1:
        raise MpiLayerError("activation shape must be positive")
    if len(values) != hidden * n_tokens:
        raise MpiLayerError("activation length is not hidden * n_tokens")
    length = hidden if shard_length is None else shard_length
    return pack_message(
        op=op,
        ggml_type=GGML_TYPE_F32,
        ne=(hidden, n_tokens, 1, 1),
        data=f32_bytes(values),
        rank=rank,
        n_ranks=n_ranks,
        root=root,
        split_axis=0 if shard_length is not None else SPLIT_NONE,
        shard_offset=shard_offset,
        shard_length=length,
    )


def f32_bytes(values: Sequence[float]) -> bytes:
    buf = array("f", (float(v) for v in values))
    if sys.byteorder != "little":
        buf.byteswap()
    return buf.tobytes()


def f32_values(data: bytes) -> list[float]:
    if len(data) % 4 != 0:
        raise MpiLayerError("f32 payload is not a multiple of 4")
    buf = array("f")
    buf.frombytes(data)
    if sys.byteorder != "little":
        buf.byteswap()
    return list(buf)


def build_bcast_envelope(shards: Sequence[bytes]) -> bytes:
    """Pack unequal payloads into one buffer whose length every rank can compute.

    Slot layout, repeated ``n_ranks`` times::

        u32 logical_len | payload | zero pad out to max(len(shard))

    ``MPI_Bcast`` is then called with ``count=len(envelope)`` on every rank.
    A rank keeps only its slot. Padding exists so the count matches.
    """
    if not shards:
        raise MpiLayerError("bcast envelope needs one slot per rank")
    max_len = max(len(shard) for shard in shards)
    if 4 + max_len > MAX_ACTIVATION_BYTES:
        raise MpiLayerError("bcast slot exceeds activation cap")
    stride = 4 + max_len
    total = len(shards) * stride
    if total > MAX_ACTIVATION_BYTES:
        raise MpiLayerError("bcast envelope exceeds activation cap")
    buf = bytearray(total)
    for index, shard in enumerate(shards):
        base = index * stride
        struct.pack_into("<I", buf, base, len(shard))
        buf[base + 4 : base + 4 + len(shard)] = shard
    return bytes(buf)


def bcast_envelope_count(shard_nbytes: Sequence[int], n_ranks: int) -> tuple[int, int]:
    """``(mpi_count, stride)`` known on every rank before ``MPI_Bcast``.

    Shard byte sizes come from the graph split (column length × tokens × 4),
    so workers allocate the envelope without having seen the payload.
    """
    if n_ranks < 1 or len(shard_nbytes) != n_ranks:
        raise MpiLayerError("shard byte sizes must cover every rank")
    if any(size < 0 for size in shard_nbytes):
        raise MpiLayerError("negative shard size")
    max_len = max(shard_nbytes) if shard_nbytes else 0
    stride = 4 + max_len
    total = n_ranks * stride
    if total > MAX_ACTIVATION_BYTES:
        raise MpiLayerError("bcast envelope exceeds activation cap")
    return total, stride


def take_bcast_slot(buffer: bytes, rank: int, n_ranks: int) -> bytes:
    if n_ranks < 1:
        raise MpiLayerError("n_ranks must be positive")
    if len(buffer) % n_ranks != 0:
        raise MpiLayerError("MPI_Bcast buffer is not divisible by n_ranks")
    stride = len(buffer) // n_ranks
    if stride < 4:
        raise MpiLayerError("bcast slot missing its length prefix")
    if not 0 <= rank < n_ranks:
        raise MpiLayerError("rank outside communicator")
    base = rank * stride
    logical = struct.unpack_from("<I", buffer, base)[0]
    if logical > stride - 4:
        raise MpiLayerError("logical shard longer than its padded slot")
    return buffer[base + 4 : base + 4 + logical]


def slice_hidden(rows: Sequence[Sequence[float]], offset: int, length: int) -> list[float]:
    """Pack ``rows[t][offset:offset+length]`` densely (GGML ne[0] is contiguous)."""
    if length < 0 or offset < 0:
        raise MpiLayerError("slice origin must be >= 0")
    packed: list[float] = []
    for row in rows:
        if offset + length > len(row):
            raise MpiLayerError("column slice exceeds hidden width")
        packed.extend(row[offset : offset + length])
    return packed


def weighted_bcast_slots(shards: Sequence[bytes]) -> tuple[list[bytes], MpiCall]:
    """Root builds the envelope; every rank would ``MPI_Bcast`` that one count.

    Returns the logical shard each rank keeps after stripping padding.
    """
    envelope = build_bcast_envelope(shards)
    recovered = [take_bcast_slot(envelope, rank, len(shards)) for rank in range(len(shards))]
    if recovered != [bytes(shard) for shard in shards]:
        raise MpiLayerError("bcast slot round-trip failed")
    nonzero = tuple(len(shard) for shard in shards)
    call = MpiCall(
        primitive="MPI_Bcast",
        count_bytes=len(envelope),
        root=0,
        note="padded slots; each rank reads its own logical length",
        nonzero_bytes=nonzero,
    )
    return recovered, call


def expand_shard(offset: int, values: Sequence[float], full_elems: int) -> list[float]:
    """Place a weighted span into the dense buffer ``MPI_Allreduce`` will sum."""
    if offset < 0 or full_elems < 0:
        raise MpiLayerError("negative allreduce shape")
    if offset + len(values) > full_elems:
        raise MpiLayerError("shard extends past the allreduce buffer")
    buf = [0.0] * full_elems
    for index, value in enumerate(values):
        buf[offset + index] = float(value)
    return buf


def mpi_allreduce_sum(buffers: Sequence[Sequence[float]]) -> tuple[list[float], MpiCall]:
    """Sum equal-length f32 buffers. This is the count passed to ``MPI_Allreduce``.

    Callers pad or expand first. Unequal lengths are an error: that mismatch
    would be undefined in MPI, and it is the wrong op for a raw column shard.
    """
    if not buffers:
        raise MpiLayerError("allreduce needs a contribution from every rank")
    width = len(buffers[0])
    if any(len(buf) != width for buf in buffers):
        raise MpiLayerError(
            "MPI_Allreduce count differs across ranks; expand weighted shards onto one width first"
        )
    acc = [0.0] * width
    for buf in buffers:
        for index, value in enumerate(buf):
            acc[index] += float(value)
    nonzero = tuple(sum(1 for value in buf if value != 0.0) * 4 for buf in buffers)
    call = MpiCall(
        primitive="MPI_Allreduce",
        count_bytes=width * 4,
        root=-1,
        note="MPI_SUM on f32; identical count; zeros hold non-owned spans",
        nonzero_bytes=nonzero,
    )
    return acc, call


def allreduce_chunked(
    buffers: Sequence[Sequence[float]],
    *,
    chunk_elems: int,
) -> tuple[list[float], list[MpiCall]]:
    """Same sum as ``mpi_allreduce_sum``, issued as equal-count chunks.

    Every rank, including the tail chunk, uses ``chunk_elems`` as the MPI
    count. The tail is zero-padded and the pad is discarded after the sum.
    A slow link therefore bounds scratch memory without changing the result.
    """
    if chunk_elems < 1:
        raise MpiLayerError("chunk_elems must be positive")
    if not buffers:
        raise MpiLayerError("allreduce needs a contribution from every rank")
    width = len(buffers[0])
    if any(len(buf) != width for buf in buffers):
        raise MpiLayerError("chunked allreduce requires equal logical widths")
    acc = [0.0] * width
    calls: list[MpiCall] = []
    chunk_count = (width + chunk_elems - 1) // chunk_elems if width else 1
    for chunk_index in range(chunk_count):
        start = chunk_index * chunk_elems
        end = min(width, start + chunk_elems)
        parts = [list(buf[start:end]) for buf in buffers]
        padded = [part + [0.0] * (chunk_elems - len(part)) for part in parts]
        reduced, call = mpi_allreduce_sum(padded)
        acc[start:end] = reduced[: end - start]
        calls.append(
            MpiCall(
                primitive="MPI_Allreduce",
                count_bytes=chunk_elems * 4,
                root=-1,
                note=f"chunk {chunk_index + 1}/{chunk_count} logical_elems={end - start}",
                nonzero_bytes=call.nonzero_bytes,
            )
        )
    return acc, calls


def weighted_allreduce(
    spans: Sequence[tuple[int, Sequence[float]]],
    *,
    full_elems: int,
) -> tuple[list[float], MpiCall]:
    """Allreduce of heterogeneous spans.

    Each ``(offset, values)`` is copied into a dense ``full_elems`` buffer.
    Disjoint spans reconstruct a concatenation (column-parallel allgather via
    sum). Identical full-width spans add (row-parallel partials). Overlapping
    partial spans are rejected when their offsets differ; identical offsets
    with equal lengths are treated as partials and summed.
    """
    if full_elems < 1:
        raise MpiLayerError("full_elems must be positive")
    if len(spans) < 1:
        raise MpiLayerError("weighted allreduce needs every rank's span")
    _validate_spans(spans, full_elems)
    dense = [expand_shard(offset, values, full_elems) for offset, values in spans]
    return mpi_allreduce_sum(dense)


def simulate_pipeline(
    plan: GraphSplit,
    tokens: Sequence[int],
    transform,
) -> tuple[list[float], list[MpiCall]]:
    """Drive one decode step across pipeline stages.

    ``transform(rank, incoming, tokens) -> activation`` runs locally. The only
    network ops are the token bcast, a point-to-point activation handoff at
    each layer boundary, and a return send to rank 0. Allreduce is not called:
    partial GEMMs never leave their backend.
    """
    if plan.mode != "pipeline":
        raise MpiLayerError("pipeline exchange requires a layer placement")
    calls: list[MpiCall] = []
    token_blob = tokens_message(tokens, n_ranks=plan.n_ranks, root=0)
    calls.append(
        MpiCall(
            primitive="MPI_Bcast",
            count_bytes=len(token_blob) - HEADER_BYTES,
            root=0,
            note="I32 token ids, equal count",
            nonzero_bytes=(len(tokens) * 4,),
        )
    )
    place = placement_message(plan)
    if b'"weight_bytes_on_wire":0' not in place:
        raise MpiLayerError("placement manifest leaked a weight payload")
    incoming: list[float] | None = None
    outgoing: list[float] = []
    for span in plan.layers:
        outgoing = [float(v) for v in transform(span.rank, incoming, list(tokens))]
        expected = plan.hidden
        if len(outgoing) != expected:
            raise MpiLayerError(
                f"rank {span.rank} activation length {len(outgoing)} != hidden {expected}"
            )
        incoming = outgoing
        if span.rank + 1 < plan.n_ranks:
            blob = activation_message(
                outgoing,
                hidden=plan.hidden,
                n_tokens=1,
                n_ranks=plan.n_ranks,
                rank=span.rank,
                op=OP_HANDOFF,
            )
            calls.append(
                MpiCall(
                    primitive="MPI_Send",
                    count_bytes=len(blob) - HEADER_BYTES,
                    root=span.rank,
                    note=f"full hidden activation rank {span.rank} -> {span.rank + 1}",
                    nonzero_bytes=(plan.hidden * 4,),
                )
            )
    if plan.n_ranks > 1:
        calls.append(
            MpiCall(
                primitive="MPI_Send",
                count_bytes=plan.hidden * 4,
                root=plan.n_ranks - 1,
                note="final activation returns to rank 0 for sampling",
                nonzero_bytes=(plan.hidden * 4,),
            )
        )
    return outgoing, calls


def simulate_tensor_block(
    plan: GraphSplit,
    rows: Sequence[Sequence[float]],
    column_fn,
    row_fn,
) -> tuple[list[float], list[float], list[MpiCall]]:
    """One transformer block under tensor mode: weighted bcast, then both reductions.

    ``column_fn(rank, local_rows) -> f32 span`` produces that rank's FFN columns.
    ``row_fn(rank, full_ffn_row_prefix) -> f32 partial of length hidden`` produces
    the row-parallel partial. Both reductions go through ``MPI_Allreduce`` with
    one count per call.
    """
    if not plan.columns_active:
        raise MpiLayerError("tensor collectives are not scheduled on a mixed-backend pipeline split")
    if len(rows) < 1:
        raise MpiLayerError("tensor block needs at least one token row")
    calls: list[MpiCall] = []
    flat = [value for row in rows for value in row]
    # Column-parallel GEMM needs the full hidden state on every rank. The envelope
    # still goes through the weighted bcast wrapper; these slots happen to match.
    _replicated, input_bcast = weighted_bcast_slots([f32_bytes(flat) for _ in plan.ranks])
    calls.append(input_bcast)

    packed_cols: list[bytes] = []
    placed: list[tuple[int, list[float]]] = []
    n_tokens = len(rows)
    for shard in plan.column_preview:
        produced = [float(v) for v in column_fn(shard.rank, [list(row) for row in rows])]
        if len(produced) != shard.length * n_tokens:
            raise MpiLayerError(
                f"rank {shard.rank} column span length {len(produced)} != {shard.length * n_tokens}"
            )
        packed_cols.append(f32_bytes(produced))
        placed.append((0, _place_column_shard(plan, shard, produced, n_tokens)))
    _slots, column_bcast = weighted_bcast_slots(packed_cols)
    calls.append(column_bcast)
    reduced_ffn, reduce_cols = weighted_allreduce(placed, full_elems=plan.n_ff * n_tokens)
    calls.append(reduce_cols)

    partials: list[tuple[int, list[float]]] = []
    for spec in plan.ranks:
        partial = [float(v) for v in row_fn(spec.rank, reduced_ffn)]
        if len(partial) != plan.hidden * len(rows):
            raise MpiLayerError("row-parallel partial must cover hidden * n_tokens")
        partials.append((0, partial))
    reduced_hidden, reduce_rows = weighted_allreduce(partials, full_elems=plan.hidden * len(rows))
    calls.append(reduce_rows)
    return reduced_ffn, reduced_hidden, calls


def send_frame(sock: socket.socket, payload: bytes) -> None:
    if len(payload) > MAX_ACTIVATION_BYTES:
        raise MpiLayerError("frame exceeds activation cap")
    sock.sendall(struct.pack("<I", len(payload)) + payload)


def recv_frame(sock: socket.socket) -> bytes:
    raw = _recvall(sock, 4)
    (size,) = struct.unpack("<I", raw)
    if size > MAX_ACTIVATION_BYTES:
        raise MpiLayerError("peer advertised an oversized frame")
    return _recvall(sock, size)


class TcpStar:
    """Private-LAN stand-in for one ``MPI_Bcast`` / ``MPI_Allreduce`` round.

    Root listens. Each other rank connects, sends a hello rank id, and then
    exchanges framed envelopes. This is the mixed-OS path: the counts match
    the MPI wrappers, without requiring one MPI library on every OS.
    """

    def __init__(self, host: str, port: int, n_ranks: int) -> None:
        assert_private_mpi_host(host)
        if n_ranks < 2:
            raise MpiLayerError("TCP star needs a peer")
        self.host = host
        self.port = port
        self.n_ranks = n_ranks
        self._server: socket.socket | None = None
        self._peers: dict[int, socket.socket] = {}

    def start(self) -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(self.n_ranks - 1)
        sock.settimeout(5.0)
        self._server = sock
        self.port = int(sock.getsockname()[1])
        return self.port

    def accept_peers(self) -> None:
        if self._server is None:
            raise MpiLayerError("TCP star is not listening")
        while len(self._peers) < self.n_ranks - 1:
            conn, _addr = self._server.accept()
            conn.settimeout(5.0)
            (rank,) = struct.unpack("<H", _recvall(conn, 2))
            if not 1 <= rank < self.n_ranks or rank in self._peers:
                conn.close()
                raise MpiLayerError(f"unexpected peer rank {rank}")
            self._peers[rank] = conn

    def connect(self, rank: int) -> socket.socket:
        if not 1 <= rank < self.n_ranks:
            raise MpiLayerError("workers connect with rank >= 1")
        sock = socket.create_connection((self.host, self.port), timeout=5.0)
        sock.settimeout(5.0)
        sock.sendall(struct.pack("<H", rank))
        return sock

    def bcast_from_root(self, envelope: bytes) -> None:
        for conn in self._peers.values():
            send_frame(conn, envelope)

    def close(self) -> None:
        for conn in self._peers.values():
            conn.close()
        self._peers.clear()
        if self._server is not None:
            self._server.close()
            self._server = None


class Mpi4pyTransport:
    """Optional backend when this process is already inside one MPI communicator.

    Buffers passed in must already be equal-count envelopes or dense allreduce
    inputs. Import is deferred so clusters without mpi4py keep working.
    """

    def bcast(self, buffer: bytes, root: int) -> bytes:
        from mpi4py import MPI

        comm = MPI.COMM_WORLD
        payload = bytearray(buffer if comm.Get_rank() == root else bytes(len(buffer)))
        comm.Bcast([payload, MPI.BYTE], root=root)
        return bytes(payload)

    def allreduce_sum_f32(self, buffer: bytes) -> bytes:
        from mpi4py import MPI

        src = array("f")
        src.frombytes(buffer)
        if sys.byteorder != "little":
            src.byteswap()
        dst = array("f", [0.0] * len(src))
        MPI.COMM_WORLD.Allreduce([src, MPI.FLOAT], [dst, MPI.FLOAT], op=MPI.SUM)
        if sys.byteorder != "little":
            dst.byteswap()
        return dst.tobytes()


def _place_column_shard(
    plan: GraphSplit,
    shard: ColumnShard,
    produced: Sequence[float],
    n_tokens: int,
) -> list[float]:
    """Scatter one rank's columns into the dense buffer the allreduce sums.

    Token ``t`` owns ``[t * n_ff, (t + 1) * n_ff)``. The rank writes only
    ``[offset, offset + length)`` inside that window and leaves zeros elsewhere.
    """
    dense = [0.0] * (plan.n_ff * n_tokens)
    for token in range(n_tokens):
        piece = produced[token * shard.length : (token + 1) * shard.length]
        base = token * plan.n_ff + shard.offset
        dense[base : base + shard.length] = list(piece)
    return dense


def _pipeline_steps(plan: GraphSplit, *, n_tokens: int, act_bytes: int) -> list[str]:
    del n_tokens
    lines = ["", "step 2  pipeline handoff (mixed backends)"]
    lines.append("  memory: rank 0 embeds tokens into f32 hidden; later ranks still hold weights only")
    first = plan.layers[0]
    lines.append(
        f"  local compute: layers [{first.start}, {first.end}) stay on {plan.ranks[0].display()}"
    )
    for span in plan.layers[:-1]:
        nxt = plan.layers[span.rank + 1]
        src = plan.ranks[span.rank]
        dst = plan.ranks[nxt.rank]
        lines.append(
            f"  network: MPI_Send activation {act_bytes} bytes  {src.host} -> {dst.host}  "
            f"after layer {span.end - 1}"
        )
        lines.append(
            f"  memory: {src.display()} forwards the hidden state; {dst.display()} "
            f"receives it and runs layers [{nxt.start}, {nxt.end})"
        )
        lines.append("  weights: both devices keep their own slices; the packet is the activation")
        lines.append(f"  boundary: private link {src.host} -> {dst.host}:{dst.port}")
    lines.append(
        f"  network: MPI_Send final activation {act_bytes} bytes back to {plan.ranks[0].host} for sampling"
    )
    lines.append("  allreduce: idle — partial GEMMs are not split across Metal/ROCm/CUDA")
    return lines


def _tensor_steps(plan: GraphSplit, *, n_tokens: int, act_bytes: int) -> list[str]:
    lengths = plan.column_lengths()
    max_len = max(lengths)
    slot_payload = max_len * n_tokens * 4
    mpi_count, stride = bcast_envelope_count([length * n_tokens * 4 for length in lengths], plan.n_ranks)
    full_ffn = plan.n_ff * n_tokens * 4
    lines = [
        "",
        "step 2  tensor block on one backend",
        f"  memory: every rank holds layers [0, {plan.n_layers}) but only its column shard of n_ff",
        f"  network: MPI_Bcast replicated activation count={act_bytes} (equal; column GEMM needs full X)",
        f"  then weighted output slots lengths={lengths}",
        f"  MPI_Bcast envelope mpi_count={mpi_count} stride={stride} max_slot_payload={slot_payload}",
        "  boundary: private LAN; padding bytes are not model weights",
        f"  network: MPI_Allreduce SUM count={full_ffn} reconstructs FFN columns (zeros off-span)",
        f"  network: MPI_Allreduce SUM count={act_bytes} adds row-parallel partials",
        "  weights: still stationary",
    ]
    return lines


def _reduction_reference(plan: GraphSplit, *, n_tokens: int) -> list[str]:
    lengths = plan.column_lengths()
    mpi_count, stride = bcast_envelope_count(
        [length * n_tokens * 4 for length in lengths],
        plan.n_ranks,
    )
    full = plan.n_ff * n_tokens * 4
    scheduled = "yes" if plan.columns_active else "no"
    return [
        "",
        "reduction loop reference (same capacity scores, column axis)",
        f"  scheduled on this split: {scheduled}",
        f"  unequal column lengths: {lengths}",
        f"  MPI_Bcast envelope: n_ranks={plan.n_ranks} stride={stride} mpi_count={mpi_count}",
        f"  MPI_Allreduce SUM: mpi_count={full} bytes over n_ff*n_tokens f32 elements",
        "  non-zero span per rank: column_length * n_tokens * 4; the rest of the buffer is zero",
        "  a homogeneous MPI_Allreduce(total/n_ranks) would be wrong for these lengths",
    ]


def _validate_spans(spans: Sequence[tuple[int, Sequence[float]]], full_elems: int) -> None:
    covered = [0] * full_elems
    partial = True
    for offset, values in spans:
        if offset != 0 or len(values) != full_elems:
            partial = False
        if offset < 0 or offset + len(values) > full_elems:
            raise MpiLayerError("span lies outside the allreduce buffer")
        for index in range(len(values)):
            covered[offset + index] += 1
    if partial:
        return
    # Column-style: either disjoint coverage of the full buffer, or each span
    # already expanded (offset 0, full width) with zeros in foreign columns.
    if all(offset == 0 and len(values) == full_elems for offset, values in spans):
        return
    if any(count != 1 for count in covered):
        raise MpiLayerError("column spans must be disjoint and cover the full buffer exactly once")


def _capacity_score(spec: RankSpec, *, headroom: float, vram_weight: float, latency_weight: float) -> float:
    memory = spec.vram_bytes * headroom
    latency_factor = 1.0 / (1.0 + spec.latency_ms / 10.0)
    blend = vram_weight + latency_weight * latency_factor
    return memory * blend * spec.compute_weight


def _normalize_weights(vram_weight: float, latency_weight: float) -> tuple[float, float]:
    if vram_weight < 0 or latency_weight < 0 or vram_weight + latency_weight <= 0:
        raise MpiLayerError("vram_weight and latency_weight must be non-negative and not both zero")
    scale = vram_weight + latency_weight
    return vram_weight / scale, latency_weight / scale


def _select_mode(
    requested: str,
    *,
    mixed: bool,
    backends: set[str],
    allow_unsafe: bool,
) -> tuple[str, str]:
    joined = ",".join(sorted(backends))
    if requested == "layer":
        return "pipeline", f"layer placement requested; backends={joined}"
    if requested == "tensor":
        if mixed and not allow_unsafe:
            raise MpiLayerError(
                "tensor collectives across mixed GGML backends are disabled "
                f"({joined}); use parallelism=layer or auto"
            )
        if mixed:
            return "tensor", f"unsafe mixed-backend tensor split forced ({joined})"
        return "tensor", f"single backend {joined}; column shards follow capacity"
    if mixed:
        return "pipeline", (
            f"mixed backends ({joined}) use pipeline handoff; "
            "tensor-parallel GEMMs would cross Metal/CUDA/ROCm kernels"
        )
    return "tensor", f"single backend {joined}; column shards follow capacity"


def _check_rank_index(ranks: Sequence[RankSpec]) -> None:
    indexes = [spec.rank for spec in ranks]
    if indexes != list(range(len(ranks))):
        raise MpiLayerError("ranks must be 0..n-1 in order")


def _contiguous_spans(counts: Sequence[int]) -> tuple[LayerSpan, ...]:
    spans: list[LayerSpan] = []
    cursor = 0
    for rank, count in enumerate(counts):
        spans.append(LayerSpan(rank=rank, start=cursor, end=cursor + count))
        cursor += count
    return tuple(spans)


def _column_shards(n_ff: int, scores: Sequence[float]) -> tuple[ColumnShard, ...]:
    lengths = largest_remainder(n_ff, scores)
    shards: list[ColumnShard] = []
    offset = 0
    for rank, length in enumerate(lengths):
        shards.append(ColumnShard(rank=rank, offset=offset, length=length))
        offset += length
    return tuple(shards)


def _weight_elems(
    span: LayerSpan,
    shard: ColumnShard,
    *,
    hidden: int,
    n_ff: int,
    column_sharded: bool,
) -> int:
    """Rough resident parameter count for the memory diagram, not a GGUF parser.

    Pipeline ranks store full-width attention and FFN tensors for their layers.
    Tensor ranks store every layer, narrowed to the column shard.
    """
    layers = span.count
    if column_sharded:
        frac = shard.length / n_ff
        attn = int(4 * hidden * hidden * frac)
        ffn = 3 * hidden * shard.length
    else:
        attn = 4 * hidden * hidden
        ffn = 3 * hidden * n_ff
    return layers * (attn + ffn)


def _element_count(ne: tuple[int, int, int, int]) -> int:
    count = 1
    for dim in ne:
        count *= dim
    return count


def _n_dims(ne: tuple[int, int, int, int]) -> int:
    dims = 1
    for index in range(1, 4):
        if ne[index] > 1:
            dims = index + 1
    return dims


def _checked_mul(a: int, b: int, c: int = 1) -> int:
    value = a * b * c
    if value > MAX_ACTIVATION_BYTES:
        raise MpiLayerError(f"activation of {value} bytes exceeds the {MAX_ACTIVATION_BYTES} byte cap")
    return value


def _os_hint(backend: str) -> str:
    if backend == "metal":
        return "darwin"
    if backend == "rocm":
        return "linux"
    return "unknown"


def _recvall(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        part = sock.recv(remaining)
        if not part:
            raise MpiLayerError("peer closed the MPI socket")
        chunks.append(part)
        remaining -= len(part)
    return b"".join(chunks)
