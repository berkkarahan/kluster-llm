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
pub struct GpuDevice {
    #[pyo3(get)]
    pub index: u32,
    #[pyo3(get)]
    pub name: String,
    /// Discrete VRAM in bytes. `None` for unified memory (Metal).
    #[pyo3(get)]
    pub vram_bytes: Option<u64>,
}

#[pymethods]
impl GpuDevice {
    fn __repr__(&self) -> String {
        format!(
            "GpuDevice(index={}, name={:?}, vram={:?})",
            self.index, self.name, self.vram_bytes
        )
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
    let (devices, backend) = enumerate_gpus();
    let vram_bytes = sum_vram(&devices);

    CapacitySnapshot {
        total_ram_bytes,
        available_ram_bytes,
        vram_bytes,
        backend: backend.as_str().to_string(),
        hostname: hostname(),
    }
}

/// Enumerate local GPUs with index, name, and VRAM.
pub fn list_gpus() -> (Vec<GpuDevice>, BackendHint) {
    enumerate_gpus()
}

fn hostname() -> String {
    System::host_name().unwrap_or_else(|| "unknown".into())
}

fn sum_vram(devices: &[GpuDevice]) -> Option<u64> {
    let mut total: u64 = 0;
    let mut any = false;
    for d in devices {
        if let Some(v) = d.vram_bytes {
            total = total.saturating_add(v);
            any = true;
        }
    }
    if any {
        Some(total)
    } else {
        None
    }
}

fn enumerate_gpus() -> (Vec<GpuDevice>, BackendHint) {
    if cfg!(target_os = "macos") {
        return (
            vec![GpuDevice {
                index: 0,
                name: "Apple GPU".into(),
                vram_bytes: None,
            }],
            BackendHint::Metal,
        );
    }

    // Preference: CUDA > ROCm > Vulkan. Never report two backends for one card.
    if let Some(devices) = list_nvidia_gpus() {
        if !devices.is_empty() {
            return (devices, BackendHint::Cuda);
        }
    }
    if let Some(devices) = list_rocm_gpus() {
        if !devices.is_empty() {
            return (devices, BackendHint::Rocm);
        }
    }
    if let Some(devices) = list_windows_wmi_gpus() {
        if !devices.is_empty() {
            return (devices, BackendHint::Vulkan);
        }
    }
    (Vec::new(), BackendHint::Cpu)
}

fn list_nvidia_gpus() -> Option<Vec<GpuDevice>> {
    let output = Command::new("nvidia-smi")
        .args([
            "--query-gpu=index,name,memory.total",
            "--format=csv,noheader,nounits",
        ])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let devices = parse_nvidia_smi_csv(&text);
    if devices.is_empty() {
        None
    } else {
        Some(devices)
    }
}

/// Parse `nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader,nounits`.
pub fn parse_nvidia_smi_csv(text: &str) -> Vec<GpuDevice> {
    let mut devices = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        // index, name (may contain commas), memory_mb — split index then rsplit memory.
        let Some((index_s, rest)) = line.split_once(',') else {
            continue;
        };
        let Some((name, mem_s)) = rest.rsplit_once(',') else {
            continue;
        };
        let Ok(index) = index_s.trim().parse::<u32>() else {
            continue;
        };
        let Ok(mb) = mem_s.trim().parse::<u64>() else {
            continue;
        };
        devices.push(GpuDevice {
            index,
            name: name.trim().to_string(),
            vram_bytes: Some(mb * 1024 * 1024),
        });
    }
    devices
}

fn list_rocm_gpus() -> Option<Vec<GpuDevice>> {
    let output = Command::new("rocm-smi")
        .args(["--showmeminfo", "vram", "--csv"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let devices = parse_rocm_smi_devices(&String::from_utf8_lossy(&output.stdout));
    if devices.is_empty() {
        None
    } else {
        Some(devices)
    }
}

/// Parse `rocm-smi --showmeminfo vram --csv` into per-card devices.
pub fn parse_rocm_smi_devices(text: &str) -> Vec<GpuDevice> {
    let mut devices = Vec::new();
    for (i, line) in text.lines().skip(1).enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        // Typical: card0,<total_bytes>,<used_bytes>
        let mut parts = line.split(',');
        let device_name = parts.next().unwrap_or("").trim();
        let total: u64 = match parts.next().and_then(|s| s.trim().parse().ok()) {
            Some(t) if t > 0 => t,
            _ => continue,
        };
        let index = device_name
            .strip_prefix("card")
            .and_then(|s| s.parse().ok())
            .unwrap_or(i as u32);
        devices.push(GpuDevice {
            index,
            name: if device_name.is_empty() {
                format!("AMD GPU {index}")
            } else {
                device_name.to_string()
            },
            vram_bytes: Some(total),
        });
    }
    devices
}

/// Sum of VRAM across devices (for capacity); None if no discrete VRAM.
#[cfg_attr(not(test), allow(dead_code))]
pub fn parse_rocm_smi_vram_csv(text: &str) -> Option<u64> {
    sum_vram(&parse_rocm_smi_devices(text))
}

#[cfg(target_os = "windows")]
fn list_windows_wmi_gpus() -> Option<Vec<GpuDevice>> {
    let script = r#"
Get-CimInstance Win32_VideoController |
  Where-Object { $_.AdapterRAM -and $_.AdapterRAM -gt 0 } |
  ForEach-Object { "{0}`t{1}" -f $_.Name, $_.AdapterRAM }
"#;
    let output = Command::new("powershell")
        .args(["-NoProfile", "-Command", script])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let devices = parse_wmi_gpu_lines(&text);
    if devices.is_empty() {
        None
    } else {
        Some(devices)
    }
}

#[cfg(not(target_os = "windows"))]
fn list_windows_wmi_gpus() -> Option<Vec<GpuDevice>> {
    None
}

#[cfg_attr(not(test), allow(dead_code))]
pub fn parse_wmi_gpu_lines(text: &str) -> Vec<GpuDevice> {
    let mut devices = Vec::new();
    for (i, line) in text.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let (name, ram_s) = match line.split_once('\t') {
            Some(p) => p,
            None => continue,
        };
        // AdapterRAM is often a signed i32 overflow for >2GB cards; treat carefully.
        let raw: i64 = match ram_s.trim().parse() {
            Ok(r) => r,
            Err(_) => continue,
        };
        if raw <= 0 {
            continue;
        }
        devices.push(GpuDevice {
            index: i as u32,
            name: name.trim().to_string(),
            vram_bytes: Some(raw as u64),
        });
    }
    devices
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
    fn parse_rocm_smi_csv_sums_all() {
        let sample = "\
device,VRAM Total Memory (B),VRAM Total Used Memory (B)
card0,8589934592,0
card1,17179869184,1024
";
        assert_eq!(parse_rocm_smi_vram_csv(sample), Some(8589934592 + 17179869184));
        let devices = parse_rocm_smi_devices(sample);
        assert_eq!(devices.len(), 2);
        assert_eq!(devices[0].index, 0);
        assert_eq!(devices[1].index, 1);
        assert_eq!(devices[1].vram_bytes, Some(17179869184));
    }

    #[test]
    fn parse_rocm_smi_csv_empty() {
        assert_eq!(parse_rocm_smi_vram_csv("device,total,used\n"), None);
    }

    #[test]
    fn parse_nvidia_smi_multi() {
        let sample = "\
0, NVIDIA GeForce RTX 4090, 24564
1, NVIDIA GeForce RTX 3090, 24576
";
        let devices = parse_nvidia_smi_csv(sample);
        assert_eq!(devices.len(), 2);
        assert_eq!(devices[0].index, 0);
        assert_eq!(devices[0].name, "NVIDIA GeForce RTX 4090");
        assert_eq!(devices[0].vram_bytes, Some(24564 * 1024 * 1024));
        assert_eq!(devices[1].index, 1);
        assert_eq!(sum_vram(&devices), Some((24564 + 24576) * 1024 * 1024));
    }

    #[test]
    fn parse_wmi_lines() {
        let sample = "AMD Radeon RX 7900 XTX\t25769803776\nIntel UHD\t1073741824\n";
        let devices = parse_wmi_gpu_lines(sample);
        assert_eq!(devices.len(), 2);
        assert_eq!(devices[0].index, 0);
        assert_eq!(devices[0].vram_bytes, Some(25769803776));
    }

    #[test]
    fn backend_hint_strings() {
        assert_eq!(BackendHint::Metal.as_str(), "metal");
        assert_eq!(BackendHint::Cuda.as_str(), "cuda");
        assert_eq!(BackendHint::Rocm.as_str(), "rocm");
        assert_eq!(BackendHint::Vulkan.as_str(), "vulkan");
    }
}
