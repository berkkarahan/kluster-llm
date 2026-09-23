//! Process-wide MPI environment: initialization, finalization and the
//! [`Universe`] handle. Mirrors `mpi::environment` in rsmpi.

use crate::topology::SystemCommunicator;
use crate::transport;
use crate::MpiError;

/// The level of thread support, matching the four MPI constants
/// (`MPI_THREAD_SINGLE`, …, `MPI_THREAD_MULTIPLE`).
///
/// This implementation is thread-safe throughout and always provides
/// [`Threading::Multiple`]; the requested level is recorded for compatibility.
#[derive(Copy, Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Threading {
    /// Only one thread will execute (`MPI_THREAD_SINGLE`).
    Single,
    /// The process may be multithreaded, but only the main thread makes MPI
    /// calls (`MPI_THREAD_FUNNELED`).
    Funneled,
    /// Multiple threads may make MPI calls, but not concurrently
    /// (`MPI_THREAD_SERIALIZED`).
    Serialized,
    /// Multiple threads may call MPI concurrently (`MPI_THREAD_MULTIPLE`).
    Multiple,
}

impl Threading {
    /// The maximum thread-support level this library provides.
    pub const fn max() -> Threading {
        Threading::Multiple
    }
}

/// Represents the initialized MPI environment.
///
/// The environment is finalized when the `Universe` is dropped, mirroring
/// rsmpi where `Universe` owns the lifetime of `MPI_Init` / `MPI_Finalize`.
pub struct Universe {
    threading: Threading,
}

impl Universe {
    /// The `MPI_COMM_WORLD` communicator containing every process in the job.
    pub fn world(&self) -> SystemCommunicator {
        SystemCommunicator::world()
    }

    /// The thread-support level actually provided.
    pub fn threading_support(&self) -> Threading {
        self.threading
    }

    /// The size of the universe (number of process slots), if known. Mirrors
    /// rsmpi's `Universe::size`; here it is the world size.
    pub fn size(&self) -> Option<usize> {
        Some(transport::runtime().size as usize)
    }

    /// Attach a buffer of `size` bytes for use by buffered sends
    /// (`MPI_Buffer_attach`).
    pub fn buffer_attach(&self, size: usize) {
        transport::runtime().buffer_attach(size);
    }

    /// The current buffered-send buffer size (`MPI_Buffer_attach` accounting).
    pub fn buffer_size(&self) -> usize {
        transport::runtime().buffer_size()
    }

    /// Set the buffered-send buffer size, mirroring rsmpi 0.8.x's
    /// `Universe::set_buffer_size`.
    pub fn set_buffer_size(&mut self, size: usize) {
        transport::runtime().set_buffer_size(size);
    }

    /// Detach the buffered-send buffer, returning its size
    /// (`MPI_Buffer_detach`).
    pub fn detach_buffer(&self) -> usize {
        transport::runtime().buffer_detach()
    }
}

impl Drop for Universe {
    fn drop(&mut self) {
        // `MPI_Finalize` is collective: synchronize so that every rank has
        // reached finalization before any tears down. Because the transport's
        // reader threads drain all incoming messages before a rank can pass the
        // barrier, this also guarantees messages sent before finalize are
        // delivered (no silent loss on exit).
        use crate::collective::CommunicatorCollectives;
        use crate::topology::Communicator;
        let world = SystemCommunicator::world();
        if world.size() > 1 {
            world.barrier();
        }
    }
}

/// Monotonic time in seconds since an arbitrary fixed point (`MPI_Wtime`).
///
/// Uses a monotonic clock so successive readings never go backwards, which is
/// what timing/benchmarking code requires.
pub fn time() -> f64 {
    static START: std::sync::OnceLock<std::time::Instant> = std::sync::OnceLock::new();
    START
        .get_or_init(std::time::Instant::now)
        .elapsed()
        .as_secs_f64()
}

/// The resolution of [`time`] in seconds (`MPI_Wtick`).
pub fn time_resolution() -> f64 {
    1e-9
}

/// The thread-support level provided by the running environment
/// (`MPI_Query_thread`).
pub fn threading_support() -> Threading {
    transport::runtime().threading
}

/// A name identifying the calling processor (`MPI_Get_processor_name`).
pub fn processor_name() -> Result<String, MpiError> {
    let host = std::env::var("HOSTNAME")
        .ok()
        .or_else(|| std::env::var("HOST").ok())
        .unwrap_or_else(|| "localhost".to_string());
    Ok(host.to_string())
}

/// A human-readable description of the MPI library (`MPI_Get_library_version`).
pub fn library_version() -> Result<String, MpiError> {
    Ok(format!(
        "pure-rust-mpi {} (rsmpi-compatible, no C library)",
        env!("CARGO_PKG_VERSION")
    ))
}

/// The supported MPI standard version as `(major, minor)` (`MPI_Get_version`).
pub fn version() -> (i32, i32) {
    (3, 1)
}

/// Initialize the MPI environment with the default (maximal) thread support,
/// returning the [`Universe`] handle. Equivalent to rsmpi's
/// [`crate::initialize`].
///
/// Returns `None` if MPI has already been initialized in this process.
pub fn initialize() -> Option<Universe> {
    initialize_with_threading(Threading::Multiple).map(|(u, _)| u)
}

/// Initialize the MPI environment requesting a given [`Threading`] level.
/// Returns the [`Universe`] and the level actually provided, mirroring
/// rsmpi's [`crate::initialize_with_threading`].
pub fn initialize_with_threading(requested: Threading) -> Option<(Universe, Threading)> {
    match transport::init(requested) {
        Ok(()) => {
            let provided = transport::runtime().threading;
            Some((
                Universe {
                    threading: provided,
                },
                provided,
            ))
        }
        Err(MpiError::AlreadyInitialized) => None,
        Err(e) => {
            // Bootstrap failures are fatal, like a failed `MPI_Init`.
            panic!("mpi::initialize failed: {e}");
        }
    }
}
