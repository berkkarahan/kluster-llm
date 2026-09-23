# Changelog

> KLLM fork note: this preserves upstream release history. The KLLM provider
> deliberately does not expose the upstream POSIX `shm` feature; its supported
> baseline is the reviewed TCP transport described in `README.md`.

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-07-05

Initial release: a pure-Rust MPI runtime with an rsmpi-compatible API and no C
library dependency.

### Added

- **Runtime & bootstrap**: pure-Rust TCP transport with per-connection reader
  threads and a matched inbox (`ANY_SOURCE` / `ANY_TAG`, non-overtaking order);
  `mpiexec` / `mpirun` launcher with a PMI-style rendezvous; singleton mode when
  run without a launcher.
- **Multi-host**: `mpiexec --host h1,h2,... --remote-bin PATH` launches ranks on
  remote hosts over SSH; nodes auto-detect their routable IP. Verified on a
  heterogeneous macOS-arm64 + Linux-x86_64 LAN running the full suite (incl.
  one-sided RMA) across nodes.
- **environment**: `initialize`, `initialize_with_threading`, `Universe`,
  `Threading`, `time`, `time_resolution`, `processor_name`, `library_version`,
  `version`, `threading_support`.
- **topology**: `Communicator`, `SimpleCommunicator` (+ `SystemCommunicator` /
  `UserCommunicator` aliases), `Process`, `AnyProcess`, `Group`, `Color`;
  `split_by_color[_with_key]`, `split_by_subgroup`, `duplicate`, `group`,
  `compare`, `abort`, communicator naming, `create_cartesian_communicator` /
  `CartesianCommunicator`, `pack` / `unpack`.
- **point_to_point**: `Source` / `Destination`, all send modes, blocking and
  `immediate_*` sends/receives, blocking/immediate/matched probes, `Status`,
  `Message`, `send_receive*`, `ReceiveFuture`.
- **collective**: `CommunicatorCollectives` and `Root` — barrier, broadcast,
  gather/scatter (+ varcount), allgather (+ varcount), all-to-all (+ varcount),
  reduce/allreduce, inclusive/exclusive scan, reduce-scatter, and `immediate_*`
  variants; `Operation`, `SystemOperation`, `UserOperation`, `reduce_local_into`.
- **datatype**: `Equivalence` for all primitives, `Buffer` / `BufferMut` /
  `Collection`, `Partition` / `PartitionMut`; `UserDatatype`
  (contiguous/vector/indexed/struct) with `pack`/`unpack`, `View` / `MutView`;
  `#[derive(Equivalence)]` behind the `derive` feature.
- **request**: `Request` (panics if dropped in-flight), `Scope` / `LocalScope` /
  `StaticScope`, `scope`, `WaitGuard`, `CancelGuard`, `wait_any`, `wait_all`.
- **window**: one-sided RMA — `Window` with `put` / `get` / `accumulate` /
  `fence` / `lock` / `unlock`, via an active-message handler on the transport.
- **io**: parallel file access — `File` with explicit-offset and collective
  read / write, size / sync, and mode flags.
- **attribute**: keyvals and `set_attr` / `get_attr` / `has_attr` / `delete_attr`.
- **topology**: Cartesian and graph communicators (+ `neighbor_all_gather_into`
  / `neighbor_all_to_all_into`), inter-communicators.
- **error**: `MPI_ERR_*` classes, `error_string`/`error_class`,
  `ErrorHandler::{Fatal, Return}` set/get.
- **dpm**: dynamic process management — `Root::spawn` launches a new world and
  returns an inter-communicator; `Communicator::parent` on the child side.
- **request**: `GeneralizedRequest` / `GeneralizedRequestCompleter`.
- **raw**: `AsRaw`, `AsRawMut`, `FromRaw`, `MatchesRaw`.

### Performance & robustness

- Collectives use scalable algorithms — binomial-tree broadcast/reduce,
  dissemination barrier, ring allgather, tree all-reduce (`O(log n)`) — with an
  ordered fallback for non-commutative reductions.
- `MPI_Finalize` (Universe drop) is a collective barrier; no messages are lost
  on a clean exit.
- `MPI_Abort` and panics propagate to every peer, so a failure never leaves
  ranks blocked in a receive.
- `MPI_Wtime` uses a monotonic clock.
- Bundled `pingpong` benchmark reports latency / bandwidth / all-reduce timing.
- **Large-message rendezvous** (>64 KiB): the receiver pulls the data when
  ready, so a fast sender cannot grow a slow receiver's memory without bound;
  small messages stay eager.
- **`MPI_IN_PLACE`** collective variants (`all_reduce_into_in_place`,
  `all_gather_into_in_place`, `reduce_into_root_in_place`,
  `gather_into_root_in_place`).
- **Persistent requests** (`Destination::send_init` / `Source::receive_init`
  → `PersistentRequest::{start, wait}`) and **generalized requests**
  (`GeneralizedRequest`).
- **Deadlock diagnostic**: a receive blocked unusually long names the stuck
  operation instead of hanging silently.

### Additional coverage

- **Shared-memory transport** behind the opt-in `shm` feature (POSIX shm ring
  via `libc`): same-host ranks bypass loopback TCP for ~3× lower small-message
  latency (19 µs → 6.5 µs); default build stays zero-dependency.
- **`MPI_Dist_graph_create_adjacent`** (`DistGraphCommunicator`) with per-rank
  in/out neighbours and neighbourhood collectives.
- **Big-endian interoperability**: typed payloads for point-to-point,
  collectives and rendezvous are transcoded to/from the canonical little-endian
  wire (compiled out on little-endian; the BE path type-checks on `s390x`). RMA
  and derived structs remain same-endian (startup warning for mixed-endian jobs).
- **Fallible API**: `try_*` `Result`-returning counterparts for the collectives
  and point-to-point sends.
- **Truly-async collectives**: `immediate_barrier` and `immediate_all_reduce_into`
  progress on a background thread (fresh per-op context) for real overlap.

### Verification

- Property/fuzz unit tests for reductions and datatype pack/unpack.
- `MPI_THREAD_MULTIPLE` concurrency stress test (`thread_multiple` example).
- The `unsafe` byte-reinterpretation / reduction code passes under **Miri**.
- GitHub Actions CI (Linux + macOS): build, multi-process tests, clippy,
  rustfmt, rustdoc, and Miri.

### Not claimed

Bit-for-bit coverage of every one of the standard's 400+ entry points and
corner-case flags, big-endian interoperability, and the
`MPI_Dist_graph_create_adjacent` constructor variant. See `PARITY.md`.
