//! # mpi-rs — a pure-Rust Message Passing Interface
//!
//! This crate (published as **`mpi-rs`**, imported as **`mpi`**) is a
//! from-scratch, **pure-Rust** implementation of MPI whose public API mirrors
//! [`rsmpi`](https://github.com/rsmpi/rsmpi) (the `mpi` crate) so that existing
//! rsmpi programs compile and run against it with little or no change. Unlike
//! rsmpi, it does **not** link against a C MPI library (Open MPI / MPICH): the
//! runtime — process bootstrap, the byte transport, and every collective
//! algorithm — is implemented in pure Rust. The KLLM fork additionally uses
//! `sha2` for an auditable provider/binary identity stamp.
//!
//! ## Quick start
//!
//! ```no_run
//! use mpi::traits::*;
//!
//! let universe = mpi::initialize().unwrap();
//! let world = universe.world();
//! let size = world.size();
//! let rank = world.rank();
//!
//! if rank == 0 {
//!     let msg = [1.0f64, 2.0, 3.0];
//!     world.process_at_rank(1).send(&msg[..]);
//! } else if rank == 1 {
//!     let (msg, status) = world.process_at_rank(0).receive_vec::<f64>();
//!     println!("rank 1 received {:?} (status {:?})", msg, status);
//! }
//! ```
//!
//! Run it with the bundled launcher:
//!
//! ```text
//! mpiexec -n 4 ./target/debug/examples/hello
//! ```
//!
//! A program that is *not* launched under `mpiexec` runs as a singleton job
//! (world size 1, rank 0), exactly like a C MPI singleton `MPI_Init`.

#![deny(unsafe_op_in_unsafe_fn)]
#![warn(missing_docs)]

use std::fmt;

// Internal runtime (networking + bootstrap). Not part of the public API.
mod transport;

/// Authenticated, structured KLLM production jobs. This is separate from the
/// upstream-compatible launcher, which remains available for test fixtures.
pub mod production;

/// Panic-contained C ABI used by the patched llama.cpp engine.
pub mod cabi;

/// Activation tensor header and packing (balanced collectives).
pub mod header;

/// Balanced activation collectives (unequal shard handling).
pub mod balanced;

// The process launcher, shared by the `mpiexec` / `mpirun` binaries.
pub mod launcher;

// Dynamic process management (MPI_Comm_spawn). Internal helpers.
mod dpm;

// Public API modules, mirroring rsmpi's module layout.
pub mod attribute;
pub mod collective;
pub mod datatype;
pub mod environment;
pub mod error;
pub mod io;
pub mod point_to_point;
pub mod raw;
pub mod request;
pub mod topology;
pub mod traits;
pub mod window;

/// The integer type used to identify a process within a communicator
/// (`MPI_Comm_rank`).
pub type Rank = i32;

/// The integer type used to tag point-to-point messages.
pub type Tag = i32;

/// The integer type counting elements in a buffer (`int` in the C API).
pub type Count = i32;

/// A byte displacement / absolute address (`MPI_Aint`).
pub type Address = i64;

/// Errors that can occur while managing the MPI environment.
#[derive(Debug, Clone)]
pub enum MpiError {
    /// `initialize` was called more than once in the same process.
    AlreadyInitialized,
    /// The job could not be bootstrapped (launcher handshake failed).
    Bootstrap(String),
    /// A generic runtime error carrying a human-readable message.
    Other(String),
}

impl fmt::Display for MpiError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            MpiError::AlreadyInitialized => {
                write!(f, "MPI has already been initialized")
            }
            MpiError::Bootstrap(m) => write!(f, "MPI bootstrap failed: {m}"),
            MpiError::Other(m) => write!(f, "MPI error: {m}"),
        }
    }
}

impl std::error::Error for MpiError {}

pub use environment::{
    initialize, initialize_with_threading, library_version, processor_name, threading_support,
    time, time_resolution, version, Threading, Universe,
};

/// Derive macro for [`datatype::Equivalence`] (enabled by the `derive` feature).
///
/// ```ignore
/// use mpi::Equivalence;
///
/// #[derive(Clone, Copy, Equivalence)]
/// #[repr(C)]
/// struct Particle { x: f64, y: f64, id: u64 }
/// ```
#[cfg(feature = "derive")]
pub use mpi_derive::Equivalence;

/// A convenience re-export of the wildcard constants.
pub use transport::{ANY_SOURCE, ANY_TAG};
