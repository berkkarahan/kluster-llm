"""MPI activation collectives: unequal slices, resident weights, mixed backends."""

from __future__ import annotations

import pytest

from kllm.mpi_comm import (
    EXAMPLE_LAYERS,
    EXAMPLE_MODEL_BYTES,
    FLAG_ACTIVATION,
    HEADER_BYTES,
    MpiProtocolError,
    Tensor,
    activation_f32,
    balanced_allreduce,
    balanced_bcast,
    contiguous_nb,
    example_mixed_cluster,
    f32_values,
    largest_remainder,
    payload_chunks,
    render_flow,
    run_activation_pipeline,
    schedule_from_plan,
    split_graph,
)


def test_mixed_cluster_is_a_pipeline_not_equal_slices():
    schedule = split_graph(
        example_mixed_cluster(),
        n_layers=EXAMPLE_LAYERS,
        model_size_bytes=EXAMPLE_MODEL_BYTES,
        n_embd=4096,
        n_vocab=32000,
    )
    counts = [rank.n_layers for rank in schedule.ranks]
    # Mac 15, Ubuntu 10, Windows 7. The fastest GPU is the smallest.
    assert counts == [15, 10, 7]
    assert sum(counts) == EXAMPLE_LAYERS
    assert [rank.backend for rank in schedule.ranks] == ["metal", "rocm", "cuda"]
    assert schedule.tensor_parallel is False
    assert schedule.weights_on_wire is False
    assert schedule.split_mode == "layer"
    ranges = [(rank.layer_start, rank.layer_end) for rank in schedule.ranks]
    assert ranges == [(0, 15), (15, 25), (25, 32)]

    flow = render_flow(schedule)
    assert "layers [  0,  15)" in flow
    assert "layers [ 15,  25)" in flow
    assert "layers [ 25,  32)" in flow
    assert "weights on wire: 0" in flow
    assert "do not publish" in flow
    assert "no MPI_Allreduce" in flow
    assert "MPI_Bcast header 96B then payload 16384B" in flow
    assert all(op["weights_moved"] is False for op in schedule.collectives)


def test_tensor_parallel_across_backends_is_refused():
    with pytest.raises(MpiProtocolError, match="mixed backends"):
        split_graph(
            example_mixed_cluster(),
            n_layers=8,
            tensor_parallel=True,
            n_cols=64,
        )


def test_same_backend_columns_follow_speed():
    gib = 1024**3
    nodes = [
        {
            "node_id": "a",
            "platform": "linux",
            "backend": "cuda",
            "vram_bytes": 24 * gib,
            "compute_speed": 1.0,
        },
        {
            "node_id": "b",
            "platform": "linux",
            "backend": "cuda",
            "vram_bytes": 24 * gib,
            "compute_speed": 1.4,
        },
    ]
    schedule = split_graph(nodes, n_layers=4, tensor_parallel=True, n_cols=100, n_embd=8)
    assert [rank.column_width for rank in schedule.ranks] == [42, 58]
    assert all(rank.layer_end - rank.layer_start == 4 for rank in schedule.ranks)
    ops = [op["op"] for op in schedule.collectives]
    assert ops == ["MPI_Bcast", "MPI_Bcast", "MPI_Allgatherv", "MPI_Bcast"]
    assert schedule.collectives[2]["widths"] == [42, 58]
    flow = render_flow(schedule)
    assert "MPI_Allgatherv" in flow
    assert "MPI_Allreduce is not called" in flow


def test_bcast_resizes_receivers_and_keeps_a_ggml_header():
    root = activation_f32([1.0, 2.0, 3.0, 4.0], ne=(4, 1, 1, 1), src_rank=0, layer=14)
    slots = [root, activation_f32([0.0], ne=(1,), src_rank=1, layer=0), None]
    trace: list[dict] = []
    balanced_bcast(slots, 0, trace)
    assert [f32_values(slot) for slot in slots] == [[1.0, 2.0, 3.0, 4.0]] * 3
    assert slots[1] is not None and slots[1].ne[0] == 4
    assert trace[0] == {"op": "MPI_Bcast", "what": "header", "bytes": HEADER_BYTES, "root": 0}
    assert trace[1]["what"] == "payload" and trace[1]["bytes"] == 16 and trace[1]["dynamic"] is True
    packed = slots[2].pack() if slots[2] is not None else b""
    assert packed[:4] == b"KMPI"
    assert b"WEIGHT-STAYS" not in packed


def test_packer_matches_the_cpp_golden_header():
    tensor = activation_f32([1.0, 2.0], ne=(2, 1, 1, 1), src_rank=0, layer=14)
    assert tensor.pack().hex() == (
        "4b4d50490100010000000000000000000e000000010000000200000000000000"
        "0100000000000000010000000000000001000000000000000400000000000000"
        "0800000000000000080000000000000008000000000000000800000000000000"
        "0000803f00000040"
    )


def test_weights_and_quantized_blocks_do_not_pack():
    dims = (2, 1, 1, 1)
    bad_type = Tensor(
        ggml_type=12,
        flags=FLAG_ACTIVATION,
        src_rank=0,
        layer=0,
        collective=1,
        ne=dims,
        nb=contiguous_nb(dims, 4),
        data=b"\x00" * 8,
    )
    with pytest.raises(MpiProtocolError, match="quantized"):
        bad_type.pack()

    flagged = activation_f32([1.0, 2.0], ne=dims, src_rank=0, layer=0)
    flagged.flags = 0x8000
    with pytest.raises(MpiProtocolError, match="weights stay"):
        flagged.pack()

    skewed = activation_f32([1.0, 2.0], ne=dims, src_rank=0, layer=0)
    skewed.nb = (0, 0, 0, 0)
    with pytest.raises(MpiProtocolError, match="contiguous"):
        skewed.pack()


def test_row_sum_allreduce_and_unequal_refusal():
    left = activation_f32([1.0, 2.0, 3.0], ne=(3,), src_rank=0, layer=1, partial=True)
    right = activation_f32([10.0, 20.0, 30.0], ne=(3,), src_rank=1, layer=1, partial=True)
    slots = [left, right]
    trace: list[dict] = []
    balanced_allreduce(slots, kind="row_sum", trace=trace)
    assert f32_values(slots[0]) == [11.0, 22.0, 33.0]
    assert f32_values(slots[1]) == [11.0, 22.0, 33.0]
    assert any(event["op"] == "MPI_Allreduce" and event["count"] == 3 for event in trace)
    assert all(event["op"] != "MPI_Allgatherv" for event in trace)

    short = activation_f32([1.0, 2.0], ne=(2,), src_rank=0, layer=1, partial=True)
    long = activation_f32([3.0, 4.0, 5.0], ne=(3,), src_rank=1, layer=1, partial=True)
    trace = []
    with pytest.raises(MpiProtocolError, match="pad"):
        balanced_allreduce([short, long], kind="row_sum", trace=trace)
    assert trace[0]["op"] == "MPI_Allgather"
    assert all(event["op"] != "MPI_Allreduce" for event in trace)


def test_column_concat_uses_allgatherv_and_checks_weights():
    parts = [
        activation_f32([1.0, 2.0], ne=(2,), src_rank=0, layer=4, partial=True),
        activation_f32([3.0, 4.0, 5.0], ne=(3,), src_rank=1, layer=4, partial=True),
    ]
    trace: list[dict] = []
    balanced_allreduce(parts, kind="column_concat", trace=trace)
    assert f32_values(parts[0]) == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert parts[0].ne[0] == 5
    gatherv = next(event for event in trace if event["op"] == "MPI_Allgatherv")
    assert gatherv["counts"] == [8, 12]
    assert gatherv["displs"] == [0, 8]
    assert all(event["op"] != "MPI_Allreduce" for event in trace)

    wide = activation_f32([0.0] * 42, ne=(42,), src_rank=0, layer=1, partial=True)
    rest = activation_f32([0.0] * 58, ne=(58,), src_rank=1, layer=1, partial=True)
    slots = [wide, rest]
    balanced_allreduce(slots, kind="column_concat", weights=[1.0, 1.4])
    assert slots[0].ne[0] == 100
    assert slots[1].ne[0] == 100

    even = [
        activation_f32([0.0] * 50, ne=(50,), src_rank=0, layer=1, partial=True),
        activation_f32([0.0] * 50, ne=(50,), src_rank=1, layer=1, partial=True),
    ]
    with pytest.raises(MpiProtocolError, match="disagree with weights"):
        balanced_allreduce(even, kind="column_concat", weights=[1.0, 1.4])


def test_pipeline_moves_activations_and_leaves_weights_resident():
    schedule = split_graph(
        example_mixed_cluster(),
        n_layers=EXAMPLE_LAYERS,
        model_size_bytes=EXAMPLE_MODEL_BYTES,
        n_embd=4,
    )
    weights = {rank: b"WEIGHT-STAYS-ON-NODE-" + bytes([rank + 1]) * 32 for rank in range(3)}
    before = {rank: bytes(blob) for rank, blob in weights.items()}
    result = run_activation_pipeline(schedule, [7], n_embd=4, local_weights=weights)
    assert weights == before
    # First byte of every resident blob is ord("W") == 87, added once per layer.
    assert result["hidden"] == [7.0 + 87.0 * EXAMPLE_LAYERS, 0.0, 0.0, 0.0]
    assert all(b"WEIGHT-STAYS" not in blob for blob in result["wire"])
    assert result["trace"][0]["what"] == "header"
    assert all(event["op"] == "MPI_Bcast" for event in result["trace"])


def test_schedule_from_plan_follows_production_fractions():
    plan = {
        "estimated_layers": 31,
        "tensor_split": [0.5, 0.5],
        "node_ids": ["win", "mac"],
    }
    nodes = [
        {"node_id": "mac", "backend": "metal", "platform": "macos", "total_ram_bytes": 32 << 30, "unified_memory": True},
        {"node_id": "win", "backend": "cuda", "platform": "windows", "vram_bytes": 12 << 30},
    ]
    schedule = schedule_from_plan(plan, nodes, headroom=0.8)
    assert [rank["n_layers"] for rank in schedule["ranks"]] == [16, 15]
    assert [rank["node_id"] for rank in schedule["ranks"]] == ["win", "mac"]
    assert schedule["weights_on_wire"] is False
    assert schedule["split_mode"] == "layer"


def test_largest_remainder_and_chunks():
    assert largest_remainder([1.0, 1.4], 100) == [42, 58]
    assert payload_chunks((1 << 30) + 10) == [1 << 30, 10]
    assert payload_chunks(0) == [0]


def test_memory_caps_reject_a_model_that_does_not_fit():
    nodes = example_mixed_cluster()
    for node in nodes:
        # Positive budget, but far below one layer of a 40 GiB model.
        node["vram_bytes"] = 1024 * 1024
        node["total_ram_bytes"] = 1024 * 1024
    with pytest.raises(MpiProtocolError, match="caps"):
        split_graph(nodes, n_layers=32, model_size_bytes=EXAMPLE_MODEL_BYTES)
