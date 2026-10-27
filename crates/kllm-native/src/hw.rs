use pyo3::prelude::*;
use std::process::Command;
use sysinfo::System;

/// Fixed share of unified memory reported as GPU VRAM on Apple Silicon (not the planner headroom slider).
/// Planning budgets use 65% of total RAM (`KLLM_UNIFIED_VRAM_FRACTION`) instead of this reported figure.
/// Unused on non-macOS builds, where the macOS probe that applies it is not compiled.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub const METAL_VRAM_FRACTION: f64 = 0.80;

/// Backend hint for inference offload.
///
/// Preference when probing: Metal (macOS) → CUDA → ROCm → Vulkan → CPU.
/// See `docs/engine-decision.md`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum BackendHint {
    /// Built only by the macOS probe. Other targets still name the variant so `as_str` stays exhaustive.
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    Metal,
    /// Built only by the non-macOS probe.
    #[cfg_attr(target_os = "macos", allow(dead_code))]
    Cuda,
    #[cfg_attr(target_os = "macos", allow(dead_code))]
    Rocm,
    #[cfg_attr(target_os = "macos", allow(dead_code))]
    Vulkan,
    #[cfg_attr(target_os = "macos", allow(dead_code))]
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

#[pyclass(from_py_object)]
#[derive(Clone, Debug)]
pub struct GpuDevice {
    #[pyo3(get)]
    pub index: u32,
    #[pyo3(get)]
    pub name: String,
    /// VRAM in bytes. On Apple Silicon this is ~80% of unified memory.
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

#[pyclass(from_py_object)]
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
    probe_capacity_with_gpus().0
}

/// Probe host memory and GPU topology in one pass.  Python needs both views
/// for a capacity refresh; returning them together avoids launching the
/// platform GPU tools twice for the same observation.
pub fn probe_capacity_with_gpus() -> (CapacitySnapshot, Vec<GpuDevice>, BackendHint) {
    let mut sys = System::new();
    sys.refresh_memory();

    let total_ram_bytes = sys.total_memory();
    #[cfg(target_os = "macos")]
    let available_ram_bytes = macos_available_ram(total_ram_bytes).unwrap_or(0);
    #[cfg(not(target_os = "macos"))]
    let available_ram_bytes = sys.available_memory();
    let (devices, backend) = enumerate_gpus();
    let vram_bytes = sum_vram(&devices);

    let snapshot = CapacitySnapshot {
        total_ram_bytes,
        available_ram_bytes,
        vram_bytes,
        backend: backend.as_str().to_string(),
        hostname: hostname(),
    };
    (snapshot, devices, backend)
}

/// Free and inactive pages are allocatable. Compressed pages are already occupied;
/// subtracting them again can turn a usable unified-memory pool into zero capacity.
#[cfg(any(target_os = "macos", test))]
pub(crate) fn macos_available_bytes(
    free: u64,
    inactive: u64,
    page_size: u64,
    total: u64,
) -> Option<u64> {
    if page_size == 0 {
        return None;
    }
    free.checked_add(inactive)?
        .checked_mul(page_size)
        .filter(|bytes| *bytes <= total)
}

#[cfg(target_os = "macos")]
#[allow(deprecated)]
fn macos_available_ram(total: u64) -> Option<u64> {
    let mut vm: libc::vm_statistics64 = unsafe { std::mem::zeroed() };
    let mut count = libc::HOST_VM_INFO64_COUNT;
    let port = unsafe { libc::mach_host_self() };
    let status = unsafe {
        libc::host_statistics64(
            port,
            libc::HOST_VM_INFO64,
            (&mut vm as *mut libc::vm_statistics64).cast(),
            &mut count,
        )
    };
    unsafe extern "C" {
        fn mach_port_deallocate(
            task: libc::mach_port_t,
            name: libc::mach_port_t,
        ) -> libc::kern_return_t;
    }
    unsafe {
        mach_port_deallocate(libc::mach_task_self(), port);
    }
    if status != 0 {
        return None;
    }
    macos_available_bytes(
        vm.free_count as u64,
        vm.inactive_count as u64,
        unsafe { libc::vm_page_size } as u64,
        total,
    )
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

#[cfg(target_os = "macos")]
fn macos_gpu_name() -> String {
    Command::new("sysctl")
        .args(["-n", "machdep.cpu.brand_string"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "Apple GPU".into())
}

fn enumerate_gpus() -> (Vec<GpuDevice>, BackendHint) {
    #[cfg(target_os = "macos")]
    {
        let mut sys = System::new();
        sys.refresh_memory();
        let total_ram = sys.total_memory();
        let vram = (total_ram as f64 * METAL_VRAM_FRACTION) as u64;
        (
            vec![GpuDevice {
                index: 0,
                name: macos_gpu_name(),
                vram_bytes: Some(vram),
            }],
            BackendHint::Metal,
        )
    }

    #[cfg(not(target_os = "macos"))]
    {
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
}

#[cfg(not(target_os = "macos"))]
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
#[cfg_attr(all(not(test), target_os = "macos"), allow(dead_code))]
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

#[cfg(not(target_os = "macos"))]
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
fn list_windows_dxgi_gpus() -> Option<Vec<GpuDevice>> {
    use windows::Win32::Graphics::Dxgi::{
        CreateDXGIFactory1, IDXGIFactory1, IDXGIAdapter3,
        DXGI_ADAPTER_FLAG_SOFTWARE, DXGI_ERROR_NOT_FOUND,
        DXGI_MEMORY_SEGMENT_GROUP_LOCAL, DXGI_QUERY_VIDEO_MEMORY_INFO,
    };
    use windows::core::Interface;

    let factory: IDXGIFactory1 = unsafe { CreateDXGIFactory1().ok()? };
    let mut devices = Vec::new();
    for index in 0..32 {
        let adapter = match unsafe { factory.EnumAdapters1(index) } {
            Ok(a) => a,
            Err(e) if e.code() == DXGI_ERROR_NOT_FOUND => break,
            Err(_) => break,
        };
        let desc = match unsafe { adapter.GetDesc1() } {
            Ok(d) => d,
            Err(_) => continue,
        };
        if (desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE.0 as u32) != 0 {
            continue;
        }
        let name = String::from_utf16_lossy(&desc.Description)
            .trim_matches('\0')
            .trim()
            .to_string();
        if name.is_empty() {
            continue;
        }

        let mut vram = desc.DedicatedVideoMemory as u64;

        if let Ok(adapter3) = adapter.cast::<IDXGIAdapter3>() {
            let mut mem_info = DXGI_QUERY_VIDEO_MEMORY_INFO::default();
            if unsafe {
                adapter3.QueryVideoMemoryInfo(
                    0,
                    DXGI_MEMORY_SEGMENT_GROUP_LOCAL,
                    &mut mem_info as *mut _,
                )
            }
            .is_ok()
            {
                if vram == 0 && mem_info.Budget > 0 {
                    vram = mem_info.Budget;
                }
            }
        }

        if vram > 0 {
            devices.push(GpuDevice {
                index,
                name,
                vram_bytes: Some(vram),
            });
        }
    }
    if devices.is_empty() {
        None
    } else {
        Some(devices)
    }
}

#[cfg(target_os = "windows")]
fn list_windows_registry_gpus() -> Option<Vec<GpuDevice>> {
    #[link(name = "advapi32")]
    extern "system" {
        fn RegGetValueW(
            hKey: isize,
            lpSubKey: *const u16,
            lpValue: *const u16,
            dwFlags: u32,
            pdwType: *mut u32,
            pvData: *mut u8,
            pcbData: *mut u32,
        ) -> i32;
    }

    const HKEY_LOCAL_MACHINE: isize = -2147483646; // 0x80000002
    const RRF_RT_REG_SZ: u32 = 0x00000002;
    const RRF_RT_REG_DWORD: u32 = 0x00000010;
    const RRF_RT_REG_QWORD: u32 = 0x00000040;

    let base_key = "SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}";
    let mut devices = Vec::new();

    for i in 0..32 {
        let subkey = format!("{base_key}\\{i:04}\0");
        let subkey_utf16: Vec<u16> = subkey.encode_utf16().collect();

        // 1. Query DriverDesc (REG_SZ)
        let desc_name: Vec<u16> = "DriverDesc\0".encode_utf16().collect();
        let mut desc_buf = [0u16; 256];
        let mut desc_size = (desc_buf.len() * 2) as u32;
        let mut val_type = 0u32;
        let res = unsafe {
            RegGetValueW(
                HKEY_LOCAL_MACHINE,
                subkey_utf16.as_ptr(),
                desc_name.as_ptr(),
                RRF_RT_REG_SZ,
                &mut val_type,
                desc_buf.as_mut_ptr() as *mut u8,
                &mut desc_size,
            )
        };
        if res != 0 {
            continue;
        }

        let name = String::from_utf16_lossy(&desc_buf[..(desc_size as usize / 2)])
            .trim_matches('\0')
            .trim()
            .to_string();
        if name.is_empty() {
            continue;
        }

        // 2. Query HardwareInformation.qwMemorySize (REG_QWORD)
        let qw_name: Vec<u16> = "HardwareInformation.qwMemorySize\0".encode_utf16().collect();
        let mut qw_vram = 0u64;
        let mut qw_size = std::mem::size_of::<u64>() as u32;
        let qw_res = unsafe {
            RegGetValueW(
                HKEY_LOCAL_MACHINE,
                subkey_utf16.as_ptr(),
                qw_name.as_ptr(),
                RRF_RT_REG_QWORD,
                &mut val_type,
                &mut qw_vram as *mut u64 as *mut u8,
                &mut qw_size,
            )
        };

        let vram = if qw_res == 0 && qw_vram > 0 {
            qw_vram
        } else {
            // 3. Fallback to AdapterRAM (REG_DWORD)
            let ram_name: Vec<u16> = "AdapterRAM\0".encode_utf16().collect();
            let mut dw_vram = 0u32;
            let mut dw_size = std::mem::size_of::<u32>() as u32;
            let dw_res = unsafe {
                RegGetValueW(
                    HKEY_LOCAL_MACHINE,
                    subkey_utf16.as_ptr(),
                    ram_name.as_ptr(),
                    RRF_RT_REG_DWORD,
                    &mut val_type,
                    &mut dw_vram as *mut u32 as *mut u8,
                    &mut dw_size,
                )
            };
            if dw_res == 0 && dw_vram > 0 {
                dw_vram as u64
            } else {
                0
            }
        };

        if vram > 0 {
            devices.push(GpuDevice {
                index: i,
                name,
                vram_bytes: Some(vram),
            });
        }
    }

    if devices.is_empty() {
        None
    } else {
        Some(devices)
    }
}

#[cfg(target_os = "windows")]
fn list_windows_powershell_gpus() -> Option<Vec<GpuDevice>> {
    // Win32_VideoController.AdapterRAM is a 32-bit field and saturates near
    // 4 GB (often 4293918720) for large discrete cards. Prefer the registry
    // QWORD HardwareInformation.qwMemorySize; fall back to AdapterRAM.
    let script = r#"
$base = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
Get-ChildItem $base -ErrorAction SilentlyContinue | ForEach-Object {
  $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
  if (-not $p) { return }
  $name = $p.DriverDesc
  if (-not $name) { return }
  $qw = $p.'HardwareInformation.qwMemorySize'
  $vram = 0
  if ($null -ne $qw -and [int64]$qw -gt 0) { $vram = [int64]$qw }
  elseif ($null -ne $p.AdapterRAM -and [int64]$p.AdapterRAM -gt 0) { $vram = [int64]$p.AdapterRAM }
  if ($vram -gt 0) { "{0}`t{1}" -f $name, $vram }
}
# Registry ACL noise can leave a non-zero exit otherwise.
exit 0
"#;
    let output = Command::new("powershell")
        .args(["-NoProfile", "-Command", script])
        .output()
        .ok()?;
    // Still trust parseable stdout over exit status — older hosts may ignore
    // the trailing `exit 0` if `-Command` parsing fails partway.
    let text = String::from_utf8_lossy(&output.stdout);
    let devices = parse_wmi_gpu_lines(&text);
    if devices.is_empty() {
        None
    } else {
        Some(devices)
    }
}

#[cfg(target_os = "windows")]
fn list_windows_wmi_gpus() -> Option<Vec<GpuDevice>> {
    // 1. Direct DXGI adapter enumeration with IDXGIAdapter3::QueryVideoMemoryInfo
    if let Some(devices) = list_windows_dxgi_gpus() {
        if !devices.is_empty() {
            return Some(devices);
        }
    }

    // 2. Direct Win32 registry enumeration without subprocess overhead
    if let Some(devices) = list_windows_registry_gpus() {
        if !devices.is_empty() {
            return Some(devices);
        }
    }

    // 3. Fallback to PowerShell registry query if direct methods fail
    list_windows_powershell_gpus()
}

#[cfg(all(not(target_os = "windows"), not(target_os = "macos")))]
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
        // Bytes from registry qwMemorySize (preferred) or WMI AdapterRAM.
        // AdapterRAM alone saturates near 4GB on large cards — callers should
        // prefer the QWORD registry value when available.
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
    fn available_pages_do_not_subtract_compressed_memory() {
        assert_eq!(
            macos_available_bytes(1, 9, 16384, 48 * 1024 * 1024 * 1024),
            Some(163840)
        );
        assert_eq!(macos_available_bytes(0, 0, 16384, 1024 * 1024), Some(0));
        assert_eq!(macos_available_bytes(1, 1, 0, 1024), None);
        assert_eq!(macos_available_bytes(u64::MAX, 1, 4096, u64::MAX), None);
        assert_eq!(macos_available_bytes(2, 2, 4096, 1024), None);
    }

    #[test]
    fn probe_returns_ram() {
        let snap = probe_capacity();
        assert!(snap.total_ram_bytes > 0);
        assert!(!snap.backend.is_empty());
    }

    #[test]
    #[cfg(target_os = "macos")]
    fn macos_reports_metal_vram_fraction() {
        let snap = probe_capacity();
        assert_eq!(snap.backend, "metal");
        let expected = (snap.total_ram_bytes as f64 * METAL_VRAM_FRACTION) as u64;
        assert_eq!(snap.vram_bytes, Some(expected));
        let (devices, _) = list_gpus();
        assert_eq!(devices.len(), 1);
        assert_eq!(devices[0].vram_bytes, Some(expected));
    }

    #[test]
    fn parse_rocm_smi_csv_sums_all() {
        let sample = "\
device,VRAM Total Memory (B),VRAM Total Used Memory (B)
card0,8589934592,0
card1,17179869184,1024
";
        assert_eq!(
            parse_rocm_smi_vram_csv(sample),
            Some(8589934592 + 17179869184)
        );
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
    fn parse_wmi_lines_registry_qword_above_4gb() {
        // Real RX 9070 XT qwMemorySize (WMI AdapterRAM would be 4293918720).
        let sample = "AMD Radeon RX 9070 XT\t17095983104\nAMD Radeon(TM) Graphics\t536870912\n";
        let devices = parse_wmi_gpu_lines(sample);
        assert_eq!(devices.len(), 2);
        assert_eq!(devices[0].vram_bytes, Some(17095983104));
        assert_eq!(devices[1].vram_bytes, Some(536870912));
        assert_eq!(sum_vram(&devices), Some(17095983104 + 536870912));
    }

    #[test]
    fn backend_hint_strings() {
        assert_eq!(BackendHint::Metal.as_str(), "metal");
        assert_eq!(BackendHint::Cuda.as_str(), "cuda");
        assert_eq!(BackendHint::Rocm.as_str(), "rocm");
        assert_eq!(BackendHint::Vulkan.as_str(), "vulkan");
    }

    #[test]
    #[cfg(target_os = "windows")]
    fn test_windows_dxgi_and_registry_gpu_query() {
        let dxgi_res = list_windows_dxgi_gpus();
        if let Some(ref devs) = dxgi_res {
            for dev in devs {
                assert!(!dev.name.is_empty(), "GPU name should not be empty");
                assert!(dev.vram_bytes.is_some() && dev.vram_bytes.unwrap() > 0, "VRAM should be > 0");
            }
        }

        let reg_res = list_windows_registry_gpus();
        if let Some(ref devs) = reg_res {
            for dev in devs {
                assert!(!dev.name.is_empty(), "Registry GPU name should not be empty");
                assert!(dev.vram_bytes.is_some() && dev.vram_bytes.unwrap() > 0, "Registry VRAM should be > 0");
            }
        }

        let wmi_res = list_windows_wmi_gpus();
        if let Some(ref devs) = wmi_res {
            for dev in devs {
                assert!(!dev.name.is_empty());
                assert!(dev.vram_bytes.is_some());
            }
        }
    }
}
