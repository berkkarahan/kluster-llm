//! Authenticated KLLM production-job transport.
//!
//! This module accepts only a structured [`MpiJobSpec`]. It does not execute a
//! command, parse a hostfile, discover a public bind address, or accept raw
//! environment variables. The legacy launcher remains a local test fixture.

use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::collections::{HashMap, HashSet};
use std::io::{ErrorKind, Read, Write};
use std::net::{IpAddr, SocketAddr, TcpListener, TcpStream};
use std::thread;
use std::time::{Duration, Instant};
use zeroize::{Zeroize, Zeroizing};

type HmacSha256 = Hmac<Sha256>;

/// Provider wire protocol implemented by this fork.
pub const PROTOCOL_VERSION: u32 = 1;
/// Ports admitted for the private MPI data plane.
pub const DATA_PORT_MIN: u16 = 20_000;
/// Ports admitted for the private MPI data plane.
pub const DATA_PORT_MAX: u16 = 20_999;
/// Upper bound for a production collective payload.
pub const DEFAULT_MAX_COLLECTIVE_BYTES: u64 = 256 * 1024 * 1024;

const AUTH_LIMIT: usize = 16 * 1024;
const CONTROL_LIMIT: usize = 64 * 1024;
const CONNECT_RETRY: Duration = Duration::from_millis(50);

/// A fixed rank address and one-use authentication nonce.
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MpiPeerSpec {
    /// Deterministic rank number.
    pub rank: u32,
    /// Fixed private listener address allocated by the KLLM control plane.
    pub address: SocketAddr,
    /// Per-rank random nonce, encoded as 64 lower-case hexadecimal characters.
    pub nonce: String,
}

/// Server-owned launch record delivered over protected stdin or an in-memory C
/// ABI buffer. It contains no executable, argv, hostfile, raw environment, or
/// user-selected port.
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MpiJobSpec {
    /// Opaque 128-bit job identity encoded as 32 lower-case hex characters.
    pub job_id: String,
    /// This process' rank.
    pub rank: u32,
    /// Fixed communicator size.
    pub world_size: u32,
    /// Exact local listener address.
    pub bind_address: SocketAddr,
    /// Full deterministic rank table.
    pub peers: Vec<MpiPeerSpec>,
    /// Per-job 256-bit secret encoded as 64 lower-case hex characters.
    pub job_secret: String,
    /// Provider protocol expected by the accepted plan.
    pub protocol_version: u32,
    /// Provider source revision expected by the accepted plan.
    pub provider_revision: String,
    /// Local fork digest expected by the accepted plan.
    pub provider_patch_digest: String,
    /// Accepted engine revision.
    pub engine_revision: String,
    /// One backend family for the complete communicator.
    pub backend: String,
    /// Backend-independent physical GPU identity for this rank.
    pub physical_device_id: String,
    /// Backend-local handle verified by the engine probe.
    pub backend_device: String,
    /// Approved local model identifier. This is not a filesystem path.
    pub model_id: String,
    /// Exact shard manifest identity shared by planning and execution.
    pub manifest_identity: String,
    /// Collective timeout for this job.
    pub collective_timeout_ms: u64,
    /// Per-operation byte bound.
    #[serde(default = "default_collective_limit")]
    pub max_collective_bytes: u64,
}

fn default_collective_limit() -> u64 {
    DEFAULT_MAX_COLLECTIVE_BYTES
}

impl Drop for MpiJobSpec {
    fn drop(&mut self) {
        self.job_secret.zeroize();
        for peer in &mut self.peers {
            peer.nonce.zeroize();
        }
    }
}

fn is_hex(value: &str, bytes: usize) -> bool {
    value.len() == bytes * 2
        && value
            .bytes()
            .all(|ch| ch.is_ascii_digit() || (b'a'..=b'f').contains(&ch))
}

fn is_private_address(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(ip) => ip.is_private() || ip.is_loopback() || ip.is_link_local(),
        IpAddr::V6(ip) => ip.is_loopback() || ip.is_unique_local() || ip.is_unicast_link_local(),
    }
}

impl MpiJobSpec {
    /// Parse and validate an untrusted structured record before binding or
    /// connecting any socket.
    pub fn from_json(bytes: &[u8]) -> Result<Self, String> {
        if bytes.len() > CONTROL_LIMIT {
            return Err("MPI job spec exceeds the control record limit".into());
        }
        let spec: Self = serde_json::from_slice(bytes)
            .map_err(|error| format!("invalid MPI job spec: {error}"))?;
        spec.validate()?;
        Ok(spec)
    }

    /// Validate identities, private addresses, allocated ports, and exact rank
    /// coverage without performing network I/O.
    pub fn validate(&self) -> Result<(), String> {
        if !is_hex(&self.job_id, 16) {
            return Err("job_id must be 128-bit lower-case hex".into());
        }
        if !is_hex(&self.job_secret, 32) {
            return Err("job_secret must be 256-bit lower-case hex".into());
        }
        if self.protocol_version != PROTOCOL_VERSION {
            return Err("provider protocol mismatch".into());
        }
        if self.provider_revision != env!("KLLM_MPI_UPSTREAM_REVISION")
            || self.provider_patch_digest != env!("KLLM_MPI_PATCH_DIGEST")
        {
            return Err("provider revision or patch digest mismatch".into());
        }
        if self.world_size == 0 || self.world_size > 128 || self.rank >= self.world_size {
            return Err("rank/world identity is out of range".into());
        }
        if self.peers.len() != self.world_size as usize {
            return Err("peer table does not cover the complete communicator".into());
        }
        if self.collective_timeout_ms == 0 || self.collective_timeout_ms > 300_000 {
            return Err("collective timeout is outside the allowed range".into());
        }
        if self.max_collective_bytes == 0
            || self.max_collective_bytes > DEFAULT_MAX_COLLECTIVE_BYTES
        {
            return Err("collective byte bound is outside the allowed range".into());
        }
        for value in [
            &self.engine_revision,
            &self.backend,
            &self.physical_device_id,
            &self.backend_device,
            &self.model_id,
            &self.manifest_identity,
        ] {
            if value.is_empty() || value.len() > 512 || value.chars().any(char::is_control) {
                return Err("MPI job identity fields must be nonempty bounded text".into());
            }
        }
        if !matches!(self.backend.as_str(), "vulkan" | "metal" | "cuda" | "rocm") {
            return Err("MPI backend is not supported".into());
        }
        let mut ranks = HashSet::new();
        let mut addresses = HashSet::new();
        let mut nonces = HashSet::new();
        for peer in &self.peers {
            if peer.rank >= self.world_size || !ranks.insert(peer.rank) {
                return Err("peer table has an invalid or duplicate rank".into());
            }
            if !is_private_address(peer.address.ip()) {
                return Err("MPI data plane address is not private".into());
            }
            if !(DATA_PORT_MIN..=DATA_PORT_MAX).contains(&peer.address.port()) {
                return Err("MPI data plane port is outside the allocated range".into());
            }
            if !addresses.insert(peer.address) {
                return Err("MPI data plane address is duplicated".into());
            }
            if !is_hex(&peer.nonce, 32) || !nonces.insert(peer.nonce.as_str()) {
                return Err("peer nonce is invalid or replayed in the job spec".into());
            }
        }
        if ranks != (0..self.world_size).collect() {
            return Err("peer rank table is not contiguous".into());
        }
        let own = &self.peers[self.rank as usize];
        if own.rank != self.rank || own.address != self.bind_address {
            return Err("local rank and fixed bind allocation disagree".into());
        }
        Ok(())
    }
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct AuthRecord {
    job_id: String,
    protocol_version: u32,
    from_rank: u32,
    to_rank: u32,
    nonce: String,
    address: SocketAddr,
    challenge: String,
    tag: String,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct AuthAck {
    job_id: String,
    protocol_version: u32,
    from_rank: u32,
    to_rank: u32,
    challenge: String,
    tag: String,
}

fn decode_hex(value: &str) -> Result<Vec<u8>, String> {
    if value.len() % 2 != 0 {
        return Err("hex value has odd length".into());
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let text = std::str::from_utf8(pair).map_err(|_| "invalid hex")?;
            u8::from_str_radix(text, 16).map_err(|_| "invalid hex")
        })
        .collect::<Result<Vec<_>, _>>()
        .map_err(str::to_string)
}

fn encode_hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn auth_message(record: &AuthRecord) -> String {
    format!(
        "kllm-mpi-auth-v{}\n{}\n{}\n{}\n{}\n{}\n{}",
        record.protocol_version,
        record.job_id,
        record.from_rank,
        record.to_rank,
        record.nonce,
        record.address,
        record.challenge,
    )
}

fn sign_record(
    spec: &MpiJobSpec,
    from_rank: u32,
    to_rank: u32,
    challenge: String,
) -> Result<AuthRecord, String> {
    let peer = &spec.peers[from_rank as usize];
    let mut record = AuthRecord {
        job_id: spec.job_id.clone(),
        protocol_version: spec.protocol_version,
        from_rank,
        to_rank,
        nonce: peer.nonce.clone(),
        address: peer.address,
        challenge,
        tag: String::new(),
    };
    let key = Zeroizing::new(decode_hex(&spec.job_secret)?);
    let mut mac = HmacSha256::new_from_slice(&key).map_err(|_| "invalid HMAC key")?;
    mac.update(auth_message(&record).as_bytes());
    record.tag = encode_hex(&mac.finalize().into_bytes());
    Ok(record)
}

fn verify_record(
    spec: &MpiJobSpec,
    record: &AuthRecord,
    expected_from: u32,
    expected_to: u32,
    expects_challenge: bool,
) -> Result<(), String> {
    let peer = spec
        .peers
        .get(expected_from as usize)
        .ok_or("authentication rank is out of range")?;
    if record.job_id != spec.job_id
        || record.protocol_version != spec.protocol_version
        || record.from_rank != expected_from
        || record.to_rank != expected_to
        || record.nonce != peer.nonce
        || record.address != peer.address
        || (expects_challenge && !is_hex(&record.challenge, 32))
        || (!expects_challenge && !record.challenge.is_empty())
    {
        return Err("MPI peer identity or nonce mismatch".into());
    }
    let key = Zeroizing::new(decode_hex(&spec.job_secret)?);
    let tag = decode_hex(&record.tag).map_err(|_| "bad authentication tag")?;
    let mut mac = HmacSha256::new_from_slice(&key).map_err(|_| "invalid HMAC key")?;
    mac.update(auth_message(record).as_bytes());
    mac.verify_slice(&tag)
        .map_err(|_| "bad authentication tag".into())
}

fn ack_message(ack: &AuthAck) -> String {
    format!(
        "kllm-mpi-auth-ack-v{}\n{}\n{}\n{}\n{}",
        ack.protocol_version, ack.job_id, ack.from_rank, ack.to_rank, ack.challenge,
    )
}

fn sign_ack(
    spec: &MpiJobSpec,
    from_rank: u32,
    to_rank: u32,
    challenge: String,
) -> Result<AuthAck, String> {
    let mut ack = AuthAck {
        job_id: spec.job_id.clone(),
        protocol_version: spec.protocol_version,
        from_rank,
        to_rank,
        challenge,
        tag: String::new(),
    };
    let key = Zeroizing::new(decode_hex(&spec.job_secret)?);
    let mut mac = HmacSha256::new_from_slice(&key).map_err(|_| "invalid HMAC key")?;
    mac.update(ack_message(&ack).as_bytes());
    ack.tag = encode_hex(&mac.finalize().into_bytes());
    Ok(ack)
}

fn verify_ack(
    spec: &MpiJobSpec,
    ack: &AuthAck,
    expected_from: u32,
    expected_to: u32,
    challenge: &str,
) -> Result<(), String> {
    if ack.job_id != spec.job_id
        || ack.protocol_version != spec.protocol_version
        || ack.from_rank != expected_from
        || ack.to_rank != expected_to
        || ack.challenge != challenge
        || !is_hex(&ack.challenge, 32)
    {
        return Err("MPI authentication acknowledgement mismatch".into());
    }
    let key = Zeroizing::new(decode_hex(&spec.job_secret)?);
    let tag = decode_hex(&ack.tag).map_err(|_| "bad authentication acknowledgement tag")?;
    let mut mac = HmacSha256::new_from_slice(&key).map_err(|_| "invalid HMAC key")?;
    mac.update(ack_message(ack).as_bytes());
    mac.verify_slice(&tag)
        .map_err(|_| "bad authentication acknowledgement tag".into())
}

fn fresh_challenge() -> Result<String, String> {
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes).map_err(|error| format!("generate peer challenge: {error}"))?;
    Ok(encode_hex(&bytes))
}

fn write_json_record<T: Serialize>(stream: &mut TcpStream, value: &T) -> Result<(), String> {
    let bytes = serde_json::to_vec(value).map_err(|error| error.to_string())?;
    if bytes.len() > AUTH_LIMIT {
        return Err("authentication record exceeds its bound".into());
    }
    stream
        .write_all(&(bytes.len() as u32).to_le_bytes())
        .and_then(|_| stream.write_all(&bytes))
        .and_then(|_| stream.flush())
        .map_err(|error| format!("write authentication record: {error}"))
}

fn read_json_record<T: for<'de> Deserialize<'de>>(stream: &mut TcpStream) -> Result<T, String> {
    let mut len = [0_u8; 4];
    stream
        .read_exact(&mut len)
        .map_err(|error| format!("read authentication header: {error}"))?;
    let len = u32::from_le_bytes(len) as usize;
    if len == 0 || len > AUTH_LIMIT {
        return Err("authentication record length is invalid".into());
    }
    let mut bytes = vec![0_u8; len];
    stream
        .read_exact(&mut bytes)
        .map_err(|error| format!("read authentication record: {error}"))?;
    serde_json::from_slice(&bytes).map_err(|error| format!("parse authentication record: {error}"))
}

/// Metadata agreed by every rank before a collective payload is transferred.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CollectiveDescriptor {
    /// Logical operation name.
    pub operation: String,
    /// Decode phase such as `warmup`, `prefill`, or `decode`.
    pub phase: String,
    /// Transformer layer or `-1` for job-wide control.
    pub layer: i32,
    /// Canonical dtype name.
    pub dtype: String,
    /// Logical tensor dimensions.
    pub shape: Vec<u64>,
    /// Exact shard manifest identity.
    pub manifest_identity: String,
}

#[derive(Debug, Deserialize, Serialize)]
struct WireMeta {
    sequence: u64,
    stage: String,
    descriptor: CollectiveDescriptor,
    counts: Vec<u64>,
    error: Option<String>,
}

fn write_frame(
    stream: &mut TcpStream,
    meta: &WireMeta,
    payload: &[u8],
    max_payload: u64,
) -> Result<(), String> {
    if payload.len() as u64 > max_payload {
        return Err("collective payload exceeds the job bound".into());
    }
    let meta_bytes = serde_json::to_vec(meta).map_err(|error| error.to_string())?;
    if meta_bytes.len() > CONTROL_LIMIT {
        return Err("collective metadata exceeds the control bound".into());
    }
    stream
        .write_all(&(meta_bytes.len() as u32).to_le_bytes())
        .and_then(|_| stream.write_all(&(payload.len() as u64).to_le_bytes()))
        .and_then(|_| stream.write_all(&meta_bytes))
        .and_then(|_| stream.write_all(payload))
        .and_then(|_| stream.flush())
        .map_err(|error| format!("write collective frame: {error}"))
}

fn read_frame(stream: &mut TcpStream, max_payload: u64) -> Result<(WireMeta, Vec<u8>), String> {
    let mut header = [0_u8; 12];
    stream
        .read_exact(&mut header)
        .map_err(|error| format!("read collective header: {error}"))?;
    let meta_len = u32::from_le_bytes(header[..4].try_into().unwrap()) as usize;
    let payload_len = u64::from_le_bytes(header[4..].try_into().unwrap());
    if meta_len == 0 || meta_len > CONTROL_LIMIT || payload_len > max_payload {
        return Err("collective frame length is outside its bound".into());
    }
    let payload_len = usize::try_from(payload_len).map_err(|_| "payload does not fit target")?;
    let mut meta_bytes = vec![0_u8; meta_len];
    let mut payload = vec![0_u8; payload_len];
    stream
        .read_exact(&mut meta_bytes)
        .and_then(|_| stream.read_exact(&mut payload))
        .map_err(|error| format!("read collective frame: {error}"))?;
    let meta = serde_json::from_slice(&meta_bytes)
        .map_err(|error| format!("parse collective metadata: {error}"))?;
    Ok((meta, payload))
}

/// Authenticated fixed communicator used by the C ABI and rank executable.
pub struct ProductionWorld {
    spec: MpiJobSpec,
    streams: HashMap<u32, TcpStream>,
    sequence: u64,
}

impl ProductionWorld {
    /// Bind the fixed private address and mutually authenticate every peer.
    pub fn connect(spec: MpiJobSpec) -> Result<Self, String> {
        spec.validate()?;
        let timeout = Duration::from_millis(spec.collective_timeout_ms);
        let bootstrap_deadline = Instant::now() + timeout;
        let listener = TcpListener::bind(spec.bind_address)
            .map_err(|error| format!("bind allocated MPI address: {error}"))?;
        listener
            .set_nonblocking(true)
            .map_err(|error| format!("configure MPI listener: {error}"))?;
        let mut streams = HashMap::new();

        for peer_rank in (spec.rank + 1)..spec.world_size {
            let peer = &spec.peers[peer_rank as usize];
            let mut stream = loop {
                let remaining = remaining_until(bootstrap_deadline)?;
                match TcpStream::connect_timeout(&peer.address, remaining.min(CONNECT_RETRY)) {
                    Ok(stream) => break stream,
                    Err(error) if Instant::now() < bootstrap_deadline => {
                        let _ = error;
                        thread::sleep(remaining.min(CONNECT_RETRY));
                    }
                    Err(error) => return Err(format!("connect rank {peer_rank}: {error}")),
                }
            };
            configure_stream(&stream, remaining_until(bootstrap_deadline)?)?;
            let hello = sign_record(&spec, spec.rank, peer_rank, String::new())?;
            write_json_record(&mut stream, &hello)?;
            let reply: AuthRecord = read_json_record(&mut stream)?;
            verify_record(&spec, &reply, peer_rank, spec.rank, true)?;
            let ack = sign_ack(&spec, spec.rank, peer_rank, reply.challenge.clone())?;
            write_json_record(&mut stream, &ack)?;
            streams.insert(peer_rank, stream);
        }

        let mut seen = HashSet::new();
        while streams.len() < (spec.world_size - 1) as usize {
            let (mut stream, remote) = match listener.accept() {
                Ok(connection) => connection,
                Err(error) if error.kind() == ErrorKind::WouldBlock => {
                    let remaining = remaining_until(bootstrap_deadline)?;
                    thread::sleep(remaining.min(CONNECT_RETRY));
                    continue;
                }
                Err(error) => return Err(format!("accept MPI peer: {error}")),
            };
            configure_stream(&stream, remaining_until(bootstrap_deadline)?)?;
            let hello: AuthRecord = read_json_record(&mut stream)?;
            if hello.from_rank >= spec.rank || !seen.insert(hello.from_rank) {
                return Err("unexpected or replayed MPI peer connection".into());
            }
            let expected_ip = spec.peers[hello.from_rank as usize].address.ip();
            if remote.ip() != expected_ip {
                return Err("unexpected MPI peer source address".into());
            }
            verify_record(&spec, &hello, hello.from_rank, spec.rank, false)?;
            let challenge = fresh_challenge()?;
            let reply = sign_record(&spec, spec.rank, hello.from_rank, challenge.clone())?;
            write_json_record(&mut stream, &reply)?;
            let ack: AuthAck = read_json_record(&mut stream)?;
            verify_ack(&spec, &ack, hello.from_rank, spec.rank, &challenge)?;
            streams.insert(hello.from_rank, stream);
        }
        for stream in streams.values() {
            configure_stream(stream, timeout)?;
        }
        Ok(Self {
            spec,
            streams,
            sequence: 0,
        })
    }

    /// Current rank.
    pub fn rank(&self) -> u32 {
        self.spec.rank
    }

    /// Fixed communicator size.
    pub fn world_size(&self) -> u32 {
        self.spec.world_size
    }

    /// Exact shard manifest identity.
    pub fn manifest_identity(&self) -> &str {
        &self.spec.manifest_identity
    }

    /// Number of successfully completed collective sequences.
    pub fn sequence(&self) -> u64 {
        self.sequence
    }

    fn meta(&self, stage: &str, descriptor: &CollectiveDescriptor) -> WireMeta {
        WireMeta {
            sequence: self.sequence,
            stage: stage.to_string(),
            descriptor: descriptor.clone(),
            counts: Vec::new(),
            error: None,
        }
    }

    fn agree(&mut self, descriptor: &CollectiveDescriptor) -> Result<(), String> {
        if descriptor.manifest_identity != self.spec.manifest_identity {
            return Err("collective manifest identity differs from the accepted job".into());
        }
        let max = self.spec.max_collective_bytes;
        if self.spec.rank == 0 {
            let mut error = None;
            for rank in 1..self.spec.world_size {
                let stream = self
                    .streams
                    .get_mut(&rank)
                    .ok_or("missing MPI peer stream")?;
                let (meta, payload) = read_frame(stream, max)?;
                if !payload.is_empty()
                    || meta.sequence != self.sequence
                    || meta.stage != "agree"
                    || meta.descriptor != *descriptor
                {
                    error = Some(format!(
                        "rank {rank} disagreed on collective sequence or descriptor"
                    ));
                }
            }
            for rank in 1..self.spec.world_size {
                let mut meta = self.meta("agreement", descriptor);
                meta.error = error.clone();
                write_frame(
                    self.streams
                        .get_mut(&rank)
                        .ok_or("missing MPI peer stream")?,
                    &meta,
                    &[],
                    max,
                )?;
            }
            if let Some(error) = error {
                return Err(error);
            }
        } else {
            let mut meta = self.meta("agree", descriptor);
            let stream = self.streams.get_mut(&0).ok_or("missing rank-zero stream")?;
            write_frame(stream, &meta, &[], max)?;
            let (reply, payload) = read_frame(stream, max)?;
            if !payload.is_empty()
                || reply.sequence != self.sequence
                || reply.stage != "agreement"
                || reply.descriptor != *descriptor
            {
                return Err("rank zero returned an invalid collective agreement".into());
            }
            if let Some(error) = reply.error {
                return Err(error);
            }
            meta.error = None;
        }
        Ok(())
    }

    /// Synchronize every rank after descriptor agreement.
    pub fn barrier(&mut self, descriptor: &CollectiveDescriptor) -> Result<(), String> {
        self.agree(descriptor)?;
        let max = self.spec.max_collective_bytes;
        if self.spec.rank == 0 {
            for rank in 1..self.spec.world_size {
                let (meta, payload) = read_frame(
                    self.streams
                        .get_mut(&rank)
                        .ok_or("missing MPI peer stream")?,
                    max,
                )?;
                if meta.sequence != self.sequence || meta.stage != "barrier" || !payload.is_empty()
                {
                    return Err("invalid barrier arrival".into());
                }
            }
            for rank in 1..self.spec.world_size {
                let meta = self.meta("barrier-release", descriptor);
                write_frame(self.streams.get_mut(&rank).unwrap(), &meta, &[], max)?;
            }
        } else {
            let meta = self.meta("barrier", descriptor);
            let stream = self.streams.get_mut(&0).ok_or("missing rank-zero stream")?;
            write_frame(stream, &meta, &[], max)?;
            let (reply, payload) = read_frame(stream, max)?;
            if reply.sequence != self.sequence
                || reply.stage != "barrier-release"
                || !payload.is_empty()
            {
                return Err("invalid barrier release".into());
            }
        }
        self.sequence += 1;
        Ok(())
    }

    /// Broadcast a bounded byte activation from `root`.
    pub fn broadcast(
        &mut self,
        root: u32,
        descriptor: &CollectiveDescriptor,
        buffer: &mut Vec<u8>,
    ) -> Result<(), String> {
        if root >= self.spec.world_size {
            return Err("broadcast root is out of range".into());
        }
        let mut agreed_descriptor = descriptor.clone();
        agreed_descriptor.operation = format!("{}:root={root}", descriptor.operation);
        self.agree(&agreed_descriptor)?;
        let max = self.spec.max_collective_bytes;
        if self.spec.rank == root {
            for rank in 0..self.spec.world_size {
                if rank != root {
                    let meta = self.meta("broadcast", descriptor);
                    write_frame(self.streams.get_mut(&rank).unwrap(), &meta, buffer, max)?;
                }
            }
        } else {
            let (meta, payload) = read_frame(self.streams.get_mut(&root).unwrap(), max)?;
            if meta.sequence != self.sequence || meta.stage != "broadcast" {
                return Err("invalid broadcast frame".into());
            }
            *buffer = payload;
        }
        self.sequence += 1;
        Ok(())
    }

    /// Sum equal-shaped f32 activation partials onto every rank.
    pub fn allreduce_f32(
        &mut self,
        descriptor: &CollectiveDescriptor,
        values: &mut [f32],
    ) -> Result<(), String> {
        let byte_len = values.len().checked_mul(4).ok_or("f32 payload overflow")?;
        if byte_len as u64 > self.spec.max_collective_bytes {
            return Err("allreduce payload exceeds the job bound".into());
        }
        self.agree(descriptor)?;
        let max = self.spec.max_collective_bytes;
        let own = f32_as_bytes(values);
        if self.spec.rank == 0 {
            let mut sum = values.to_vec();
            for rank in 1..self.spec.world_size {
                let (meta, payload) = read_frame(self.streams.get_mut(&rank).unwrap(), max)?;
                if meta.sequence != self.sequence
                    || meta.stage != "allreduce"
                    || payload.len() != byte_len
                {
                    return Err("allreduce rank supplied a different shape".into());
                }
                for (dst, chunk) in sum.iter_mut().zip(payload.chunks_exact(4)) {
                    *dst += f32::from_le_bytes(chunk.try_into().unwrap());
                }
            }
            values.copy_from_slice(&sum);
            let reduced = f32_as_bytes(&sum);
            for rank in 1..self.spec.world_size {
                let meta = self.meta("allreduce-result", descriptor);
                write_frame(self.streams.get_mut(&rank).unwrap(), &meta, &reduced, max)?;
            }
        } else {
            let meta = self.meta("allreduce", descriptor);
            let stream = self.streams.get_mut(&0).unwrap();
            write_frame(stream, &meta, &own, max)?;
            let (reply, payload) = read_frame(stream, max)?;
            if reply.sequence != self.sequence
                || reply.stage != "allreduce-result"
                || payload.len() != byte_len
            {
                return Err("invalid allreduce result".into());
            }
            for (dst, chunk) in values.iter_mut().zip(payload.chunks_exact(4)) {
                *dst = f32::from_le_bytes(chunk.try_into().unwrap());
            }
        }
        self.sequence += 1;
        Ok(())
    }

    /// Concatenate unequal byte slices in deterministic rank order.
    pub fn allgatherv(
        &mut self,
        descriptor: &CollectiveDescriptor,
        local: &[u8],
    ) -> Result<(Vec<u64>, Vec<u8>), String> {
        if local.len() as u64 > self.spec.max_collective_bytes {
            return Err("allgatherv local payload exceeds the job bound".into());
        }
        self.agree(descriptor)?;
        let max = self.spec.max_collective_bytes;
        if self.spec.rank == 0 {
            let mut parts = vec![local.to_vec()];
            for rank in 1..self.spec.world_size {
                let (meta, payload) = read_frame(self.streams.get_mut(&rank).unwrap(), max)?;
                if meta.sequence != self.sequence || meta.stage != "allgatherv" {
                    return Err("invalid allgatherv contribution".into());
                }
                parts.push(payload);
            }
            let counts: Vec<u64> = parts.iter().map(|part| part.len() as u64).collect();
            let total: u64 = counts.iter().sum();
            if total > max {
                return Err("allgatherv aggregate exceeds the job bound".into());
            }
            let data: Vec<u8> = parts.into_iter().flatten().collect();
            for rank in 1..self.spec.world_size {
                let mut meta = self.meta("allgatherv-result", descriptor);
                meta.counts = counts.clone();
                write_frame(self.streams.get_mut(&rank).unwrap(), &meta, &data, max)?;
            }
            self.sequence += 1;
            Ok((counts, data))
        } else {
            let mut meta = self.meta("allgatherv", descriptor);
            meta.counts = vec![local.len() as u64];
            let stream = self.streams.get_mut(&0).unwrap();
            write_frame(stream, &meta, local, max)?;
            let (reply, data) = read_frame(stream, max)?;
            if reply.sequence != self.sequence || reply.stage != "allgatherv-result" {
                return Err("invalid allgatherv result".into());
            }
            if reply.counts.len() != self.spec.world_size as usize
                || reply.counts.iter().sum::<u64>() != data.len() as u64
            {
                return Err("allgatherv counts disagree with the result".into());
            }
            self.sequence += 1;
            Ok((reply.counts, data))
        }
    }
}

fn remaining_until(deadline: Instant) -> Result<Duration, String> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|remaining| !remaining.is_zero())
        .ok_or_else(|| "MPI peer bootstrap timed out".into())
}

fn configure_stream(stream: &TcpStream, timeout: Duration) -> Result<(), String> {
    stream
        .set_nonblocking(false)
        .and_then(|_| stream.set_nodelay(true))
        .and_then(|_| stream.set_read_timeout(Some(timeout)))
        .and_then(|_| stream.set_write_timeout(Some(timeout)))
        .map_err(|error| format!("configure MPI peer stream: {error}"))
}

fn f32_as_bytes(values: &[f32]) -> Vec<u8> {
    let mut bytes = Vec::with_capacity(values.len() * 4);
    for value in values {
        bytes.extend_from_slice(&value.to_le_bytes());
    }
    bytes
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::sync::{mpsc, Mutex, OnceLock};

    fn network_test_lock() -> &'static Mutex<()> {
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| Mutex::new(()))
    }

    fn available_addresses(count: u16) -> Vec<SocketAddr> {
        for base in DATA_PORT_MIN..=(DATA_PORT_MAX - count + 1) {
            let mut reservations = Vec::new();
            let mut addresses = Vec::new();
            for port in base..(base + count) {
                let address: SocketAddr = format!("127.0.0.1:{port}").parse().unwrap();
                match TcpListener::bind(address) {
                    Ok(listener) => {
                        reservations.push(listener);
                        addresses.push(address);
                    }
                    Err(_) => break,
                }
            }
            if reservations.len() == count as usize {
                drop(reservations);
                return addresses;
            }
        }
        panic!("no contiguous production-test ports are available");
    }

    fn ranked_spec(rank: u32, addresses: &[SocketAddr]) -> MpiJobSpec {
        let mut value = spec();
        value.rank = rank;
        value.world_size = addresses.len() as u32;
        value.bind_address = addresses[rank as usize];
        value.peers = addresses
            .iter()
            .enumerate()
            .map(|(rank, address)| MpiPeerSpec {
                rank: rank as u32,
                address: *address,
                nonce: format!("{:064x}", rank + 10),
            })
            .collect();
        value.collective_timeout_ms = 5_000;
        value.max_collective_bytes = 4096;
        value
    }

    fn collective(operation: &str, phase: &str, shape: Vec<u64>) -> CollectiveDescriptor {
        CollectiveDescriptor {
            operation: operation.into(),
            phase: phase.into(),
            layer: 3,
            dtype: if operation == "allreduce" {
                "f32"
            } else {
                "bytes"
            }
            .into(),
            shape,
            manifest_identity: "sha256:manifest".into(),
        }
    }

    fn spec() -> MpiJobSpec {
        let revision = env!("KLLM_MPI_UPSTREAM_REVISION").to_string();
        let digest = env!("KLLM_MPI_PATCH_DIGEST").to_string();
        MpiJobSpec {
            job_id: "01".repeat(16),
            rank: 0,
            world_size: 2,
            bind_address: "127.0.0.1:20000".parse().unwrap(),
            peers: vec![
                MpiPeerSpec {
                    rank: 0,
                    address: "127.0.0.1:20000".parse().unwrap(),
                    nonce: "02".repeat(32),
                },
                MpiPeerSpec {
                    rank: 1,
                    address: "127.0.0.1:20001".parse().unwrap(),
                    nonce: "03".repeat(32),
                },
            ],
            job_secret: "04".repeat(32),
            protocol_version: 1,
            provider_revision: revision,
            provider_patch_digest: digest,
            engine_revision: "b11026-test".into(),
            backend: "vulkan".into(),
            physical_device_id: "gpu-0".into(),
            backend_device: "Vulkan0".into(),
            model_id: "toy.gguf".into(),
            manifest_identity: "sha256:manifest".into(),
            collective_timeout_ms: 2_000,
            max_collective_bytes: 1024,
        }
    }

    #[test]
    fn structured_spec_rejects_public_and_unallocated_addresses() {
        let mut value = spec();
        value.peers[1].address = "8.8.8.8:20001".parse().unwrap();
        assert!(value.validate().unwrap_err().contains("not private"));
        value.peers[1].address = "127.0.0.1:19999".parse().unwrap();
        assert!(value.validate().unwrap_err().contains("allocated range"));
    }

    #[test]
    fn authenticated_record_rejects_identity_tag_and_replay_nonce() {
        let mut value = spec();
        let record = sign_record(&value, 0, 1, String::new()).unwrap();
        verify_record(&value, &record, 0, 1, false).unwrap();
        assert!(verify_record(&value, &record, 0, 0, false).is_err());
        let ack = sign_ack(&value, 0, 1, "05".repeat(32)).unwrap();
        assert!(verify_ack(&value, &ack, 0, 1, &"06".repeat(32)).is_err());
        value.peers[1].nonce = value.peers[0].nonce.clone();
        assert!(value.validate().unwrap_err().contains("replayed"));
    }

    #[test]
    fn job_spec_rejects_raw_launch_fields() {
        let value = spec();
        let mut json = serde_json::to_value(&value).unwrap();
        json["argv"] = serde_json::json!(["cmd.exe", "/c", "whoami"]);
        let bytes = serde_json::to_vec(&json).unwrap();
        assert!(MpiJobSpec::from_json(&bytes)
            .unwrap_err()
            .contains("unknown field"));
    }

    #[test]
    fn authenticated_three_rank_world_runs_bounded_collectives() {
        let _guard = network_test_lock().lock().unwrap();
        let addresses = available_addresses(3);
        let handles: Vec<_> = (0..3_u32)
            .map(|rank| {
                let spec = ranked_spec(rank, &addresses);
                thread::spawn(move || {
                    let mut world = ProductionWorld::connect(spec).unwrap();
                    world
                        .barrier(&collective("barrier", "warmup", Vec::new()))
                        .unwrap();
                    let mut broadcast = if rank == 1 {
                        b"rank-one-root".to_vec()
                    } else {
                        Vec::new()
                    };
                    world
                        .broadcast(
                            1,
                            &collective("broadcast", "prefill", vec![13]),
                            &mut broadcast,
                        )
                        .unwrap();
                    assert_eq!(broadcast, b"rank-one-root");
                    let mut values = [rank as f32 + 1.0, 10.0];
                    world
                        .allreduce_f32(&collective("allreduce", "decode", vec![1, 2]), &mut values)
                        .unwrap();
                    assert_eq!(values, [6.0, 30.0]);
                    let local = vec![rank as u8; rank as usize + 1];
                    let (counts, data) = world
                        .allgatherv(&collective("allgatherv", "decode", vec![6]), &local)
                        .unwrap();
                    assert_eq!(counts, vec![1, 2, 3]);
                    assert_eq!(data, vec![0, 1, 1, 2, 2, 2]);
                })
            })
            .collect();
        for handle in handles {
            handle.join().unwrap();
        }
    }

    #[test]
    fn rank_loss_fails_the_next_collective_within_the_job_timeout() {
        let _guard = network_test_lock().lock().unwrap();
        let addresses = available_addresses(2);
        let (connected_tx, connected_rx) = mpsc::channel();
        let rank_one = {
            let spec = ranked_spec(1, &addresses);
            thread::spawn(move || {
                let _world = ProductionWorld::connect(spec).unwrap();
                connected_tx.send(()).unwrap();
            })
        };
        let spec = ranked_spec(0, &addresses);
        let mut rank_zero = ProductionWorld::connect(spec).unwrap();
        connected_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        rank_one.join().unwrap();
        let started = Instant::now();
        let error = rank_zero
            .barrier(&collective("barrier", "rank_loss", Vec::new()))
            .unwrap_err();
        assert!(error.contains("read collective"));
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn broadcast_root_mismatch_is_rejected_during_descriptor_agreement() {
        let _guard = network_test_lock().lock().unwrap();
        let addresses = available_addresses(2);
        let (result_tx, result_rx) = mpsc::channel();
        let rank_one = {
            let spec = ranked_spec(1, &addresses);
            thread::spawn(move || {
                let mut world = ProductionWorld::connect(spec).unwrap();
                let result =
                    world.broadcast(1, &collective("broadcast", "decode", vec![1]), &mut vec![1]);
                result_tx.send(result).unwrap();
            })
        };
        let mut rank_zero = ProductionWorld::connect(ranked_spec(0, &addresses)).unwrap();
        let rank_zero_result =
            rank_zero.broadcast(0, &collective("broadcast", "decode", vec![1]), &mut vec![0]);
        assert!(rank_zero_result.is_err());
        assert!(result_rx
            .recv_timeout(Duration::from_secs(2))
            .unwrap()
            .is_err());
        rank_one.join().unwrap();
    }

    #[test]
    fn missing_lower_rank_fails_bootstrap_within_the_job_timeout() {
        let _guard = network_test_lock().lock().unwrap();
        let addresses = available_addresses(2);
        let mut spec = ranked_spec(1, &addresses);
        spec.collective_timeout_ms = 100;
        let started = Instant::now();
        let error = match ProductionWorld::connect(spec) {
            Ok(_) => panic!("missing rank unexpectedly completed bootstrap"),
            Err(error) => error,
        };
        assert!(error.contains("bootstrap timed out"));
        assert!(started.elapsed() < Duration::from_secs(1));
    }
}
