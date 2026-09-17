use pyo3::prelude::*;
use std::process::Command;
use sysinfo::System;

/// Backend hint for inference offload.
///
/// Preference when probing: Metal (macOS) → CUDA → ROCm → Vulkan → CPU.
/// See `docs/engine-decision.md`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum BackendHint {
    Metal,
    Cuda,
    Rocm,
    Vulkan,
    Cpu,
    #[allow(dead_code)]
    Unknown,
}

impl BackendHint {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Metal => "metal",
            Self::Cuda => "cuda",
            Self::Rocm => "rocm",
            Self::Vulkan => "vulkan",
            Self::Cpu => "cpu",
            Self::Unknown => "unknown",
        }
    }
}

#[pyclass]
#[derive(Clone, Debug)]
pub struct CapacitySnapshot {
    #[pyo3(get)]
    pub total_ram_bytes: u64,
    #[pyo3(get)]
    pub available_ram_bytes: u64,
    /// Discrete or reported GPU VRAM. `None` means unknown (caller must override).
    #[pyo3(get)]
    pub vram_bytes: Option<u64>,
    #[pyo3(get)]
    pub backend: String,
    #[pyo3(get)]
    pub hostname: String,
}

#[pymethods]
impl CapacitySnapshot {
    fn __repr__(&self) -> String {
        format!(
            "CapacitySnapshot(ram_total={}, ram_avail={}, vram={:?}, backend={})",
            self.total_ram_bytes, self.available_ram_bytes, self.vram_bytes, self.backend
        )
    }
}

pub fn probe_capacity() -> CapacitySnapshot {
    let mut sys = System::new();
    sys.refresh_memory();

    let total_ram_bytes = sys.total_memory();
    let available_ram_bytes = sys.available_memory();
    let (vram_bytes, backend) = probe_gpu();

    CapacitySnapshot {
        total_ram_bytes,
        available_ram_bytes,
        vram_bytes,
        backend: backend.as_str().to_string(),
        hostname: hostname(),
    }
}

fn hostname() -> String {
    System::host_name().unwrap_or_else(|| "unknown".into())
}

fn probe_gpu() -> (Option<u64>, BackendHint) {
    if cfg!(target_os = "macos") {
        // Apple Silicon: unified memory — treat as Metal with no separate VRAM pool.
        return (None, BackendHint::Metal);
    }

    // Preference: CUDA > ROCm > Vulkan. Never report two backends for one card.
    if let Some(vram) = probe_nvidia_smi() {
        return (Some(vram), BackendHint::Cuda);
    }
    if let Some(vram) = probe_rocm_smi() {
        return (Some(vram), BackendHint::Rocm);
    }
    if let Some(vram) = probe_windows_wmi_vram() {
        // Windows discrete GPU without CUDA/ROCm tools → Vulkan path (AMD default).
        return (Some(vram), BackendHint::Vulkan);
    }
    (None, BackendHint::Cpu)
}

fn probe_nvidia_smi() -> Option<u64> {
    let output = Command::new("nvidia-smi")
        .args([
            "--query-gpu=memory.total",
            "--format=csv,noheader,nounits",
        ])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let first = text.lines().next()?.trim();
    let mb: u64 = first.parse().ok()?;
    Some(mb * 1024 * 1024)
}

/// Probe AMD ROCm VRAM via `rocm-smi` (Linux; skipped when unavailable).
fn probe_rocm_smi() -> Option<u64> {
    let output = Command::new("rocm-smi")
        .args(["--showmeminfo", "vram", "--csv"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    parse_rocm_smi_vram_csv(&String::from_utf8_lossy(&output.stdout))
}

/// Parse `rocm-smi --showmeminfo vram --csv` and return the largest total VRAM in bytes.
fn parse_rocm_smi_vram_csv(text: &str) -> Option<u64> {
    let mut best: Option<u64> = None;
    for line in text.lines().skip(1) {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        // Typical: card0,<total_bytes>,<used_bytes>
        let mut parts = line.split(',');
        let _device = parts.next()?;
        let total: u64 = parts.next()?.trim().parse().ok()?;
        if total == 0 {
            continue;
        }
        best = Some(best.map_or(total, |b| b.max(total)));
    }
    best
}

#[cfg(target_os = "windows")]
fn probe_windows_wmi_vram() -> Option<u64> {
    let script = r#"
Get-CimInstance Win32_VideoController |
  Where-Object { $_.AdapterRAM -and $_.AdapterRAM -gt 0 } |
  Sort-Object AdapterRAM -Descending |
  Select-Object -First 1 -ExpandProperty AdapterRAM
"#;
    let output = Command::new("powershell")
        .args(["-NoProfile", "-Command", script])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let line = text.lines().next()?.trim();
    // AdapterRAM is often a signed i32 overflow for >2GB cards; treat carefully.
    let raw: i64 = line.parse().ok()?;
    if raw <= 0 {
        return None;
    }
    Some(raw as u64)
}

#[cfg(not(target_os = "windows"))]
fn probe_windows_wmi_vram() -> Option<u64> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn probe_returns_ram() {
        let snap = probe_capacity();
        assert!(snap.total_ram_bytes > 0);
        assert!(!snap.backend.is_empty());
    }

    #[test]
    fn parse_rocm_smi_csv_picks_largest() {
        let sample = "\
device,VRAM Total Memory (B),VRAM Total Used Memory (B)
card0,8589934592,0
card1,17179869184,1024
";
        assert_eq!(parse_rocm_smi_vram_csv(sample), Some(17179869184));
    }

    #[test]
    fn parse_rocm_smi_csv_empty() {
        assert_eq!(parse_rocm_smi_vram_csv("device,total,used\n"), None);
    }

    #[test]
    fn backend_hint_strings() {
        assert_eq!(BackendHint::Metal.as_str(), "metal");
        assert_eq!(BackendHint::Cuda.as_str(), "cuda");
        assert_eq!(BackendHint::Rocm.as_str(), "rocm");
        assert_eq!(BackendHint::Vulkan.as_str(), "vulkan");
    }
}
