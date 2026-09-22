# Experimental MPI activation collectives

Production inference stays on llama.cpp RPC with `--split-mode layer`. This path does not replace that engine. It plans a weighted split and moves **tokens and activations** between ranks. GGML weight tensors stay on the device that owns them.

OpenMPI, MPICH, and MS-MPI do not form one communicator across macOS, Linux, and Windows. The loops in `python/kllm/mpi_layer.py` and `cpp/kllm-mpi/` are the counts a single MPI library would use inside one OS, and the counts the private-LAN TCP star uses for a mixed cluster. Port **50053** is unauthenticated. Bind a private IPv4 address. Do not publish it. The same rule applies to rpc-server on port **50052**.

## Graph split

Each rank gets a score from VRAM × headroom, a compute weight, and a latency factor `1 / (1 + latency_ms / 10)`. Integer layer counts and column widths use largest-remainder so a 32 GB Metal node, a 16 GB ROCm node, and a 12 GB CUDA node do not receive equal slices.

Mixed backends (Metal, CUDA, ROCm, Vulkan, CPU) take a **pipeline** placement. Tensor-parallel GEMMs across those kernels drift. Tensor mode, where every rank holds every layer and an unequal column shard, is selected only when every rank reports the same backend.

## What the collectives actually send

`MPI_Bcast` and `MPI_Allreduce` require the same count on every rank. The wrappers never pass `total / n_ranks` as that count.

- **Token bcast.** Identical I32 ids. Count is `n_tokens * 4`.
- **Weighted bcast.** Root packs one slot per rank: `u32` logical length, payload, zero pad out to the longest shard. Every rank calls `MPI_Bcast` with `count = n_ranks * stride` and keeps its own slot.
- **Pipeline handoff.** `MPI_Send` of the full f32 hidden state to the next stage. Weights are not in the packet.
- **Weighted allreduce.** Each rank expands its span into a dense buffer of `full_elems` floats. Non-owned elements are zero. `MPI_Allreduce(MPI_SUM)` uses that one width. Disjoint column spans reconstruct the concatenation. Equal full-width spans add row-parallel partials. A raw allreduce of unequal lengths is rejected.

Chunked reduction uses the same sum. Every chunk, including the tail, is padded to `chunk_elems` so the MPI count matches across ranks.

## Three-node walkthrough

Cluster: macOS Metal 32 GB at `192.168.10.1`, Ubuntu ROCm 16 GB at `192.168.10.2`, Windows CUDA 12 GB at `192.168.10.3`. Model shape used for the figure: 32 layers, hidden 4096, FFN 14336, one token.

Step 0 places layers `[0, 14)`, `[14, 24)`, and `[24, 32)`. Each node maps its GGUF slice locally. The placement message is JSON metadata with `weight_bytes_on_wire = 0`.

Step 1 broadcasts the token id from the Mac. Four bytes, copied to both workers. Scratch activations are still empty.

Step 2 runs the resident layers and forwards one hidden state (16384 bytes) Mac → Ubuntu after layer 13, then Ubuntu → Windows after layer 23. The Windows node returns that activation to the Mac for sampling. Allreduce does not run: a partial GEMM never crosses a backend boundary.

The column lengths `(6398, 4286, 3652)` are the reduction the same scores would use on a single backend. They are recorded on this split and left inactive.

<!-- flow-begin -->
```text
graph-split mode=pipeline layers=32 hidden=4096 n_ff=14336
reason: mixed backends (cuda,metal,rocm) use pipeline handoff; tensor-parallel GEMMs would cross Metal/CUDA/ROCm kernels
network boundary: private IPv4 only, TCP 50053, no auth — do not publish (same posture as rpc-server :50052)
weights: stationary GGML tensors; quantized bytes are not an MPI payload

memory map before the first token
  [0] macOS/Metal mac-master @ 192.168.10.1:50053
      score=2.706490e+10 fraction=0.4463 vram=34359738368 compute=1.0 latency_ms=0.4
      layers [0, 14) resident on device
      column shard offset=0 length=6398 active=false
      weight_elems~3405774848 activation_scratch=16384 bytes (empty until step 2)
      wire_weight_bytes=0
  [1] Ubuntu/ROCm ubuntu-rocm @ 192.168.10.2:50053
      score=1.813416e+10 fraction=0.2990 vram=17179869184 compute=1.35 latency_ms=0.6
      layers [14, 24) resident on device
      column shard offset=6398 length=4286 active=false
      weight_elems~2432696320 activation_scratch=16384 bytes (empty until step 2)
      wire_weight_bytes=0
  [2] Windows/CUDA windows-cuda @ 192.168.10.3:50053
      score=1.544959e+10 fraction=0.2547 vram=12884901888 compute=1.55 latency_ms=0.9
      layers [24, 32) resident on device
      column shard offset=10684 length=3652 active=false
      weight_elems~1946157056 activation_scratch=16384 bytes (empty until step 2)
      wire_weight_bytes=0

step 0  placement metadata
  memory: GGUF slices stay mapped; activation scratch is empty
  network: MPI_Bcast of the placement JSON (spans, column offsets, weight_bytes_on_wire=0)
  boundary: rank 0 -> every worker, inside the private subnet

step 1  token broadcast
  memory: I32 token ids replicated on every rank (4 bytes)
  network: MPI_Bcast count=4 root=0
  boundary: equal count; tokens are not capacity-weighted
  weights: untouched

step 2  pipeline handoff (mixed backends)
  memory: rank 0 embeds tokens into f32 hidden; later ranks still hold weights only
  local compute: layers [0, 14) stay on macOS/Metal
  network: MPI_Send activation 16384 bytes  192.168.10.1 -> 192.168.10.2  after layer 13
  memory: macOS/Metal forwards the hidden state; Ubuntu/ROCm receives it and runs layers [14, 24)
  weights: both devices keep their own slices; the packet is the activation
  boundary: private link 192.168.10.1 -> 192.168.10.2:50053
  network: MPI_Send activation 16384 bytes  192.168.10.2 -> 192.168.10.3  after layer 23
  memory: Ubuntu/ROCm forwards the hidden state; Windows/CUDA receives it and runs layers [24, 32)
  weights: both devices keep their own slices; the packet is the activation
  boundary: private link 192.168.10.2 -> 192.168.10.3:50053
  network: MPI_Send final activation 16384 bytes back to 192.168.10.1 for sampling
  allreduce: idle — partial GEMMs are not split across Metal/ROCm/CUDA

reduction loop reference (same capacity scores, column axis)
  scheduled on this split: no
  unequal column lengths: (6398, 4286, 3652)
  MPI_Bcast envelope: n_ranks=3 stride=25596 mpi_count=76788
  MPI_Allreduce SUM: mpi_count=57344 bytes over n_ff*n_tokens f32 elements
  non-zero span per rank: column_length * n_tokens * 4; the rest of the buffer is zero
  a homogeneous MPI_Allreduce(total/n_ranks) would be wrong for these lengths
```
<!-- flow-end -->

## Code

| Piece | Role |
|-------|------|
| `python/kllm/mpi_layer.py` | Planner, wire format, bcast envelope, allreduce loops, TCP star |
| `cpp/kllm-mpi/kllm_mpi.hpp` | Same header, envelope, and reduction loops for an in-process rank |
| `tests/test_mpi_layer.py` | Unequal shards, pipeline exchange, TCP delivery, C++ byte match |
