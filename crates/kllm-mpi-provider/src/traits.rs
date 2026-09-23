//! Convenient re-export of every public trait, mirroring `mpi::traits` in
//! rsmpi. The idiomatic import is:
//!
//! ```
//! use mpi::traits::*;
//! ```

pub use crate::attribute::CommunicatorAttributes;
pub use crate::collective::{CommunicatorCollectives, Operation, Root};
pub use crate::datatype::{
    Buffer, BufferMut, Collection, Equivalence, Partitioned, PartitionedBuffer,
    PartitionedBufferMut,
};
pub use crate::error::CommunicatorErrorHandler;
pub use crate::point_to_point::{Destination, Source};
pub use crate::raw::{AsRaw, AsRawMut, FromRaw, MatchesRaw};
pub use crate::topology::Communicator;
