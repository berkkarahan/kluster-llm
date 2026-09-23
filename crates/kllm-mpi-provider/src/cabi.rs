//! Narrow, panic-contained C ABI for the distributed llama.cpp patch.
//!
//! The ABI owns one authenticated production world per process. Callers pass
//! tensor metadata and already allocated activation buffers; the provider does
//! not receive filesystem paths, commands, environment variables, or device
//! selection input through this interface.

use crate::production::{CollectiveDescriptor, MpiJobSpec, ProductionWorld};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr;
use std::slice;
use std::str;
use std::sync::{Mutex, OnceLock};

/// Operation completed successfully.
pub const KLLM_MPI_OK: i32 = 0;
/// The provider rejected the operation. The error buffer contains the reason.
pub const KLLM_MPI_ERROR: i32 = 1;
/// A caller-owned output buffer is too small. Required lengths are populated.
pub const KLLM_MPI_BUFFER_TOO_SMALL: i32 = 2;

static WORLD: OnceLock<Mutex<Option<ProductionWorld>>> = OnceLock::new();

fn world_slot() -> &'static Mutex<Option<ProductionWorld>> {
    WORLD.get_or_init(|| Mutex::new(None))
}

unsafe fn input_bytes<'a>(ptr: *const u8, len: usize, name: &str) -> Result<&'a [u8], String> {
    if len == 0 {
        return Ok(&[]);
    }
    if ptr.is_null() {
        return Err(format!("{name} pointer is null"));
    }
    // SAFETY: the caller promises that a non-null pointer is readable for len
    // bytes for the duration of this call. The slice never escapes the call.
    Ok(unsafe { slice::from_raw_parts(ptr, len) })
}

unsafe fn input_f32_mut<'a>(ptr: *mut f32, len: usize) -> Result<&'a mut [f32], String> {
    if len == 0 {
        return Ok(&mut []);
    }
    if ptr.is_null() {
        return Err("activation pointer is null".into());
    }
    // SAFETY: the caller promises that this buffer is uniquely writable for
    // len f32 values for the duration of this call.
    Ok(unsafe { slice::from_raw_parts_mut(ptr, len) })
}

unsafe fn shape_from_raw(ptr: *const u64, len: usize) -> Result<Vec<u64>, String> {
    if len > 16 {
        return Err("activation rank exceeds 16 dimensions".into());
    }
    if len == 0 {
        return Ok(Vec::new());
    }
    if ptr.is_null() {
        return Err("shape pointer is null".into());
    }
    // SAFETY: the caller promises that the shape is readable for len values.
    Ok(unsafe { slice::from_raw_parts(ptr, len) }.to_vec())
}

unsafe fn bounded_text(ptr: *const u8, len: usize, name: &str) -> Result<String, String> {
    if len == 0 || len > 256 {
        return Err(format!("{name} must contain 1 to 256 UTF-8 bytes"));
    }
    // SAFETY: delegated to input_bytes with the same lifetime restriction.
    let bytes = unsafe { input_bytes(ptr, len, name)? };
    let value = str::from_utf8(bytes).map_err(|_| format!("{name} is not valid UTF-8"))?;
    if value.chars().any(char::is_control) {
        return Err(format!("{name} contains a control character"));
    }
    Ok(value.to_string())
}

unsafe fn write_error(ptr: *mut u8, cap: usize, message: &str) {
    if ptr.is_null() || cap == 0 {
        return;
    }
    let bytes = message.as_bytes();
    let count = bytes.len().min(cap.saturating_sub(1));
    // SAFETY: the caller supplied a writable error buffer of cap bytes.
    unsafe {
        ptr::copy_nonoverlapping(bytes.as_ptr(), ptr, count);
        *ptr.add(count) = 0;
    }
}

fn ffi_call<F>(error_ptr: *mut u8, error_cap: usize, call: F) -> i32
where
    F: FnOnce() -> Result<i32, String>,
{
    match catch_unwind(AssertUnwindSafe(call)) {
        Ok(Ok(status)) => status,
        Ok(Err(error)) => {
            // SAFETY: error_ptr/cap are used only as the caller-owned optional
            // output described by every exported function.
            unsafe { write_error(error_ptr, error_cap, &error) };
            KLLM_MPI_ERROR
        }
        Err(_) => {
            // Do not unwind through llama.cpp. Avoid exposing panic internals.
            unsafe { write_error(error_ptr, error_cap, "MPI provider internal panic") };
            KLLM_MPI_ERROR
        }
    }
}

fn with_world<T>(
    call: impl FnOnce(&mut ProductionWorld) -> Result<T, String>,
) -> Result<T, String> {
    let mut slot = world_slot()
        .lock()
        .map_err(|_| "MPI provider state lock is poisoned".to_string())?;
    let world = slot.as_mut().ok_or("MPI provider is not initialized")?;
    call(world)
}

unsafe fn descriptor(
    world: &ProductionWorld,
    operation: &str,
    phase_ptr: *const u8,
    phase_len: usize,
    layer: i32,
    dtype_ptr: *const u8,
    dtype_len: usize,
    shape_ptr: *const u64,
    shape_len: usize,
) -> Result<CollectiveDescriptor, String> {
    // SAFETY: every raw input is validated and borrowed only for this call.
    let phase = unsafe { bounded_text(phase_ptr, phase_len, "phase")? };
    let dtype = unsafe { bounded_text(dtype_ptr, dtype_len, "dtype")? };
    let shape = unsafe { shape_from_raw(shape_ptr, shape_len)? };
    Ok(CollectiveDescriptor {
        operation: operation.to_string(),
        phase,
        layer,
        dtype,
        shape,
        manifest_identity: world.manifest_identity().to_string(),
    })
}

/// Initialize one authenticated provider world from a bounded JSON job spec.
///
/// Returns [`KLLM_MPI_ERROR`] if a world is already active.
#[no_mangle]
pub unsafe extern "C" fn kllm_mpi_init(
    spec_ptr: *const u8,
    spec_len: usize,
    error_ptr: *mut u8,
    error_cap: usize,
) -> i32 {
    ffi_call(error_ptr, error_cap, || {
        // SAFETY: the pointer is validated before a temporary slice is made.
        let bytes = unsafe { input_bytes(spec_ptr, spec_len, "job spec")? };
        let spec = MpiJobSpec::from_json(bytes)?;
        let mut slot = world_slot()
            .lock()
            .map_err(|_| "MPI provider state lock is poisoned".to_string())?;
        if slot.is_some() {
            return Err("MPI provider is already initialized".into());
        }
        let world = ProductionWorld::connect(spec)?;
        *slot = Some(world);
        Ok(KLLM_MPI_OK)
    })
}

/// Return the authenticated rank and communicator size.
#[no_mangle]
pub unsafe extern "C" fn kllm_mpi_identity(
    rank_out: *mut u32,
    world_size_out: *mut u32,
    error_ptr: *mut u8,
    error_cap: usize,
) -> i32 {
    ffi_call(error_ptr, error_cap, || {
        if rank_out.is_null() || world_size_out.is_null() {
            return Err("identity output pointer is null".into());
        }
        with_world(|world| {
            // SAFETY: non-null output pointers are caller-owned for this call.
            unsafe {
                *rank_out = world.rank();
                *world_size_out = world.world_size();
            }
            Ok(())
        })?;
        Ok(KLLM_MPI_OK)
    })
}

/// Return local provider health and the next collective sequence number.
#[no_mangle]
pub unsafe extern "C" fn kllm_mpi_health(
    sequence_out: *mut u64,
    error_ptr: *mut u8,
    error_cap: usize,
) -> i32 {
    ffi_call(error_ptr, error_cap, || {
        if sequence_out.is_null() {
            return Err("health sequence output pointer is null".into());
        }
        with_world(|world| {
            // SAFETY: the non-null scalar is caller-owned for this call.
            unsafe { *sequence_out = world.sequence() };
            Ok(())
        })?;
        Ok(KLLM_MPI_OK)
    })
}

/// Synchronize ranks at a graph phase and layer boundary.
#[no_mangle]
pub unsafe extern "C" fn kllm_mpi_barrier(
    phase_ptr: *const u8,
    phase_len: usize,
    layer: i32,
    error_ptr: *mut u8,
    error_cap: usize,
) -> i32 {
    ffi_call(error_ptr, error_cap, || {
        with_world(|world| {
            // SAFETY: descriptor inputs are borrowed only during this call.
            let desc = unsafe {
                descriptor(
                    world,
                    "barrier",
                    phase_ptr,
                    phase_len,
                    layer,
                    b"none".as_ptr(),
                    4,
                    ptr::null(),
                    0,
                )?
            };
            world.barrier(&desc)
        })?;
        Ok(KLLM_MPI_OK)
    })
}

/// Sum an f32 activation buffer across all ranks in place.
#[no_mangle]
pub unsafe extern "C" fn kllm_mpi_allreduce_f32(
    values_ptr: *mut f32,
    values_len: usize,
    phase_ptr: *const u8,
    phase_len: usize,
    layer: i32,
    shape_ptr: *const u64,
    shape_len: usize,
    error_ptr: *mut u8,
    error_cap: usize,
) -> i32 {
    ffi_call(error_ptr, error_cap, || {
        // SAFETY: the raw activation buffer is validated and scoped to the call.
        let values = unsafe { input_f32_mut(values_ptr, values_len)? };
        with_world(|world| {
            let desc = unsafe {
                descriptor(
                    world,
                    "allreduce",
                    phase_ptr,
                    phase_len,
                    layer,
                    b"f32".as_ptr(),
                    3,
                    shape_ptr,
                    shape_len,
                )?
            };
            world.allreduce_f32(&desc, values)
        })?;
        Ok(KLLM_MPI_OK)
    })
}

/// Broadcast bytes from root. `length_in_out` carries the root length on input
/// and the received/required length on output.
#[no_mangle]
pub unsafe extern "C" fn kllm_mpi_broadcast_bytes(
    root: u32,
    buffer_ptr: *mut u8,
    buffer_cap: usize,
    length_in_out: *mut usize,
    phase_ptr: *const u8,
    phase_len: usize,
    layer: i32,
    shape_ptr: *const u64,
    shape_len: usize,
    error_ptr: *mut u8,
    error_cap: usize,
) -> i32 {
    ffi_call(error_ptr, error_cap, || {
        if length_in_out.is_null() {
            return Err("broadcast length pointer is null".into());
        }
        // SAFETY: the caller supplied this scalar for the duration of the call.
        let input_len = unsafe { *length_in_out };
        let mut data = with_world(|world| {
            let desc = unsafe {
                descriptor(
                    world,
                    "broadcast",
                    phase_ptr,
                    phase_len,
                    layer,
                    b"bytes".as_ptr(),
                    5,
                    shape_ptr,
                    shape_len,
                )?
            };
            let mut data = if world.rank() == root {
                if input_len > buffer_cap {
                    return Err("broadcast input length exceeds buffer capacity".into());
                }
                unsafe { input_bytes(buffer_ptr.cast_const(), input_len, "broadcast buffer")? }
                    .to_vec()
            } else {
                Vec::new()
            };
            world.broadcast(root, &desc, &mut data)?;
            Ok(data)
        })?;
        // SAFETY: the scalar remains caller-owned and valid for this call.
        unsafe { *length_in_out = data.len() };
        if data.len() > buffer_cap || (data.len() > 0 && buffer_ptr.is_null()) {
            return Ok(KLLM_MPI_BUFFER_TOO_SMALL);
        }
        if !data.is_empty() {
            // SAFETY: capacity and non-nullness were checked above.
            unsafe { ptr::copy_nonoverlapping(data.as_mut_ptr(), buffer_ptr, data.len()) };
        }
        Ok(KLLM_MPI_OK)
    })
}

/// Concatenate unequal byte activations in rank order. Required result and
/// count lengths are always written when their scalar pointers are non-null.
#[no_mangle]
pub unsafe extern "C" fn kllm_mpi_allgatherv_bytes(
    local_ptr: *const u8,
    local_len: usize,
    output_ptr: *mut u8,
    output_cap: usize,
    output_len: *mut usize,
    counts_ptr: *mut u64,
    counts_cap: usize,
    counts_len: *mut usize,
    phase_ptr: *const u8,
    phase_len: usize,
    layer: i32,
    shape_ptr: *const u64,
    shape_len: usize,
    error_ptr: *mut u8,
    error_cap: usize,
) -> i32 {
    ffi_call(error_ptr, error_cap, || {
        if output_len.is_null() || counts_len.is_null() {
            return Err("allgatherv length output pointer is null".into());
        }
        let local = unsafe { input_bytes(local_ptr, local_len, "local activation")? };
        let (counts, data) = with_world(|world| {
            let desc = unsafe {
                descriptor(
                    world,
                    "allgatherv",
                    phase_ptr,
                    phase_len,
                    layer,
                    b"bytes".as_ptr(),
                    5,
                    shape_ptr,
                    shape_len,
                )?
            };
            world.allgatherv(&desc, local)
        })?;
        unsafe {
            *output_len = data.len();
            *counts_len = counts.len();
        }
        if data.len() > output_cap
            || counts.len() > counts_cap
            || (!data.is_empty() && output_ptr.is_null())
            || (!counts.is_empty() && counts_ptr.is_null())
        {
            return Ok(KLLM_MPI_BUFFER_TOO_SMALL);
        }
        unsafe {
            if !data.is_empty() {
                ptr::copy_nonoverlapping(data.as_ptr(), output_ptr, data.len());
            }
            if !counts.is_empty() {
                ptr::copy_nonoverlapping(counts.as_ptr(), counts_ptr, counts.len());
            }
        }
        Ok(KLLM_MPI_OK)
    })
}

/// Drop all peer sockets and zeroize the retained job credentials.
#[no_mangle]
pub unsafe extern "C" fn kllm_mpi_finalize(error_ptr: *mut u8, error_cap: usize) -> i32 {
    ffi_call(error_ptr, error_cap, || {
        let mut slot = world_slot()
            .lock()
            .map_err(|_| "MPI provider state lock is poisoned".to_string())?;
        *slot = None;
        Ok(KLLM_MPI_OK)
    })
}

/// Drop local peer sockets after an engine error. The KLLM supervisor observes
/// the rank exit and terminates every owned process in the fixed job.
#[no_mangle]
pub unsafe extern "C" fn kllm_mpi_abort(error_ptr: *mut u8, error_cap: usize) -> i32 {
    // SAFETY: forwards the same optional error buffer contract to finalize.
    unsafe { kllm_mpi_finalize(error_ptr, error_cap) }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn ffi_test_lock() -> &'static Mutex<()> {
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| Mutex::new(()))
    }

    #[test]
    fn null_inputs_are_reported_without_unwinding() {
        let _guard = ffi_test_lock().lock().unwrap();
        let mut error = [0_u8; 128];
        let status = unsafe { kllm_mpi_init(ptr::null(), 10, error.as_mut_ptr(), error.len()) };
        assert_eq!(status, KLLM_MPI_ERROR);
        assert!(str::from_utf8(&error)
            .unwrap()
            .contains("job spec pointer is null"));
    }

    #[test]
    fn identity_requires_an_initialized_world() {
        let _guard = ffi_test_lock().lock().unwrap();
        unsafe { kllm_mpi_finalize(ptr::null_mut(), 0) };
        let mut rank = 0;
        let mut size = 0;
        let mut error = [0_u8; 128];
        let status =
            unsafe { kllm_mpi_identity(&mut rank, &mut size, error.as_mut_ptr(), error.len()) };
        assert_eq!(status, KLLM_MPI_ERROR);
        assert!(str::from_utf8(&error).unwrap().contains("not initialized"));
    }

    #[test]
    fn singleton_c_abi_covers_lifecycle_sequence_and_collectives() {
        let _guard = ffi_test_lock().lock().unwrap();
        unsafe { kllm_mpi_finalize(ptr::null_mut(), 0) };
        let address = "127.0.0.1:20999";
        let spec = json!({
            "job_id": "41".repeat(16),
            "rank": 0,
            "world_size": 1,
            "bind_address": address,
            "peers": [{"rank": 0, "address": address, "nonce": "42".repeat(32)}],
            "job_secret": "43".repeat(32),
            "protocol_version": 1,
            "provider_revision": env!("KLLM_MPI_UPSTREAM_REVISION"),
            "provider_patch_digest": env!("KLLM_MPI_PATCH_DIGEST"),
            "engine_revision": "b11026-test",
            "backend": "vulkan",
            "physical_device_id": "gpu-test",
            "backend_device": "Vulkan0",
            "model_id": "test.gguf",
            "manifest_identity": "sha256:ffi-manifest",
            "collective_timeout_ms": 2000,
            "max_collective_bytes": 4096,
        });
        let bytes = serde_json::to_vec(&spec).unwrap();
        let mut error = [0_u8; 256];
        assert_eq!(
            unsafe { kllm_mpi_init(bytes.as_ptr(), bytes.len(), error.as_mut_ptr(), error.len()) },
            KLLM_MPI_OK,
            "{}",
            str::from_utf8(&error).unwrap()
        );
        let mut rank = 99;
        let mut size = 0;
        assert_eq!(
            unsafe { kllm_mpi_identity(&mut rank, &mut size, error.as_mut_ptr(), error.len()) },
            KLLM_MPI_OK
        );
        assert_eq!((rank, size), (0, 1));
        let mut sequence = 99;
        assert_eq!(
            unsafe { kllm_mpi_health(&mut sequence, error.as_mut_ptr(), error.len()) },
            KLLM_MPI_OK
        );
        assert_eq!(sequence, 0);
        assert_eq!(
            unsafe { kllm_mpi_barrier(b"warmup".as_ptr(), 6, -1, error.as_mut_ptr(), error.len()) },
            KLLM_MPI_OK
        );
        unsafe { kllm_mpi_health(&mut sequence, error.as_mut_ptr(), error.len()) };
        assert_eq!(sequence, 1);
        let mut values = [1.5_f32, 2.5];
        let shape = [2_u64];
        assert_eq!(
            unsafe {
                kllm_mpi_allreduce_f32(
                    values.as_mut_ptr(),
                    values.len(),
                    b"decode".as_ptr(),
                    6,
                    0,
                    shape.as_ptr(),
                    shape.len(),
                    error.as_mut_ptr(),
                    error.len(),
                )
            },
            KLLM_MPI_OK
        );
        assert_eq!(values, [1.5, 2.5]);
        let local = b"abc";
        let mut output = [0_u8; 3];
        let mut output_len = 0;
        let mut counts = [0_u64; 1];
        let mut counts_len = 0;
        assert_eq!(
            unsafe {
                kllm_mpi_allgatherv_bytes(
                    local.as_ptr(),
                    local.len(),
                    output.as_mut_ptr(),
                    output.len(),
                    &mut output_len,
                    counts.as_mut_ptr(),
                    counts.len(),
                    &mut counts_len,
                    b"decode".as_ptr(),
                    6,
                    0,
                    shape.as_ptr(),
                    shape.len(),
                    error.as_mut_ptr(),
                    error.len(),
                )
            },
            KLLM_MPI_OK
        );
        assert_eq!(
            (&output[..output_len], counts, counts_len),
            (local.as_slice(), [3], 1)
        );
        assert_eq!(
            unsafe { kllm_mpi_abort(error.as_mut_ptr(), error.len()) },
            KLLM_MPI_OK
        );
    }
}
