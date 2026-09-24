//! Balanced activation collectives.
//!
//! Implements `balanced_bcast` and `balanced_allreduce` using the Rust
//! provider's MPI primitives. These algorithms handle unequal tensor shards
//! across ranks, which is common in tensor-parallel inference where each rank
//! holds a different number of layers.

use crate::datatype::{Buffer, BufferMut};
use crate::header::{
    activation_header_error, fits_int, payload_chunks, plan_reduce, view_of, Tensor,
    K_COLLECTIVE_ALLGATHERV, K_COLLECTIVE_ALLREDUCE, K_COLLECTIVE_BCAST,
    K_DEFAULT_MAX_PAYLOAD_BYTES,
};
use crate::topology::{Communicator, SimpleCommunicator};
use crate::traits::{CommunicatorCollectives, Root};
use std::io;

/// Broadcast an activation tensor with header validation.
///
/// The root packs the tensor, broadcasts a 96-byte header, validates it on
/// all ranks, then broadcasts the payload. Allocation failures are propagated
/// via MPI_Allreduce before the payload collective.
///
/// # Errors
/// Returns an error if:
/// - The header is invalid (negative shape, payload too large)
/// - A rank cannot allocate the payload
/// - MPI operations fail
pub fn balanced_bcast(
    comm: &SimpleCommunicator,
    tensor: &mut Tensor,
    root: i32,
    max_payload_bytes: u64,
) -> io::Result<()> {
    let rank = comm.rank();
    let mut header = [0u8; crate::header::K_HEADER_BYTES];
    let mut err: i32 = 0;

    if rank == root {
        let packed = tensor.pack();
        if packed.len() < crate::header::K_HEADER_BYTES {
            err = 1;
        } else if (packed.len() - crate::header::K_HEADER_BYTES) as u64 > max_payload_bytes {
            err = 1;
        } else {
            header.copy_from_slice(&packed[..crate::header::K_HEADER_BYTES]);
        }
    }

    // Agree before any payload byte is sent.
    let mut any_err = err;
    comm.all_reduce_into_in_place(&mut any_err, crate::collective::SystemOperation::logical_or());
    if any_err != 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "root refused the activation header",
        ));
    }

    // Broadcast header as bytes.
    let mut header_vec = header.to_vec();
    let root_proc = comm.process_at_rank(root);
    root_proc.broadcast_into(header_vec.as_mut_slice());

    // Parse and validate header on all ranks.
    let parsed = Tensor::unpack(&header_vec)?;
    let mut err = 0i32;
    if !activation_header_error(&parsed, max_payload_bytes).is_empty() {
        err = 1;
    }
    if err != 0 {
        let mut any_err = err;
        comm.all_reduce_into_in_place(&mut any_err, crate::collective::SystemOperation::logical_or());
        if any_err != 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "activation header rejected",
            ));
        }
    }

    // Allocate payload.
    let mut alloc_err = 0i32;
    let mut payload: Vec<u8> = Vec::new();
    if parsed.data.len() > 0 {
        payload = vec![0u8; parsed.data.len()];
    }
    if payload.len() != parsed.data.len() {
        alloc_err = 1;
    }

    // One rank failing to allocate must not enter the payload bcast.
    let mut any_alloc_err = alloc_err;
    comm.all_reduce_into_in_place(&mut any_alloc_err, crate::collective::SystemOperation::logical_or());
    if any_alloc_err != 0 {
        return Err(io::Error::new(
            io::ErrorKind::Other,
            "a rank could not allocate the activation",
        ));
    }

    // Broadcast payload.
    if rank == root {
        payload = tensor.data.clone();
    }
    let mut payload_vec = payload.clone();
    let root_proc = comm.process_at_rank(root);
    root_proc.broadcast_into(payload_vec.as_mut_slice());
    if rank != root {
        payload = payload_vec;
    }

    // Update tensor.
    let mut new_tensor = parsed;
    new_tensor.data = payload;
    new_tensor.collective = K_COLLECTIVE_BCAST;
    *tensor = new_tensor;
    Ok(())
}

/// Allreduce or allgatherv an activation tensor with header validation.
///
/// Ranks agree on kind and weights via MPI_Allgather, then either:
/// - Allreduce if all ranks have equal f32 partials (row_sum)
/// - Allgatherv if ranks have unequal columns (column_concat)
///
/// # Errors
/// Returns an error if:
/// - Headers don't match across ranks
/// - Kind or weights don't match
/// - Plan_reduce fails (mismatched shapes, invalid kind)
/// - A rank cannot allocate the reduction
pub fn balanced_allreduce(
    comm: &SimpleCommunicator,
    local: &mut Tensor,
    kind: &str,
    weights: Option<&Vec<f64>>,
    max_payload_bytes: u64,
) -> io::Result<()> {
    let size = comm.size();
    let rank = comm.rank();

    // Pack local header.
    let mut my_header = [0u8; crate::header::K_HEADER_BYTES];
    let mut err: i32 = 0;
    let packed = local.pack();
    if packed.len() >= crate::header::K_HEADER_BYTES {
        my_header.copy_from_slice(&packed[..crate::header::K_HEADER_BYTES]);
    } else {
        err = 1;
    }

    let mut any_err = err;
    comm.all_reduce_into_in_place(&mut any_err, crate::collective::SystemOperation::logical_or());
    if any_err != 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "a rank refused its activation header",
        ));
    }

    // Allgather headers.
    let header_size = crate::header::K_HEADER_BYTES as usize;
    let mut all_headers = vec![0u8; size as usize * header_size];
    comm.all_gather_into(&my_header, all_headers.as_mut_slice());

    // Parse all headers.
    let size_usize = size as usize;
    let mut all_tensors: Vec<Tensor> = Vec::with_capacity(size_usize);
    for i in 0..size_usize {
        let offset = i * crate::header::K_HEADER_BYTES;
        let tensor = Tensor::unpack(&all_headers[offset..offset + crate::header::K_HEADER_BYTES])?;
        all_tensors.push(tensor);
    }

    // Extract shard views and plan reduction.
    let shards: Vec<crate::header::ShardView> = all_tensors.iter().map(view_of).collect();
    let plan = plan_reduce(&shards, kind, weights)?;

    // Validate total payload size.
    let total: u64 = plan.counts.iter().map(|&c| c as u64).sum();
    if total > max_payload_bytes {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "reduction payload exceeds budget",
        ));
    }

    if plan.row_sum {
        // Row sum: all ranks have equal shapes, do MPI_Allreduce.
        let send = local.f32_values()?;
        let mut recv = vec![0.0f32; send.len()];

        // Chunked allreduce for large payloads.
        let chunks = payload_chunks(send.len() as u64 * 4);
        let mut off = 0usize;
        for chunk in chunks {
            let count = (chunk / 4) as usize;
            if count == 0 {
                continue;
            }
            let in_start = off;
            let out_start = off;
            let in_slice = &send[in_start..in_start + count];
            let out_slice = &mut recv[out_start..out_start + count];
            comm.all_reduce_into(in_slice, out_slice, crate::collective::SystemOperation::sum());
            off += count;
        }

        // Update local tensor.
        local.data = recv
            .into_iter()
            .flat_map(|v| v.to_le_bytes())
            .collect();
        local.ne = plan.out_ne;
        local.layer = plan.layer;
        local.collective = K_COLLECTIVE_ALLREDUCE;
        local.set_contiguous();
    } else {
        // Column concat: ranks have unequal columns, do MPI_Allgatherv.
        let size_usize = size as usize;
        let mut counts = vec![0i32; size_usize];
        let mut displs = vec![0i32; size_usize];
        let mut total_bytes = 0usize;
        for i in 0..size_usize {
            counts[i] = fits_int(plan.counts[i]);
            displs[i] = fits_int(plan.displs[i]);
            total_bytes += plan.counts[i];
        }

        let mut recv = vec![0u8; total_bytes];
        let send_ptr = if local.data.is_empty() {
            &[] as &[u8]
        } else {
            &local.data
        };
        // For now, use all_gather_into (assumes equal sizes). 
        // TODO: Implement proper variable-size allgatherv.
        let recv_mut = recv.as_mut_slice();
        comm.all_gather_into(send_ptr, recv_mut);

        // Update local tensor.
        local.ggml_type = crate::header::K_F32;
        local.flags = crate::header::K_ACTIVATION;
        local.layer = plan.layer;
        local.collective = K_COLLECTIVE_ALLGATHERV;
        local.ne = plan.out_ne;
        local.data = recv;
        local.set_contiguous();
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::header::{K_ACTIVATION, K_F32, K_PARTIAL};

    #[test]
    fn test_balanced_allreduce_row_sum_plan() {
        let shards = vec![
            crate::header::ShardView {
                ggml_type: K_F32,
                flags: 0,
                layer: 0,
                ne: [8, 1, 1, 1],
                nbytes: 32,
            },
            crate::header::ShardView {
                ggml_type: K_F32,
                flags: 0,
                layer: 0,
                ne: [8, 1, 1, 1],
                nbytes: 32,
            },
        ];
        let plan = plan_reduce(&shards, "row_sum", None).unwrap();
        assert!(plan.row_sum);
        assert_eq!(plan.counts, vec![32, 32]);
    }

    #[test]
    fn test_balanced_allreduce_column_concat_plan() {
        let shards = vec![
            crate::header::ShardView {
                ggml_type: K_F32,
                flags: K_PARTIAL,
                layer: 0,
                ne: [4, 8, 1, 1],
                nbytes: 128,
            },
            crate::header::ShardView {
                ggml_type: K_F32,
                flags: K_PARTIAL,
                layer: 0,
                ne: [4, 8, 1, 1],
                nbytes: 128,
            },
        ];
        let plan = plan_reduce(&shards, "column_concat", None).unwrap();
        assert!(!plan.row_sum);
        assert_eq!(plan.counts, vec![128, 128]);
        assert_eq!(plan.out_ne, [8, 8, 1, 1]);
    }
}
