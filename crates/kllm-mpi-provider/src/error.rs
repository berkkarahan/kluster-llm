//! Error classes and error handlers (`MPI_Errhandler`, `MPI_ERR_*`).
//!
//! Communicators carry an error handler; the two predefined handlers are
//! [`ErrorHandler::Fatal`] (abort the job on error, the MPI default) and
//! [`ErrorHandler::Return`] (return the error code to the caller). Because the
//! rest of this implementation reports failures by panicking or via `Result`,
//! the handler is primarily informational, but the full class vocabulary and
//! `set`/`get` API are provided.

use std::collections::HashMap;
use std::sync::Mutex;

use crate::topology::Communicator;

/// Error classes, mirroring the standard `MPI_ERR_*` constants.
pub mod class {
    /// No error (`MPI_SUCCESS`).
    pub const SUCCESS: i32 = 0;
    /// Invalid buffer pointer (`MPI_ERR_BUFFER`).
    pub const BUFFER: i32 = 1;
    /// Invalid count argument (`MPI_ERR_COUNT`).
    pub const COUNT: i32 = 2;
    /// Invalid datatype argument (`MPI_ERR_TYPE`).
    pub const TYPE: i32 = 3;
    /// Invalid tag argument (`MPI_ERR_TAG`).
    pub const TAG: i32 = 4;
    /// Invalid communicator (`MPI_ERR_COMM`).
    pub const COMM: i32 = 5;
    /// Invalid rank (`MPI_ERR_RANK`).
    pub const RANK: i32 = 6;
    /// Invalid request (`MPI_ERR_REQUEST`).
    pub const REQUEST: i32 = 7;
    /// Invalid root (`MPI_ERR_ROOT`).
    pub const ROOT: i32 = 8;
    /// Invalid group (`MPI_ERR_GROUP`).
    pub const GROUP: i32 = 9;
    /// Invalid operation (`MPI_ERR_OP`).
    pub const OP: i32 = 10;
    /// Invalid topology (`MPI_ERR_TOPOLOGY`).
    pub const TOPOLOGY: i32 = 11;
    /// Invalid dimension argument (`MPI_ERR_DIMS`).
    pub const DIMS: i32 = 12;
    /// Invalid argument (`MPI_ERR_ARG`).
    pub const ARG: i32 = 13;
    /// Unknown error (`MPI_ERR_UNKNOWN`).
    pub const UNKNOWN: i32 = 14;
    /// Message truncated on receive (`MPI_ERR_TRUNCATE`).
    pub const TRUNCATE: i32 = 15;
    /// Other known error (`MPI_ERR_OTHER`).
    pub const OTHER: i32 = 16;
    /// Internal implementation error (`MPI_ERR_INTERN`).
    pub const INTERN: i32 = 17;
    /// Pending request (`MPI_ERR_PENDING`).
    pub const PENDING: i32 = 18;
    /// Error in an I/O operation (`MPI_ERR_IO`).
    pub const IO: i32 = 32;
    /// The last standard error class (`MPI_ERR_LASTCODE`).
    pub const LASTCODE: i32 = 64;
}

/// A human-readable description of an error class (`MPI_Error_string`).
pub fn error_string(code: i32) -> String {
    let s = match code {
        class::SUCCESS => "no error",
        class::BUFFER => "invalid buffer pointer",
        class::COUNT => "invalid count argument",
        class::TYPE => "invalid datatype argument",
        class::TAG => "invalid tag argument",
        class::COMM => "invalid communicator",
        class::RANK => "invalid rank",
        class::REQUEST => "invalid request",
        class::ROOT => "invalid root",
        class::GROUP => "invalid group",
        class::OP => "invalid operation",
        class::TOPOLOGY => "invalid topology",
        class::DIMS => "invalid dimension argument",
        class::ARG => "invalid argument",
        class::TRUNCATE => "message truncated on receive",
        class::OTHER => "known error not in this list",
        class::INTERN => "internal MPI error",
        class::PENDING => "pending request",
        class::IO => "I/O error",
        _ => "unknown error",
    };
    s.to_string()
}

/// The error class of an error code (`MPI_Error_class`). Here codes are their
/// own class.
pub fn error_class(code: i32) -> i32 {
    code
}

/// A communicator error handler (`MPI_Errhandler`).
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum ErrorHandler {
    /// Abort the job when an error is raised (`MPI_ERRORS_ARE_FATAL`, default).
    Fatal,
    /// Return the error code to the caller (`MPI_ERRORS_RETURN`).
    Return,
}

static HANDLERS: Mutex<Option<HashMap<u32, ErrorHandler>>> = Mutex::new(None);

fn with_handlers<R>(f: impl FnOnce(&mut HashMap<u32, ErrorHandler>) -> R) -> R {
    let mut guard = HANDLERS.lock().unwrap();
    f(guard.get_or_insert_with(HashMap::new))
}

/// Error-handler operations on communicators. Blanket-implemented for every
/// [`Communicator`].
pub trait CommunicatorErrorHandler: Communicator {
    /// Set this communicator's error handler (`MPI_Comm_set_errhandler`).
    fn set_error_handler(&self, handler: ErrorHandler) {
        let ctx = self.comm_data().context;
        with_handlers(|h| {
            h.insert(ctx, handler);
        });
    }

    /// Get this communicator's error handler (`MPI_Comm_get_errhandler`).
    /// Defaults to [`ErrorHandler::Fatal`].
    fn error_handler(&self) -> ErrorHandler {
        let ctx = self.comm_data().context;
        with_handlers(|h| *h.get(&ctx).unwrap_or(&ErrorHandler::Fatal))
    }
}

impl<C: Communicator + ?Sized> CommunicatorErrorHandler for C {}

/// Re-exports for `use mpi::error::traits::*;`.
pub mod traits {
    pub use super::CommunicatorErrorHandler;
}
