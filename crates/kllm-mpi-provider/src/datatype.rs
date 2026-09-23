//! Datatypes and buffers.
//!
//! Mirrors `mpi::datatype` in rsmpi: the [`Equivalence`] trait maps a Rust
//! type to an MPI datatype, and the [`Buffer`] / [`BufferMut`] traits describe
//! contiguous send / receive buffers. Varying-count collectives use
//! [`Partition`] / [`PartitionMut`].
//!
//! Because this is a pure-Rust implementation there is no opaque `MPI_Datatype`
//! handle to manage; a datatype is fully described by [`DatatypeRef`] (a stable
//! id plus the element size), which is all the runtime needs to move and
//! type-check bytes.

use crate::Count;

/// Numeric identifiers for the built-in datatypes. These are stable within a
/// run and are carried on the wire so a receiver can detect a datatype
/// mismatch.
pub mod ids {
    /// `bool` (`MPI_C_BOOL`).
    pub const BOOL: u32 = 1;
    /// `i8` (`MPI_INT8_T`).
    pub const I8: u32 = 2;
    /// `u8` (`MPI_UINT8_T`).
    pub const U8: u32 = 3;
    /// `i16` (`MPI_INT16_T`).
    pub const I16: u32 = 4;
    /// `u16` (`MPI_UINT16_T`).
    pub const U16: u32 = 5;
    /// `i32` (`MPI_INT32_T`).
    pub const I32: u32 = 6;
    /// `u32` (`MPI_UINT32_T`).
    pub const U32: u32 = 7;
    /// `i64` (`MPI_INT64_T`).
    pub const I64: u32 = 8;
    /// `u64` (`MPI_UINT64_T`).
    pub const U64: u32 = 9;
    /// `i128`.
    pub const I128: u32 = 10;
    /// `u128`.
    pub const U128: u32 = 11;
    /// `isize`.
    pub const ISIZE: u32 = 12;
    /// `usize`.
    pub const USIZE: u32 = 13;
    /// `f32` (`MPI_FLOAT`).
    pub const F32: u32 = 14;
    /// `f64` (`MPI_DOUBLE`).
    pub const F64: u32 = 15;
    /// `char` (Unicode scalar value, 4 bytes).
    pub const CHAR: u32 = 16;
    /// Base id from which user-defined / derived datatype ids are allocated.
    pub const USER_BASE: u32 = 1024;
}

/// The size in bytes of one element of a built-in datatype id, or `1` (treat as
/// opaque bytes) for user/derived datatypes whose layout the transport cannot
/// know. Used to byte-swap typed payloads for big-endian interoperability.
#[cfg(target_endian = "big")]
pub(crate) fn wire_elem_size(id: u32) -> usize {
    match id {
        ids::BOOL | ids::I8 | ids::U8 => 1,
        ids::I16 | ids::U16 => 2,
        ids::I32 | ids::U32 | ids::F32 | ids::CHAR => 4,
        ids::I64 | ids::U64 | ids::ISIZE | ids::USIZE | ids::F64 => 8,
        ids::I128 | ids::U128 => 16,
        _ => 1,
    }
}

/// A lightweight description of a datatype: a stable id and the size in bytes
/// of one element.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DatatypeRef {
    /// Stable identifier used for on-the-wire type checking.
    pub id: u32,
    /// Size of a single element in bytes.
    pub size: usize,
}

/// Types that have an equivalent MPI datatype and a well-defined byte
/// representation.
///
/// # Safety
///
/// Implementing this trait asserts that the type is "plain old data": it is
/// `Copy`, contains no padding that carries meaning, no pointers/references,
/// and can be safely reinterpreted as a `[u8]` of length `size_of::<Self>()`
/// and reconstructed from such bytes. All built-in impls satisfy this.
pub unsafe trait Equivalence: Copy + 'static {
    /// The datatype describing `Self`.
    fn equivalent_datatype() -> DatatypeRef;
}

macro_rules! impl_equivalence {
    ($($t:ty => $id:expr),+ $(,)?) => {$(
        // SAFETY: all listed types are primitive POD types.
        unsafe impl Equivalence for $t {
            fn equivalent_datatype() -> DatatypeRef {
                DatatypeRef { id: $id, size: ::core::mem::size_of::<$t>() }
            }
        }
    )+};
}

impl_equivalence! {
    bool  => ids::BOOL,
    i8    => ids::I8,
    u8    => ids::U8,
    i16   => ids::I16,
    u16   => ids::U16,
    i32   => ids::I32,
    u32   => ids::U32,
    i64   => ids::I64,
    u64   => ids::U64,
    i128  => ids::I128,
    u128  => ids::U128,
    isize => ids::ISIZE,
    usize => ids::USIZE,
    f32   => ids::F32,
    f64   => ids::F64,
    char  => ids::CHAR,
}

/// Something with a known element count and datatype. The common supertrait of
/// [`Buffer`] and [`BufferMut`].
pub trait Collection {
    /// Number of elements.
    fn count(&self) -> Count;
    /// The datatype of the elements.
    fn as_datatype(&self) -> DatatypeRef;
}

/// A read-only, contiguous send buffer.
///
/// # Safety
///
/// [`Buffer::as_bytes`] must return exactly `count * element_size` bytes that
/// are a valid representation of the buffer's contents.
pub unsafe trait Buffer: Collection {
    /// A byte view of the whole buffer.
    fn as_bytes(&self) -> &[u8];
}

/// A writable, contiguous receive buffer.
///
/// # Safety
///
/// [`BufferMut::as_bytes_mut`] must return exactly `count * element_size`
/// bytes; writing any bit pattern of that length must leave the buffer in a
/// valid state (guaranteed for [`Equivalence`] POD element types).
pub unsafe trait BufferMut: Collection {
    /// A mutable byte view of the whole buffer.
    fn as_bytes_mut(&mut self) -> &mut [u8];

    /// Place received `bytes` into this buffer.
    ///
    /// The default performs a contiguous copy, which is correct for plain
    /// buffers. Strided / derived-datatype receive buffers (e.g.
    /// [`MutView`]) override this to scatter the incoming bytes according to
    /// their type map.
    fn scatter_from(&mut self, bytes: &[u8]) {
        let dst = self.as_bytes_mut();
        let n = dst.len().min(bytes.len());
        dst[..n].copy_from_slice(&bytes[..n]);
    }
}

#[inline]
fn as_byte_slice<T: Equivalence>(s: &[T]) -> &[u8] {
    // SAFETY: `T: Equivalence` is POD, so its bytes are always initialized and
    // meaningful; the length is exactly `size_of_val`.
    unsafe { core::slice::from_raw_parts(s.as_ptr() as *const u8, core::mem::size_of_val(s)) }
}

#[inline]
fn as_byte_slice_mut<T: Equivalence>(s: &mut [T]) -> &mut [u8] {
    // SAFETY: as above; POD types accept any bit pattern.
    unsafe { core::slice::from_raw_parts_mut(s.as_mut_ptr() as *mut u8, core::mem::size_of_val(s)) }
}

// ---- Scalars: a single value is a buffer of count 1. ----

impl<T: Equivalence> Collection for T {
    fn count(&self) -> Count {
        1
    }
    fn as_datatype(&self) -> DatatypeRef {
        T::equivalent_datatype()
    }
}

unsafe impl<T: Equivalence> Buffer for T {
    fn as_bytes(&self) -> &[u8] {
        as_byte_slice(core::slice::from_ref(self))
    }
}

unsafe impl<T: Equivalence> BufferMut for T {
    fn as_bytes_mut(&mut self) -> &mut [u8] {
        as_byte_slice_mut(core::slice::from_mut(self))
    }
}

// ---- Slices. ----

impl<T: Equivalence> Collection for [T] {
    fn count(&self) -> Count {
        self.len() as Count
    }
    fn as_datatype(&self) -> DatatypeRef {
        T::equivalent_datatype()
    }
}

unsafe impl<T: Equivalence> Buffer for [T] {
    fn as_bytes(&self) -> &[u8] {
        as_byte_slice(self)
    }
}

unsafe impl<T: Equivalence> BufferMut for [T] {
    fn as_bytes_mut(&mut self) -> &mut [u8] {
        as_byte_slice_mut(self)
    }
}

// ---- Fixed-size arrays. ----

impl<T: Equivalence, const N: usize> Collection for [T; N] {
    fn count(&self) -> Count {
        N as Count
    }
    fn as_datatype(&self) -> DatatypeRef {
        T::equivalent_datatype()
    }
}

unsafe impl<T: Equivalence, const N: usize> Buffer for [T; N] {
    fn as_bytes(&self) -> &[u8] {
        as_byte_slice(self.as_slice())
    }
}

unsafe impl<T: Equivalence, const N: usize> BufferMut for [T; N] {
    fn as_bytes_mut(&mut self) -> &mut [u8] {
        as_byte_slice_mut(self.as_mut_slice())
    }
}

/// A buffer whose elements are partitioned into per-rank blocks of possibly
/// differing sizes, described by counts and displacements. Used by the
/// varying-count ("v") collectives.
///
/// # Safety
///
/// The counts and displacements must describe blocks that lie within the
/// underlying buffer.
pub unsafe trait Partitioned {
    /// The datatype of one element.
    fn as_datatype(&self) -> DatatypeRef;
    /// Per-block element counts.
    fn counts(&self) -> &[Count];
    /// Per-block element displacements (in elements from the start).
    fn displs(&self) -> &[Count];
}

/// A read-only partitioned buffer (send side of `*v` collectives).
///
/// # Safety
///
/// [`PartitionedBuffer::as_bytes`] must be valid for reading
/// `element_size * (max displ+count)` bytes.
pub unsafe trait PartitionedBuffer: Partitioned {
    /// Byte view of the whole underlying buffer.
    fn as_bytes(&self) -> &[u8];
}

/// A writable partitioned buffer (receive side of `*v` collectives).
///
/// # Safety
///
/// As [`PartitionedBuffer`], but for mutable access.
pub unsafe trait PartitionedBufferMut: Partitioned {
    /// Mutable byte view of the whole underlying buffer.
    fn as_bytes_mut(&mut self) -> &mut [u8];
}

/// A read-only view over a buffer partitioned by `counts` at `displs`.
pub struct Partition<'a, T: Equivalence> {
    buf: &'a [T],
    counts: Vec<Count>,
    displs: Vec<Count>,
}

impl<'a, T: Equivalence> Partition<'a, T> {
    /// Build a partition from a buffer, per-block counts and displacements
    /// (both measured in elements).
    pub fn new<C, D>(buf: &'a [T], counts: C, displs: D) -> Partition<'a, T>
    where
        C: Into<Vec<Count>>,
        D: Into<Vec<Count>>,
    {
        Partition {
            buf,
            counts: counts.into(),
            displs: displs.into(),
        }
    }
}

unsafe impl<T: Equivalence> Partitioned for Partition<'_, T> {
    fn as_datatype(&self) -> DatatypeRef {
        T::equivalent_datatype()
    }
    fn counts(&self) -> &[Count] {
        &self.counts
    }
    fn displs(&self) -> &[Count] {
        &self.displs
    }
}

unsafe impl<T: Equivalence> PartitionedBuffer for Partition<'_, T> {
    fn as_bytes(&self) -> &[u8] {
        as_byte_slice(self.buf)
    }
}

/// A mutable view over a buffer partitioned by `counts` at `displs`.
pub struct PartitionMut<'a, T: Equivalence> {
    buf: &'a mut [T],
    counts: Vec<Count>,
    displs: Vec<Count>,
}

impl<'a, T: Equivalence> PartitionMut<'a, T> {
    /// Build a mutable partition from a buffer, per-block counts and
    /// displacements (both measured in elements).
    pub fn new<C, D>(buf: &'a mut [T], counts: C, displs: D) -> PartitionMut<'a, T>
    where
        C: Into<Vec<Count>>,
        D: Into<Vec<Count>>,
    {
        PartitionMut {
            buf,
            counts: counts.into(),
            displs: displs.into(),
        }
    }
}

unsafe impl<T: Equivalence> Partitioned for PartitionMut<'_, T> {
    fn as_datatype(&self) -> DatatypeRef {
        T::equivalent_datatype()
    }
    fn counts(&self) -> &[Count] {
        &self.counts
    }
    fn displs(&self) -> &[Count] {
        &self.displs
    }
}

unsafe impl<T: Equivalence> PartitionedBufferMut for PartitionMut<'_, T> {
    fn as_bytes_mut(&mut self) -> &mut [u8] {
        as_byte_slice_mut(self.buf)
    }
}

/// A user-defined (derived) datatype describing a non-contiguous layout as a
/// type map: a set of `(byte_offset, byte_length)` blocks repeated with a
/// fixed byte `extent`. Mirrors rsmpi's `UserDatatype`.
///
/// This implementation supports the common constructors — [`contiguous`],
/// [`vector`], [`indexed`] and [`structured`] — over a primitive base type.
/// Send a derived layout with [`View`]; receive one with [`MutView`].
///
/// [`contiguous`]: UserDatatype::contiguous
/// [`vector`]: UserDatatype::vector
/// [`indexed`]: UserDatatype::indexed
/// [`structured`]: UserDatatype::structured
#[derive(Clone, Debug)]
pub struct UserDatatype {
    id: u32,
    base: DatatypeRef,
    /// `(byte_offset, byte_length)` blocks within one element.
    blocks: Vec<(usize, usize)>,
    /// The byte extent (stride) of one element of this datatype.
    extent: usize,
    /// The number of base elements described by one element of this datatype.
    base_count: Count,
}

impl UserDatatype {
    fn from_blocks(base: DatatypeRef, blocks: Vec<(usize, usize)>, extent: usize) -> UserDatatype {
        let bytes: usize = blocks.iter().map(|(_, l)| *l).sum();
        let base_count = (bytes / base.size.max(1)) as Count;
        UserDatatype {
            id: ids::USER_BASE + base.id,
            base,
            blocks,
            extent,
            base_count,
        }
    }

    /// `count` contiguous copies of `base` (`MPI_Type_contiguous`).
    pub fn contiguous(count: Count, base: DatatypeRef) -> UserDatatype {
        let len = count as usize * base.size;
        UserDatatype::from_blocks(base, vec![(0, len)], len)
    }

    /// `count` blocks of `blocklength` base elements, each `stride` base
    /// elements apart (`MPI_Type_vector`).
    pub fn vector(
        count: Count,
        blocklength: Count,
        stride: Count,
        base: DatatypeRef,
    ) -> UserDatatype {
        let bl = blocklength as usize * base.size;
        let mut blocks = Vec::with_capacity(count as usize);
        for i in 0..count as usize {
            blocks.push((i * stride as usize * base.size, bl));
        }
        let extent = if count == 0 {
            0
        } else {
            ((count as usize - 1) * stride as usize + blocklength as usize) * base.size
        };
        UserDatatype::from_blocks(base, blocks, extent)
    }

    /// Blocks of the given per-block lengths at the given base-element
    /// displacements (`MPI_Type_indexed`).
    pub fn indexed(
        blocklengths: &[Count],
        displacements: &[Count],
        base: DatatypeRef,
    ) -> UserDatatype {
        assert_eq!(blocklengths.len(), displacements.len());
        let mut blocks = Vec::with_capacity(blocklengths.len());
        let mut extent = 0usize;
        for (bl, d) in blocklengths.iter().zip(displacements) {
            let off = *d as usize * base.size;
            let len = *bl as usize * base.size;
            blocks.push((off, len));
            extent = extent.max(off + len);
        }
        UserDatatype::from_blocks(base, blocks, extent)
    }

    /// Blocks of the given per-block base-element lengths at explicit byte
    /// displacements (`MPI_Type_create_struct`, single base type).
    pub fn structured(
        blocklengths: &[Count],
        byte_displacements: &[isize],
        base: DatatypeRef,
    ) -> UserDatatype {
        assert_eq!(blocklengths.len(), byte_displacements.len());
        let mut blocks = Vec::with_capacity(blocklengths.len());
        let mut extent = 0usize;
        for (bl, d) in blocklengths.iter().zip(byte_displacements) {
            let off = *d as usize;
            let len = *bl as usize * base.size;
            blocks.push((off, len));
            extent = extent.max(off + len);
        }
        UserDatatype::from_blocks(base, blocks, extent)
    }

    /// The datatype's stable id.
    pub fn id(&self) -> u32 {
        self.id
    }

    /// The byte extent (stride) of one element (`MPI_Type_extent`).
    pub fn extent(&self) -> usize {
        self.extent
    }

    /// The number of base elements in one element of this datatype.
    pub fn base_count(&self) -> Count {
        self.base_count
    }

    /// Gather `count` elements of this datatype from `base` into a contiguous
    /// packed byte buffer.
    pub fn pack(&self, base: &[u8], count: Count) -> Vec<u8> {
        let mut out =
            Vec::with_capacity(count as usize * self.base_count as usize * self.base.size);
        for e in 0..count as usize {
            let elem = e * self.extent;
            for &(off, len) in &self.blocks {
                out.extend_from_slice(&base[elem + off..elem + off + len]);
            }
        }
        out
    }

    /// Scatter `count` packed elements back into `base` according to the type
    /// map.
    pub fn unpack(&self, packed: &[u8], base: &mut [u8], count: Count) {
        let mut pos = 0usize;
        for e in 0..count as usize {
            let elem = e * self.extent;
            for &(off, len) in &self.blocks {
                let end = (pos + len).min(packed.len());
                let take = end - pos;
                base[elem + off..elem + off + take].copy_from_slice(&packed[pos..pos + take]);
                pos += take;
            }
        }
    }
}

/// A read-only send buffer that applies a [`UserDatatype`] layout to a base
/// buffer, packing the selected bytes contiguously. Mirrors rsmpi's `View`.
pub struct View {
    packed: Vec<u8>,
    base: DatatypeRef,
}

impl View {
    /// Create a view of `count` elements of `datatype` over `buf`.
    pub fn with_count<B: Buffer + ?Sized>(buf: &B, datatype: &UserDatatype, count: Count) -> View {
        View {
            packed: datatype.pack(buf.as_bytes(), count),
            base: datatype.base,
        }
    }
}

impl Collection for View {
    fn count(&self) -> Count {
        (self.packed.len() / self.base.size.max(1)) as Count
    }
    fn as_datatype(&self) -> DatatypeRef {
        self.base
    }
}

// SAFETY: `packed` is a contiguous owned byte buffer.
unsafe impl Buffer for View {
    fn as_bytes(&self) -> &[u8] {
        &self.packed
    }
}

/// A writable receive buffer that scatters incoming bytes into a base buffer
/// according to a [`UserDatatype`] layout. Mirrors rsmpi's `MutView`.
pub struct MutView<'a, B: BufferMut + ?Sized> {
    base: &'a mut B,
    datatype: UserDatatype,
    count: Count,
}

impl<'a, B: BufferMut + ?Sized> MutView<'a, B> {
    /// Create a mutable view of `count` elements of `datatype` over `buf`.
    pub fn with_count(buf: &'a mut B, datatype: UserDatatype, count: Count) -> MutView<'a, B> {
        MutView {
            base: buf,
            datatype,
            count,
        }
    }
}

impl<B: BufferMut + ?Sized> Collection for MutView<'_, B> {
    fn count(&self) -> Count {
        self.datatype.base_count * self.count
    }
    fn as_datatype(&self) -> DatatypeRef {
        self.datatype.base
    }
}

// SAFETY: `scatter_from` places received bytes into the base buffer per the
// type map; `as_bytes_mut` exposes the underlying base bytes.
unsafe impl<B: BufferMut + ?Sized> BufferMut for MutView<'_, B> {
    fn as_bytes_mut(&mut self) -> &mut [u8] {
        self.base.as_bytes_mut()
    }
    fn scatter_from(&mut self, bytes: &[u8]) {
        self.datatype
            .unpack(bytes, self.base.as_bytes_mut(), self.count);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn datatype_ids_and_sizes() {
        assert_eq!(i32::equivalent_datatype().id, ids::I32);
        assert_eq!(i32::equivalent_datatype().size, 4);
        assert_eq!(f64::equivalent_datatype().size, 8);
        assert_eq!(u8::equivalent_datatype().size, 1);
    }

    #[test]
    fn slice_byte_view_roundtrips() {
        let data: [i32; 3] = [1, 2, 0x0102_0304];
        let bytes = data.as_slice().as_bytes();
        assert_eq!(bytes.len(), 12);
        assert_eq!(<[i32] as Collection>::count(&data[..]), 3);
        // Reconstruct and compare.
        let mut out = [0i32; 3];
        out.as_mut_slice().as_bytes_mut().copy_from_slice(bytes);
        assert_eq!(out, data);
    }

    #[test]
    fn scalar_is_a_buffer_of_one() {
        let x = 7u64;
        assert_eq!(Collection::count(&x), 1);
        assert_eq!(x.as_bytes().len(), 8);
    }

    #[test]
    fn vector_datatype_pack_unpack() {
        // Pick every other i32 from a 6-element base: elements 0,2,4.
        let dt = UserDatatype::vector(3, 1, 2, i32::equivalent_datatype());
        let base = [10i32, 11, 20, 21, 30, 31];
        let packed = dt.pack(base.as_slice().as_bytes(), 1);
        let got: Vec<i32> = crate::point_to_point::vec_from_bytes(&packed);
        assert_eq!(got, vec![10, 20, 30]);

        // Round-trip: scatter back into a fresh base of the same shape.
        let mut out = [0i32; 6];
        dt.unpack(&packed, out.as_mut_slice().as_bytes_mut(), 1);
        assert_eq!(out, [10, 0, 20, 0, 30, 0]);
    }

    #[test]
    fn contiguous_and_indexed() {
        let c = UserDatatype::contiguous(4, f64::equivalent_datatype());
        assert_eq!(c.base_count(), 4);
        assert_eq!(c.extent(), 32);

        let idx = UserDatatype::indexed(&[2, 1], &[0, 4], i32::equivalent_datatype());
        let base = [1i32, 2, 3, 4, 5, 6];
        let packed = idx.pack(base.as_slice().as_bytes(), 1);
        let got: Vec<i32> = crate::point_to_point::vec_from_bytes(&packed);
        assert_eq!(got, vec![1, 2, 5]);
    }

    /// Property test: for many random `vector` layouts, pack-then-unpack into a
    /// fresh buffer must reproduce exactly the selected elements.
    #[test]
    fn vector_pack_unpack_roundtrips() {
        let mut state: u64 = 0xdead_beef_0000_0001;
        let mut rng = || {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            state
        };
        for _ in 0..300 {
            let count = (rng() % 4) as i32 + 1;
            let blocklen = (rng() % 3) as i32 + 1;
            let stride = blocklen + (rng() % 3) as i32; // stride >= blocklen
            let dt = UserDatatype::vector(count, blocklen, stride, i32::equivalent_datatype());
            // Build a base buffer large enough for the datatype's extent.
            let n_elems = dt.extent() / 4;
            let base: Vec<i32> = (0..n_elems as i32).collect();
            let packed = dt.pack(base.as_slice().as_bytes(), 1);
            let got: Vec<i32> = crate::point_to_point::vec_from_bytes(&packed);

            // Reference: gather blocks manually.
            let mut expected = Vec::new();
            for c in 0..count {
                for k in 0..blocklen {
                    expected.push(c * stride + k);
                }
            }
            assert_eq!(got, expected, "vector pack mismatch");

            // Unpack into a zeroed buffer and check the selected slots.
            let mut out = vec![0i32; n_elems];
            dt.unpack(&packed, out.as_mut_slice().as_bytes_mut(), 1);
            for c in 0..count {
                for k in 0..blocklen {
                    let idx = (c * stride + k) as usize;
                    assert_eq!(out[idx], idx as i32, "vector unpack mismatch");
                }
            }
        }
    }
}
