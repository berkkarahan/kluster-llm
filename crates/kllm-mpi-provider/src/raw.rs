//! Bridge between Rust types and their underlying raw handles. Mirrors
//! `mpi::raw` in rsmpi. In a C-backed MPI these expose the opaque `MPI_*`
//! handles; here the "raw" form of a handle is the pure-Rust identifier the
//! runtime uses internally (e.g. a communicator context id).

use crate::datatype::DatatypeRef;
use crate::topology::{Communicator, SimpleCommunicator};

/// A type that can produce its raw handle.
///
/// # Safety
///
/// The returned raw handle must remain valid for as long as `self` is alive.
pub unsafe trait AsRaw {
    /// The raw handle type.
    type Raw;
    /// Obtain the raw handle.
    fn as_raw(&self) -> Self::Raw;
}

/// A type that can produce a mutable pointer to its raw handle.
///
/// # Safety
///
/// See [`AsRaw`].
pub unsafe trait AsRawMut: AsRaw {
    /// Obtain a mutable pointer to the raw handle.
    fn as_raw_mut(&mut self) -> *mut Self::Raw;
}

/// A type that can be reconstructed from its raw handle.
pub trait FromRaw: AsRaw {
    /// Rebuild `Self` from a raw handle.
    ///
    /// # Safety
    ///
    /// `raw` must be a valid handle produced by a matching [`AsRaw::as_raw`].
    unsafe fn from_raw(raw: Self::Raw) -> Self;
}

/// Marker for types whose in-memory layout is identical to their raw handle,
/// so that `&[Self]` can be reinterpreted as `&[Self::Raw]`.
///
/// # Safety
///
/// The implementing type must be layout-compatible with `Self::Raw`.
pub unsafe trait MatchesRaw: AsRaw {}

// SAFETY: a communicator's raw identity is its context id.
unsafe impl AsRaw for SimpleCommunicator {
    type Raw = u32;
    fn as_raw(&self) -> u32 {
        self.comm_data().context
    }
}

// SAFETY: a datatype's raw identity is its numeric id.
unsafe impl AsRaw for DatatypeRef {
    type Raw = u32;
    fn as_raw(&self) -> u32 {
        self.id
    }
}

/// Re-exports of the raw-handle traits, for `use mpi::raw::traits::*;`.
pub mod traits {
    pub use super::{AsRaw, AsRawMut, FromRaw, MatchesRaw};
}
