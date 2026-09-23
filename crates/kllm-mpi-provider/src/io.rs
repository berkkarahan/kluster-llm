//! Parallel file I/O ([`File`]), corresponding to MPI's I/O chapter
//! (`MPI_File_*`), which released rsmpi does not currently expose.
//!
//! Each rank opens the same path with its own OS file handle; explicit-offset
//! reads and writes seek to a byte offset and transfer a typed buffer.
//! Collective variants add a barrier so all ranks participate together. Offsets
//! are measured in **bytes**.
//!
//! ```no_run
//! use mpi::traits::*;
//! use mpi::io::{File, MODE_CREATE, MODE_RDWR};
//!
//! let universe = mpi::initialize().unwrap();
//! let world = universe.world();
//! let mut f = File::open(&world, "/tmp/data.bin", MODE_CREATE | MODE_RDWR).unwrap();
//! let rank = world.rank();
//! // each rank writes its block at a distinct offset
//! f.write_at_all((rank as u64) * 16, &[rank; 4]);
//! ```

use std::fs::OpenOptions;
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use crate::collective::CommunicatorCollectives;
use crate::datatype::Equivalence;
use crate::topology::{Communicator, SimpleCommunicator};

/// Open the file for reading (`MPI_MODE_RDONLY`).
pub const MODE_RDONLY: u32 = 1;
/// Open the file for writing (`MPI_MODE_WRONLY`).
pub const MODE_WRONLY: u32 = 2;
/// Open the file for reading and writing (`MPI_MODE_RDWR`).
pub const MODE_RDWR: u32 = 4;
/// Create the file if it does not exist (`MPI_MODE_CREATE`).
pub const MODE_CREATE: u32 = 8;
/// Open in append mode (`MPI_MODE_APPEND`).
pub const MODE_APPEND: u32 = 16;
/// Fail if the file already exists (`MPI_MODE_EXCL`).
pub const MODE_EXCL: u32 = 32;
/// Delete the file when it is closed (`MPI_MODE_DELETE_ON_CLOSE`).
pub const MODE_DELETE_ON_CLOSE: u32 = 64;

#[inline]
fn as_bytes<T>(v: &[T]) -> &[u8] {
    // SAFETY: callers use POD `Equivalence` types.
    unsafe { std::slice::from_raw_parts(v.as_ptr() as *const u8, std::mem::size_of_val(v)) }
}

#[inline]
fn as_bytes_mut<T>(v: &mut [T]) -> &mut [u8] {
    // SAFETY: as above.
    unsafe { std::slice::from_raw_parts_mut(v.as_mut_ptr() as *mut u8, std::mem::size_of_val(v)) }
}

/// A file opened collectively by a communicator (`MPI_File`).
pub struct File {
    inner: std::fs::File,
    comm: SimpleCommunicator,
    path: PathBuf,
    delete_on_close: bool,
}

impl File {
    /// Collectively open `path` with the given mode flags (`MPI_File_open`).
    pub fn open<C: Communicator>(comm: &C, path: impl AsRef<Path>, mode: u32) -> io::Result<File> {
        let path = path.as_ref().to_path_buf();

        // Rank 0 handles creation so CREATE/EXCL are well-defined.
        if mode & MODE_CREATE != 0 && comm.rank() == 0 {
            let mut oo = OpenOptions::new();
            oo.write(true);
            if mode & MODE_EXCL != 0 {
                oo.create_new(true);
            } else {
                oo.create(true);
            }
            let _ = oo.open(&path)?;
        }
        comm.barrier();

        let mut oo = OpenOptions::new();
        let read = mode & (MODE_RDONLY | MODE_RDWR) != 0;
        let write = mode & (MODE_WRONLY | MODE_RDWR | MODE_APPEND | MODE_CREATE) != 0;
        oo.read(read || !write).write(write);
        if mode & MODE_APPEND != 0 {
            oo.append(true);
        }
        let inner = oo.open(&path)?;

        Ok(File {
            inner,
            comm: comm.duplicate(),
            path,
            delete_on_close: mode & MODE_DELETE_ON_CLOSE != 0,
        })
    }

    /// Write `data` at byte `offset` (`MPI_File_write_at`).
    pub fn write_at<T: Equivalence>(&mut self, offset: u64, data: &[T]) -> io::Result<()> {
        self.inner.seek(SeekFrom::Start(offset))?;
        self.inner.write_all(as_bytes(data))
    }

    /// Read into `buf` from byte `offset` (`MPI_File_read_at`).
    pub fn read_at<T: Equivalence>(&mut self, offset: u64, buf: &mut [T]) -> io::Result<()> {
        self.inner.seek(SeekFrom::Start(offset))?;
        self.inner.read_exact(as_bytes_mut(buf))
    }

    /// Collective write at an explicit offset (`MPI_File_write_at_all`).
    pub fn write_at_all<T: Equivalence>(&mut self, offset: u64, data: &[T]) -> io::Result<()> {
        self.comm.barrier();
        let r = self.write_at(offset, data);
        self.inner.flush()?;
        self.comm.barrier();
        r
    }

    /// Collective read at an explicit offset (`MPI_File_read_at_all`).
    pub fn read_at_all<T: Equivalence>(&mut self, offset: u64, buf: &mut [T]) -> io::Result<()> {
        self.comm.barrier();
        let r = self.read_at(offset, buf);
        self.comm.barrier();
        r
    }

    /// The current file size in bytes (`MPI_File_get_size`).
    pub fn size(&self) -> io::Result<u64> {
        Ok(self.inner.metadata()?.len())
    }

    /// Set the file size in bytes (`MPI_File_set_size`).
    pub fn set_size(&mut self, size: u64) -> io::Result<()> {
        self.inner.set_len(size)
    }

    /// Flush buffered data to storage (`MPI_File_sync`).
    pub fn sync(&mut self) -> io::Result<()> {
        self.inner.sync_all()
    }

    /// Collectively close the file (`MPI_File_close`), deleting it if it was
    /// opened with [`MODE_DELETE_ON_CLOSE`].
    pub fn close(self) -> io::Result<()> {
        // Drop does the work; this consumes `self` for an explicit close.
        drop(self);
        Ok(())
    }
}

impl Drop for File {
    fn drop(&mut self) {
        let _ = self.inner.sync_all();
        if self.delete_on_close {
            self.comm.barrier();
            if self.comm.rank() == 0 {
                let _ = std::fs::remove_file(&self.path);
            }
        }
    }
}
