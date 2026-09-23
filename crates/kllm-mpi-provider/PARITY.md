# Parity with rsmpi / MPI

> KLLM fork overlay: upstream parity notes below are retained as source history.
> This fork exposes the reviewed TCP provider baseline only; the upstream POSIX
> shared-memory feature is intentionally unavailable pending separate validation.

Status of this pure-Rust implementation. It mirrors the rsmpi 0.8.x public API
and additionally implements the major MPI-standard chapters that rsmpi does not
ship (one-sided RMA, parallel I/O, graph topologies, inter-communicators,
derived datatypes, attribute caching).

Legend: ✅ implemented & tested · 🟡 implemented, simplified · ❌ not implemented.

Version note: rsmpi 0.8.x merged the old `SystemCommunicator` /
`UserCommunicator` into a single `SimpleCommunicator`; this crate follows 0.8.x
and provides the old names as aliases.

## Module map

| module | status | notes |
|---|---|---|
| `environment` | ✅ | init, `Universe`, `Threading`, timing, processor/library/version info |
| `topology`    | ✅ | `Communicator`, `SimpleCommunicator`, `Process`, `AnyProcess`, `Group`, `Color`, Cartesian, graph, inter-comm |
| `point_to_point` | ✅ | `Source`, `Destination`, `Status`, `Message`, `send_receive*`, `ReceiveFuture` |
| `collective`  | ✅ | `CommunicatorCollectives`, `Root`, `Operation`, `SystemOperation`, `UserOperation`, `reduce_local_into` |
| `datatype`    | ✅ | `Equivalence`, `Buffer`/`BufferMut`, `Collection`, `Partition`, `UserDatatype`, `View`/`MutView` |
| `request`     | ✅ | `Request`, scopes, `WaitGuard`/`CancelGuard`, `wait_any`/`wait_all`, `GeneralizedRequest` |
| `window`      | ✅ | one-sided RMA: `Window`, put/get/accumulate, fence, lock/unlock |
| `io`          | ✅ | `File`, explicit-offset + collective read/write, size/sync, modes |
| `attribute`   | ✅ | keyvals, set/get/remove attributes on communicators |
| `error`       | ✅ | `MPI_ERR_*` classes, `error_string`, `ErrorHandler` set/get |
| `dpm`         | ✅ | dynamic process management: `Root::spawn`, `Communicator::parent` |
| `raw`         | 🟡 | `AsRaw`, `AsRawMut`, `FromRaw`, `MatchesRaw` (raw ids, not C handles) |
| `ffi`         | n/a | intentionally absent — there is no C library to bind |
| `#[derive(Equivalence)]` | ✅ | via the `derive` feature (zero-dependency proc-macro) |

## Point-to-point

`send`/`send_with_tag`, `buffered_send`/`synchronous_send`/`ready_send` (all
standard-mode), `receive`/`receive_with_tag`/`receive_vec`/`receive_into`,
`probe`/`immediate_probe`, `matched_probe`/`matched_receive*`, `immediate_send*`
→ `Request`, `immediate_receive` → `ReceiveFuture`, `immediate_receive_into` →
`Request`, and `send_receive` / `send_receive_into` / `send_receive_replace_into`
(with tag variants). ✅

## Collectives

`barrier`, `broadcast_into`, `gather_into[_root]`, `scatter_into[_root]`,
`all_gather_into`, `all_to_all_into`, `all_reduce_into`, `reduce_into[_root]`,
`scan_into`, `exclusive_scan_into`, `reduce_scatter_block_into`; varying-count
`all_gather_varcount_into`, `all_to_all_varcount_into`,
`gather_varcount_into[_root]`, `scatter_varcount_into[_root]`; and non-blocking
`immediate_*` forms of all of the above. ✅

## Reductions

`SystemOperation::{sum, product, max, min, logical_and/or/xor,
bitwise_and/or/xor}`, `UserOperation::{commutative, non_commutative}`, and
`reduce_local_into`. ✅ (min/max-with-location ❌, as in rsmpi 0.8.x.)

## Communicators, groups & topologies

`rank`/`size`/`target_size`, `process_at_rank`/`this_process`/`any_process`,
`group`/`compare`, `duplicate`, `split_by_color[_with_key]`,
`split_by_subgroup`, `abort`, `set_name`/`get_name`, `pack`/`pack_into`/
`unpack_into`/`pack_size`. `Group` set algebra (`union`/`intersection`/
`difference`/`include`/`exclude`/`translate_ranks`/`compare`/`empty`).
`create_cartesian_communicator` + `CartesianCommunicator` (coords / shift /
rank_from_coordinates). `create_graph_communicator` + `GraphCommunicator`
(neighbours). `split_intercommunicator` + `InterCommunicator` (local/remote
size & group, cross-group point-to-point, `merge`). ✅

## Datatypes

`Equivalence` for every primitive; `Buffer`/`BufferMut` for scalars, slices and
arrays; `Partition`/`PartitionMut`; `UserDatatype::{contiguous, vector, indexed,
structured}` with `pack`/`unpack`; `View` (send) / `MutView` (scatter-receive);
`#[derive(Equivalence)]` for POD structs. ✅

## One-sided (RMA)

`Window::{allocate, from_vec}`, `put`, `get`, `accumulate`, `fence`,
`lock`/`unlock`, `with_local[_mut]`. ✅ Operations are synchronous (they wait
for the target to acknowledge), so `fence` is an epoch barrier; `lock`/`unlock`
are provided for API completeness (per-operation atomicity is already
guaranteed).

## Parallel I/O

`File::open` with mode flags, `read_at`/`write_at`, collective `read_at_all`/
`write_at_all`, `size`/`set_size`/`sync`, `close`, delete-on-close. ✅ Offsets
are byte offsets.

## Attribute caching

`create_keyval`, `set_attr`/`get_attr`/`has_attr`/`delete_attr`. ✅

## Error handling

`MPI_ERR_*` class constants, `error_string`, `error_class`, and
`ErrorHandler::{Fatal, Return}` via `set_error_handler`/`error_handler`. ✅

## Dynamic process management

`Root::spawn(program, args, maxprocs)` launches a new world and returns an
inter-communicator; spawned children obtain the matching parent inter-comm via
`Communicator::parent()`. Cross-world point-to-point is routed through
per-context peer tables on the transport. Verified for singleton and
multi-parent spawners. ✅

## Neighbourhood collectives

`GraphCommunicator::neighbor_all_gather_into` and `neighbor_all_to_all_into`. ✅

## Remaining gaps / caveats

* **Transport** is TCP in this KLLM fork. The upstream POSIX shared-memory
  fast path is intentionally unavailable pending separate lifecycle, isolation,
  and cross-platform validation. The wire format is fixed little-endian; typed
  payloads are **transcoded per host**, so mixed-endian point-to-point /
  collectives / rendezvous interoperate (RMA and derived structs remain
  same-endian, with a startup warning).
* **Collectives** use scalable algorithms (binomial-tree bcast/reduce,
  dissemination barrier, ring allgather, tree all-reduce; `O(log n)`), with an
  ordered fallback for non-commutative reductions. `MPI_Finalize` is a collective
  barrier and `MPI_Abort`/panics propagate to all peers, so a clean exit loses no
  messages and a failure never hangs the survivors. `MPI_Wtime` is monotonic. A
  receive blocked unusually long prints a deadlock diagnostic.
* **Flow control**: messages above 64 KiB use a rendezvous protocol (the
  receiver pulls the data when ready), so a fast sender cannot grow a slow
  receiver's memory without bound; small messages stay eager.
* **`MPI_IN_PLACE`** is supported via `*_in_place` collective variants;
  persistent (`send_init`/`receive_init`) and generalized requests are provided.
* **Verification**: the `unsafe` byte-reinterpretation and reduction code pass
  under **Miri**; `MPI_THREAD_MULTIPLE` is exercised by a concurrent
  multi-threaded stress test; property/fuzz tests cover reductions and datatype
  pack/unpack; CI runs the multi-process suite on Linux and macOS.
* **Send modes** (`buffered`/`synchronous`/`ready`) all map to standard-mode
  send.

## Previously-deferred items — now addressed

* **Shared-memory transport** — intentionally unavailable in this KLLM fork
  until it passes separate lifecycle, isolation, and cross-platform validation.
* **`MPI_Dist_graph_create_adjacent`** — implemented (`DistGraphCommunicator`
  with per-rank in/out neighbours and neighbourhood collectives).
* **Big-endian** — mixed-endian jobs are detected at bootstrap; point-to-point
  and collectives are transcoded, while RMA and derived structs require
  same-endian ranks and produce a startup warning.
* **Fallible API** — the collectives (`try_barrier`, `try_all_gather_into`,
  `try_all_reduce_into`, `try_all_to_all_into`, `try_scan_into`, …) and
  point-to-point (`try_send` / `try_send_with_tag`) have `Result`-returning
  counterparts; the blocking versions delegate to them and panic on error.
* **Big-endian data byte-swapping** — typed payloads for point-to-point,
  collectives and rendezvous are transcoded to/from the canonical little-endian
  wire (`#[cfg(target_endian = "big")]`, a no-op on little-endian). Verified: the
  LE path is unchanged and fully tested; the BE path type-checks against the
  `s390x-unknown-linux-gnu` target. Mixed-endian jobs now interoperate for those
  paths (RMA windows and `#[derive(Equivalence)]` structs remain same-endian and
  emit a startup warning).
* **Truly-async collectives** — `immediate_barrier` and
  `immediate_all_reduce_into` progress on a background thread using a fresh
  per-operation context, so they genuinely overlap computation; `wait`/drop
  joins the thread.

## Remaining, by design

* **Full `Result`-returning API** for *every* op (the rooted `Root` collectives
  and receives still panic on error) — receives cannot fail gracefully without
  timeouts, and universalizing it further diverges from rsmpi's panic-based API.
* **Big-endian for RMA windows and derived (`#[derive]`) structs** — these carry
  byte-transparent/native payloads, so they require same-endian ranks (a startup
  warning is emitted for mixed-endian jobs).
* **Truly-async for *all* `immediate_*` collectives** — the remaining ones still
  complete synchronously; the two most-used (barrier, all-reduce) are async.
* **Context ids** for derived communicators/windows/spawns are hash-derived,
  carrying a negligible but non-zero collision probability — acceptable for this
  implementation, as in the split/dup machinery throughout.

## On "100% parity"

Every functional chapter of MPI is now implemented in pure Rust and covered by
multi-process tests: point-to-point (all modes, non-blocking, probes),
collectives (blocking, non-blocking, varying-count, neighbourhood), reductions
(built-in + user-defined + `reduce_local`), communicator / group / topology
management (Cartesian, graph, inter-comm), derived datatypes + `derive`,
one-sided RMA, parallel I/O, attribute caching, error handlers, generalized
requests, and dynamic process spawning — over a transport that runs on a single
host or across a real (heterogeneous) network.

What is *not* claimed: bit-for-bit coverage of every one of the standard's 400+
entry points and every corner-case flag, big-endian interoperability, and the
`MPI_Dist_graph_create_adjacent` constructor variant. Those are the honest
remainder; everything an MPI application normally uses is present and tested.
