use super::{now, Device, Discovery, Host, MemoryView, Metric, SourceResult};
use std::collections::HashMap;
use std::time::Instant;
use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::{CloseHandle, FILETIME};
use windows::Win32::Graphics::Dxgi::{CreateDXGIFactory1, IDXGIFactory1, DXGI_ADAPTER_DESC1};
use windows::Win32::System::Performance::*;
use windows::Win32::System::ProcessStatus::{GetProcessMemoryInfo, PROCESS_MEMORY_COUNTERS};
use windows::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};
use windows::Win32::System::Threading::{GetActiveProcessorCount, ALL_PROCESSOR_GROUPS};
use windows::Win32::System::Threading::{
    GetProcessTimes, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_VM_READ,
};
use windows::Win32::System::WindowsProgramming::QueryUnbiasedInterruptTime;

pub fn process(pid: u32) -> Result<super::ProcessCounters, String> {
    let handle = unsafe {
        OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ,
            false,
            pid,
        )
    }
    .or_else(|_| unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) })
    .map_err(|e| {
        if e.code().0 as u32 == 0x80070057 {
            "process_exited"
        } else {
            "permission_denied"
        }
        .to_string()
    })?;
    let mut created = FILETIME::default();
    let mut exited = FILETIME::default();
    let mut kernel = FILETIME::default();
    let mut user = FILETIME::default();
    let times =
        unsafe { GetProcessTimes(handle, &mut created, &mut exited, &mut kernel, &mut user) };
    let mut memory = PROCESS_MEMORY_COUNTERS::default();
    memory.cb = std::mem::size_of::<PROCESS_MEMORY_COUNTERS>() as u32;
    let rss = unsafe {
        GetProcessMemoryInfo(
            handle,
            &mut memory,
            std::mem::size_of::<PROCESS_MEMORY_COUNTERS>() as u32,
        )
    }
    .ok()
    .map(|_| memory.WorkingSetSize as u64);
    unsafe {
        let _ = CloseHandle(handle);
    }
    times.map_err(|_| "permission_denied")?;
    let ticks = |t: FILETIME| ((t.dwHighDateTime as u64) << 32) | t.dwLowDateTime as u64;
    if ticks(exited) != 0 {
        return Err("process_exited".into());
    }
    let token = ticks(created);
    let epoch = token
        .checked_sub(116444736000000000)
        .ok_or("invalid_counter")?;
    let started = chrono::DateTime::from_timestamp(
        (epoch / 10000000) as i64,
        ((epoch % 10000000) * 100) as u32,
    )
    .ok_or("invalid_counter")?
    .to_rfc3339_opts(chrono::SecondsFormat::Micros, true);
    let cpu_ns = ticks(kernel)
        .checked_add(ticks(user))
        .and_then(|t| t.checked_mul(100))
        .ok_or("invalid_counter")?;
    Ok(super::ProcessCounters {
        token: token.to_string(),
        started,
        cpu_ns,
        rss,
    })
}

struct Query {
    handle: isize,
    counters: Vec<isize>,
}

impl Drop for Query {
    fn drop(&mut self) {
        unsafe {
            PdhCloseQuery(self.handle);
        }
    }
}

impl Query {
    fn new(paths: &[PCWSTR]) -> Option<Self> {
        let mut handle = 0;
        if unsafe { PdhOpenQueryW(PCWSTR::null(), 0, &mut handle) } != 0 {
            return None;
        }
        let mut result = Self {
            handle,
            counters: vec![],
        };
        for path in paths {
            let mut counter = 0;
            if unsafe { PdhAddEnglishCounterW(handle, *path, 0, &mut counter) } != 0 {
                return None;
            }
            result.counters.push(counter);
        }
        Some(result)
    }

    fn array(&self, index: usize) -> HashMap<String, u64> {
        let mut bytes = 0;
        let mut count = 0;
        unsafe {
            PdhGetFormattedCounterArrayW(
                self.counters[index],
                PDH_FMT_LARGE,
                &mut bytes,
                &mut count,
                None,
            );
        }
        if bytes == 0 || bytes > 1024 * 1024 {
            return HashMap::new();
        }
        // u64 storage ensures the native item array has the required alignment.
        let mut buffer = vec![0u64; (bytes as usize + 7) / 8];
        let ptr = buffer.as_mut_ptr().cast::<PDH_FMT_COUNTERVALUE_ITEM_W>();
        if unsafe {
            PdhGetFormattedCounterArrayW(
                self.counters[index],
                PDH_FMT_LARGE,
                &mut bytes,
                &mut count,
                Some(ptr),
            )
        } != 0
            || count as usize * std::mem::size_of::<PDH_FMT_COUNTERVALUE_ITEM_W>()
                > buffer.len() * 8
        {
            return HashMap::new();
        }
        let mut values = HashMap::new();
        for item in unsafe { std::slice::from_raw_parts(ptr, count as usize) } {
            if item.FmtValue.CStatus > 1 {
                continue;
            }
            let value = unsafe { item.FmtValue.Anonymous.largeValue };
            if value < 0 {
                continue;
            }
            if let Ok(name) = unsafe { item.szName.to_string() } {
                values.insert(name.to_lowercase(), value as u64);
            }
        }
        values
    }

    fn percentages(&self, index: usize) -> HashMap<String, f64> {
        let mut bytes = 0;
        let mut count = 0;
        unsafe {
            PdhGetFormattedCounterArrayW(
                self.counters[index],
                PDH_FMT_DOUBLE,
                &mut bytes,
                &mut count,
                None,
            );
        }
        if bytes == 0 || bytes > 1024 * 1024 {
            return HashMap::new();
        }
        let mut buffer = vec![0u64; (bytes as usize + 7) / 8];
        let ptr = buffer.as_mut_ptr().cast::<PDH_FMT_COUNTERVALUE_ITEM_W>();
        if unsafe {
            PdhGetFormattedCounterArrayW(
                self.counters[index],
                PDH_FMT_DOUBLE,
                &mut bytes,
                &mut count,
                Some(ptr),
            )
        } != 0
            || count as usize * std::mem::size_of::<PDH_FMT_COUNTERVALUE_ITEM_W>()
                > buffer.len() * 8
        {
            return HashMap::new();
        }
        let mut values = HashMap::new();
        for item in unsafe { std::slice::from_raw_parts(ptr, count as usize) } {
            if item.FmtValue.CStatus > 1 {
                continue;
            }
            if let Ok(name) = unsafe { item.szName.to_string() } {
                values.insert(name.to_lowercase(), unsafe {
                    item.FmtValue.Anonymous.doubleValue
                });
            }
        }
        values
    }
}

#[derive(Default)]
pub struct Collector {
    cpu: Option<Query>,
    gpu: Option<Query>,
    gpu_engine: Option<Query>,
    gpu_engine_previous: Option<Instant>,
    cpu_previous: Option<(Instant, String)>,
    last_cpu: Option<Metric>,
    cpu_count: Option<u32>,
    unbiased: Option<(u64, Instant)>,
    pub reset_process_baselines: bool,
}

impl Collector {
    pub fn host(&mut self) -> Host {
        let mut result = Host::unavailable();
        self.reset_process_baselines = false;
        let mut unbiased = 0;
        if unsafe { QueryUnbiasedInterruptTime(&mut unbiased) }.as_bool() {
            let instant = Instant::now();
            if let Some((old, prior)) = self.unbiased.replace((unbiased, instant)) {
                // The unbiased clock excludes sleep; allow the interrupt clock's coarse ticks.
                if unbiased.checked_sub(old).is_none_or(|ticks| {
                    (ticks as f64 / 1e7 - instant.duration_since(prior).as_secs_f64()).abs() > 0.05
                }) {
                    self.reset_process_baselines = true;
                    self.cpu_previous = None;
                    self.last_cpu = None;
                }
            }
        }
        let count = unsafe { GetActiveProcessorCount(ALL_PROCESSOR_GROUPS) };
        if self.cpu_count.is_some_and(|old| old != count) {
            self.reset_process_baselines = true;
            self.cpu_previous = None;
            self.last_cpu = None;
        }
        self.cpu_count = Some(count);
        result.logical_cpu_count = (count > 0).then_some(count);
        let mut memory = MEMORYSTATUSEX {
            dwLength: std::mem::size_of::<MEMORYSTATUSEX>() as u32,
            ..Default::default()
        };
        match unsafe { GlobalMemoryStatusEx(&mut memory) } {
            Ok(()) if memory.ullAvailPhys <= memory.ullTotalPhys => {
                result.memory.total =
                    Metric::bytes(memory.ullTotalPhys, "host", "win.physical.total");
                result.memory.available =
                    Metric::bytes(memory.ullAvailPhys, "host", "win.physical.available");
                result.memory.used = Metric::bytes(
                    memory.ullTotalPhys - memory.ullAvailPhys,
                    "host",
                    "win.physical.non_available",
                );
            }
            _ => {
                for m in [
                    &mut result.memory.total,
                    &mut result.memory.available,
                    &mut result.memory.used,
                ] {
                    *m = Metric::unavailable("bytes", "host", "source_error", "win.physical");
                }
            }
        }
        if let Some((instant, _)) = self.cpu_previous.as_ref() {
            if instant.elapsed().as_millis() < 200 {
                result.cpu = self.last_cpu.clone().unwrap_or_else(|| {
                    Metric::unavailable(
                        "percent_machine",
                        "host",
                        "baseline_required",
                        "win.pdh.cpu",
                    )
                });
                return result;
            }
        }
        if self.cpu.is_none() {
            self.cpu = Query::new(&[w!("\\Processor Information(_Total)\\% Processor Time")]);
        }
        let mut cpu = Metric::unavailable("percent_machine", "host", "source_error", "win.pdh.cpu");
        if let Some(query) = &self.cpu {
            if unsafe { PdhCollectQueryData(query.handle) } == 0 {
                let observed = now();
                let instant = Instant::now();
                if let Some((previous, start)) =
                    self.cpu_previous.replace((instant, observed.clone()))
                {
                    let mut value = PDH_FMT_COUNTERVALUE::default();
                    if unsafe {
                        PdhGetFormattedCounterValue(
                            query.counters[0],
                            PDH_FMT_DOUBLE,
                            None,
                            &mut value,
                        )
                    } == 0
                        && value.CStatus <= 1
                    {
                        let percent = unsafe { value.Anonymous.doubleValue };
                        if percent.is_finite()
                            && (0.0..=100.0).contains(&percent)
                            && super::valid_interval(
                                &start,
                                &observed,
                                instant.duration_since(previous).as_secs_f64(),
                            )
                        {
                            cpu.value = serde_json::Number::from_f64(percent);
                            cpu.status = "measured".into();
                            cpu.reason = None;
                            cpu.observed_at = observed.clone();
                            cpu.interval_start = Some(start);
                            cpu.interval_end = Some(observed);
                            cpu.duration_ms =
                                Some(instant.duration_since(previous).as_secs_f64() * 1000.0);
                        }
                    }
                } else {
                    cpu = Metric::unavailable(
                        "percent_machine",
                        "host",
                        "baseline_required",
                        "win.pdh.cpu",
                    );
                }
            } else {
                self.cpu_previous = None;
            }
        }
        if result.logical_cpu_count.is_none() {
            cpu = Metric::unavailable(
                "percent_machine",
                "host",
                "source_error",
                "win.logical_cpu_count",
            );
        }
        self.last_cpu = Some(cpu.clone());
        result.cpu = cpu;
        result
    }

    pub fn gpu(&mut self) -> SourceResult {
        let mut result = SourceResult::unavailable("gpu");
        let factory: IDXGIFactory1 = match unsafe { CreateDXGIFactory1() } {
            Ok(v) => v,
            Err(_) => {
                result.device_discovery = Some(Discovery {
                    status: "error".into(),
                    reason: Some("source_error".into()),
                    observed_at: now(),
                });
                return result;
            }
        };
        if self.gpu.is_none() {
            self.gpu = Query::new(&[
                w!("\\GPU Adapter Memory(*)\\Dedicated Usage"),
                w!("\\GPU Adapter Memory(*)\\Shared Usage"),
            ]);
        }
        let (dedicated, shared) = match &self.gpu {
            Some(q) if unsafe { PdhCollectQueryData(q.handle) } == 0 => (q.array(0), q.array(1)),
            _ => (HashMap::new(), HashMap::new()),
        };
        if self.gpu_engine.is_none() {
            self.gpu_engine = Query::new(&[w!("\\GPU Engine(*)\\Utilization Percentage")]);
            self.gpu_engine_previous = None;
        }
        let mut engines: HashMap<String, f64> = HashMap::new();
        let engine_reason = match &self.gpu_engine {
            None => Some("unsupported_counter"),
            Some(query) if unsafe { PdhCollectQueryData(query.handle) } != 0 => {
                self.gpu_engine_previous = None;
                Some("source_error")
            }
            Some(query) => {
                let interval = self.gpu_engine_previous.replace(Instant::now());
                if interval.is_some_and(|previous| previous.elapsed().as_millis() >= 200) {
                    for (name, value) in query.percentages(0) {
                        // Separate PID instances of one physical engine share its capacity.
                        if let Some(start) = name.find("luid_") {
                            let engine = name[start..].split('#').next().unwrap_or("");
                            *engines.entry(engine.to_string()).or_default() += value;
                        }
                    }
                    None
                } else {
                    Some("baseline_required")
                }
            }
        };
        let mut ambiguous_topology = false;
        for index in 0..33 {
            let adapter = match unsafe { factory.EnumAdapters1(index) } {
                Ok(v) => v,
                Err(e) if e.code() == windows::Win32::Graphics::Dxgi::DXGI_ERROR_NOT_FOUND => break,
                Err(_) => {
                    result.device_discovery = Some(Discovery {
                        status: "error".into(),
                        reason: Some("source_error".into()),
                        observed_at: now(),
                    });
                    return result;
                }
            };
            let mut desc = DXGI_ADAPTER_DESC1::default();
            if unsafe { adapter.GetDesc1(&mut desc) }.is_err() {
                result.device_discovery = Some(Discovery {
                    status: "error".into(),
                    reason: Some("source_error".into()),
                    observed_at: now(),
                });
                return result;
            }
            if desc.Flags & 2 != 0 {
                continue;
            } // Software adapter is not physical GPU memory.
            if result.devices.len() == 32 {
                result.device_discovery = Some(Discovery {
                    status: "error".into(),
                    reason: Some("enumeration_limit".into()),
                    observed_at: now(),
                });
                return result;
            }
            let luid = format!(
                "luid_0x{:08x}_0x{:08x}",
                desc.AdapterLuid.HighPart as u32, desc.AdapterLuid.LowPart
            );
            let prefix = format!("{luid}_phys_");
            let mut keys: Vec<String> = dedicated
                .keys()
                .chain(shared.keys())
                .filter(|k| {
                    k.strip_prefix(&prefix)
                        .is_some_and(|n| n.parse::<u32>().is_ok())
                })
                .cloned()
                .collect();
            keys.sort();
            keys.dedup();
            let ambiguous = keys.len() > 1 || desc.DedicatedSystemMemory > 0;
            ambiguous_topology |= desc.DedicatedSystemMemory > 0;
            if keys.is_empty() {
                keys.push(format!("{luid}_phys_0"));
            }
            for key in keys {
                if result.devices.len() == 32 {
                    result.device_discovery = Some(Discovery {
                        status: "error".into(),
                        reason: Some("enumeration_limit".into()),
                        observed_at: now(),
                    });
                    return result;
                }
                let device_key = format!("win:{key}");
                let mut views = vec![];
                if desc.DedicatedVideoMemory > 0 {
                    let mut total = Metric::bytes(
                        desc.DedicatedVideoMemory as u64,
                        "device",
                        "win.dxgi.dedicated_video_memory",
                    );
                    if ambiguous {
                        total = Metric::unavailable(
                            "bytes",
                            "device",
                            "ambiguous_pool",
                            "win.dxgi.dedicated_video_memory",
                        );
                    }
                    let used = dedicated
                        .get(&key)
                        .map(|n| Metric::bytes(*n, "device", "win.pdh.gpu_adapter.dedicated_usage"))
                        .unwrap_or_else(|| {
                            Metric::unavailable(
                                "bytes",
                                "device",
                                "unsupported_counter",
                                "win.pdh.gpu_adapter.dedicated_usage",
                            )
                        });
                    let free = match (
                        total.value.as_ref().and_then(|v| v.as_u64()),
                        used.value.as_ref().and_then(|v| v.as_u64()),
                    ) {
                        (Some(t), Some(u)) if u <= t && desc.DedicatedSystemMemory == 0 => {
                            Metric::bytes(t - u, "device", "win.dedicated.capacity_minus_usage")
                        }
                        _ => Metric::unavailable(
                            "bytes",
                            "device",
                            "ambiguous_pool",
                            "win.dedicated.capacity_minus_usage",
                        ),
                    };
                    views.push(MemoryView {
                        kind: "dedicated".into(),
                        pool_id: format!("{device_key}:dedicated"),
                        backing_pool_id: None,
                        total,
                        used,
                        free,
                    });
                }
                if desc.SharedSystemMemory > 0 {
                    views.push(MemoryView {
                        kind: "shared".into(),
                        pool_id: "host:ram".into(),
                        backing_pool_id: Some("host:ram".into()),
                        total: Metric::unavailable(
                            "bytes",
                            "device",
                            "unsupported_counter",
                            "win.shared",
                        ),
                        used: shared
                            .get(&key)
                            .map(|n| {
                                Metric::bytes(*n, "device", "win.pdh.gpu_adapter.shared_usage")
                            })
                            .unwrap_or_else(|| {
                                Metric::unavailable(
                                    "bytes",
                                    "device",
                                    "unsupported_counter",
                                    "win.pdh.gpu_adapter.shared_usage",
                                )
                            }),
                        free: Metric::unavailable(
                            "bytes",
                            "device",
                            "unsupported_counter",
                            "win.shared",
                        ),
                    });
                }
                result.devices.push(Device {
                    utilization: Some(match engine_reason {
                        Some(reason) => Metric::unavailable(
                            "percent_device",
                            "device",
                            reason,
                            "win.pdh.gpu_engine",
                        ),
                        None => {
                            let prefix = format!("{key}_eng_");
                            let values: Vec<f64> = engines
                                .iter()
                                .filter(|(name, _)| name.starts_with(&prefix))
                                .map(|(_, value)| *value)
                                .collect();
                            if values.is_empty() {
                                Metric::unavailable(
                                    "percent_device",
                                    "device",
                                    "unsupported_counter",
                                    "win.pdh.gpu_engine",
                                )
                            } else if values
                                .iter()
                                .any(|value| !value.is_finite() || !(0.0..=100.0).contains(value))
                            {
                                Metric::unavailable(
                                    "percent_device",
                                    "device",
                                    "invalid_counter",
                                    "win.pdh.gpu_engine",
                                )
                            } else {
                                Metric::device_percent(
                                    values.into_iter().fold(0.0, f64::max),
                                    "win.pdh.gpu_engine",
                                )
                            }
                        }
                    }),
                    device_key,
                    name: String::from_utf16_lossy(
                        &desc.Description[..desc
                            .Description
                            .iter()
                            .position(|c| *c == 0)
                            .unwrap_or(desc.Description.len())],
                    ),
                    vendor: format!("{:04x}", desc.VendorId),
                    memory_kind:
                        if desc.DedicatedSystemMemory > 0 && desc.DedicatedVideoMemory == 0 {
                            "unknown"
                        } else if views.len() == 2 {
                            "mixed"
                        } else if desc.DedicatedVideoMemory > 0 {
                            "dedicated"
                        } else {
                            "shared"
                        }
                        .into(),
                    inventory_device_id: None,
                    memory: views,
                });
            }
        }
        result.device_discovery = Some(Discovery {
            status: if ambiguous_topology {
                "error"
            } else {
                "measured"
            }
            .into(),
            reason: ambiguous_topology.then(|| "ambiguous_pool".into()),
            observed_at: now(),
        });
        result
    }
}
