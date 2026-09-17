use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::env;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Mutex, OnceLock};
use thiserror::Error;

#[derive(Error, Debug)]
pub enum ProcessError {
    #[error("{0}")]
    Message(String),
    #[error(transparent)]
    Io(#[from] std::io::Error),
}

#[pyclass]
#[derive(Clone, Debug)]
pub struct ProcessHandle {
    #[pyo3(get)]
    pub pid: u32,
    #[pyo3(get)]
    pub role: String,
}

#[pyclass]
#[derive(Clone, Debug)]
pub struct LaunchResult {
    #[pyo3(get)]
    pub pid: u32,
    #[pyo3(get)]
    pub binary: String,
    #[pyo3(get)]
    pub argv: Vec<String>,
    #[pyo3(get)]
    pub role: String,
}

struct TrackedChild {
    child: Child,
}

fn children() -> &'static Mutex<Vec<TrackedChild>> {
    static CELL: OnceLock<Mutex<Vec<TrackedChild>>> = OnceLock::new();
    CELL.get_or_init(|| Mutex::new(Vec::new()))
}

pub struct LaunchConfig {
    pub role: String,
    pub binary: PathBuf,
    pub argv: Vec<String>,
}

impl LaunchConfig {
    pub fn master_from_pydict(dict: &Bound<'_, PyDict>) -> PyResult<Self> {
        let model: String = dict
            .get_item("model")?
            .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err("model"))?
            .extract()?;
        let host: String = dict
            .get_item("host")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or_else(|| "0.0.0.0".into());
        let port: u16 = dict
            .get_item("port")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or(8080);
        let context: u32 = dict
            .get_item("context")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or(4096);
        let threads: u32 = dict
            .get_item("threads")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or(8);
        let micro_batch: u32 = dict
            .get_item("micro_batch")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or(2);
        let tensor_split: String = dict
            .get_item("tensor_split")?
            .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err("tensor_split"))?
            .extract()?;
        let ngl: u32 = dict
            .get_item("ngl")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or(99);
        let rpc_servers: Vec<String> = dict
            .get_item("rpc_servers")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or_default();
        let repo_root: Option<String> = dict
            .get_item("repo_root")?
            .map(|v| v.extract())
            .transpose()?;

        let binary = resolve_llama_server(repo_root.as_deref()).ok_or_else(|| {
            pyo3::exceptions::PyRuntimeError::new_err(
                "llama-server not found; set KLLM_LLAMA_SERVER or build vendor/llama.cpp",
            )
        })?;

        let mut argv = vec![
            binary.clone(),
            "--model".into(),
            model,
            "--host".into(),
            host,
            "--port".into(),
            port.to_string(),
            "--split-mode".into(),
            "layer".into(),
            "--tensor-split".into(),
            tensor_split,
            "--cache-type-k".into(),
            "q8_0".into(),
            "--cache-type-v".into(),
            "q8_0".into(),
            "-ngl".into(),
            ngl.to_string(),
            "--threads".into(),
            threads.to_string(),
            "--ctx-size".into(),
            context.to_string(),
            "--ubatch-size".into(),
            micro_batch.to_string(),
        ];

        if !rpc_servers.is_empty() {
            argv.push("--rpc".into());
            argv.push(rpc_servers.join(","));
        }

        Ok(Self {
            role: "master".into(),
            binary: PathBuf::from(&binary),
            argv,
        })
    }

    pub fn worker_from_pydict(dict: &Bound<'_, PyDict>) -> PyResult<Self> {
        let host: String = dict
            .get_item("host")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or_else(|| "0.0.0.0".into());
        let port: u16 = dict
            .get_item("port")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or(50052);
        let threads: u32 = dict
            .get_item("threads")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or(0);
        let repo_root: Option<String> = dict
            .get_item("repo_root")?
            .map(|v| v.extract())
            .transpose()?;

        let binary = resolve_rpc_server(repo_root.as_deref()).ok_or_else(|| {
            pyo3::exceptions::PyRuntimeError::new_err(
                "rpc-server not found; set KLLM_RPC_SERVER or place rpc-server in worker-windows/",
            )
        })?;

        let mut argv = vec![
            binary.clone(),
            "--host".into(),
            host,
            "--port".into(),
            port.to_string(),
            // Local tensor cache: avoids re-transferring large tensors over RPC on reconnect.
            // See docs/engine-decision.md (RPC operational rules).
            "-c".into(),
        ];
        if threads > 0 {
            argv.push("--threads".into());
            argv.push(threads.to_string());
        }

        Ok(Self {
            role: "worker".into(),
            binary: PathBuf::from(&binary),
            argv,
        })
    }
}

pub fn resolve_llama_server(repo_root: Option<&str>) -> Option<String> {
    if let Ok(p) = env::var("KLLM_LLAMA_SERVER") {
        if Path::new(&p).is_file() {
            return Some(p);
        }
    }
    let roots = candidate_roots(repo_root);
    let rels = [
        "vendor/bin/llama-server",
        "vendor/bin/llama-server.exe",
        "vendor/llama.cpp/build/bin/llama-server",
        "vendor/llama.cpp/build/release/bin/llama-server",
        "vendor/llama.cpp/build/bin/Release/llama-server.exe",
        "vendor/llama.cpp/build/bin/llama-server.exe",
        "vendor/llama.cpp/llama-server",
        "vendor/llama.cpp/llama-server.exe",
    ];
    for root in &roots {
        for rel in rels {
            let p = root.join(rel);
            if p.is_file() {
                return Some(p.to_string_lossy().into());
            }
        }
    }
    which("llama-server")
}

pub fn resolve_rpc_server(repo_root: Option<&str>) -> Option<String> {
    if let Ok(p) = env::var("KLLM_RPC_SERVER") {
        if Path::new(&p).is_file() {
            return Some(p);
        }
    }
    let roots = candidate_roots(repo_root);
    let rels = [
        "vendor/bin/rpc-server",
        "vendor/bin/rpc-server.exe",
        "worker-windows/rpc-server.exe",
        "vendor/llama.cpp/build/bin/rpc-server",
        "vendor/llama.cpp/build/bin/rpc-server.exe",
        "vendor/llama.cpp/build/bin/ggml-rpc-server",
        "vendor/llama.cpp/build/bin/ggml-rpc-server.exe",
        "vendor/llama.cpp/build/bin/Release/rpc-server.exe",
        "vendor/llama.cpp/build/bin/Release/ggml-rpc-server.exe",
        "vendor/llama.cpp/rpc-server",
        "vendor/llama.cpp/rpc-server.exe",
        "vendor/llama.cpp/ggml-rpc-server",
        "vendor/llama.cpp/ggml-rpc-server.exe",
    ];
    for root in &roots {
        for rel in rels {
            let p = root.join(rel);
            if p.is_file() {
                return Some(p.to_string_lossy().into());
            }
        }
    }
    which("rpc-server")
}

fn candidate_roots(repo_root: Option<&str>) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Some(r) = repo_root {
        roots.push(PathBuf::from(r));
    }
    if let Ok(cwd) = env::current_dir() {
        roots.push(cwd.clone());
        if let Some(parent) = cwd.parent() {
            roots.push(parent.to_path_buf());
        }
    }
    if let Ok(here) = env::var("KLLM_REPO_ROOT") {
        roots.push(PathBuf::from(here));
    }
    roots
}

fn which(name: &str) -> Option<String> {
    let path = env::var_os("PATH")?;
    for dir in env::split_paths(&path) {
        let candidate = dir.join(name);
        if candidate.is_file() {
            return Some(candidate.to_string_lossy().into());
        }
        #[cfg(windows)]
        {
            let exe = dir.join(format!("{name}.exe"));
            if exe.is_file() {
                return Some(exe.to_string_lossy().into());
            }
        }
    }
    None
}

pub fn start_process(cfg: &LaunchConfig) -> Result<LaunchResult, ProcessError> {
    if !cfg.binary.is_file() {
        return Err(ProcessError::Message(format!(
            "binary not found: {}",
            cfg.binary.display()
        )));
    }
    let mut cmd = Command::new(&cfg.binary);
    if cfg.argv.len() > 1 {
        cmd.args(&cfg.argv[1..]);
    }
    cmd.stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    let child = cmd.spawn()?;
    let pid = child.id();
    children()
        .lock()
        .map_err(|_| ProcessError::Message("process lock poisoned".into()))?
        .push(TrackedChild { child });

    Ok(LaunchResult {
        pid,
        binary: cfg.binary.to_string_lossy().into(),
        argv: cfg.argv.clone(),
        role: cfg.role.clone(),
    })
}

pub fn stop_process(pid: u32, force: bool) -> bool {
    if let Ok(mut guard) = children().lock() {
        if let Some(idx) = guard.iter().position(|c| c.child.id() == pid) {
            let mut tracked = guard.remove(idx);
            let _ = tracked.child.kill();
            let _ = tracked.child.wait();
            let _ = force;
            return true;
        }
    }

    #[cfg(windows)]
    {
        let mut args = vec!["/PID".to_string(), pid.to_string()];
        if force {
            args.push("/F".into());
        }
        args.push("/T".into());
        let status = Command::new("taskkill").args(&args).status();
        return status.map(|s| s.success()).unwrap_or(false);
    }
    #[cfg(unix)]
    {
        let sig = if force { "-KILL" } else { "-TERM" };
        let status = Command::new("kill").args([sig, &pid.to_string()]).status();
        status.map(|s| s.success()).unwrap_or(false)
    }
}

pub fn process_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    #[cfg(windows)]
    {
        let output = Command::new("tasklist")
            .args(["/FI", &format!("PID eq {pid}"), "/NH"])
            .output();
        match output {
            Ok(o) => String::from_utf8_lossy(&o.stdout).contains(&pid.to_string()),
            Err(_) => false,
        }
    }
    #[cfg(unix)]
    {
        Path::new(&format!("/proc/{pid}")).exists()
            || Command::new("kill")
                .args(["-0", &pid.to_string()])
                .status()
                .map(|s| s.success())
                .unwrap_or(false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_does_not_panic() {
        let _ = resolve_llama_server(None);
        let _ = resolve_rpc_server(None);
    }

    #[test]
    fn candidate_roots_include_explicit() {
        let roots = candidate_roots(Some("/tmp/kllm-test-root"));
        assert_eq!(roots[0], PathBuf::from("/tmp/kllm-test-root"));
    }
}
