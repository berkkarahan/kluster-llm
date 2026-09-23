//! Attribute caching on communicators. Mirrors rsmpi's `attribute` module: a
//! keyval identifies a slot, and arbitrary typed values can be cached on a
//! communicator and retrieved later.
//!
//! ```
//! use mpi::attribute::{create_keyval, CommunicatorAttributes};
//! # fn demo(world: &impl mpi::topology::Communicator) {
//! let key = create_keyval();
//! world.set_attr(&key, 123u64);
//! assert_eq!(world.get_attr::<u64>(&key), Some(123));
//! # }
//! ```

use std::any::Any;
use std::sync::atomic::{AtomicI32, Ordering};

use crate::topology::Communicator;

/// A key identifying an attribute slot (`MPI_Comm_create_keyval`).
#[derive(Copy, Clone, Debug, PartialEq, Eq, Hash)]
pub struct CommKeyval {
    id: i32,
}

impl CommKeyval {
    /// The raw keyval id.
    pub fn id(&self) -> i32 {
        self.id
    }
}

static NEXT_KEYVAL: AtomicI32 = AtomicI32::new(1);

/// Create a fresh communicator keyval (`MPI_Comm_create_keyval`).
pub fn create_keyval() -> CommKeyval {
    CommKeyval {
        id: NEXT_KEYVAL.fetch_add(1, Ordering::Relaxed),
    }
}

/// Attribute-caching operations on communicators. Blanket-implemented for every
/// [`Communicator`].
pub trait CommunicatorAttributes: Communicator {
    /// Cache `value` under `key` on this communicator (`MPI_Comm_set_attr`).
    fn set_attr<T: Any + Send + Sync>(&self, key: &CommKeyval, value: T) {
        self.comm_data()
            .attributes
            .lock()
            .unwrap()
            .insert(key.id, Box::new(value));
    }

    /// Retrieve a clone of the attribute cached under `key`
    /// (`MPI_Comm_get_attr`), if present and of type `T`.
    fn get_attr<T: Any + Clone>(&self, key: &CommKeyval) -> Option<T> {
        self.comm_data()
            .attributes
            .lock()
            .unwrap()
            .get(&key.id)
            .and_then(|b| b.downcast_ref::<T>())
            .cloned()
    }

    /// Whether an attribute is cached under `key`.
    fn has_attr(&self, key: &CommKeyval) -> bool {
        self.comm_data()
            .attributes
            .lock()
            .unwrap()
            .contains_key(&key.id)
    }

    /// Remove the attribute cached under `key` (`MPI_Comm_delete_attr`).
    fn delete_attr(&self, key: &CommKeyval) {
        self.comm_data().attributes.lock().unwrap().remove(&key.id);
    }
}

impl<C: Communicator + ?Sized> CommunicatorAttributes for C {}

/// Re-exports for `use mpi::attribute::traits::*;`.
pub mod traits {
    pub use super::CommunicatorAttributes;
}
