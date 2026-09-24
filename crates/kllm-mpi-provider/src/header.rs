//! Activation tensor header and packing.
//!
//! Mirrors `cpp/kllm_mpi/kllm_mpi.hpp`. The header carries type, flags, layer,
//! and shape metadata. Payloads are chunked into MPI-safe sizes.

use std::io::{self, Read, Write};

/// Header size in bytes.
pub const K_HEADER_BYTES: usize = 96;

/// Maximum chunk size for collective transfers (1 GiB).
pub const K_MPI_CHUNK_BYTES: u64 = 1 << 30;

/// Protocol version.
pub const K_VERSION: u16 = 1;

/// Default maximum payload bytes for a single collective.
/// A full GGUF does not fit, so labeling weights as an activation still fails.
pub const K_DEFAULT_MAX_PAYLOAD_BYTES: u64 = 4 << 30;

/// GGML data type identifiers.
pub const K_F32: u32 = 0;
pub const K_F16: u32 = 1;
pub const K_I32: u32 = 26;
pub const K_BF16: u32 = 30;

/// Tensor flags.
pub const K_ACTIVATION: u16 = 0x1;
pub const K_TOKEN_IDS: u16 = 0x2;
pub const K_LOGITS: u16 = 0x4;
pub const K_PARTIAL: u16 = 0x8;

/// Collective type identifiers.
pub const K_COLLECTIVE_BCAST: u32 = 1;
pub const K_COLLECTIVE_ALLREDUCE: u32 = 2;
pub const K_COLLECTIVE_ALLGATHERV: u32 = 3;

/// Activation tensor with header and payload.
///
/// Mirrors `kllm_mpi::Tensor`. The header is fixed-size (96 bytes); the
/// payload carries the raw bytes (dtype-dependent).
#[derive(Clone, Debug, PartialEq)]
pub struct Tensor {
    /// GGML data type (`kF32`, `kF16`, `kI32`, `kBf16`).
    pub ggml_type: u32,
    /// Bit flags (`kActivation`, `kTokenIds`, `kLogits`, `kPartial`).
    pub flags: u16,
    /// Source rank (for bcast root).
    pub src_rank: i32,
    /// Layer index (-1 if unknown).
    pub layer: i32,
    /// Collective type (`kBcast`, `kAllreduce`, `kAllgatherv`).
    pub collective: u32,
    /// Shape (ne[0..4]).
    pub ne: [i64; 4],
    /// Stride (nb[0..4]) in bytes, zero if not contiguous.
    pub nb: [u64; 4],
    /// Raw payload bytes.
    pub data: Vec<u8>,
}

impl Tensor {
    /// Create a float32 tensor from values and shape.
    pub fn f32(values: Vec<f32>, ne: [i64; 4], src_rank: i32, layer: i32, partial: bool) -> Self {
        let data: Vec<u8> = values
            .into_iter()
            .flat_map(|v| v.to_le_bytes())
            .collect();
        let mut tensor = Self {
            ggml_type: K_F32,
            flags: if partial { K_PARTIAL } else { 0 },
            src_rank,
            layer,
            collective: K_COLLECTIVE_BCAST,
            ne,
            nb: [0; 4],
            data,
        };
        tensor.set_contiguous();
        tensor
    }

    /// Mark the tensor as contiguous and compute strides.
    pub fn set_contiguous(&mut self) {
        let elem_size = match self.ggml_type {
            K_F32 | K_I32 => 4,
            K_F16 | K_BF16 => 2,
            _ => 4,
        };
        let mut stride = 1u64;
        for d in 0..4 {
            self.nb[d] = stride * elem_size as u64;
            if d < 3 {
                stride *= self.ne[d] as u64;
            }
        }
    }

    /// Pack header + payload into a single byte buffer.
    pub fn pack(&self) -> Vec<u8> {
        let mut buf = Vec::with_capacity(K_HEADER_BYTES + self.data.len());
        // Header (96 bytes): type(4) + flags(2) + src_rank(4) + layer(4) + collective(4) + ne(32) + nb(32) + version(2) + padding(8)
        buf.extend_from_slice(&self.ggml_type.to_le_bytes());
        buf.extend_from_slice(&self.flags.to_le_bytes());
        buf.extend_from_slice(&self.src_rank.to_le_bytes());
        buf.extend_from_slice(&self.layer.to_le_bytes());
        buf.extend_from_slice(&self.collective.to_le_bytes());
        for &n in &self.ne {
            buf.extend_from_slice(&n.to_le_bytes());
        }
        for &b in &self.nb {
            buf.extend_from_slice(&b.to_le_bytes());
        }
        buf.extend_from_slice(&K_VERSION.to_le_bytes());
        // Pad to 96 bytes
        buf.resize(K_HEADER_BYTES, 0);
        // Payload
        buf.extend_from_slice(&self.data);
        buf
    }

    /// Unpack a byte buffer into a tensor.
    pub fn unpack(bytes: &[u8]) -> io::Result<Self> {
        if bytes.len() < K_HEADER_BYTES {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "buffer too short for header",
            ));
        }
        let mut offset = 0;
        let ggml_type = u32::from_le_bytes(
            bytes[offset..offset + 4]
                .try_into()
                .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?,
        );
        offset += 4;
        let flags = u16::from_le_bytes(
            bytes[offset..offset + 2]
                .try_into()
                .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?,
        );
        offset += 2;
        let src_rank = i32::from_le_bytes(
            bytes[offset..offset + 4]
                .try_into()
                .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?,
        );
        offset += 4;
        let layer = i32::from_le_bytes(
            bytes[offset..offset + 4]
                .try_into()
                .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?,
        );
        offset += 4;
        let collective = u32::from_le_bytes(
            bytes[offset..offset + 4]
                .try_into()
                .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?,
        );
        offset += 4;
        let mut ne = [0i64; 4];
        for i in 0..4 {
            ne[i] = i64::from_le_bytes(
                bytes[offset..offset + 8]
                    .try_into()
                    .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?,
            );
            offset += 8;
        }
        let mut nb = [0u64; 4];
        for i in 0..4 {
            nb[i] = u64::from_le_bytes(
                bytes[offset..offset + 8]
                    .try_into()
                    .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?,
            );
            offset += 8;
        }
        let _version = u16::from_le_bytes(
            bytes[offset..offset + 2]
                .try_into()
                .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?,
        );
        offset += 2;
        // Pad to 96
        offset = K_HEADER_BYTES;
        let data = bytes[offset..].to_vec();
        Ok(Self {
            ggml_type,
            flags,
            src_rank,
            layer,
            collective,
            ne,
            nb,
            data,
        })
    }

    /// Extract float32 values from the payload.
    pub fn f32_values(&self) -> io::Result<Vec<f32>> {
        if self.ggml_type != K_F32 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "not float32",
            ));
        }
        if self.data.len() % 4 != 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "payload not aligned to f32",
            ));
        }
        let values: Vec<f32> = self
            .data
            .chunks_exact(4)
            .map(|chunk| f32::from_le_bytes(chunk.try_into().unwrap()))
            .collect();
        Ok(values)
    }

    /// Number of payload bytes.
    pub fn nbytes(&self) -> usize {
        self.data.len()
    }
}

/// Shard metadata for reduction planning.
#[derive(Clone, Debug)]
pub struct ShardView {
    pub ggml_type: u32,
    pub flags: u16,
    pub layer: i32,
    pub ne: [i64; 4],
    pub nbytes: usize,
}

/// Reduction plan: whether to allreduce or allgatherv, and per-rank counts.
#[derive(Clone, Debug)]
pub struct ReducePlan {
    /// True if this is a row-wise sum (all ranks have same shape).
    pub row_sum: bool,
    /// Payload bytes to send from each rank.
    pub counts: Vec<usize>,
    /// Byte displacement of each rank's payload in the recv buffer.
    pub displs: Vec<usize>,
    /// Output shape (concatenated).
    pub out_ne: [i64; 4],
    /// Layer index.
    pub layer: i32,
}

/// Chunk a payload into MPI-safe sizes.
///
/// Returns a list of chunk sizes in bytes. Each chunk is at most
/// `K_MPI_CHUNK_BYTES` bytes.
pub fn payload_chunks(nbytes: u64) -> Vec<u64> {
    if nbytes == 0 {
        return vec![];
    }
    let mut chunks = Vec::new();
    let mut remaining = nbytes;
    while remaining > 0 {
        let chunk = remaining.min(K_MPI_CHUNK_BYTES);
        chunks.push(chunk);
        remaining -= chunk;
    }
    chunks
}

/// Extract a ShardView from a Tensor.
pub fn view_of(tensor: &Tensor) -> ShardView {
    ShardView {
        ggml_type: tensor.ggml_type,
        flags: tensor.flags,
        layer: tensor.layer,
        ne: tensor.ne,
        nbytes: tensor.data.len(),
    }
}

/// Check if a usize value fits in an i32.
pub fn fits_int(value: usize) -> i32 {
    if value > i32::MAX as usize {
        -1
    } else {
        value as i32
    }
}

/// Decide whether to allreduce (row_sum) or allgatherv (column_concat).
///
/// Returns a `ReducePlan` with per-rank counts and displacements.
///
/// # Errors
/// Returns an error if:
/// - The kind is not "row_sum" or "column_concat"
/// - Shards have mismatched types or shapes
/// - Weights don't match shard widths
/// - Row sum would require padding
pub fn plan_reduce(
    shards: &[ShardView],
    kind: &str,
    weights: Option<&Vec<f64>>,
) -> io::Result<ReducePlan> {
    if shards.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "allreduce needs a slot per rank",
        ));
    }
    if kind != "row_sum" && kind != "column_concat" {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "kind must be row_sum or column_concat",
        ));
    }
    let mut plan = ReducePlan {
        row_sum: kind == "row_sum",
        counts: Vec::with_capacity(shards.len()),
        displs: Vec::with_capacity(shards.len()),
        out_ne: [1, 1, 1, 1],
        layer: shards[0].layer,
    };
    let mut total_elems: u64 = 0;
    for shard in shards {
        // Validate flags and type
        if shard.ggml_type != K_F32 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "reductions are f32 activations",
            ));
        }
        let elems = shard.ne.iter().product::<i64>() as u64;
        if elems * 4 != shard.nbytes as u64 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "payload is not a whole number of ggml elements",
            ));
        }
        if kind == "column_concat" && !(shard.flags & K_PARTIAL != 0) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "column shards must be marked partial activations",
            ));
        }
        // All shards must share ne[1..4]
        for d in 1..4 {
            if shard.ne[d] != shards[0].ne[d] {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    format!("column shards must share ne[1:] (got ne[{}]={}, expected ne[{}]={})",
                           d, shard.ne[d], d, shards[0].ne[d]),
                ));
            }
        }
        plan.counts.push(shard.nbytes);
        total_elems += elems;
    }
    // Validate against weights if provided
    if let Some(weights) = weights {
        if weights.len() != shards.len() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "weights length must match the communicator",
            ));
        }
        if total_elems > i64::MAX as u64 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "shard is too wide for the weight check",
            ));
        }
        let expected = largest_remainder(weights, total_elems as usize, None);
        for (i, shard) in shards.iter().enumerate() {
            let shard_elems = shard.ne.iter().product::<i64>() as usize;
            if shard_elems != expected[i] {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "payload widths disagree with weights",
                ));
            }
        }
    }
    // Row sum requires equal shapes
    if plan.row_sum {
        for shard in shards {
            if shard.ne != shards[0].ne {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "row-parallel MPI_Allreduce needs equal shapes; refusing to pad unequal slices (use column_concat for unequal widths)",
                ));
            }
        }
    }
    // Compute displacements
    let mut off: u64 = 0;
    for &n in &plan.counts {
        plan.displs.push(off as usize);
        off += n as u64;
    }
    // Compute output shape
    if plan.row_sum {
        plan.out_ne = shards[0].ne;
    } else {
        plan.out_ne = shards[0].ne;
        // Column concat: ne[0] is sum of all shard ne[0]
        plan.out_ne[0] = shards.iter().map(|s| s.ne[0]).sum();
    }
    Ok(plan)
}

/// Allocate `total` items across `weights` using largest-remainder method.
///
/// Returns a vector of `weights.len()` integers that sum to `total`.
/// Optionally respects per-rank `caps` (maximum allocations).
pub fn largest_remainder(weights: &Vec<f64>, total: usize, caps: Option<&Vec<i32>>) -> Vec<usize> {
    let _n = weights.len();
    // Normalize weights to sum to 1.0
    let weight_sum: f64 = weights.iter().sum();
    let normalized: Vec<f64> = weights.iter().map(|&w| w / weight_sum).collect();
    let raw: Vec<f64> = normalized.iter().map(|&w| w * total as f64).collect();
    let mut allocated: Vec<usize> = raw.iter().map(|&r| r.floor() as usize).collect();
    let mut remainder: Vec<f64> = raw.iter().enumerate().map(|(i, &r)| r - allocated[i] as f64).collect();
    let mut remaining = total;
    for a in &allocated {
        remaining = remaining.saturating_sub(*a);
    }
    // Distribute remaining by largest remainder
    while remaining > 0 {
        let mut max_idx = 0;
        let mut max_rem = -1.0f64;
        for (i, &rem) in remainder.iter().enumerate() {
            if rem > max_rem {
                max_rem = rem;
                max_idx = i;
            }
        }
        // Check cap
        if let Some(caps) = caps {
            if allocated[max_idx] >= caps[max_idx] as usize {
                remainder[max_idx] = -2.0; // Mark as exhausted
                continue;
            }
        }
        allocated[max_idx] += 1;
        remaining -= 1;
        remainder[max_idx] = -2.0; // Mark as distributed
    }
    allocated
}

/// Validate an activation header. Returns an error message if invalid.
pub fn activation_header_error(parsed: &Tensor, max_payload_bytes: u64) -> String {
    if parsed.ne.iter().any(|&n| n <= 0) {
        return "negative or zero shape dimension".to_string();
    }
    if parsed.data.len() as u64 > max_payload_bytes {
        return format!(
            "payload {} bytes exceeds budget {} bytes",
            parsed.data.len(),
            max_payload_bytes
        );
    }
    if parsed.collective != K_COLLECTIVE_BCAST
        && parsed.collective != K_COLLECTIVE_ALLREDUCE
        && parsed.collective != K_COLLECTIVE_ALLGATHERV
    {
        return format!("unknown collective type {}", parsed.collective);
    }
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tensor_f32_roundtrip() {
        let values = vec![1.0f32, 2.0, 3.0];
        let ne = [3, 1, 1, 1];
        let tensor = Tensor::f32(values, ne, 0, 0, false);
        let packed = tensor.pack();
        assert_eq!(packed.len(), K_HEADER_BYTES + 12); // 96 header + 3*4 bytes
        let unpacked = Tensor::unpack(&packed).unwrap();
        assert_eq!(unpacked.ggml_type, K_F32);
        assert_eq!(unpacked.ne, [3, 1, 1, 1]);
        assert_eq!(unpacked.data.len(), 12);
        let values_back = unpacked.f32_values().unwrap();
        assert_eq!(values_back, vec![1.0, 2.0, 3.0]);
    }

    #[test]
    fn test_tensor_flags() {
        let tensor = Tensor::f32(vec![1.0], [1, 1, 1, 1], 0, 0, true);
        assert_eq!(tensor.flags, K_PARTIAL);
        let tensor2 = Tensor::f32(vec![1.0], [1, 1, 1, 1], 0, 0, false);
        assert_eq!(tensor2.flags, 0);
    }

    #[test]
    fn test_payload_chunks_zero() {
        assert!(payload_chunks(0).is_empty());
    }

    #[test]
    fn test_payload_chunks_small() {
        let chunks = payload_chunks(100);
        assert_eq!(chunks, vec![100]);
    }

    #[test]
    fn test_payload_chunks_large() {
        let chunks = payload_chunks(K_MPI_CHUNK_BYTES + 100);
        assert_eq!(chunks, vec![K_MPI_CHUNK_BYTES, 100]);
    }

    #[test]
    fn test_view_of() {
        let tensor = Tensor::f32(vec![1.0, 2.0], [2, 1, 1, 1], 0, 5, false);
        let view = view_of(&tensor);
        assert_eq!(view.ggml_type, K_F32);
        assert_eq!(view.ne, [2, 1, 1, 1]);
        assert_eq!(view.layer, 5);
        assert_eq!(view.nbytes, 8);
    }

    #[test]
    fn test_activation_header_error_valid() {
        let tensor = Tensor::f32(vec![1.0], [1, 1, 1, 1], 0, 0, false);
        assert!(activation_header_error(&tensor, K_DEFAULT_MAX_PAYLOAD_BYTES).is_empty());
    }

    #[test]
    fn test_activation_header_error_too_large() {
        let mut tensor = Tensor::f32(vec![1.0], [1, 1, 1, 1], 0, 0, false);
        tensor.data = vec![0u8; (K_DEFAULT_MAX_PAYLOAD_BYTES + 1) as usize];
        let err = activation_header_error(&tensor, K_DEFAULT_MAX_PAYLOAD_BYTES);
        assert!(err.contains("exceeds budget"));
    }

    #[test]
    fn test_fits_int() {
        assert_eq!(fits_int(0), 0);
        assert_eq!(fits_int(i32::MAX as usize), i32::MAX);
        assert_eq!(fits_int(i32::MAX as usize + 1), -1);
    }

    #[test]
    fn test_plan_reduce_row_sum() {
        let shards = vec![
            ShardView { ggml_type: K_F32, flags: 0, layer: 0, ne: [8, 1, 1, 1], nbytes: 32 },
            ShardView { ggml_type: K_F32, flags: 0, layer: 0, ne: [8, 1, 1, 1], nbytes: 32 },
        ];
        let plan = plan_reduce(&shards, "row_sum", None).unwrap();
        assert!(plan.row_sum);
        assert_eq!(plan.counts, vec![32, 32]);
        assert_eq!(plan.displs, vec![0, 32]);
        assert_eq!(plan.out_ne, [8, 1, 1, 1]);
    }

    #[test]
    fn test_plan_reduce_column_concat() {
        let shards = vec![
            ShardView { ggml_type: K_F32, flags: K_PARTIAL, layer: 0, ne: [4, 8, 1, 1], nbytes: 128 },
            ShardView { ggml_type: K_F32, flags: K_PARTIAL, layer: 0, ne: [4, 8, 1, 1], nbytes: 128 },
        ];
        let plan = plan_reduce(&shards, "column_concat", None).unwrap();
        assert!(!plan.row_sum);
        assert_eq!(plan.counts, vec![128, 128]);
        assert_eq!(plan.displs, vec![0, 128]);
        assert_eq!(plan.out_ne, [8, 8, 1, 1]); // Sum of ne[0]
    }

    #[test]
    fn test_plan_reduce_invalid_kind() {
        let shards = vec![ShardView { ggml_type: K_F32, flags: 0, layer: 0, ne: [1, 1, 1, 1], nbytes: 4 }];
        let err = plan_reduce(&shards, "invalid", None).unwrap_err();
        assert!(err.to_string().contains("kind must be"));
    }

    #[test]
    fn test_plan_reduce_mismatched_ne() {
        let shards = vec![
            ShardView { ggml_type: K_F32, flags: K_PARTIAL, layer: 0, ne: [4, 8, 1, 1], nbytes: 128 },
            ShardView { ggml_type: K_F32, flags: K_PARTIAL, layer: 0, ne: [4, 16, 1, 1], nbytes: 256 },
        ];
        let err = plan_reduce(&shards, "column_concat", None).unwrap_err();
        assert!(err.to_string().contains("ne[1:]"));
    }

    #[test]
    fn test_plan_reduce_row_sum_unequal() {
        let shards = vec![
            ShardView { ggml_type: K_F32, flags: 0, layer: 0, ne: [4, 1, 1, 1], nbytes: 16 },
            ShardView { ggml_type: K_F32, flags: 0, layer: 0, ne: [8, 1, 1, 1], nbytes: 32 },
        ];
        let err = plan_reduce(&shards, "row_sum", None).unwrap_err();
        assert!(err.to_string().contains("equal shapes"));
    }

    #[test]
    fn test_largest_remainder_basic() {
        let weights = vec![0.5, 0.5];
        let result = largest_remainder(&weights, 10, None);
        assert_eq!(result, vec![5, 5]);
    }

    #[test]
    fn test_largest_remainder_uneven() {
        let weights = vec![2.0, 1.0]; // 2:1 ratio
        let result = largest_remainder(&weights, 12, None);
        assert_eq!(result, vec![8, 4]);
    }

    #[test]
    fn test_largest_remainder_with_caps() {
        let weights = vec![1.0, 1.0];
        let caps = vec![5, 10]; // Rank 0 capped at 5
        let result = largest_remainder(&weights, 10, Some(&caps));
        assert_eq!(result[0], 5); // Capped
        assert_eq!(result[1], 5); // Rest goes to rank 1
    }
}
