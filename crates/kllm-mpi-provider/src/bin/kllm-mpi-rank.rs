//! Structured production-rank entrypoint owned by the KLLM control plane.
//!
//! One bounded [`MpiJobSpec`] JSON record is read from protected stdin. After
//! peer authentication and a communicator warmup, only a small fixed command
//! grammar remains available. There is no command, executable, hostfile,
//! environment, path, address, or port field in that grammar.

use mpi::production::{CollectiveDescriptor, MpiJobSpec, ProductionWorld};
use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::env;
use std::io::{self, BufRead, Write};
use std::process::ExitCode;

const CONTROL_LINE_LIMIT: usize = 64 * 1024;

#[derive(Debug, Deserialize)]
#[serde(tag = "command", rename_all = "snake_case", deny_unknown_fields)]
enum RankCommand {
    Health {},
    Barrier {
        phase: String,
        #[serde(default = "control_layer")]
        layer: i32,
    },
    Collectives {
        phase: String,
    },
    Stop {},
}

fn control_layer() -> i32 {
    -1
}

fn read_bounded_line(reader: &mut impl BufRead) -> Result<Option<Vec<u8>>, String> {
    let mut line = Vec::new();
    loop {
        let available = reader
            .fill_buf()
            .map_err(|error| format!("read control record: {error}"))?;
        if available.is_empty() {
            return if line.is_empty() {
                Ok(None)
            } else {
                Err("control record ended without a newline".into())
            };
        }
        let take = available
            .iter()
            .position(|byte| *byte == b'\n')
            .map_or(available.len(), |index| index + 1);
        if line.len().saturating_add(take) > CONTROL_LINE_LIMIT {
            return Err("control record exceeds 64 KiB".into());
        }
        line.extend_from_slice(&available[..take]);
        reader.consume(take);
        if line.last() == Some(&b'\n') {
            line.pop();
            if line.last() == Some(&b'\r') {
                line.pop();
            }
            return Ok(Some(line));
        }
    }
}

fn descriptor(
    world: &ProductionWorld,
    operation: &str,
    phase: String,
    layer: i32,
) -> Result<CollectiveDescriptor, String> {
    if phase.is_empty() || phase.len() > 256 || phase.chars().any(char::is_control) {
        return Err("command phase must contain 1 to 256 printable characters".into());
    }
    Ok(CollectiveDescriptor {
        operation: operation.to_string(),
        phase,
        layer,
        dtype: "none".into(),
        shape: Vec::new(),
        manifest_identity: world.manifest_identity().to_string(),
    })
}

fn emit(value: serde_json::Value) -> Result<(), String> {
    let mut stdout = io::stdout().lock();
    serde_json::to_writer(&mut stdout, &value)
        .map_err(|error| format!("write rank response: {error}"))?;
    stdout
        .write_all(b"\n")
        .and_then(|_| stdout.flush())
        .map_err(|error| format!("flush rank response: {error}"))
}

fn run() -> Result<(), String> {
    if env::args_os().len() != 1 {
        return Err(
            "direct arguments are disabled; use an authenticated KLLM control-plane launch".into(),
        );
    }
    let stdin = io::stdin();
    let mut input = stdin.lock();
    let spec_line = read_bounded_line(&mut input)?
        .ok_or("missing structured MpiJobSpec from the KLLM control plane")?;
    let spec = MpiJobSpec::from_json(&spec_line)?;
    let mut world = ProductionWorld::connect(spec)?;
    let warmup = descriptor(&world, "barrier", "provider_warmup".into(), -1)?;
    world.barrier(&warmup)?;
    emit(json!({
        "kind": "kllm-mpi-rank-ready",
        "protocol_version": env!("KLLM_MPI_PROTOCOL_VERSION").parse::<u32>().unwrap_or(0),
        "provider_revision": env!("KLLM_MPI_UPSTREAM_REVISION"),
        "provider_patch_digest": env!("KLLM_MPI_PATCH_DIGEST"),
        "rank": world.rank(),
        "world_size": world.world_size(),
        "manifest_identity": world.manifest_identity(),
        "warmup": "passed",
    }))?;

    while let Some(line) = read_bounded_line(&mut input)? {
        let command: RankCommand = serde_json::from_slice(&line)
            .map_err(|error| format!("invalid rank command: {error}"))?;
        match command {
            RankCommand::Health {} => emit(json!({
                "kind": "kllm-mpi-rank-health",
                "rank": world.rank(),
                "world_size": world.world_size(),
                "status": "ready",
            }))?,
            RankCommand::Barrier { phase, layer } => {
                let barrier = descriptor(&world, "barrier", phase, layer)?;
                world.barrier(&barrier)?;
                emit(json!({
                    "kind": "kllm-mpi-rank-barrier",
                    "rank": world.rank(),
                    "status": "passed",
                }))?;
            }
            RankCommand::Collectives { phase } => {
                if phase.is_empty() || phase.len() > 256 || phase.chars().any(char::is_control) {
                    return Err("command phase must contain 1 to 256 printable characters".into());
                }
                let mut broadcast = if world.rank() == 0 {
                    b"kllm-provider-validation".to_vec()
                } else {
                    Vec::new()
                };
                let broadcast_desc = CollectiveDescriptor {
                    operation: "broadcast".into(),
                    phase: phase.clone(),
                    layer: -1,
                    dtype: "bytes".into(),
                    shape: vec![24],
                    manifest_identity: world.manifest_identity().into(),
                };
                world.broadcast(0, &broadcast_desc, &mut broadcast)?;
                let mut reduced = [world.rank() as f32 + 1.0, 1.0];
                let allreduce_desc = CollectiveDescriptor {
                    operation: "allreduce".into(),
                    phase: phase.clone(),
                    layer: -1,
                    dtype: "f32".into(),
                    shape: vec![2],
                    manifest_identity: world.manifest_identity().into(),
                };
                world.allreduce_f32(&allreduce_desc, &mut reduced)?;
                let local = vec![world.rank() as u8; world.rank() as usize + 1];
                let allgather_desc = CollectiveDescriptor {
                    operation: "allgatherv".into(),
                    phase,
                    layer: -1,
                    dtype: "bytes".into(),
                    shape: vec![(world.world_size() * (world.world_size() + 1) / 2) as u64],
                    manifest_identity: world.manifest_identity().into(),
                };
                let (counts, gathered) = world.allgatherv(&allgather_desc, &local)?;
                emit(json!({
                    "kind": "kllm-mpi-rank-collectives",
                    "rank": world.rank(),
                    "broadcast_sha256": format!("sha256:{:x}", Sha256::digest(&broadcast)),
                    "allreduce": reduced,
                    "allgatherv_counts": counts,
                    "allgatherv_sha256": format!("sha256:{:x}", Sha256::digest(&gathered)),
                    "status": "passed",
                }))?;
            }
            RankCommand::Stop {} => {
                emit(json!({
                    "kind": "kllm-mpi-rank-stopped",
                    "rank": world.rank(),
                }))?;
                return Ok(());
            }
        }
    }
    Err("rank control channel closed before the stop command".into())
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("kllm-mpi-rank: {error}");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn command_grammar_rejects_launch_fields() {
        let command = br#"{"command":"health","executable":"cmd.exe"}"#;
        assert!(serde_json::from_slice::<RankCommand>(command).is_err());
    }

    #[test]
    fn bounded_reader_requires_a_complete_record() {
        let mut unterminated = Cursor::new(b"{}".to_vec());
        assert!(read_bounded_line(&mut unterminated)
            .unwrap_err()
            .contains("without a newline"));
        let mut complete = Cursor::new(b"{}\r\n".to_vec());
        assert_eq!(
            read_bounded_line(&mut complete).unwrap(),
            Some(b"{}".to_vec())
        );
    }
}
