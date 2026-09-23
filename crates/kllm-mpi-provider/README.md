# mpi-rs — a pure-Rust Message Passing Interface

[![CI](https://github.com/eugenehp/mpi-rs/actions/workflows/ci.yml/badge.svg)](https://github.com/eugenehp/mpi-rs/actions/workflows/ci.yml)
[![crates.io](https://img.shields.io/crates/v/mpi-rs.svg)](https://crates.io/crates/mpi-rs)
[![docs.rs](https://docs.rs/mpi-rs/badge.svg)](https://docs.rs/mpi-rs)
[![license](https://img.shields.io/crates/l/mpi-rs.svg)](#license)

A from-scratch, **pure-Rust** implementation of MPI whose public API mirrors
[`rsmpi`](https://github.com/rsmpi/rsmpi) (the `mpi` crate) so that existing
rsmpi programs compile and run against it with little or no change.

## KLLM fork boundary

This directory is KLLM's locally maintained provider fork of upstream revision
`99be8ac11c04f5419452428a486ae0eee1f5552e`; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). Its auditable, provider-only
entrypoint is:

```console
$ cargo run --bin kllm-mpi-provider-check -- --self-test --ranks 3
$ cargo run --bin kllm-mpi-provider-check -- --bad-header-agreement-test --ranks 3
$ cargo run --bin kllm-mpi-provider-check -- --truncated-frame-agreement-test --ranks 3
$ cargo run --bin kllm-mpi-provider-check -- --malformed-rendezvous-agreement-test --ranks 3
$ cargo run --bin kllm-mpi-provider-check -- --oversized-frame-agreement-test --ranks 3
```

The JSON result identifies the source revision, local patch digest, protocol,
target, feature set, and binary hash. It deliberately reports the KLLM engine
and GPU lanes as unavailable: a successful provider check is not inference
readiness. R0 has no authenticated listener admission or aggregate resource
budget, so its loopback-only checks must not be exposed as a service or network
endpoint. Each bootstrap or ready control line is capped at 1 KiB and shares the
self-test's 15-second watchdog; malformed or slow local control peers trigger
bounded rank cleanup. The upstream SSH launcher is a compatibility fixture, not
a transport security boundary.

`mpiexec`, `mpirun`, and their SSH multi-host path are retained as upstream
compatibility test fixtures only. They are not a KLLM production launch path;
KLLM jobs will use the authenticated structured control-plane launcher defined
in the implementation plan. The upstream `MPI_Comm_spawn` API and
`examples/spawn.rs` are likewise experimental test fixtures: they accept a
program/argument pair and open an ephemeral bootstrap listener, so KLLM must not
surface them in any capability, CLI, API, or job path.

The crucial difference from rsmpi: **there is no C library**. rsmpi is a set of
FFI bindings that link against Open MPI or MPICH. `mpi-rs` implements the MPI
runtime itself — process bootstrap, the network transport, and every collective
algorithm — in Rust. The KLLM fork adds `sha2` to stamp provider source and
executable identity, plus `libc` on Unix and test-only `windows-sys` on Windows
for bounded, ownership-aware local rank lifecycle handling. These platform APIs
are not an MPI C-library dependency.

```
rsmpi:  your code ── mpi (Rust bindings) ──FFI──▶ libmpi.so (C: Open MPI / MPICH)
mpi-rs: your code ── mpi (pure Rust: transport + collectives + launcher)
```

The crate publishes as **`mpi-rs`** but its library is named **`mpi`**, so code
keeps using `use mpi::traits::*;`:

```toml
[dependencies]
mpi-rs = "0.1"
```

## Quick start

```rust
use mpi::traits::*;

fn main() {
    let universe = mpi::initialize().unwrap();
    let world = universe.world();
    let size = world.size();
    let rank = world.rank();

    if rank == 0 {
        let msg = [1.0f64, 2.0, 3.0];
        world.process_at_rank(1).send(&msg[..]);
    } else if rank == 1 {
        let (msg, status) = world.process_at_rank(0).receive_vec::<f64>();
        println!("rank 1 received {msg:?} from {}", status.source_rank());
    }
}
```

Build and run across 4 processes with the bundled launcher:

```console
$ cargo build --examples
$ ./target/debug/mpiexec -n 4 ./target/debug/examples/hello
Hello from rank 0 of 4 on localhost
Hello from rank 1 of 4 on localhost
Hello from rank 2 of 4 on localhost
Hello from rank 3 of 4 on localhost
All 4 processes checked in.
```

A program run **without** the launcher behaves like a C MPI *singleton* init:
world size 1, rank 0.

## How it works

### Process launch & bootstrap (`mpiexec` / `mpirun`)

The launcher spawns *N* copies of your program and runs a tiny
[PMI](https://en.wikipedia.org/wiki/Process_Management_Interface)-style
rendezvous:

1. `mpiexec` binds a TCP rendezvous socket and spawns *N* children, passing each
   its rank, the world size and the rendezvous address in environment
   variables (`MPI_PMI_RANK`, `MPI_PMI_SIZE`, `MPI_PMI_ROOT`).
2. In `mpi::initialize()`, each child binds its own data socket, connects to the
   rendezvous and reports its address.
3. The launcher gathers all addresses and broadcasts the full table back, so
   every rank learns how to reach every peer. `initialize()` then returns.

### Transport

Each rank runs a background acceptor thread; every peer connection gets its own
reader thread that drains framed messages into a shared, matched inbox. A
receive scans the inbox for the first message matching `(communicator, source,
tag)` — honouring `ANY_SOURCE` / `ANY_TAG` — and blocks on a condition variable
until one arrives. FIFO order per `(source, tag)` gives MPI's non-overtaking
guarantee. Messages to `self` short-circuit into the local inbox.

### Communicators & collectives

Every communicator carries a *context id*; point-to-point traffic and collective
traffic run on separate contexts so they never alias, and each collective phase
uses a distinct tag. Collectives use scalable algorithms — **binomial-tree**
broadcast/reduce, **dissemination** barrier, **ring** allgather, and tree
all-reduce (`O(log n)` steps) — with an ordered fallback for non-commutative
reductions. `split`/`dup` agree on fresh contexts via a deterministic derivation
from an all-gathered `(color, key)` table.

### Robustness

`MPI_Finalize` (dropping the `Universe`) is a collective barrier, so no rank
exits with messages still undelivered. `MPI_Abort` — and any panic (e.g. a
failed assertion) — is propagated to every peer, so one rank's failure tears the
whole job down promptly instead of leaving the others blocked in a receive.
The reader rejects oversized, partial, and malformed rendezvous/control records
before delivery and propagates the resulting abort to the whole job. `MPI_Wtime`
uses a monotonic clock. Messages above 64 KiB use a rendezvous protocol so a fast
sender can't exhaust a slow receiver's memory. Mixed-endian jobs are detected at
startup; point-to-point and collectives are transcoded, but RMA windows and
`#[derive(Equivalence)]` structs require same-endian ranks.

### Shared memory

The upstream POSIX shared-memory fast path is intentionally not exposed by this
KLLM provider fork. It remains outside the Windows-compatible TCP baseline until
it has separate lifecycle, isolation, and cross-platform validation. Every
currently supported build uses the TCP transport.

## Examples

| file | shows |
|------|-------|
| `examples/hello.rs`       | rank / size / processor name / barrier |
| `examples/ring.rs`        | non-blocking `immediate_send` + `WaitGuard` ring |
| `examples/reduce.rs`      | `reduce_into_root`, `all_reduce_into` |
| `examples/derive.rs`      | `#[derive(Equivalence)]` on a POD struct (`--features derive`) |
| `examples/rma.rs`         | one-sided `Window` put / get / accumulate |
| `examples/parallel_io.rs` | parallel `File` write / read |
| `examples/topo_inter.rs`  | graph topology + neighbourhood collectives + inter-communicators |
| `examples/spawn.rs`       | dynamic process spawning (`MPI_Comm_spawn`) |
| `examples/abort.rs`       | `MPI_Abort` tears the whole job down (no hung peers) |
| `examples/pingpong.rs`    | latency / bandwidth / all-reduce micro-benchmark |
| `examples/bigmsg.rs`      | multi-MB transfers via the rendezvous protocol |
| `examples/thread_multiple.rs` | concurrent MPI calls from many threads per rank |
| `examples/selftest.rs`    | asserts correctness of the core API (used by the test suite) |

```console
$ ./target/debug/mpiexec -n 4 ./target/debug/examples/selftest
SELFTEST PASS: all checks passed on 4 ranks.
```

## Testing

```console
$ cargo test          # unit tests + multi-process integration tests + 3-rank R0 agreement checks
```

The integration suite launches `examples/selftest` under `mpiexec` at several
process counts and exercises provider-only, exactly-three-rank terminal agreement
checks for invalid headers, truncated frames, malformed rendezvous announcements,
and oversized frames.

On Unix, the process-owning test harness creates a dedicated session and
requires the default `SIGCHLD` disposition, no `SA_NOCLDWAIT`, and exclusive
ownership of its direct children from launch through final reap. Test ranks must
not alter their session/process-group membership or create breakaway descendants.
The harness checks the signal precondition without modifying it before it starts
a job. A violation is a harness error rather than permission to signal a possibly
recycled numeric process-group ID. This is a local test-harness contract, not a
provider runtime or production KLLM launch requirement.

On Windows, the harness creates the launcher suspended, assigns it to a
non-breakaway, kill-on-close Job Object, then resumes its primary thread. A
failed assignment or resume uses the same bounded cleanup deadline and returns
both the setup failure and any cleanup failure. This is likewise test-harness
containment, not a production launcher guarantee.

## Multi-host clusters

`mpiexec` can launch ranks on remote hosts over SSH. Ranks are assigned to the
`--host` list round-robin; each rank auto-detects its own routable IP, so no
per-node configuration is needed:

```console
$ mpiexec -n 4 --host localhost,node2 --remote-bin /path/on/node2/prog ./prog
```

`--remote-bin` is the program path on the remote hosts (they need the binary
and passwordless SSH). A host of `localhost` runs locally; anything else is
launched with `ssh <host> …`.

This has been verified on a **heterogeneous** two-node LAN — rank 0 on macOS
(arm64) and ranks on Linux (x86_64) — running the full `selftest` (every
collective, communicator split, graph/inter-comm, pack/unpack) and the one-sided
`rma` example across the network. The wire format is fixed little-endian, so
little-endian 64-bit hosts of different OS/arch interoperate. (Cross-compile the
Linux binary from macOS with e.g. `zig` as the linker for
`x86_64-unknown-linux-gnu`.)

## Parity with rsmpi

See [`PARITY.md`](PARITY.md) for the full feature-by-feature status matrix.
Implemented and tested (pure Rust, multi-process tests):

* **environment** — init, `Universe`, threading, timing, processor/library info
* **point-to-point** — all send modes, blocking + `immediate_*` + probes +
  matched probes, `send_receive*`
* **collectives** — barrier, broadcast, gather/scatter (+ varcount), allgather
  (+ varcount), all-to-all (+ varcount), reduce/allreduce, inclusive/exclusive
  scan, reduce-scatter, and their non-blocking `immediate_*` forms
* **reductions** — all built-in `SystemOperation`s plus `UserOperation` closures
* **communicators** — `split`/`dup`/`group`, groups & set algebra, Cartesian
  and graph topologies (+ neighbourhood collectives), inter-communicators,
  naming, `pack`/`unpack`
* **datatypes** — `Equivalence`, `Buffer`/`BufferMut`, `Partition`,
  `UserDatatype` (contiguous/vector/indexed/struct), `View`/`MutView`, and
  `#[derive(Equivalence)]` (feature `derive`)
* **one-sided RMA** — `Window` put / get / accumulate / fence / lock
* **parallel I/O** — `File` explicit-offset & collective read / write
* **attribute caching** — keyvals and comm attributes
* **error handling** — `MPI_ERR_*` classes, `error_string`, error handlers
* **dynamic processes** — `Root::spawn` / `Communicator::parent` (`MPI_Comm_spawn`)
* **requests** — `Request`, scopes, `WaitGuard`/`CancelGuard`, `wait_any`/`wait_all`,
  generalized requests
* **transport** — TCP; single-host loopback or multi-host over SSH with
  auto-detected node IPs (verified heterogeneous macOS/Linux)

This covers every functional chapter of MPI, going well beyond rsmpi's shipped
surface (which has no `window`/`io`/spawn). What is *not* claimed: every one of
the standard's 400+ entry points and corner-case flags, big-endian
interoperability, and the `MPI_Dist_graph_create_adjacent` variant — see
`PARITY.md`.

## License

MIT OR Apache-2.0.
