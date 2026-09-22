"""Heterogeneous MPI collective planning and activation wire format."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from kllm.mpi_layer import (
    FLAG_PADDED,
    GGML_TYPE_F32,
    GGML_TYPE_I8,
    HEADER_BYTES,
    MAX_ACTIVATION_BYTES,
    MpiLayerError,
    RankSpec,
    StationaryWeight,
    TcpStar,
    _HEADER,
    allreduce_chunked,
    assert_private_mpi_host,
    bcast_envelope_count,
    build_bcast_envelope,
    demo_mixed_cluster,
    f32_bytes,
    mpi_allreduce_sum,
    pack_message,
    plan_graph_split,
    ranks_from_members,
    recv_frame,
    render_flow,
    simulate_pipeline,
    slice_hidden,
    simulate_tensor_block,
    take_bcast_slot,
    tokens_message,
    unpack_message,
    weighted_allreduce,
    weighted_bcast_slots,
)

_ENVELOPE = bytes.fromhex("020000000102000100000003000003000000040506")
_REPO = Path(__file__).resolve().parents[1]


def _cuda_ranks() -> list[RankSpec]:
    raw = demo_mixed_cluster()
    return [
        RankSpec(
            rank=spec.rank,
            node_id=spec.node_id,
            os_name="linux",
            backend="cuda",
            vram_bytes=spec.vram_bytes,
            compute_weight=spec.compute_weight,
            latency_ms=spec.latency_ms,
            host=f"10.1.0.{spec.rank + 1}",
            label=f"cuda-{spec.rank}",
        )
        for spec in raw
    ]


def test_header_is_64_bytes():
    assert _HEADER.size == HEADER_BYTES == 64


def test_demo_cluster_is_unequal_pipeline():
    plan = plan_graph_split(demo_mixed_cluster(), n_layers=32, hidden=4096, n_ff=14336)
    assert plan.mode == "pipeline"
    assert plan.columns_active is False
    assert [(span.start, span.end) for span in plan.layers] == [(0, 14), (14, 24), (24, 32)]
    assert plan.column_lengths() == (6398, 4286, 3652)
    assert sum(plan.column_lengths()) == 14336
    assert plan.column_lengths()[0] != plan.column_lengths()[1] != plan.column_lengths()[2]
    text = render_flow(plan)
    assert "192.168.10.1" in text and "192.168.10.2" in text and "192.168.10.3" in text
    assert "wire_weight_bytes=0" in text
    assert "allreduce: idle" in text
    assert "TCP 50053" in text
    doc = (_REPO / "docs" / "mpi-collectives.md").read_text(encoding="utf-8")
    section = doc.split("<!-- flow-begin -->", 1)[1].split("<!-- flow-end -->", 1)[0]
    embedded = "\n".join(line for line in section.splitlines() if not line.startswith("```")).strip("\n")
    assert embedded == render_flow(plan).rstrip("\n")


def test_mixed_backends_refuse_tensor_mode():
    with pytest.raises(MpiLayerError, match="mixed GGML backends"):
        plan_graph_split(
            demo_mixed_cluster(),
            n_layers=8,
            hidden=4,
            n_ff=8,
            parallelism="tensor",
        )


def test_same_backend_auto_uses_unequal_columns():
    plan = plan_graph_split(_cuda_ranks(), n_layers=8, hidden=4, n_ff=14336)
    assert plan.mode == "tensor"
    assert plan.columns_active is True
    assert all(span.start == 0 and span.end == 8 for span in plan.layers)
    assert plan.column_lengths() == (6398, 4286, 3652)


def test_equal_capacity_layer_split_breaks_ties_by_rank():
    ranks = [
        RankSpec(
            rank=i,
            node_id=f"n{i}",
            os_name="linux",
            backend="rocm",
            vram_bytes=16 * 1024**3,
            compute_weight=1.0,
            latency_ms=0.0,
            host=f"10.0.0.{i + 1}",
        )
        for i in range(3)
    ]
    plan = plan_graph_split(ranks, n_layers=32, hidden=4, n_ff=30, parallelism="layer")
    assert [span.count for span in plan.layers] == [11, 11, 10]
    even = plan_graph_split(ranks, n_layers=30, hidden=4, n_ff=30, parallelism="layer")
    assert [span.count for span in even.layers] == [10, 10, 10]


def test_pipeline_needs_a_layer_per_rank():
    with pytest.raises(MpiLayerError, match="at least one layer"):
        plan_graph_split(demo_mixed_cluster(), n_layers=2, hidden=4, n_ff=8, parallelism="layer")


def test_public_and_unspecified_binds_are_refused():
    with pytest.raises(MpiLayerError):
        assert_private_mpi_host("8.8.8.8")
    with pytest.raises(MpiLayerError):
        assert_private_mpi_host("0.0.0.0")
    with pytest.raises(MpiLayerError):
        assert_private_mpi_host("169.254.1.1")
    assert_private_mpi_host("192.168.10.1")
    assert_private_mpi_host("127.0.0.1")


def test_weight_types_and_placement_stay_off_the_wire():
    weight = StationaryWeight(
        name="blk.0.attn_q.weight",
        ggml_type=12,
        ne=(4096, 4096, 1, 1),
        file_offset=4096,
        nbytes=8_000_000,
    )
    meta = weight.meta()
    assert "data" not in meta
    assert meta["nbytes"] == 8_000_000
    with pytest.raises(MpiLayerError, match="weight types"):
        pack_message(op=1, ggml_type=12, ne=(1, 1, 1, 1), data=b"\x00")
    plan = plan_graph_split(demo_mixed_cluster(), n_layers=32, hidden=32, n_ff=64)
    from kllm.mpi_layer import placement_message

    blob = placement_message(plan)
    view = unpack_message(blob)
    assert view.ggml_type == GGML_TYPE_I8
    assert b"weight_bytes_on_wire" in view.data
    assert b'"data"' not in view.data


def test_pack_roundtrip_and_padding_flag():
    data = f32_bytes([1.0, 0.0, 0.0, 0.0])
    blob = pack_message(
        op=4,
        ggml_type=GGML_TYPE_F32,
        ne=(4, 1, 1, 1),
        data=data,
        rank=1,
        n_ranks=3,
        root=0,
        split_axis=0xFF,
    )
    view = unpack_message(blob)
    assert view.data == data
    assert view.flags & FLAG_PADDED == 0
    assert view.rank == 1 and view.n_ranks == 3
    padded = pack_message(
        op=1,
        ggml_type=GGML_TYPE_I8,
        ne=(4, 1, 1, 1),
        data=b"abcd",
        mpi_count=8,
    )
    padded_view = unpack_message(padded)
    assert padded_view.flags & FLAG_PADDED
    assert padded_view.data == b"abcd"
    with pytest.raises(MpiLayerError):
        pack_message(
            op=1,
            ggml_type=GGML_TYPE_I8,
            ne=(4, 1, 1, 1),
            data=b"abcd",
            mpi_count=MAX_ACTIVATION_BYTES + 1,
        )


def test_bcast_envelope_pads_unequal_slots():
    shards = [b"\x01\x02", b"\x03", b"\x04\x05\x06"]
    envelope = build_bcast_envelope(shards)
    assert envelope == _ENVELOPE
    recovered, call = weighted_bcast_slots(shards)
    assert recovered == shards
    assert call.primitive == "MPI_Bcast"
    assert call.count_bytes == len(envelope) == 21
    assert call.nonzero_bytes == (2, 1, 3)
    count, stride = bcast_envelope_count([len(s) for s in shards], 3)
    assert (count, stride) == (21, 7)
    assert take_bcast_slot(envelope, 1, 3) == b"\x03"
    assert slice_hidden([[10.0, 11.0, 12.0, 13.0, 14.0]], 1, 3) == [11.0, 12.0, 13.0]


def test_allreduce_rejects_raw_unequal_counts_and_sums_expanded_spans():
    with pytest.raises(MpiLayerError, match="count differs"):
        mpi_allreduce_sum([[1.0, 2.0], [1.0]])
    stitched, call = weighted_allreduce([(0, [1.0, 2.0]), (2, [3.0, 4.0])], full_elems=4)
    assert stitched == [1.0, 2.0, 3.0, 4.0]
    assert call.primitive == "MPI_Allreduce"
    assert call.count_bytes == 16
    partials, _call = weighted_allreduce([(0, [1.0, 0.0]), (0, [0.5, 4.0])], full_elems=2)
    assert partials == [1.5, 4.0]
    with pytest.raises(MpiLayerError, match="disjoint"):
        weighted_allreduce([(0, [1.0, 2.0]), (1, [9.0])], full_elems=3)


def test_chunked_allreduce_matches_the_full_sum():
    buffers = [[1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0]]
    full, _call = mpi_allreduce_sum(buffers)
    chunked, calls = allreduce_chunked(buffers, chunk_elems=3)
    assert chunked == full == [11.0, 22.0, 33.0, 44.0]
    assert len(calls) == 2
    assert all(call.count_bytes == 12 and call.primitive == "MPI_Allreduce" for call in calls)


def test_pipeline_step_moves_activations_only():
    plan = plan_graph_split(demo_mixed_cluster(), n_layers=32, hidden=4, n_ff=8)

    def transform(rank, incoming, tokens):
        assert tokens == [7]
        if incoming is None:
            assert rank == 0
            return [1.0, 1.0, 1.0, 1.0]
        return [value + 1.0 for value in incoming]

    final, calls = simulate_pipeline(plan, [7], transform)
    assert final == [3.0, 3.0, 3.0, 3.0]
    assert [call.primitive for call in calls] == ["MPI_Bcast", "MPI_Send", "MPI_Send", "MPI_Send"]
    assert calls[0].count_bytes == 4
    assert all(call.count_bytes == 16 for call in calls[1:])
    token_blob = tokens_message([7], n_ranks=3)
    assert unpack_message(token_blob).ggml_type == 26


def test_tensor_block_bcast_and_allreduce_keep_one_mpi_count():
    plan = plan_graph_split(_cuda_ranks(), n_layers=4, hidden=4, n_ff=7)

    def column_fn(rank, rows):
        shard = plan.column_preview[rank]
        assert rows == [[9.0, 8.0, 7.0, 6.0]]
        return [float(shard.offset + i + 1) for i in range(shard.length)]

    def row_fn(rank, ffn):
        assert ffn == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        return [float(rank + 1), 0.0, 0.0, 0.0]

    ffn, hidden, calls = simulate_tensor_block(plan, [[9.0, 8.0, 7.0, 6.0]], column_fn, row_fn)
    assert ffn == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    assert hidden == [6.0, 0.0, 0.0, 0.0]
    assert [call.primitive for call in calls] == [
        "MPI_Bcast",
        "MPI_Bcast",
        "MPI_Allreduce",
        "MPI_Allreduce",
    ]
    lengths = plan.column_lengths()
    assert len(set(lengths)) > 1
    expected_count, _stride = bcast_envelope_count([length * 4 for length in lengths], 3)
    assert calls[1].count_bytes == expected_count
    assert calls[1].nonzero_bytes == tuple(length * 4 for length in lengths)
    assert calls[2].count_bytes == 7 * 4
    assert len(set(calls[2].nonzero_bytes)) > 1


def test_master_is_mpi_rank_zero():
    specs = ranks_from_members(
        [
            {
                "node_id": "win",
                "role": "worker",
                "host": "10.0.0.2",
                "backend": "cuda",
                "vram_bytes": 8 * 1024**3,
                "os_name": "windows",
            },
            {
                "node_id": "mac",
                "role": "master",
                "host": "10.0.0.1",
                "backend": "metal",
                "vram_bytes": 16 * 1024**3,
                "os_name": "darwin",
            },
        ]
    )
    assert [spec.node_id for spec in specs] == ["mac", "win"]
    assert specs[0].rank == 0


def test_tcp_star_delivers_each_ranks_slot():
    shards = [b"\x01\x02", b"\x03", b"\x04\x05\x06"]
    envelope = build_bcast_envelope(shards)
    star = TcpStar("127.0.0.1", 0, 3)
    star.start()
    found: dict[int, bytes] = {}

    def worker(rank: int) -> None:
        sock = star.connect(rank)
        try:
            found[rank] = take_bcast_slot(recv_frame(sock), rank, 3)
        finally:
            sock.close()

    threads = [threading.Thread(target=worker, args=(rank,)) for rank in (1, 2)]
    for thread in threads:
        thread.start()
    star.accept_peers()
    star.bcast_from_root(envelope)
    for thread in threads:
        thread.join()
    star.close()
    assert found[1] == b"\x03"
    assert found[2] == b"\x04\x05\x06"


def test_cpp_loops_match_python_bytes():
    compiler = "g++"
    binary = Path("/tmp/kllm-mpi-test")
    sources = [
        _REPO / "cpp" / "kllm-mpi" / "kllm_mpi.cpp",
        _REPO / "cpp" / "kllm-mpi" / "test_driver.cpp",
    ]
    built = subprocess.run(
        [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-o", str(binary), *map(str, sources)],
        check=False,
        capture_output=True,
        text=True,
    )
    if built.returncode != 0:
        pytest.fail(built.stderr)
    out = subprocess.check_output([str(binary)], text=True)
    assert "envelope 020000000102000100000003000003000000040506" in out
    data = f32_bytes([1.0, 0.0, 0.0, 0.0])
    blob = pack_message(
        op=4,
        ggml_type=GGML_TYPE_F32,
        ne=(4, 1, 1, 1),
        data=data,
        rank=1,
        n_ranks=3,
        root=0,
        split_axis=0xFF,
    )
    assert f"frame {blob.hex()}" in out
    assert out.strip().endswith("ok")
