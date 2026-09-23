//! Groups and communicators. Mirrors `mpi::topology` in rsmpi (0.8.x), where
//! the former `SystemCommunicator`/`UserCommunicator` split is unified into a
//! single [`SimpleCommunicator`]. Backwards-compatible aliases for the old
//! names are provided.

use std::any::Any;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex};

use crate::datatype::{Buffer, BufferMut, DatatypeRef};
use crate::point_to_point::{AnyProcess, Process};
use crate::transport;
use crate::{Count, Rank, Tag};

/// Key used to order processes within a colour when splitting a communicator.
pub type Key = i32;

/// The high context bit reserved to isolate collective traffic from user
/// point-to-point traffic on the same communicator.
pub(crate) const COLL_CONTEXT_BIT: u32 = 0x8000_0000;

/// Internal shared description of a communicator: its context id, this
/// process's rank within it, its size, and the mapping from communicator-local
/// rank to world rank (used for routing on the transport, which addresses
/// peers by world rank).
pub struct CommData {
    pub(crate) context: u32,
    pub(crate) rank: Rank,
    pub(crate) size: Rank,
    /// `world_ranks[comm_rank] == world_rank`.
    pub(crate) world_ranks: Vec<i32>,
    /// Monotonic counter used to derive child-communicator contexts. All
    /// members increment it in lock-step (collective calls happen in the same
    /// order on every member), so derived contexts agree across processes.
    pub(crate) child_seq: AtomicU32,
    /// The communicator's name (`MPI_Comm_set_name` / `MPI_Comm_get_name`).
    pub(crate) name: Mutex<Option<String>>,
    /// Cached attributes, keyed by keyval id (attribute caching).
    pub(crate) attributes: Mutex<HashMap<i32, Box<dyn Any + Send + Sync>>>,
}

impl CommData {
    /// Build the `MPI_COMM_WORLD` description for this process.
    fn world() -> CommData {
        let rt = transport::runtime();
        let size = rt.size;
        CommData {
            context: 0,
            rank: rt.rank,
            size,
            world_ranks: (0..size).collect(),
            child_seq: AtomicU32::new(0),
            name: Mutex::new(Some("MPI_COMM_WORLD".to_string())),
            attributes: Mutex::new(HashMap::new()),
        }
    }

    /// Context id used for this communicator's collective operations.
    pub(crate) fn coll_context(&self) -> u32 {
        self.context | COLL_CONTEXT_BIT
    }

    /// World rank of a communicator-local rank.
    pub(crate) fn world_rank(&self, comm_rank: Rank) -> i32 {
        self.world_ranks[comm_rank as usize]
    }

    /// An owned copy of this communicator's routing info under a fresh context,
    /// for running a collective on a background thread (async collectives).
    pub(crate) fn async_clone(&self, context: u32) -> CommData {
        CommData {
            context,
            rank: self.rank,
            size: self.size,
            world_ranks: self.world_ranks.clone(),
            child_seq: AtomicU32::new(0),
            name: Mutex::new(None),
            attributes: Mutex::new(HashMap::new()),
        }
    }

    /// Derive a fresh, globally-agreed child context from a discriminator
    /// (e.g. a colour). All members that pass the same `seq` and `disc`
    /// compute the same value.
    pub(crate) fn derive_context(&self, disc: u32) -> u32 {
        let seq = self.child_seq.fetch_add(1, Ordering::SeqCst);
        // A small avalanche mix of (parent context, sequence, discriminator).
        let mut h = self
            .context
            .wrapping_mul(0x9E37_79B1)
            .wrapping_add(seq.wrapping_mul(0x85EB_CA77))
            .wrapping_add(disc.wrapping_mul(0xC2B2_AE3D));
        h ^= h >> 15;
        h = h.wrapping_mul(0x2545_F491);
        h ^= h >> 13;
        // Keep the collective bit clear; it is added on demand.
        h & !COLL_CONTEXT_BIT
    }
}

/// How two communicators relate (`MPI_Comm_compare`).
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum CommunicatorRelation {
    /// The communicators are handles to the same object.
    Identical,
    /// Same group and rank order, different context.
    Congruent,
    /// Same members, different rank order.
    Similar,
    /// The groups differ.
    Unequal,
}

/// How two groups relate (`MPI_Group_compare`).
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum GroupRelation {
    /// Same members in the same order.
    Identical,
    /// Same members in a different order.
    Similar,
    /// The members differ.
    Unequal,
}

/// A colour used by [`Communicator::split_by_color`]. `undefined` processes are
/// dropped from the split (they receive `None`).
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub struct Color(Option<i32>);

impl Color {
    /// A concrete colour value; processes sharing a value end up together.
    pub fn with_value(value: Rank) -> Color {
        Color(Some(value))
    }
    /// The "undefined" colour (`MPI_UNDEFINED`); such a process is excluded.
    pub fn undefined() -> Color {
        Color(None)
    }
    fn value(&self) -> Option<i32> {
        self.0
    }
}

/// A group of processes, identified by their world ranks. Mirrors rsmpi's
/// `Group` / `UserGroup`.
#[derive(Clone, Debug)]
pub struct Group {
    /// Member world ranks, in group-rank order.
    members: Vec<i32>,
}

/// Backwards-compatible alias for [`Group`].
pub type UserGroup = Group;

impl Group {
    pub(crate) fn from_world_ranks(mut members: Vec<i32>) -> Group {
        members.dedup();
        Group { members }
    }

    /// The empty group (`MPI_GROUP_EMPTY`).
    pub fn empty() -> Group {
        Group {
            members: Vec::new(),
        }
    }

    /// Number of processes in the group.
    pub fn size(&self) -> Rank {
        self.members.len() as Rank
    }

    /// This process's rank within the group, if it is a member.
    pub fn rank(&self) -> Option<Rank> {
        let me = transport::runtime().rank;
        self.members
            .iter()
            .position(|&w| w == me)
            .map(|p| p as Rank)
    }

    /// Translate group-local ranks of `self` into the ranks they occupy in
    /// `other` (or `None` if not present).
    pub fn translate_ranks(&self, ranks: &[Rank], other: &Group) -> Vec<Option<Rank>> {
        ranks
            .iter()
            .map(|&r| {
                let w = self.members.get(r as usize).copied()?;
                other
                    .members
                    .iter()
                    .position(|&o| o == w)
                    .map(|p| p as Rank)
            })
            .collect()
    }

    /// Set union with `other` (ranks of `self` first, then new ranks of
    /// `other`, in order).
    pub fn union(&self, other: &Group) -> Group {
        let mut m = self.members.clone();
        for &w in &other.members {
            if !m.contains(&w) {
                m.push(w);
            }
        }
        Group { members: m }
    }

    /// Set intersection with `other` (order of `self`).
    pub fn intersection(&self, other: &Group) -> Group {
        let m = self
            .members
            .iter()
            .copied()
            .filter(|w| other.members.contains(w))
            .collect();
        Group { members: m }
    }

    /// Set difference `self \ other` (order of `self`).
    pub fn difference(&self, other: &Group) -> Group {
        let m = self
            .members
            .iter()
            .copied()
            .filter(|w| !other.members.contains(w))
            .collect();
        Group { members: m }
    }

    /// Sub-group containing only the listed group-local ranks (in the given
    /// order).
    pub fn include(&self, ranks: &[Rank]) -> Group {
        let m = ranks
            .iter()
            .filter_map(|&r| self.members.get(r as usize).copied())
            .collect();
        Group { members: m }
    }

    /// Sub-group excluding the listed group-local ranks.
    pub fn exclude(&self, ranks: &[Rank]) -> Group {
        let drop: Vec<i32> = ranks
            .iter()
            .filter_map(|&r| self.members.get(r as usize).copied())
            .collect();
        let m = self
            .members
            .iter()
            .copied()
            .filter(|w| !drop.contains(w))
            .collect();
        Group { members: m }
    }

    /// Compare two groups.
    pub fn compare(&self, other: &Group) -> GroupRelation {
        if self.members == other.members {
            GroupRelation::Identical
        } else {
            let mut a = self.members.clone();
            let mut b = other.members.clone();
            a.sort_unstable();
            b.sort_unstable();
            if a == b {
                GroupRelation::Similar
            } else {
                GroupRelation::Unequal
            }
        }
    }

    pub(crate) fn members(&self) -> &[i32] {
        &self.members
    }
}

/// The behaviour shared by every communicator. Mirrors rsmpi's
/// [`Communicator`](https://docs.rs/mpi/latest/mpi/topology/trait.Communicator.html)
/// trait; the required method here is the internal handle accessor rather than
/// `target_size`.
pub trait Communicator {
    /// Internal: the shared communicator description. Not part of the stable
    /// public API.
    #[doc(hidden)]
    fn comm_data(&self) -> &CommData;

    /// Number of processes in the communicator (`MPI_Comm_size`).
    fn size(&self) -> Rank {
        self.comm_data().size
    }

    /// This process's rank in the communicator (`MPI_Comm_rank`).
    fn rank(&self) -> Rank {
        self.comm_data().rank
    }

    /// Number of processes on the remote side (equals [`Communicator::size`]
    /// for an intra-communicator).
    fn target_size(&self) -> Rank {
        self.comm_data().size
    }

    /// A handle to the process with the given rank.
    fn process_at_rank(&self, r: Rank) -> Process<'_> {
        Process::new(self.comm_data(), r)
    }

    /// A handle to this process.
    fn this_process(&self) -> Process<'_> {
        Process::new(self.comm_data(), self.comm_data().rank)
    }

    /// A handle matching any source (`MPI_ANY_SOURCE`), for receives.
    fn any_process(&self) -> AnyProcess<'_> {
        AnyProcess::new(self.comm_data())
    }

    /// The group underlying this communicator (`MPI_Comm_group`).
    fn group(&self) -> Group {
        Group::from_world_ranks(self.comm_data().world_ranks.clone())
    }

    /// Compare this communicator to `other` (`MPI_Comm_compare`).
    fn compare(&self, other: &dyn Communicator) -> CommunicatorRelation {
        let a = self.comm_data();
        let b = other.comm_data();
        if a.context == b.context {
            CommunicatorRelation::Identical
        } else if a.world_ranks == b.world_ranks {
            CommunicatorRelation::Congruent
        } else {
            let mut sa = a.world_ranks.clone();
            let mut sb = b.world_ranks.clone();
            sa.sort_unstable();
            sb.sort_unstable();
            if sa == sb {
                CommunicatorRelation::Similar
            } else {
                CommunicatorRelation::Unequal
            }
        }
    }

    /// Duplicate the communicator with a fresh context (`MPI_Comm_dup`).
    /// Collective over all members.
    fn duplicate(&self) -> SimpleCommunicator {
        let data = self.comm_data();
        let ctx = data.derive_context(0xD00Du32);
        SimpleCommunicator::from_parts(ctx, data.rank, data.size, data.world_ranks.clone())
    }

    /// Split the communicator by colour (`MPI_Comm_split`). Collective.
    fn split_by_color(&self, color: Color) -> Option<SimpleCommunicator> {
        self.split_by_color_with_key(color, self.rank())
    }

    /// Split by colour, ordering new ranks by `key` then old rank
    /// (`MPI_Comm_split`). Collective.
    fn split_by_color_with_key(&self, color: Color, key: Key) -> Option<SimpleCommunicator> {
        split_impl(self.comm_data(), color, key)
    }

    /// Split by an explicit subgroup (`MPI_Comm_create` style). Collective;
    /// returns `None` for processes not in `group`.
    fn split_by_subgroup(&self, group: &Group) -> Option<SimpleCommunicator> {
        let data = self.comm_data();
        let me = transport::runtime().rank;
        if !group.members().contains(&me) {
            return None;
        }
        // Derive a context agreed by all members. Using the group's member set
        // as the discriminator keeps disjoint subgroups distinct.
        let disc = group
            .members()
            .iter()
            .fold(0u32, |a, &w| a.wrapping_mul(31).wrapping_add(w as u32));
        let ctx = data.derive_context(disc ^ 0x5EED);
        let world_ranks: Vec<i32> = group.members().to_vec();
        let rank = world_ranks.iter().position(|&w| w == me).unwrap() as Rank;
        let size = world_ranks.len() as Rank;
        Some(SimpleCommunicator::from_parts(ctx, rank, size, world_ranks))
    }

    /// Abort the whole job with an exit code (`MPI_Abort`). Notifies every peer
    /// so the entire job exits rather than leaving ranks blocked.
    fn abort(&self, errorcode: i32) -> ! {
        eprintln!(
            "MPI_Abort called on rank {} (code {})",
            self.rank(),
            errorcode
        );
        transport::abort_job(errorcode);
    }

    /// Set the communicator's name (`MPI_Comm_set_name`).
    fn set_name(&self, name: &str) {
        *self.comm_data().name.lock().unwrap() = Some(name.to_string());
    }

    /// Get the communicator's name (`MPI_Comm_get_name`).
    fn get_name(&self) -> String {
        self.comm_data()
            .name
            .lock()
            .unwrap()
            .clone()
            .unwrap_or_default()
    }

    /// Number of bytes needed to pack `incount` elements of datatype `dt`
    /// (`MPI_Pack_size`).
    fn pack_size(&self, incount: Count, dt: DatatypeRef) -> Count {
        incount * dt.size as Count
    }

    /// Pack a buffer into a freshly allocated byte vector (`MPI_Pack`). Since
    /// this implementation uses contiguous native layouts, packing is a copy.
    fn pack<Buf: Buffer + ?Sized>(&self, inbuf: &Buf) -> Vec<u8>
    where
        Self: Sized,
    {
        inbuf.as_bytes().to_vec()
    }

    /// Pack a buffer into `outbuf` starting at byte offset `position`, returning
    /// the new position (`MPI_Pack`).
    fn pack_into<Buf: Buffer + ?Sized>(
        &self,
        inbuf: &Buf,
        outbuf: &mut [u8],
        position: Count,
    ) -> Count
    where
        Self: Sized,
    {
        let bytes = inbuf.as_bytes();
        let start = position as usize;
        outbuf[start..start + bytes.len()].copy_from_slice(bytes);
        position + bytes.len() as Count
    }

    /// Unpack from `inbuf` starting at byte offset `position` into `outbuf`,
    /// returning the new position (`MPI_Unpack`).
    ///
    /// # Safety
    ///
    /// `outbuf` must be able to receive the unpacked bytes; retained `unsafe`
    /// for signature parity with rsmpi.
    unsafe fn unpack_into<Buf: BufferMut + ?Sized>(
        &self,
        inbuf: &[u8],
        outbuf: &mut Buf,
        position: Count,
    ) -> Count
    where
        Self: Sized,
    {
        let dst = outbuf.as_bytes_mut();
        let start = position as usize;
        let n = dst.len().min(inbuf.len() - start);
        dst[..n].copy_from_slice(&inbuf[start..start + n]);
        position + n as Count
    }

    /// Create a graph topology communicator (`MPI_Graph_create`).
    ///
    /// `index[i]` is the cumulative number of neighbours of nodes `0..=i`, and
    /// `edges` is the concatenation of each node's neighbour list. Ranks
    /// `>= index.len()` are excluded (they receive `None`).
    fn create_graph_communicator(
        &self,
        index: &[Count],
        edges: &[Count],
    ) -> Option<GraphCommunicator> {
        let nnodes = index.len() as Count;
        let color = if self.rank() < nnodes {
            Color::with_value(0)
        } else {
            Color::undefined()
        };
        let sub = self.split_by_color(color)?;
        Some(GraphCommunicator {
            comm: sub,
            index: index.to_vec(),
            edges: edges.to_vec(),
        })
    }

    /// Create a distributed-graph topology where this rank receives from
    /// `sources` and sends to `destinations` (`MPI_Dist_graph_create_adjacent`).
    /// Collective; each rank supplies its own adjacency.
    fn create_dist_graph_adjacent(
        &self,
        sources: &[Rank],
        destinations: &[Rank],
    ) -> DistGraphCommunicator {
        DistGraphCommunicator {
            comm: self.duplicate(),
            sources: sources.to_vec(),
            destinations: destinations.to_vec(),
        }
    }

    /// Collectively split this communicator into an inter-communicator between
    /// two disjoint groups. Ranks passing `true` form one group ("A"); the rest
    /// form the other. Each rank's [`InterCommunicator`] has the *other* group
    /// as its remote group (`MPI_Intercomm_create`-style, collective).
    fn split_intercommunicator(&self, in_group_a: bool) -> InterCommunicator {
        let data = self.comm_data();
        let me = transport::runtime().rank;
        let mut rec = Vec::with_capacity(5);
        rec.push(in_group_a as u8);
        rec.extend_from_slice(&me.to_le_bytes());
        let table = allgather_bytes(data, &rec);

        let mut group_a = Vec::new();
        let mut group_b = Vec::new();
        for r in &table {
            let a = r[0] != 0;
            let w = i32::from_le_bytes(r[1..5].try_into().unwrap());
            if a {
                group_a.push(w);
            } else {
                group_b.push(w);
            }
        }
        let (local, remote) = if in_group_a {
            (group_a, group_b)
        } else {
            (group_b, group_a)
        };
        let ctx = data.derive_context(0x1E7E_1C0D);
        let my_local_rank = local.iter().position(|&w| w == me).unwrap() as Rank;
        InterCommunicator::new(ctx, my_local_rank, local, remote)
    }

    /// Create a Cartesian topology communicator (`MPI_Cart_create`).
    ///
    /// `dims` gives the extent of each dimension, `periods` whether each
    /// dimension wraps around. Processes whose rank is outside the grid
    /// (`rank >= product(dims)`) receive `None`. `reorder` is accepted for
    /// signature parity but ranks are not reordered.
    fn create_cartesian_communicator(
        &self,
        dims: &[Count],
        periods: &[bool],
        _reorder: bool,
    ) -> Option<CartesianCommunicator> {
        assert_eq!(
            dims.len(),
            periods.len(),
            "dims and periods length mismatch"
        );
        let total: Count = dims.iter().product();
        let color = if self.rank() < total {
            Color::with_value(0)
        } else {
            Color::undefined()
        };
        let sub = self.split_by_color(color)?;
        Some(CartesianCommunicator {
            comm: sub,
            dims: dims.to_vec(),
            periods: periods.to_vec(),
        })
    }

    /// If this process was created by [`crate::collective::Root::spawn`],
    /// return the inter-communicator to the parent group (`MPI_Comm_get_parent`).
    /// The local group is this world; the remote group is the spawner.
    fn parent(&self) -> Option<InterCommunicator> {
        let (ictx, paddrs) = transport::spawn_parent()?;
        transport::runtime().register_context_peers(ictx, paddrs.clone());
        let data = self.comm_data();
        Some(InterCommunicator::new_spawned(
            ictx,
            data.rank,
            data.world_ranks.clone(),
            paddrs.len(),
        ))
    }
}

/// A communicator. In rsmpi 0.8.x this single type replaces the earlier
/// `SystemCommunicator` (world) and `UserCommunicator` (derived) types.
pub struct SimpleCommunicator {
    inner: Arc<CommData>,
}

/// Backwards-compatible alias: `MPI_COMM_WORLD`-style communicator.
pub type SystemCommunicator = SimpleCommunicator;
/// Backwards-compatible alias: a derived communicator.
pub type UserCommunicator = SimpleCommunicator;

impl SimpleCommunicator {
    /// The world communicator for the current process.
    pub fn world() -> SimpleCommunicator {
        SimpleCommunicator {
            inner: Arc::new(CommData::world()),
        }
    }

    fn from_parts(
        context: u32,
        rank: Rank,
        size: Rank,
        world_ranks: Vec<i32>,
    ) -> SimpleCommunicator {
        SimpleCommunicator {
            inner: Arc::new(CommData {
                context,
                rank,
                size,
                world_ranks,
                child_seq: AtomicU32::new(0),
                name: Mutex::new(None),
                attributes: Mutex::new(HashMap::new()),
            }),
        }
    }
}

impl Communicator for SimpleCommunicator {
    fn comm_data(&self) -> &CommData {
        &self.inner
    }
}

impl Clone for SimpleCommunicator {
    fn clone(&self) -> Self {
        SimpleCommunicator {
            inner: Arc::clone(&self.inner),
        }
    }
}

/// Collective colour/key exchange implementing `MPI_Comm_split`.
fn split_impl(parent: &CommData, color: Color, key: Key) -> Option<SimpleCommunicator> {
    // Each process contributes (color_defined, color, key, its world rank).
    let mut rec = Vec::with_capacity(16);
    let c = color.value();
    rec.extend_from_slice(&(c.is_some() as i32).to_le_bytes());
    rec.extend_from_slice(&c.unwrap_or(0).to_le_bytes());
    rec.extend_from_slice(&key.to_le_bytes());
    rec.extend_from_slice(&transport::runtime().rank.to_le_bytes());

    let table = allgather_bytes(parent, &rec);

    // Decode all records.
    struct Entry {
        defined: bool,
        color: i32,
        key: i32,
        world: i32,
    }
    let entries: Vec<Entry> = table
        .iter()
        .map(|r| Entry {
            defined: i32::from_le_bytes(r[0..4].try_into().unwrap()) != 0,
            color: i32::from_le_bytes(r[4..8].try_into().unwrap()),
            key: i32::from_le_bytes(r[8..12].try_into().unwrap()),
            world: i32::from_le_bytes(r[12..16].try_into().unwrap()),
        })
        .collect();

    let my_world = transport::runtime().rank;
    let my_color = color.value()?; // undefined -> None

    // Collect co-coloured members, ordered by (key, original parent rank).
    let mut members: Vec<(&Entry, usize)> = entries
        .iter()
        .enumerate()
        .filter(|(_, e)| e.defined && e.color == my_color)
        .map(|(i, e)| (e, i))
        .collect();
    members.sort_by(|a, b| a.0.key.cmp(&b.0.key).then(a.1.cmp(&b.1)));

    let world_ranks: Vec<i32> = members.iter().map(|(e, _)| e.world).collect();
    let rank = world_ranks.iter().position(|&w| w == my_world).unwrap() as Rank;
    let size = world_ranks.len() as Rank;

    // Context is derived from the parent context + colour so that every member
    // of a colour agrees while distinct colours differ.
    let ctx = parent.derive_context(my_color as u32);
    Some(SimpleCommunicator::from_parts(ctx, rank, size, world_ranks))
}

const SPLIT_GATHER_TAG: Tag = 1;
const SPLIT_BCAST_TAG: Tag = 2;

/// A minimal all-gather of equal-length byte records over a communicator,
/// implemented directly on the transport (linear gather to rank 0 followed by
/// a broadcast). Used by control-plane operations such as `split`.
pub(crate) fn allgather_bytes(comm: &CommData, mine: &[u8]) -> Vec<Vec<u8>> {
    let rt = transport::runtime();
    let ctx = comm.coll_context();
    let n = comm.size;
    let me = comm.rank;
    let dt = crate::datatype::ids::U8;

    if me == 0 {
        let mut table: Vec<Vec<u8>> = vec![Vec::new(); n as usize];
        table[0] = mine.to_vec();
        for src in 1..n {
            let (_s, _t, _c, _d, payload) = rt.recv(ctx, src, SPLIT_GATHER_TAG);
            table[src as usize] = payload;
        }
        // Serialize the whole table and broadcast to every other member.
        let mut blob = Vec::new();
        blob.extend_from_slice(&(n as u32).to_le_bytes());
        for rec in &table {
            blob.extend_from_slice(&(rec.len() as u32).to_le_bytes());
            blob.extend_from_slice(rec);
        }
        for dst in 1..n {
            rt.send(
                ctx,
                0,
                comm.world_rank(dst),
                SPLIT_BCAST_TAG,
                blob.len() as u64,
                dt,
                &blob,
            )
            .expect("split broadcast failed");
        }
        table
    } else {
        rt.send(
            ctx,
            me,
            comm.world_rank(0),
            SPLIT_GATHER_TAG,
            mine.len() as u64,
            dt,
            mine,
        )
        .expect("split gather failed");
        let (_s, _t, _c, _d, blob) = rt.recv(ctx, 0, SPLIT_BCAST_TAG);
        decode_table(&blob)
    }
}

fn decode_table(blob: &[u8]) -> Vec<Vec<u8>> {
    let mut pos = 0;
    let n = u32::from_le_bytes(blob[pos..pos + 4].try_into().unwrap()) as usize;
    pos += 4;
    let mut out = Vec::with_capacity(n);
    for _ in 0..n {
        let len = u32::from_le_bytes(blob[pos..pos + 4].try_into().unwrap()) as usize;
        pos += 4;
        out.push(blob[pos..pos + len].to_vec());
        pos += len;
    }
    out
}

/// A communicator carrying a Cartesian grid topology (`MPI_Cart_create`).
/// Coordinates are laid out row-major (the last dimension varies fastest),
/// matching the MPI standard.
#[derive(Clone)]
pub struct CartesianCommunicator {
    comm: SimpleCommunicator,
    dims: Vec<Count>,
    periods: Vec<bool>,
}

impl Communicator for CartesianCommunicator {
    fn comm_data(&self) -> &CommData {
        self.comm.comm_data()
    }
}

impl CartesianCommunicator {
    /// The number of grid dimensions (`MPI_Cartdim_get`).
    pub fn num_dimensions(&self) -> usize {
        self.dims.len()
    }

    /// The extent of each dimension.
    pub fn dimensions(&self) -> &[Count] {
        &self.dims
    }

    /// Whether each dimension is periodic.
    pub fn periods(&self) -> &[bool] {
        &self.periods
    }

    /// The Cartesian coordinates of a given rank (`MPI_Cart_coords`).
    pub fn coordinates(&self, rank: Rank) -> Vec<Count> {
        let mut coords = vec![0; self.dims.len()];
        let mut r = rank;
        for i in (0..self.dims.len()).rev() {
            coords[i] = r % self.dims[i];
            r /= self.dims[i];
        }
        coords
    }

    /// This process's Cartesian coordinates.
    pub fn my_coordinates(&self) -> Vec<Count> {
        self.coordinates(self.rank())
    }

    /// The rank at the given coordinates (`MPI_Cart_rank`). Returns `None` if a
    /// non-periodic coordinate is out of range; periodic coordinates wrap.
    pub fn rank_from_coordinates(&self, coords: &[Count]) -> Option<Rank> {
        let mut rank = 0;
        for ((&dim, &periodic), &coord) in self.dims.iter().zip(&self.periods).zip(coords) {
            let mut c = coord;
            if periodic {
                c = c.rem_euclid(dim);
            } else if c < 0 || c >= dim {
                return None;
            }
            rank = rank * dim + c;
        }
        Some(rank)
    }

    /// Compute the source and destination ranks for a shift along `direction`
    /// by `disp` steps (`MPI_Cart_shift`). Returns `(source, dest)`, either of
    /// which is `None` at a non-periodic boundary.
    pub fn shift(&self, direction: usize, disp: Count) -> (Option<Rank>, Option<Rank>) {
        let coords = self.my_coordinates();
        let mut dest = coords.clone();
        dest[direction] += disp;
        let mut source = coords;
        source[direction] -= disp;
        (
            self.rank_from_coordinates(&source),
            self.rank_from_coordinates(&dest),
        )
    }
}

/// A communicator carrying a general graph topology (`MPI_Graph_create`).
#[derive(Clone)]
pub struct GraphCommunicator {
    comm: SimpleCommunicator,
    index: Vec<Count>,
    edges: Vec<Count>,
}

impl Communicator for GraphCommunicator {
    fn comm_data(&self) -> &CommData {
        self.comm.comm_data()
    }
}

impl GraphCommunicator {
    /// Total number of nodes in the graph.
    pub fn num_nodes(&self) -> Count {
        self.index.len() as Count
    }

    /// Total number of (directed) edges in the graph.
    pub fn num_edges(&self) -> Count {
        self.edges.len() as Count
    }

    /// The number of neighbours of `rank` (`MPI_Graph_neighbors_count`).
    pub fn neighbor_count(&self, rank: Rank) -> Count {
        let (s, e) = self.range(rank);
        (e - s) as Count
    }

    /// The neighbours of `rank` (`MPI_Graph_neighbors`).
    pub fn neighbors(&self, rank: Rank) -> Vec<Rank> {
        let (s, e) = self.range(rank);
        self.edges[s..e].to_vec()
    }

    /// This process's neighbours.
    pub fn my_neighbors(&self) -> Vec<Rank> {
        self.neighbors(self.rank())
    }

    /// Gather each rank's `sendbuf` from all of its neighbours
    /// (`MPI_Neighbor_allgather`). `recvbuf` holds one block per neighbour, in
    /// neighbour order.
    pub fn neighbor_all_gather_into<S, R>(&self, sendbuf: &S, recvbuf: &mut R)
    where
        S: Buffer + ?Sized,
        R: BufferMut + ?Sized,
    {
        const TAG: Tag = 40;
        let rt = transport::runtime();
        let ctx = self.comm.comm_data().coll_context();
        let me = self.comm.comm_data().rank;
        let nbrs = self.my_neighbors();
        let send = sendbuf.as_bytes();
        let dt = sendbuf.as_datatype().id;
        for &nb in &nbrs {
            rt.send(
                ctx,
                me,
                self.comm.comm_data().world_rank(nb),
                TAG,
                sendbuf.count() as u64,
                dt,
                send,
            )
            .expect("neighbor_all_gather send");
        }
        let out = recvbuf.as_bytes_mut();
        let blk = send.len();
        for (k, &nb) in nbrs.iter().enumerate() {
            let (_s, _t, _c, _d, payload) = rt.recv(ctx, nb, TAG);
            out[k * blk..k * blk + payload.len()].copy_from_slice(&payload);
        }
    }

    /// Exchange a distinct block with each neighbour
    /// (`MPI_Neighbor_alltoall`). `sendbuf` and `recvbuf` hold one block per
    /// neighbour, in neighbour order.
    pub fn neighbor_all_to_all_into<S, R>(&self, sendbuf: &S, recvbuf: &mut R)
    where
        S: Buffer + ?Sized,
        R: BufferMut + ?Sized,
    {
        const TAG: Tag = 41;
        let rt = transport::runtime();
        let ctx = self.comm.comm_data().coll_context();
        let me = self.comm.comm_data().rank;
        let nbrs = self.my_neighbors();
        let send = sendbuf.as_bytes();
        let dt = sendbuf.as_datatype().id;
        let esize = sendbuf.as_datatype().size.max(1);
        let blk = if nbrs.is_empty() {
            0
        } else {
            send.len() / nbrs.len()
        };
        for (k, &nb) in nbrs.iter().enumerate() {
            rt.send(
                ctx,
                me,
                self.comm.comm_data().world_rank(nb),
                TAG,
                (blk / esize) as u64,
                dt,
                &send[k * blk..(k + 1) * blk],
            )
            .expect("neighbor_all_to_all send");
        }
        let out = recvbuf.as_bytes_mut();
        for (k, &nb) in nbrs.iter().enumerate() {
            let (_s, _t, _c, _d, payload) = rt.recv(ctx, nb, TAG);
            out[k * blk..k * blk + payload.len()].copy_from_slice(&payload);
        }
    }

    fn range(&self, rank: Rank) -> (usize, usize) {
        let r = rank as usize;
        let start = if r == 0 {
            0
        } else {
            self.index[r - 1] as usize
        };
        let end = self.index[r] as usize;
        (start, end)
    }
}

/// An inter-communicator linking two disjoint groups of processes
/// (`MPI_Intercomm`). Point-to-point operations address the **remote** group
/// by rank; [`InterCommunicator::merge`] flattens both groups into an ordinary
/// intra-communicator.
pub struct InterCommunicator {
    /// A communicator description whose `world_ranks` are the *remote* group,
    /// `rank` is this process's rank in its *local* group, and `size` is the
    /// local group size.
    data: CommData,
    local: Vec<i32>,
    remote: Vec<i32>,
}

impl InterCommunicator {
    fn new(context: u32, local_rank: Rank, local: Vec<i32>, remote: Vec<i32>) -> InterCommunicator {
        let data = CommData {
            context,
            rank: local_rank,
            size: local.len() as Rank,
            world_ranks: remote.clone(),
            child_seq: AtomicU32::new(0),
            name: Mutex::new(None),
            attributes: Mutex::new(HashMap::new()),
        };
        InterCommunicator {
            data,
            local,
            remote,
        }
    }

    /// Build a spawned inter-communicator whose remote group lives in another
    /// world, reached via a registered context-peer table (see
    /// [`crate::window`]-style routing). `world_ranks` is set to the identity
    /// `0..remote_count` so routing indexes the context-peer table directly.
    pub(crate) fn new_spawned(
        context: u32,
        local_rank: Rank,
        local: Vec<i32>,
        remote_count: usize,
    ) -> InterCommunicator {
        let remote: Vec<i32> = (0..remote_count as i32).collect();
        let data = CommData {
            context,
            rank: local_rank,
            size: local.len() as Rank,
            world_ranks: remote.clone(),
            child_seq: AtomicU32::new(0),
            name: Mutex::new(None),
            attributes: Mutex::new(HashMap::new()),
        };
        InterCommunicator {
            data,
            local,
            remote,
        }
    }

    /// The size of the local group (`MPI_Comm_size` on an inter-communicator).
    pub fn local_size(&self) -> Rank {
        self.local.len() as Rank
    }

    /// The size of the remote group (`MPI_Comm_remote_size`).
    pub fn remote_size(&self) -> Rank {
        self.remote.len() as Rank
    }

    /// This process's rank within the local group.
    pub fn rank(&self) -> Rank {
        self.data.rank
    }

    /// The local group.
    pub fn local_group(&self) -> Group {
        Group::from_world_ranks(self.local.clone())
    }

    /// The remote group.
    pub fn remote_group(&self) -> Group {
        Group::from_world_ranks(self.remote.clone())
    }

    /// Merge the two groups into a single intra-communicator
    /// (`MPI_Intercomm_merge`). Ranks are ordered by world rank.
    pub fn merge(&self) -> SimpleCommunicator {
        let mut all = self.local.clone();
        all.extend_from_slice(&self.remote);
        all.sort_unstable();
        all.dedup();
        // Deterministic context agreed by all ranks from the member set.
        let mut ctx = 0x4D_4552u32; // "MER"
        for &w in &all {
            ctx = ctx.wrapping_mul(31).wrapping_add(w as u32);
        }
        ctx &= !COLL_CONTEXT_BIT;
        let me = transport::runtime().rank;
        let rank = all.iter().position(|&w| w == me).unwrap() as Rank;
        let size = all.len() as Rank;
        SimpleCommunicator::from_parts(ctx, rank, size, all)
    }
}

impl Communicator for InterCommunicator {
    fn comm_data(&self) -> &CommData {
        &self.data
    }

    /// On an inter-communicator, `size` is the local group size.
    fn size(&self) -> Rank {
        self.local.len() as Rank
    }
}

/// A distributed-graph topology communicator (`MPI_Dist_graph_create_adjacent`)
/// where each rank declares its own in-neighbours (`sources`) and out-neighbours
/// (`destinations`).
#[derive(Clone)]
pub struct DistGraphCommunicator {
    comm: SimpleCommunicator,
    sources: Vec<Rank>,
    destinations: Vec<Rank>,
}

impl Communicator for DistGraphCommunicator {
    fn comm_data(&self) -> &CommData {
        self.comm.comm_data()
    }
}

impl DistGraphCommunicator {
    /// Number of in-neighbours.
    pub fn in_degree(&self) -> usize {
        self.sources.len()
    }

    /// Number of out-neighbours.
    pub fn out_degree(&self) -> usize {
        self.destinations.len()
    }

    /// The ranks this process receives from.
    pub fn sources(&self) -> &[Rank] {
        &self.sources
    }

    /// The ranks this process sends to.
    pub fn destinations(&self) -> &[Rank] {
        &self.destinations
    }

    /// Send `sendbuf` to every out-neighbour and gather one block from each
    /// in-neighbour, in source order (`MPI_Neighbor_allgather`).
    pub fn neighbor_all_gather_into<S, R>(&self, sendbuf: &S, recvbuf: &mut R)
    where
        S: Buffer + ?Sized,
        R: BufferMut + ?Sized,
    {
        const TAG: Tag = 42;
        let comm = self.comm.comm_data();
        let ctx = comm.coll_context();
        let me = comm.rank;
        let send = sendbuf.as_bytes();
        let dt = sendbuf.as_datatype().id;
        for &d in &self.destinations {
            transport::runtime()
                .send(
                    ctx,
                    me,
                    comm.world_rank(d),
                    TAG,
                    sendbuf.count() as u64,
                    dt,
                    send,
                )
                .expect("dist-graph neighbor_all_gather send");
        }
        let out = recvbuf.as_bytes_mut();
        let blk = send.len();
        for (k, &s) in self.sources.iter().enumerate() {
            let (_s, _t, _c, _d, payload) = transport::runtime().recv(ctx, s, TAG);
            out[k * blk..k * blk + payload.len()].copy_from_slice(&payload);
        }
    }
}
