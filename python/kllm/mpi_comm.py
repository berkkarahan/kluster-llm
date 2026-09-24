"""MPI activation collectives for a mixed-backend cluster.

Production inference stays on llama.cpp RPC with ``split_mode=layer``
(see ``docs/engine-decision.md``). This module is the MPI transport for that
same cut: each rank keeps its GGUF weights and KV cache, and the wire carries
only token ids, activations, and logits.

Mixed Metal / ROCm / CUDA ranks do not tensor-split one matrix. Those backends
do not round the same way, so a row-parallel ``MPI_Allreduce`` across them
drifts. Tensor-parallel column shards are opt-in and only when every rank in
the group reports the same backend.

Collectives do not assume equal slices:

* ``balanced_bcast`` sends a fixed 96-byte GGML header, then a payload whose
  length comes from that header.
* ``balanced_allreduce`` allgathers the per-rank byte counts. Equal f32
  partial sums use ``MPI_Allreduce``. Unequal column shards use
  ``MPI_Allgatherv``. Unequal inputs are never padded into an allreduce.

The theoretical 3-node picture is ``render_flow(split_graph(...))`` on
``example_mixed_cluster()`` (32 layers, 40 GiB of weights):

::

    rank 0  macos   metal  layers [  0,  15)  budget 25.6 GiB  speed 1.00
            [ weights resident | KV resident | activation slot ]
    rank 1  ubuntu  rocm   layers [ 15,  25)  budget 12.8 GiB  speed 0.85
            [ weights resident | KV resident | activation slot ]
    rank 2  windows cuda   layers [ 25,  32)  budget  9.6 GiB  speed 1.40
            [ weights resident | KV resident | activation slot ]
    ---- private fabric (MPI has no auth) ----
    step 0  MPI_Bcast  token ids     root 0   4 B
    step 1  MPI_Bcast  activation    root 0   16384 B   after layer 14
    step 2  MPI_Bcast  activation    root 1   16384 B   after layer 24
    step 3  MPI_Bcast  activation    root 2   16384 B   after layer 31
    step 4  MPI_Bcast  logits        root 2
    weights on wire: 0
    no MPI_Allreduce on this cut (backends differ; slices are not equal)

The Windows GPU is the fastest and still receives the fewest layers: 12 GiB
caps it at 7, and those layers are returned to the remainder pool instead of
being padded into a homogeneous shard.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Sequence

HEADER_BYTES = 96
MPI_CHUNK_BYTES = 1 << 30
MAGIC = b"KMPI"
VERSION = 1

# ggml_type values from ggml.h. Only dense activation / token types are legal
# on this wire; block-quantized weight types are rejected.
GGML_F32 = 0
GGML_F16 = 1
GGML_I32 = 26
GGML_BF16 = 30

FLAG_ACTIVATION = 0x1
FLAG_TOKEN_IDS = 0x2
FLAG_LOGITS = 0x4
FLAG_PARTIAL = 0x8
_KIND_BITS = FLAG_ACTIVATION | FLAG_TOKEN_IDS | FLAG_LOGITS
_ALLOWED_FLAGS = _KIND_BITS | FLAG_PARTIAL

COLLECTIVE_BCAST = 1
COLLECTIVE_ALLREDUCE = 2
COLLECTIVE_ALLGATHERV = 3

_ITEMSIZE = {
    GGML_F32: 4,
    GGML_F16: 2,
    GGML_I32: 4,
    GGML_BF16: 2,
}

# Relative throughput hints used only when a node does not report compute_speed.
# They order pipeline stages; they are not a benchmark.
_DEFAULT_SPEED = {
    "metal": 1.0,
    "cuda": 1.3,
    "rocm": 0.85,
    "vulkan": 0.7,
    "cpu": 0.15,
}

_HEADER = struct.Struct("<4sHHIiiI4q4QQ")
assert _HEADER.size == HEADER_BYTES

EXAMPLE_LAYERS = 32
# 40 GiB / 32 layers = 1.25 GiB of resident weights per layer.
EXAMPLE_MODEL_BYTES = 40 * (1024**3)
# One collective cannot carry a multi-gigabyte weight tensor by labeling it an activation.
DEFAULT_MAX_PAYLOAD_BYTES = 4 * (1024**3)
# Matches crates/kllm-native planner.rs KV_BASE_BYTES_PER_TOKEN_PER_LAYER.
_KV_BASE_BYTES_PER_TOKEN_PER_LAYER = 128.0


class MpiProtocolError(ValueError):
    """The buffer is not an activation the MPI wrapper is allowed to move."""


def example_mixed_cluster() -> list[dict]:
    """macOS Metal, Ubuntu ROCm, and Windows CUDA with unequal memory and speed.

    The Windows GPU is the fastest and the smallest. A homogeneous split would
    hand it the same number of layers as the Mac; the memory cap will not.
    """
    gib = 1024**3
    return [
        {
            "node_id": "mac-metal",
            "platform": "macos",
            "backend": "metal",
            "unified_memory": True,
            "total_ram_bytes": 32 * gib,
            "available_ram_bytes": 24 * gib,
            "vram_bytes": 32 * gib,
            "compute_speed": 1.0,
            "is_master": True,
        },
        {
            "node_id": "linux-rocm",
            "platform": "ubuntu",
            "backend": "rocm",
            "unified_memory": False,
            "total_ram_bytes": 64 * gib,
            "available_ram_bytes": 48 * gib,
            "vram_bytes": 16 * gib,
            "compute_speed": 0.85,
            "is_master": False,
        },
        {
            "node_id": "win-cuda",
            "platform": "windows",
            "backend": "cuda",
            "unified_memory": False,
            "total_ram_bytes": 32 * gib,
            "available_ram_bytes": 16 * gib,
            "vram_bytes": 12 * gib,
            "compute_speed": 1.40,
            "is_master": False,
        },
    ]


def payload_chunks(nbytes: int) -> list[int]:
    """``MPI_Bcast`` counts are ``int``. Split a dynamic payload into <= 1 GiB chunks."""
    if nbytes < 0:
        raise MpiProtocolError("negative payload")
    if nbytes == 0:
        return [0]
    out: list[int] = []
    left = nbytes
    while left:
        take = min(MPI_CHUNK_BYTES, left)
        out.append(take)
        left -= take
    return out


def largest_remainder(
    weights: Sequence[float],
    total: int,
    caps: Sequence[int] | None = None,
) -> list[int]:
    """Integer shares of ``total`` proportional to ``weights``, then clamped to ``caps``.

    Floors are applied first. Anything that does not fit a cap goes back into
    the pool and is handed out by descending remainder, ties broken by the
    lower rank (stable pipeline order). Ranks already at their cap are skipped.
    """
    n = len(weights)
    if n == 0:
        raise MpiProtocolError("at least one rank is required")
    if total < 0:
        raise MpiProtocolError("total must be >= 0")
    if caps is not None and len(caps) != n:
        raise MpiProtocolError("caps length must match weights")
    if total == 0:
        return [0] * n
    if caps is not None:
        if any(int(c) < 0 for c in caps):
            raise MpiProtocolError("caps must be >= 0")
        if sum(int(c) for c in caps) < total:
            raise MpiProtocolError(
                f"memory caps hold {sum(int(c) for c in caps)} units but {total} are required"
            )

    raw = [float(w) for w in weights]
    if any(w < 0 for w in raw):
        raise MpiProtocolError("weights must be >= 0")
    capped = list(raw)
    if caps is not None:
        capped = [0.0 if int(caps[i]) == 0 else raw[i] for i in range(n)]
    wsum = sum(capped)
    if wsum <= 0:
        room = [i for i in range(n) if caps is None or int(caps[i]) > 0]
        if not room:
            raise MpiProtocolError("no rank can take a share")
        capped = [1.0 if i in room else 0.0 for i in range(n)]
        wsum = float(len(room))

    exact = [total * (capped[i] / wsum) for i in range(n)]
    counts = [int(exact[i] // 1) for i in range(n)]
    if caps is not None:
        for i in range(n):
            cap_i = int(caps[i])
            if counts[i] > cap_i:
                counts[i] = cap_i
    leftover = total - sum(counts)
    remainders = [exact[i] - (exact[i] // 1) for i in range(n)]
    order = sorted(range(n), key=lambda i: (-remainders[i], i))
    cursor = 0
    steps = 0
    limit = total + n + 1
    while leftover > 0:
        steps += 1
        if steps > limit:
            raise MpiProtocolError("failed to place shares under memory caps")
        i = order[cursor % n]
        cursor += 1
        if caps is not None and counts[i] >= int(caps[i]):
            if caps is not None and all(counts[j] >= int(caps[j]) for j in range(n)):
                raise MpiProtocolError("memory caps cannot hold the assignment")
            continue
        counts[i] += 1
        leftover -= 1
    return counts


def node_budget_bytes(node: dict, headroom: float) -> int:
    """Bytes of weight budget. Metal uses unified memory; discrete GPUs use VRAM."""
    if not 0.1 <= float(headroom) <= 1.0:
        raise MpiProtocolError("headroom must be between 0.1 and 1.0")
    backend = str(node.get("backend", "")).lower()
    unified = bool(node.get("unified_memory") or backend == "metal")
    if unified:
        base = int(node.get("total_ram_bytes") or node.get("available_ram_bytes") or 0)
    else:
        # 0 is a real probe result (no VRAM). Only a missing field falls back to RAM.
        if "vram_bytes" in node and node.get("vram_bytes") is not None:
            base = int(node["vram_bytes"])
        else:
            base = int(node.get("available_ram_bytes") or 0)
    return int(base * float(headroom))


def node_speed(node: dict) -> float:
    if node.get("compute_speed") is not None:
        speed = float(node["compute_speed"])
    else:
        speed = _DEFAULT_SPEED.get(str(node.get("backend", "")).lower(), 0.5)
    if speed < 0:
        raise MpiProtocolError("compute_speed must be >= 0")
    return speed


def harmonic_weights(budgets: Sequence[int], speeds: Sequence[float]) -> list[float]:
    """Harmonic mean of normalized memory and normalized speed.

    A fast card with little VRAM and a large card with low throughput both
    pull their share down. A zero budget or zero speed gets weight 0.
    """
    if len(budgets) != len(speeds):
        raise MpiProtocolError("budgets and speeds must be the same length")
    if not budgets:
        raise MpiProtocolError("at least one rank is required")
    bsum = sum(int(b) for b in budgets)
    ssum = sum(float(s) for s in speeds)
    if bsum <= 0 or ssum <= 0:
        raise MpiProtocolError("cluster budget and compute speed must be positive")
    out: list[float] = []
    for b, s in zip(budgets, speeds):
        b = int(b)
        s = float(s)
        if b <= 0 or s <= 0:
            out.append(0.0)
            continue
        nb = b / bsum
        ns = s / ssum
        out.append(2.0 / ((1.0 / nb) + (1.0 / ns)))
    return out


def _kv_dtype_bytes(dtype: str) -> float:
    """Same widths as the Rust planner's ``kv_dtype_bytes``."""
    key = str(dtype).lower()
    return {
        "f32": 4.0,
        "f16": 2.0,
        "bf16": 2.0,
        "q8_0": 1.0,
        "q5_0": 0.625,
        "q5_1": 0.625,
        "q4_0": 0.5,
        "q4_1": 0.5,
        "iq4_nl": 0.5,
    }.get(key, 1.0)


def kv_bytes_per_layer(
    context: int,
    cache_type_k: str = "q8_0",
    cache_type_v: str = "q8_0",
    parallel_slots: int = 1,
) -> int:
    """KV bytes one repeating layer needs. Same product as the Rust ``estimate_kv``."""
    if int(context) <= 0:
        return 0
    width = _kv_dtype_bytes(cache_type_k) + _kv_dtype_bytes(cache_type_v)
    slots = max(1, int(parallel_slots))
    return int(int(context) * _KV_BASE_BYTES_PER_TOKEN_PER_LAYER * width * slots)


def layer_caps(
    budgets: Sequence[int],
    model_size_bytes: int,
    n_layers: int,
    kv_per_layer: int = 0,
) -> list[int] | None:
    """Max whole layers whose weights and KV fit in each budget.

    ``None`` if the model size is unknown. A layer costs
    ``model_size / n_layers + kv_per_layer``.
    """
    if n_layers <= 0:
        raise MpiProtocolError("n_layers must be positive")
    if model_size_bytes <= 0:
        return None
    weight = max(1, int(model_size_bytes) // int(n_layers))
    cost = weight + max(0, int(kv_per_layer))
    return [int(b) // cost for b in budgets]


def column_caps(
    budgets: Sequence[int],
    model_size_bytes: int,
    n_cols: int,
    kv_total: int = 0,
) -> list[int] | None:
    """Max columns of weights that fit after the full KV cache is reserved.

    Tensor-parallel ranks each keep every layer, so each pays the whole KV
    and only a column slice of the weights.
    """
    if n_cols <= 0:
        raise MpiProtocolError("n_cols must be positive")
    if model_size_bytes <= 0:
        return None
    per_col = max(1, int(model_size_bytes) // int(n_cols))
    caps: list[int] = []
    for budget in budgets:
        usable = int(budget) - max(0, int(kv_total))
        caps.append(0 if usable <= 0 else usable // per_col)
    return caps


def _ne4(ne: Sequence[int]) -> tuple[int, int, int, int]:
    if not 1 <= len(ne) <= 4:
        raise MpiProtocolError("ne must have 1 to 4 dimensions")
    dims = [int(x) for x in ne]
    if any(d < 0 for d in dims):
        raise MpiProtocolError("ne dimensions must be >= 0")
    dims.extend([1] * (4 - len(dims)))
    return dims[0], dims[1], dims[2], dims[3]


def numel(ne: Sequence[int]) -> int:
    n = 1
    for d in _ne4(ne):
        n *= int(d)
    return n


def contiguous_nb(ne: Sequence[int], itemsize: int) -> tuple[int, int, int, int]:
    n0, n1, n2, _n3 = _ne4(ne)
    nb0 = int(itemsize)
    nb1 = nb0 * n0
    nb2 = nb1 * n1
    nb3 = nb2 * n2
    return nb0, nb1, nb2, nb3


def _check_flags(flags: int, ggml_type: int) -> None:
    flags = int(flags)
    if flags & ~_ALLOWED_FLAGS:
        raise MpiProtocolError(
            "refusing payload that is not tokens, activations, or logits; weights stay on the node"
        )
    kind = flags & _KIND_BITS
    if kind.bit_count() != 1:
        raise MpiProtocolError("payload needs exactly one of activation, token_ids, or logits")
    if flags & FLAG_PARTIAL and not flags & FLAG_ACTIVATION:
        raise MpiProtocolError("partial flag is only valid on activations")
    if ggml_type not in _ITEMSIZE:
        raise MpiProtocolError(
            f"ggml type {ggml_type} is not a dense activation type; quantized weights are not broadcast"
        )
    if ggml_type == GGML_I32 and not flags & FLAG_TOKEN_IDS:
        raise MpiProtocolError("i32 tensors on this wire are token ids")
    if ggml_type != GGML_I32 and flags & FLAG_TOKEN_IDS:
        raise MpiProtocolError("token ids are ggml i32")


@dataclass
class Tensor:
    """Contiguous GGML-shaped activation. Pointers inside ``ggml_tensor`` are not stored."""

    ggml_type: int
    flags: int
    src_rank: int
    layer: int
    collective: int
    ne: tuple[int, int, int, int]
    nb: tuple[int, int, int, int]
    data: bytes

    def pack(self) -> bytes:
        _check_flags(self.flags, self.ggml_type)
        item = _ITEMSIZE[self.ggml_type]
        expect_nb = contiguous_nb(self.ne, item)
        if tuple(self.nb) != expect_nb:
            raise MpiProtocolError("only contiguous activations are broadcast (nb must be packed)")
        count = numel(self.ne)
        expect = count * item
        if len(self.data) != expect:
            raise MpiProtocolError(
                f"payload is {len(self.data)} bytes, contiguous ne requires {expect}"
            )
        header = _HEADER.pack(
            MAGIC,
            VERSION,
            int(self.flags),
            int(self.ggml_type),
            int(self.src_rank),
            int(self.layer),
            int(self.collective),
            *self.ne,
            *self.nb,
            expect,
        )
        return header + self.data

    @staticmethod
    def unpack(blob: bytes) -> Tensor:
        if len(blob) < HEADER_BYTES:
            raise MpiProtocolError("truncated GGML MPI header")
        (
            magic,
            version,
            flags,
            ggml_type,
            src_rank,
            layer,
            collective,
            n0,
            n1,
            n2,
            n3,
            b0,
            b1,
            b2,
            b3,
            nbytes,
        ) = _HEADER.unpack(blob[:HEADER_BYTES])
        if magic != MAGIC or version != VERSION:
            raise MpiProtocolError("not a KMPI activation header")
        _check_flags(flags, ggml_type)
        ne = (int(n0), int(n1), int(n2), int(n3))
        nb = (int(b0), int(b1), int(b2), int(b3))
        data = blob[HEADER_BYTES:]
        if len(data) != int(nbytes):
            raise MpiProtocolError("payload length does not match header nbytes")
        tensor = Tensor(
            ggml_type=int(ggml_type),
            flags=int(flags),
            src_rank=int(src_rank),
            layer=int(layer),
            collective=int(collective),
            ne=ne,
            nb=nb,
            data=bytes(data),
        )
        # pack() re-checks contiguity and numel. Reject a header that lies.
        tensor.pack()
        return tensor


def _tensor(
    data: bytes,
    *,
    ggml_type: int,
    flags: int,
    ne: Sequence[int],
    src_rank: int,
    layer: int,
    collective: int,
) -> Tensor:
    dims = _ne4(ne)
    nb = contiguous_nb(dims, _ITEMSIZE[ggml_type])
    return Tensor(
        ggml_type=ggml_type,
        flags=flags,
        src_rank=src_rank,
        layer=layer,
        collective=collective,
        ne=dims,
        nb=nb,
        data=data,
    )


def token_tensor(token_ids: Sequence[int], *, src_rank: int = 0) -> Tensor:
    data = struct.pack("<" + "i" * len(token_ids), *[int(t) for t in token_ids])
    return _tensor(
        data,
        ggml_type=GGML_I32,
        flags=FLAG_TOKEN_IDS,
        ne=(len(token_ids), 1, 1, 1),
        src_rank=src_rank,
        layer=-1,
        collective=COLLECTIVE_BCAST,
    )


def activation_f32(
    values: Sequence[float],
    *,
    ne: Sequence[int],
    src_rank: int,
    layer: int,
    partial: bool = False,
) -> Tensor:
    dims = _ne4(ne)
    if numel(dims) != len(values):
        raise MpiProtocolError("f32 values do not match ne")
    data = struct.pack("<" + "f" * len(values), *[float(v) for v in values])
    flags = FLAG_ACTIVATION | (FLAG_PARTIAL if partial else 0)
    return _tensor(
        data,
        ggml_type=GGML_F32,
        flags=flags,
        ne=dims,
        src_rank=src_rank,
        layer=layer,
        collective=COLLECTIVE_BCAST,
    )


def f32_values(tensor: Tensor) -> list[float]:
    if tensor.ggml_type != GGML_F32:
        raise MpiProtocolError("expected f32 activation")
    count = numel(tensor.ne)
    return list(struct.unpack("<" + "f" * count, tensor.data))


def balanced_bcast(
    slots: list[Tensor | None],
    root: int,
    trace: list[dict] | None = None,
    max_payload_bytes: int | None = None,
) -> None:
    """Every rank calls this. Loopback runs the ranks in one process.

    Step 1. Root packs a GGML header (type, ne, nb, nbytes). Weights are not
    in that struct: no data pointer, no buffer pointer, no quantized blocks.
    Step 2. ``MPI_Bcast`` of the 96-byte header. This count is the same on
    every rank.
    Step 3. Each rank reads ``nbytes`` and resizes. The payload count is the
    header's, not a compile-time shard width.
    Step 4. ``MPI_Bcast`` of the payload, chunked so the C ``int`` count stays
    in range. Non-roots replace their local activation. Resident weights are
    not arguments to this function.
    """
    if not slots:
        raise MpiProtocolError("bcast needs a slot per rank")
    if not 0 <= int(root) < len(slots):
        raise MpiProtocolError("bcast root is outside the communicator")
    src = slots[root]
    if src is None:
        raise MpiProtocolError("bcast root has no activation")
    packed = src.pack()
    header, payload = packed[:HEADER_BYTES], packed[HEADER_BYTES:]
    limit = DEFAULT_MAX_PAYLOAD_BYTES if max_payload_bytes is None else int(max_payload_bytes)
    if limit < 0:
        raise MpiProtocolError("payload budget must be >= 0")
    # The activation flag is a sender claim. The budget is what stops a weight
    # tensor from crossing the fabric under that flag.
    if len(payload) > limit:
        raise MpiProtocolError(
            f"payload is {len(payload)} bytes, budget is {limit}; weights stay on the node"
        )
    events = trace if trace is not None else []
    events.append(
        {"op": "MPI_Bcast", "what": "header", "bytes": HEADER_BYTES, "root": int(root)}
    )
    for chunk in payload_chunks(len(payload)):
        events.append(
            {
                "op": "MPI_Bcast",
                "what": "payload",
                "bytes": chunk,
                "root": int(root),
                "dynamic": True,
            }
        )
    cloned = Tensor.unpack(header + payload)
    for i in range(len(slots)):
        # Each rank owns its copy. A later local write must not alias the root.
        slots[i] = Tensor.unpack(cloned.pack())


def _widths(slots: list[Tensor]) -> list[int]:
    widths: list[int] = []
    for t in slots:
        item = _ITEMSIZE[t.ggml_type]
        if len(t.data) % item:
            raise MpiProtocolError("payload is not a whole number of ggml elements")
        widths.append(numel(t.ne))
    return widths


def _check_weights(slots: list[Tensor], weights: Sequence[float] | None) -> None:
    if weights is None:
        return
    if len(weights) != len(slots):
        raise MpiProtocolError("weights length must match the communicator")
    widths = _widths(slots)
    expected = largest_remainder(weights, sum(widths))
    if widths != expected:
        raise MpiProtocolError(
            f"payload widths {widths} disagree with weights {list(weights)} -> {expected}"
        )


def balanced_allreduce(
    slots: list[Tensor],
    *,
    kind: str,
    weights: Sequence[float] | None = None,
    trace: list[dict] | None = None,
    max_payload_bytes: int | None = None,
) -> None:
    """Weighted wrapper around ``MPI_Allreduce`` / ``MPI_Allgatherv``.

    ``kind="row_sum"`` is a residual partial-sum: every rank has the same
    ``ne`` and the values are added. Counts are gathered first; if they
    differ, this raises instead of padding with zeros and summing.

    ``kind="column_concat"`` is an unequal column shard. Counts are gathered,
    displacements are the prefix sum of those counts, and the result is
    ``MPI_Allgatherv`` in rank order. Equal widths stay on this path: summing
    column shards would mix distinct outputs.

    ``weights``, when given, are the shard fractions from the planner. The
    observed element counts must be exactly ``largest_remainder(weights)``.
    """
    if not slots:
        raise MpiProtocolError("allreduce needs a slot per rank")
    if kind not in ("row_sum", "column_concat"):
        raise MpiProtocolError("kind must be row_sum or column_concat")
    for t in slots:
        t.pack()
    if any(t.ggml_type != GGML_F32 for t in slots):
        raise MpiProtocolError("reductions are f32 activations")
    _check_weights(slots, weights)
    if kind == "column_concat" and any(not (t.flags & FLAG_PARTIAL) for t in slots):
        raise MpiProtocolError("column shards must be marked partial activations")
    limit = DEFAULT_MAX_PAYLOAD_BYTES if max_payload_bytes is None else int(max_payload_bytes)
    if limit < 0:
        raise MpiProtocolError("payload budget must be >= 0")
    counts = [len(t.data) for t in slots]
    if sum(counts) > limit or any(n > limit for n in counts):
        raise MpiProtocolError(
            f"reduction payload exceeds budget {limit}; weights stay on the node"
        )
    events = trace if trace is not None else []
    events.append(
        {
            "op": "MPI_Allgather",
            "what": "counts",
            "bytes": 8 * len(slots),
            "counts": list(counts),
        }
    )
    if kind == "row_sum":
        shapes = {t.ne for t in slots}
        if len(shapes) != 1 or len(set(counts)) != 1:
            raise MpiProtocolError(
                "row-parallel MPI_Allreduce needs equal shapes; "
                "refusing to pad unequal slices (use column_concat for unequal widths)"
            )
        count = numel(slots[0].ne)
        events.append(
            {
                "op": "MPI_Allreduce",
                "what": "payload",
                "dtype": "f32",
                "opnum": "SUM",
                "count": count,
                "chunks": payload_chunks(counts[0]),
            }
        )
        acc = f32_values(slots[0])
        for t in slots[1:]:
            for i, v in enumerate(f32_values(t)):
                acc[i] += v
        reduced = activation_f32(
            acc,
            ne=slots[0].ne,
            src_rank=0,
            layer=slots[0].layer,
            partial=False,
        )
        reduced.collective = COLLECTIVE_ALLREDUCE
        for i in range(len(slots)):
            slots[i] = Tensor.unpack(reduced.pack())
        return

    tail = slots[0].ne[1:]
    for t in slots[1:]:
        if t.ne[1:] != tail:
            raise MpiProtocolError("column shards must share ne[1:]")
    displs: list[int] = []
    off = 0
    for n in counts:
        displs.append(off)
        off += n
    events.append(
        {
            "op": "MPI_Allgatherv",
            "what": "payload",
            "counts": list(counts),
            "displs": displs,
        }
    )
    merged = bytearray(off)
    for t, disp in zip(slots, displs):
        merged[disp : disp + len(t.data)] = t.data
    width = sum(_widths(slots))
    gathered = _tensor(
        bytes(merged),
        ggml_type=GGML_F32,
        flags=FLAG_ACTIVATION,
        ne=(width, *tail),
        src_rank=0,
        layer=slots[0].layer,
        collective=COLLECTIVE_ALLGATHERV,
    )
    for i in range(len(slots)):
        slots[i] = Tensor.unpack(gathered.pack())


@dataclass(frozen=True)
class RankAssignment:
    rank: int
    node_id: str
    platform: str
    backend: str
    layer_start: int
    layer_end: int
    n_layers: int
    budget_bytes: int
    compute_speed: float
    weight: float
    column_width: int | None = None

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "node_id": self.node_id,
            "platform": self.platform,
            "backend": self.backend,
            "layer_start": self.layer_start,
            "layer_end": self.layer_end,
            "n_layers": self.n_layers,
            "budget_bytes": self.budget_bytes,
            "compute_speed": self.compute_speed,
            "weight": self.weight,
            "column_width": self.column_width,
            "resident": "weights+kv",
            "wire": "tokens+activations+logits",
        }


@dataclass
class PipelineSchedule:
    split_mode: str
    tensor_parallel: bool
    weights_on_wire: bool
    n_layers: int
    ranks: list[RankAssignment]
    collectives: list[dict]
    notes: str

    def to_dict(self) -> dict:
        return {
            "enabled": True,
            "transport": "mpi",
            "split_mode": self.split_mode,
            "tensor_parallel": self.tensor_parallel,
            "weights_on_wire": self.weights_on_wire,
            "n_layers": self.n_layers,
            "ranks": [r.to_dict() for r in self.ranks],
            "collectives": self.collectives,
            "notes": self.notes,
        }


def _backends_homogeneous(nodes: Sequence[dict]) -> bool:
    names = [str(n.get("backend", "")).strip().lower() for n in nodes]
    return bool(names) and len(set(names)) == 1 and names[0] not in {"", "unknown"}


def _assignment(
    nodes: Sequence[dict],
    counts: Sequence[int],
    *,
    budgets: Sequence[int],
    speeds: Sequence[float],
    weights: Sequence[float],
    columns: Sequence[int] | None,
    pipeline: bool,
) -> list[RankAssignment]:
    ranks: list[RankAssignment] = []
    start = 0
    for i, node in enumerate(nodes):
        n_layers = int(counts[i])
        if pipeline:
            layer_start, layer_end = start, start + n_layers
            start = layer_end
        else:
            # Column-parallel: every rank holds every layer, different columns.
            layer_start, layer_end = 0, int(counts[i])
        ranks.append(
            RankAssignment(
                rank=i,
                node_id=str(node.get("node_id", f"rank-{i}")),
                platform=str(node.get("platform", "")),
                backend=str(node.get("backend", "")).lower(),
                layer_start=layer_start,
                layer_end=layer_end,
                n_layers=n_layers if pipeline else (layer_end - layer_start),
                budget_bytes=int(budgets[i]),
                compute_speed=float(speeds[i]),
                weight=float(weights[i]),
                column_width=None if columns is None else int(columns[i]),
            )
        )
    return ranks


def _elem(dtype: str) -> tuple[str, int]:
    key = dtype.lower()
    if key == "f32":
        return key, 4
    if key == "f16":
        return key, 2
    if key == "bf16":
        return key, 2
    raise MpiProtocolError("activation dtype must be f32, f16, or bf16")


def _bcast_op(
    *,
    root: int,
    payload: str,
    ggml_type: str,
    ne: list[int] | None,
    nbytes: int | None,
    layer: int,
) -> dict:
    return {
        "op": "MPI_Bcast",
        "root": root,
        "payload": payload,
        "ggml_type": ggml_type,
        "ne": ne,
        "nbytes": nbytes,
        "layer": layer,
        "weights_moved": False,
        "header_bytes": HEADER_BYTES,
    }


def _pipeline_collectives(
    ranks: Sequence[RankAssignment],
    *,
    n_embd: int | None,
    n_vocab: int | None,
    batch: int,
    dtype: str,
    include_logits: bool = True,
) -> list[dict]:
    ggml, elem = _elem(dtype)
    ops = [
        _bcast_op(
            root=0,
            payload="token_ids",
            ggml_type="i32",
            ne=[batch, 1, 1, 1],
            nbytes=batch * 4,
            layer=-1,
        )
    ]
    for rank in ranks:
        if rank.n_layers <= 0:
            continue
        numel_act = None if n_embd is None else int(n_embd) * batch
        ops.append(
            _bcast_op(
                root=rank.rank,
                payload="activation",
                ggml_type=ggml,
                ne=None if n_embd is None else [int(n_embd), batch, 1, 1],
                nbytes=None if numel_act is None else numel_act * elem,
                layer=rank.layer_end - 1,
            )
        )
    owners = [r for r in ranks if r.n_layers > 0]
    if include_logits and owners:
        last = owners[-1]
        numel_logits = None if n_vocab is None else int(n_vocab) * batch
        ops.append(
            _bcast_op(
                root=last.rank,
                payload="logits",
                ggml_type=ggml,
                ne=None if n_vocab is None else [int(n_vocab), batch, 1, 1],
                nbytes=None if numel_logits is None else numel_logits * elem,
                layer=last.layer_end - 1,
            )
        )
    return ops


def _column_collectives(
    ranks: Sequence[RankAssignment],
    *,
    n_embd: int | None,
    n_vocab: int | None,
    batch: int,
    dtype: str,
) -> list[dict]:
    ops = _pipeline_collectives(
        [ranks[0]] if ranks else [],
        n_embd=n_embd,
        n_vocab=None,
        batch=batch,
        dtype=dtype,
        include_logits=False,
    )
    # Token bcast, then one activation bcast (the full X). Column shards
    # allgatherv; rank 0 broadcasts logits after the gather.
    widths = [r.column_width for r in ranks]
    ops.append(
        {
            "op": "MPI_Allgatherv",
            "payload": "partial",
            "kind": "column_concat",
            "widths": widths,
            "weights": [r.weight for r in ranks],
            "repeat_layers": ranks[0].n_layers if ranks else 0,
            "weights_moved": False,
        }
    )
    if ranks:
        ggml, elem = _elem(dtype)
        numel_logits = None if n_vocab is None else int(n_vocab) * batch
        ops.append(
            _bcast_op(
                root=0,
                payload="logits",
                ggml_type=ggml,
                ne=None if n_vocab is None else [int(n_vocab), batch, 1, 1],
                nbytes=None if numel_logits is None else numel_logits * elem,
                layer=ranks[0].layer_end - 1,
            )
        )
    return ops


def split_graph(
    nodes: Sequence[dict],
    *,
    n_layers: int,
    model_size_bytes: int = 0,
    headroom: float = 0.8,
    tensor_parallel: bool = False,
    n_embd: int | None = None,
    n_vocab: int | None = None,
    n_cols: int | None = None,
    batch: int = 1,
    dtype: str = "f32",
    context: int = 4096,
    cache_type_k: str = "q8_0",
    cache_type_v: str = "q8_0",
    parallel_slots: int = 1,
) -> PipelineSchedule:
    """Assign layers or columns. Weights stay where they are loaded.

    Pipeline (default): contiguous layer ranges in list order. Share is the
    harmonic mean of memory budget and compute speed, then largest-remainder,
    then a cap of ``floor(budget / (weight_per_layer + kv_per_layer))``.

    Tensor parallel: legal only when every node names the same backend.
    Every rank stores every layer and a column shard of width proportional
    to compute speed.
    """
    if not nodes:
        raise MpiProtocolError("at least one node is required")
    if int(batch) <= 0:
        raise MpiProtocolError("batch must be positive")
    budgets = [node_budget_bytes(n, headroom) for n in nodes]
    speeds = [node_speed(n) for n in nodes]
    kv_layer = kv_bytes_per_layer(context, cache_type_k, cache_type_v, parallel_slots)
    if tensor_parallel:
        if not _backends_homogeneous(nodes):
            names = ", ".join(str(n.get("backend", "")) for n in nodes)
            raise MpiProtocolError(
                "tensor-parallel allreduce across mixed backends is refused "
                f"({names}); use pipeline layer splits so Metal, ROCm, and CUDA "
                "do not share one matmul"
            )
        if n_cols is None:
            raise MpiProtocolError("tensor parallel needs n_cols")
        weights = list(speeds)
        caps = None
        if model_size_bytes > 0:
            caps = column_caps(
                budgets,
                model_size_bytes,
                int(n_cols),
                kv_total=kv_layer * int(n_layers),
            )
        columns = largest_remainder(weights, int(n_cols), caps)
        # Each rank still executes every layer; `counts` carries the layer span.
        counts = [int(n_layers)] * len(nodes)
        ranks = _assignment(
            nodes,
            counts,
            budgets=budgets,
            speeds=speeds,
            weights=weights,
            columns=columns,
            pipeline=False,
        )
        notes = (
            "same-backend column shards; MPI_Bcast of the full activation, "
            "then MPI_Allgatherv of unequal partials; weights stay resident"
        )
        return PipelineSchedule(
            split_mode="column",
            tensor_parallel=True,
            weights_on_wire=False,
            n_layers=int(n_layers),
            ranks=ranks,
            collectives=_column_collectives(
                ranks, n_embd=n_embd, n_vocab=n_vocab, batch=int(batch), dtype=dtype
            ),
            notes=notes,
        )

    weights = harmonic_weights(budgets, speeds)
    caps = layer_caps(budgets, int(model_size_bytes), int(n_layers), kv_per_layer=kv_layer)
    counts = largest_remainder(weights, int(n_layers), caps)
    ranks = _assignment(
        nodes,
        counts,
        budgets=budgets,
        speeds=speeds,
        weights=weights,
        columns=None,
        pipeline=True,
    )
    mixed = not _backends_homogeneous(nodes)
    notes = (
        "pipeline layer split; activations cross the private fabric; "
        "weights and KV do not"
    )
    if mixed:
        notes += "; tensor parallel is off because backends differ"
    return PipelineSchedule(
        split_mode="layer",
        tensor_parallel=False,
        weights_on_wire=False,
        n_layers=int(n_layers),
        ranks=ranks,
        collectives=_pipeline_collectives(
            ranks, n_embd=n_embd, n_vocab=n_vocab, batch=int(batch), dtype=dtype
        ),
        notes=notes,
    )


def _f32(value: float) -> float:
    """Round to IEEE-754 binary32, matching llama.cpp's ``float`` splits."""
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def llama_cli_fractions(fractions: Sequence[float]) -> list[float]:
    """Fractions as llama-server parses ``--tensor-split`` (4 decimal places, then float)."""
    return [_f32(float(f"{float(v):.4f}")) for v in fractions]


def llama_cpp_layer_owners(
    fractions: Sequence[float],
    n_layers: int,
    n_gpu_layers: int | None = None,
) -> list[int]:
    """Device index per repeating layer, or -1 when llama.cpp leaves it on CPU.

    Pinned llama.cpp ``v0.5.0`` ``llama_model::load_tensors``:

    * cumulative ``float`` splits, each divided by the float sum
    * ``i_gpu_start = max(n_layer + 1 - n_gpu_layers, 0)``
    * ``act_gpu_layers = min(n_gpu_layers, n_layer + 1)``
    * device = ``std::upper_bound(splits, float(il - i_gpu_start) / act_gpu_layers)``

    ``n_gpu_layers is None`` means full offload (``n_layer + 1``), so every
    repeating layer is on the device list.
    """
    n_layers = int(n_layers)
    n_devices = len(fractions)
    if n_devices == 0:
        raise MpiProtocolError("at least one device is required")
    if n_layers < 0:
        raise MpiProtocolError("n_layers must be >= 0")
    if n_gpu_layers is None:
        n_gpu_layers = n_layers + 1
    n_gpu_layers = int(n_gpu_layers)
    if n_gpu_layers < 0:
        raise MpiProtocolError("n_gpu_layers must be >= 0")
    i_gpu_start = max(n_layers + 1 - n_gpu_layers, 0)
    act_gpu_layers = min(n_gpu_layers, n_layers + 1)
    weights = [_f32(float(w)) for w in fractions]
    if any(w < 0 for w in weights):
        raise MpiProtocolError("tensor_split fractions must be >= 0")
    split_sum = _f32(0.0)
    splits: list[float] = []
    for weight in weights:
        split_sum = _f32(split_sum + weight)
        splits.append(split_sum)
    if split_sum == 0.0:
        raise MpiProtocolError("tensor_split sum is 0")
    splits = [_f32(value / split_sum) for value in splits]
    owners: list[int] = []
    for il in range(n_layers):
        if act_gpu_layers <= 0 or il < i_gpu_start or (il - i_gpu_start) >= act_gpu_layers:
            owners.append(-1)
            continue
        pos = _f32(float(il - i_gpu_start) / float(act_gpu_layers))
        device = n_devices - 1
        for index, split in enumerate(splits):
            if split > pos:
                device = index
                break
        owners.append(device)
    return owners


def _disabled_schedule(plan: dict, notes: str, *, error: str | None = None) -> dict:
    body = {
        "enabled": False,
        "runtime_transport": "llama.cpp-rpc",
        "mpi_moves_activations": False,
        "layer_ranges_authoritative": False,
        "layer_assignment": "unavailable",
        "transport": "mpi",
        "split_mode": "layer",
        "tensor_parallel": False,
        "weights_on_wire": False,
        "n_layers": None,
        "estimated_layers": int(plan.get("estimated_layers") or 0),
        "ranks": [],
        "collectives": [],
        "cpu_layers": None,
        "notes": notes,
    }
    if error is not None:
        body["error"] = error
    return body


def schedule_from_plan(
    plan: dict,
    nodes: Sequence[dict],
    *,
    headroom: float = 0.8,
    block_count: int | None = None,
    n_gpu_layers: int | None = None,
) -> dict:
    """Describe llama.cpp's layer owners. This does not start an MPI job.

    ``estimated_layers`` is a file-size bucket (32, 48, or 80). Ranges are
    published only for a real GGUF ``block_count``, using the v0.5.0
    ``upper_bound`` walk on the 4-decimal ``--tensor-split`` the server parses.
    """
    fractions = [float(x) for x in plan.get("tensor_split") or []]
    ordered = _align_nodes(plan, nodes)
    if not ordered or not fractions:
        return _disabled_schedule(plan, "planner did not produce a tensor_split to describe")
    if len(fractions) != len(ordered):
        raise MpiProtocolError(
            f"tensor_split length {len(fractions)} != nodes {len(ordered)}"
        )
    if block_count is None or int(block_count) <= 0:
        return _disabled_schedule(
            plan,
            "Layer owners are not published. estimated_layers is a file-size bucket "
            "(32/48/80), not GGUF block_count, so it is not the split llama-server loads. "
            "Runtime transport is llama.cpp RPC; this process does not move tensors over MPI.",
        )
    n_layers = int(block_count)
    if n_gpu_layers is None and plan.get("ngl") is not None:
        n_gpu_layers = int(plan["ngl"])
    cli_fractions = llama_cli_fractions(fractions)
    owners = llama_cpp_layer_owners(cli_fractions, n_layers, n_gpu_layers)
    counts = [sum(1 for owner in owners if owner == index) for index in range(len(ordered))]
    budgets = [node_budget_bytes(n, headroom) for n in ordered]
    speeds = [node_speed(n) for n in ordered]
    ranks = _assignment(
        ordered,
        counts,
        budgets=budgets,
        speeds=speeds,
        weights=cli_fractions,
        columns=None,
        pipeline=True,
    )
    # _assignment lays counts contiguously from layer 0. CPU prefix layers are
    # not on a device, so shift GPU ranges to start at i_gpu_start.
    cpu_count = sum(1 for owner in owners if owner < 0)
    if cpu_count:
        shifted: list[RankAssignment] = []
        for rank in ranks:
            shifted.append(
                RankAssignment(
                    rank=rank.rank,
                    node_id=rank.node_id,
                    platform=rank.platform,
                    backend=rank.backend,
                    layer_start=rank.layer_start + cpu_count,
                    layer_end=rank.layer_end + cpu_count,
                    n_layers=rank.n_layers,
                    budget_bytes=rank.budget_bytes,
                    compute_speed=rank.compute_speed,
                    weight=rank.weight,
                    column_width=rank.column_width,
                )
            )
        ranks = shifted
    schedule = PipelineSchedule(
        split_mode="layer",
        tensor_parallel=False,
        weights_on_wire=False,
        n_layers=n_layers,
        ranks=ranks,
        collectives=[],
        notes=(
            "Layer owners follow llama.cpp v0.5.0 load_tensors "
            "(upper_bound on cumulative f32 splits of the 4-decimal tensor_split) "
            "for this GGUF block_count. Runtime transport is llama.cpp RPC; "
            "this schedule does not move tensors."
        ),
    )
    body = schedule.to_dict()
    body["transport"] = "llama.cpp-rpc"
    body["runtime_transport"] = "llama.cpp-rpc"
    body["mpi_moves_activations"] = False
    for rank in body["ranks"]:
        rank["wire"] = "none"
    body["layer_ranges_authoritative"] = True
    body["layer_assignment"] = "llama.cpp-v0.5.0-upper-bound"
    body["block_count"] = n_layers
    body["estimated_layers"] = int(plan.get("estimated_layers") or 0)
    body["cpu_layers"] = [0, cpu_count] if cpu_count else None
    body["n_gpu_layers"] = n_layers + 1 if n_gpu_layers is None else int(n_gpu_layers)
    return body


def attach_mpi_schedule(
    plan: dict,
    nodes: Sequence[dict],
    *,
    headroom: float = 0.8,
    block_count: int | None = None,
    n_gpu_layers: int | None = None,
) -> dict:
    """Never raises. A bad annotation leaves the llama.cpp RPC plan usable."""
    try:
        return schedule_from_plan(
            plan,
            nodes,
            headroom=headroom,
            block_count=block_count,
            n_gpu_layers=n_gpu_layers,
        )
    except Exception as exc:
        return _disabled_schedule(
            plan,
            "MPI schedule was not attached. llama-server still uses RPC.",
            error=str(exc),
        )


def _align_nodes(plan: dict, nodes: Sequence[dict]) -> list[dict]:
    ids = list(plan.get("node_ids") or [])
    if not ids:
        return [dict(n) for n in nodes]
    by_id = {str(n.get("node_id")): dict(n) for n in nodes}
    aligned = []
    for node_id in ids:
        found = by_id.get(str(node_id))
        if found is None:
            raise MpiProtocolError(f"plan node_id {node_id} is not in the cluster")
        aligned.append(found)
    return aligned


def render_flow(schedule: PipelineSchedule) -> str:
    """Draw resident memory, then each hop across the network boundary."""
    lines: list[str] = []
    lines.append(
        f"graph split  layers={schedule.n_layers}  mode={schedule.split_mode}  "
        f"tensor_parallel={schedule.tensor_parallel}  weights_on_wire={schedule.weights_on_wire}"
    )
    lines.append("resident memory (not an MPI buffer)")
    for rank in schedule.ranks:
        gib = rank.budget_bytes / (1024**3)
        cols = "" if rank.column_width is None else f"  cols={rank.column_width}"
        lines.append(
            f"  rank {rank.rank}  {rank.platform or rank.node_id:<8}  {rank.backend:<6}  "
            f"layers [{rank.layer_start:3}, {rank.layer_end:3})  "
            f"n={rank.n_layers:<3}  budget={gib:.1f}GiB  speed={rank.compute_speed:.2f}{cols}"
        )
        lines.append(
            "          [ weights resident | KV resident | activation slot ]"
        )
    lines.append("network boundary  (private fabric; MPI has no auth — do not publish the job)")
    step = 0
    for op in schedule.collectives:
        if op["op"] == "MPI_Bcast":
            nbytes = op.get("nbytes")
            size = "n_embd*batch*sizeof" if nbytes is None else f"{nbytes}B"
            lines.append(
                f"step {step}  MPI_Bcast header {HEADER_BYTES}B then payload {size}  "
                f"root={op['root']}  {op['payload']}  layer={op['layer']}"
            )
            lines.append(
                "        memory: sender keeps weights and KV; only the activation slot is packed"
            )
            lines.append(
                "        network: every rank resizes to header.nbytes (slice width is not assumed)"
            )
        elif op["op"] == "MPI_Allgatherv":
            lines.append(
                f"step {step}  MPI_Allgather counts, then MPI_Allgatherv widths={op.get('widths')}  "
                f"repeat_layers={op.get('repeat_layers')}"
            )
            lines.append(
                "        memory: each rank reads its own column of W; partial Y is the only buffer queued"
            )
            lines.append(
                "        network: unequal widths take Allgatherv; MPI_Allreduce is not called"
            )
        else:
            lines.append(f"step {step}  {op['op']}")
        step += 1
    if schedule.split_mode == "layer":
        lines.append("step reduce  no MPI_Allreduce on this mixed or pipeline cut")
    lines.append("weights on wire: 0")
    return "\n".join(lines)


def run_activation_pipeline(
    schedule: PipelineSchedule,
    token_ids: Sequence[int],
    *,
    n_embd: int,
    local_weights: dict[int, bytes],
) -> dict:
    """Loopback of one forward pass. ``local_weights`` never enter ``pack()``.

    Each owned layer adds the first resident weight byte into hidden[0]. The
    activation that crosses MPI is only that f32 vector. After the last
    boundary every rank holds the same hidden state, and the weight blobs are
    unchanged.
    """
    if schedule.tensor_parallel:
        raise MpiProtocolError("run_activation_pipeline is the layer-pipeline loop")
    size = len(schedule.ranks)
    if set(local_weights) != set(range(size)):
        raise MpiProtocolError("local_weights must cover every rank")
    before = {i: bytes(local_weights[i]) for i in range(size)}
    trace: list[dict] = []
    wire: list[bytes] = []

    slots: list[Tensor | None] = [None] * size
    slots[0] = token_tensor(token_ids, src_rank=0)
    balanced_bcast(slots, root=0, trace=trace, max_payload_bytes=max(0, len(token_ids)) * 4)
    wire.append(slots[0].pack() if slots[0] is not None else b"")

    hidden = [0.0] * int(n_embd)
    if token_ids:
        hidden[0] = float(int(token_ids[0]))
    for rank in schedule.ranks:
        if rank.n_layers <= 0:
            continue
        scale = float(local_weights[rank.rank][0])
        for _layer in range(rank.layer_start, rank.layer_end):
            hidden[0] += scale
        act = activation_f32(
            hidden,
            ne=(int(n_embd), 1, 1, 1),
            src_rank=rank.rank,
            layer=rank.layer_end - 1,
        )
        slots = [None] * size
        slots[rank.rank] = act
        balanced_bcast(slots, root=rank.rank, trace=trace, max_payload_bytes=int(n_embd) * 4)
        if slots[0] is None:
            raise MpiProtocolError("activation bcast did not fill rank 0")
        hidden = f32_values(slots[0])
        wire.append(slots[0].pack())

    after = {i: bytes(local_weights[i]) for i in range(size)}
    if before != after:
        raise MpiProtocolError("resident weights changed during the activation pipeline")
    for blob in wire:
        if b"WEIGHT-STAYS" in blob:
            raise MpiProtocolError("weight sentinel appeared on the MPI buffer")
    return {"hidden": hidden, "trace": trace, "wire": wire, "slots": slots}
