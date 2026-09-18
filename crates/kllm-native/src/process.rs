use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::collections::HashMap;
use std::env;
use std::fs::OpenOptions;
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
    /// Extra environment variables (e.g. CUDA_VISIBLE_DEVICES).
    pub env: HashMap<String, String>,
    /// When set, child stdout and stderr are appended to this file.
    pub log_path: Option<PathBuf>,
}

fn extract_env(dict: &Bound<'_, PyDict>) -> PyResult<HashMap<String, String>> {
    match dict.get_item("env")? {
        Some(v) => v.extract(),
        None => Ok(HashMap::new()),
    }
}

fn opt_string(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<Option<String>> {
    match dict.get_item(key)? {
        Some(v) if v.is_none() => Ok(None),
        Some(v) => Ok(Some(v.extract()?)),
        None => Ok(None),
    }
}

/// llama.cpp i32 flags: accept Python int or str ("2", "-1", "auto").
fn opt_i32_arg(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<Option<String>> {
    match dict.get_item(key)? {
        Some(v) if v.is_none() => Ok(None),
        None => Ok(None),
        Some(v) => {
            if let Ok(n) = v.extract::<i64>() {
                return Ok(Some(n.to_string()));
            }
            if let Ok(s) = v.extract::<String>() {
                return Ok(Some(s));
            }
            Err(pyo3::exceptions::PyTypeError::new_err(format!(
                "{key} must be int or str"
            )))
        }
    }
}

fn opt_string_list(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<Vec<String>> {
    match dict.get_item(key)? {
        Some(v) if v.is_none() => Ok(Vec::new()),
        Some(v) => v.extract(),
        None => Ok(Vec::new()),
    }
}

fn push_auto_i32(argv: &mut Vec<String>, flag: &str, value: Option<String>, fallback: i32) {
    let n = match value.as_deref() {
        Some("auto") | None => fallback,
        Some(s) => s.parse::<i32>().unwrap_or(fallback),
    };
    argv.push(flag.into());
    argv.push(n.to_string());
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
        let micro_batch = opt_i32_arg(dict, "micro_batch")?;
        let cache_type_k = opt_string(dict, "cache_type_k")?.unwrap_or_else(|| "q8_0".into());
        let cache_type_v = opt_string(dict, "cache_type_v")?.unwrap_or_else(|| "q8_0".into());
        let parallel = opt_i32_arg(dict, "parallel")?;
        let batch_size = opt_i32_arg(dict, "batch_size")?;
        let load_mode = opt_string(dict, "load_mode")?;
        let ctx_checkpoints = opt_i32_arg(dict, "ctx_checkpoints")?;
        let cache_ram = opt_i32_arg(dict, "cache_ram")?;
        let chat_template = opt_string(dict, "chat_template")?;
        let chat_template_file = opt_string(dict, "chat_template_file")?;
        let mmproj = opt_string(dict, "mmproj")?;
        let mmproj_auto = dict
            .get_item("mmproj_auto")?
            .map(|v| v.extract::<bool>())
            .transpose()?
            .unwrap_or(false);
        let draft_model = opt_string(dict, "draft_model")?;
        let extra_args = opt_string_list(dict, "extra_args")?;
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
        let env = extract_env(dict)?;
        let log_path = opt_string(dict, "log_path")?.map(PathBuf::from);

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
            cache_type_k.clone(),
            "--cache-type-v".into(),
            cache_type_v,
            "-ngl".into(),
            ngl.to_string(),
            "--threads".into(),
            threads.to_string(),
            "--ctx-size".into(),
            context.to_string(),
        ];
        push_auto_i32(&mut argv, "--ubatch-size", micro_batch, 2);
        push_auto_i32(&mut argv, "--parallel", parallel, -1);
        push_auto_i32(&mut argv, "--batch-size", batch_size, -1);
        if let Some(mode) = load_mode {
            if mode != "auto" {
                argv.push("--load-mode".into());
                argv.push(mode);
            }
        }
        push_auto_i32(&mut argv, "--ctx-checkpoints", ctx_checkpoints, -1);
        push_auto_i32(&mut argv, "--cache-ram", cache_ram, -1);
        if let Some(path) = chat_template_file {
            argv.push("--chat-template-file".into());
            argv.push(path);
        } else if let Some(tmpl) = chat_template {
            if tmpl != "default" && !tmpl.is_empty() {
                argv.push("--chat-template".into());
                argv.push(tmpl);
            }
        }
        if mmproj_auto {
            argv.push("--mmproj-auto".into());
        } else if let Some(path) = mmproj {
            argv.push("--mmproj".into());
            argv.push(path);
        }
        if let Some(draft) = draft_model {
            argv.push("--model-draft".into());
            argv.push(draft);
        }

        if !rpc_servers.is_empty() {
            argv.push("--rpc".into());
            argv.push(rpc_servers.join(","));
        }
        argv.extend(extra_args);

        Ok(Self {
            role: "master".into(),
            binary: PathBuf::from(&binary),
            argv,
            env,
            log_path,
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
        let env = extract_env(dict)?;
        let log_path = opt_string(dict, "log_path")?.map(PathBuf::from);

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
            env,
            log_path,
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

#[cfg(windows)]
fn worker_dll_path_for(binary: &Path) -> Option<PathBuf> {
    let parent = binary.parent()?;
    let dll_dir = parent.join("src");
    if dll_dir.is_dir() {
        Some(dll_dir)
    } else {
        None
    }
}

fn stdio_for_log(path: &Path) -> Result<(Stdio, Stdio), ProcessError> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let file = OpenOptions::new().create(true).append(true).open(path)?;
    let cloned = file.try_clone()?;
    Ok((Stdio::from(file), Stdio::from(cloned)))
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
    if !cfg.env.is_empty() {
        cmd.envs(&cfg.env);
    }
    #[cfg(windows)]
    if let Some(dll_dir) = worker_dll_path_for(&cfg.binary) {
        let path_key = "PATH";
        let existing = cfg
            .env
            .get(path_key)
            .cloned()
            .or_else(|| env::var(path_key).ok());
        let merged = match existing {
            Some(path) => format!("{};{}", dll_dir.display(), path),
            None => dll_dir.to_string_lossy().into_owned(),
        };
        cmd.env(path_key, merged);
    }
    cmd.stdin(Stdio::null());
    match &cfg.log_path {
        Some(path) => {
            let (out, err) = stdio_for_log(path)?;
            cmd.stdout(out).stderr(err);
        }
        None => {
            cmd.stdout(Stdio::null()).stderr(Stdio::null());
        }
    }

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

    #[test]
    fn stdio_for_log_creates_append_file() {
        let dir = std::env::temp_dir().join(format!("kllm-log-{}", std::process::id()));
        let path = dir.join("nested").join("child.log");
        let _ = stdio_for_log(&path).expect("open log");
        assert!(path.is_file());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
