use super::{now, Device, Discovery, Host, MemoryView, Metric, SourceResult};
use objc2_metal::{MTLCopyAllDevices, MTLDevice};
use std::collections::HashMap;
use std::ffi::{c_char, c_void};
use std::time::Instant;

#[link(name = "IOKit", kind = "framework")]
extern "C" {
    fn IOServiceMatching(name: *const c_char) -> *mut c_void;
    fn IOServiceGetMatchingServices(port: u32, matching: *mut c_void, iterator: *mut u32) -> i32;
    fn IOIteratorNext(iterator: u32) -> u32;
    fn IOObjectRelease(object: u32) -> i32;
    fn IORegistryEntryGetRegistryEntryID(entry: u32, id: *mut u64) -> i32;
    fn IORegistryEntryCreateCFProperty(
        entry: u32,
        key: *const c_void,
        allocator: *const c_void,
        options: u32,
    ) -> *const c_void;
}

#[link(name = "CoreFoundation", kind = "framework")]
extern "C" {
    fn CFStringCreateWithCString(
        allocator: *const c_void,
        string: *const c_char,
        encoding: u32,
    ) -> *const c_void;
    fn CFRelease(object: *const c_void);
    fn CFGetTypeID(object: *const c_void) -> usize;
    fn CFDictionaryGetTypeID() -> usize;
    fn CFNumberGetTypeID() -> usize;
    fn CFDictionaryGetValue(dictionary: *const c_void, key: *const c_void) -> *const c_void;
    fn CFNumberGetValue(number: *const c_void, kind: isize, value: *mut c_void) -> u8;
}

struct Cf(*const c_void);
impl Drop for Cf {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                CFRelease(self.0);
            }
        }
    }
}
struct Io(u32);
impl Drop for Io {
    fn drop(&mut self) {
        unsafe {
            IOObjectRelease(self.0);
        }
    }
}

fn cf_string(value: &'static [u8]) -> Cf {
    Cf(unsafe { CFStringCreateWithCString(std::ptr::null(), value.as_ptr().cast(), 0x08000100) })
}

/// IORegistry identities match Metal registryID; no name/order-based attribution.
fn gpu_statistics() -> Result<HashMap<u64, HashMap<&'static str, i64>>, &'static str> {
    let matching = unsafe { IOServiceMatching(b"IOAccelerator\0".as_ptr().cast()) };
    if matching.is_null() {
        return Err("source_error");
    }
    let mut iterator = 0;
    if unsafe { IOServiceGetMatchingServices(0, matching, &mut iterator) } != 0 {
        return Err("permission_denied");
    }
    let iterator = Io(iterator);
    let statistics_key = cf_string(b"PerformanceStatistics\0");
    let mut result = HashMap::new();
    if statistics_key.0.is_null() {
        return Err("source_error");
    }
    for _ in 0..32 {
        let entry = unsafe { IOIteratorNext(iterator.0) };
        if entry == 0 {
            return Ok(result);
        }
        let entry = Io(entry);
        let mut id = 0;
        if unsafe { IORegistryEntryGetRegistryEntryID(entry.0, &mut id) } != 0 {
            continue;
        }
        let statistics = Cf(unsafe {
            IORegistryEntryCreateCFProperty(entry.0, statistics_key.0, std::ptr::null(), 0)
        });
        if statistics.0.is_null() || unsafe { CFGetTypeID(statistics.0) != CFDictionaryGetTypeID() }
        {
            continue;
        }
        let mut counters = HashMap::new();
        for (name, bytes) in [
            ("utilization", b"Device Utilization %\0".as_slice()),
            ("used", b"In use system memory\0".as_slice()),
        ] {
            let key = cf_string(bytes);
            if key.0.is_null() {
                continue;
            }
            let number = unsafe { CFDictionaryGetValue(statistics.0, key.0) };
            let mut value: i64 = 0;
            if !number.is_null()
                && unsafe {
                    CFGetTypeID(number) == CFNumberGetTypeID()
                        && CFNumberGetValue(number, 4, (&mut value as *mut i64).cast()) != 0
                }
            {
                counters.insert(name, value);
            }
        }
        result.insert(id, counters);
    }
    Err("enumeration_limit")
}

// libc exposes the sampling calls but not this send-right cleanup function.
extern "C" {
    fn mach_port_deallocate(
        task: libc::mach_port_t,
        name: libc::mach_port_t,
    ) -> libc::kern_return_t;
}

#[derive(Default)]
pub struct Collector {
    previous: Option<([u32; 4], Instant, String)>,
    last_cpu: Option<Metric>,
    pub reset_process_baselines: bool,
    logical_count: Option<u32>,
}

fn sysctl<T: Default>(name: &[u8]) -> Option<T> {
    let mut value = T::default();
    let mut size = std::mem::size_of::<T>();
    let status = unsafe {
        libc::sysctlbyname(
            name.as_ptr().cast(),
            (&mut value as *mut T).cast(),
            &mut size,
            std::ptr::null_mut(),
            0,
        )
    };
    (status == 0 && size == std::mem::size_of::<T>()).then_some(value)
}

pub fn process(pid: u32) -> Result<super::ProcessCounters, String> {
    if pid > i32::MAX as u32 {
        return Err("process_exited".into());
    }
    fn info<T>(pid: i32, flavor: i32) -> Result<T, String> {
        let mut value: T = unsafe { std::mem::zeroed() };
        let size = std::mem::size_of::<T>();
        let got = unsafe {
            libc::proc_pidinfo(pid, flavor, 0, (&mut value as *mut T).cast(), size as i32)
        };
        if got != size as i32 {
            return Err(
                if std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) {
                    "process_exited"
                } else {
                    "permission_denied"
                }
                .into(),
            );
        }
        Ok(value)
    }
    let first: libc::proc_bsdinfo = info(pid as i32, libc::PROC_PIDTBSDINFO)?;
    if first.pbi_status == libc::SZOMB {
        return Err("process_exited".into());
    }
    let task: Result<libc::proc_taskinfo, _> = info(pid as i32, libc::PROC_PIDTASKINFO);
    let last: libc::proc_bsdinfo = info(pid as i32, libc::PROC_PIDTBSDINFO)?;
    if last.pbi_status == libc::SZOMB {
        return Err("process_exited".into());
    }
    if (first.pbi_start_tvsec, first.pbi_start_tvusec)
        != (last.pbi_start_tvsec, last.pbi_start_tvusec)
    {
        return Err("process_identity_mismatch".into());
    }
    let started = chrono::DateTime::from_timestamp(
        first.pbi_start_tvsec as i64,
        first.pbi_start_tvusec as u32 * 1000,
    )
    .ok_or("invalid_counter")?
    .to_rfc3339_opts(chrono::SecondsFormat::Micros, true);
    let task = task?;
    Ok(super::ProcessCounters {
        token: format!("{}:{}", first.pbi_start_tvsec, first.pbi_start_tvusec),
        started,
        cpu_ns: task
            .pti_total_user
            .checked_add(task.pti_total_system)
            .ok_or("invalid_counter")?,
        rss: Some(task.pti_resident_size),
    })
}

impl Collector {
    #[allow(deprecated)] // Match libc's Mach sampling types without a second binding dependency.
    pub fn host(&mut self) -> Host {
        self.reset_process_baselines = false;
        let mut result = Host::unavailable();
        result.logical_cpu_count = sysctl::<u32>(b"hw.logicalcpu\0").filter(|n| *n > 0);
        if self.previous.is_some() && self.logical_count != result.logical_cpu_count {
            self.previous = None;
            self.last_cpu = None;
            self.reset_process_baselines = true;
        }
        self.logical_count = result.logical_cpu_count;
        let port = unsafe { libc::mach_host_self() };
        let total = sysctl::<u64>(b"hw.memsize\0");
        let page_size = unsafe { libc::vm_page_size };
        let mut vm: libc::vm_statistics64 = unsafe { std::mem::zeroed() };
        let mut count = libc::HOST_VM_INFO64_COUNT;
        let vm_ok = unsafe {
            page_size > 0
                && libc::host_statistics64(
                    port,
                    libc::HOST_VM_INFO64,
                    (&mut vm as *mut libc::vm_statistics64).cast(),
                    &mut count,
                ) == 0
        };
        if let Some(total) = total {
            result.memory.total = Metric::bytes(total, "host", "mac.hw.memsize");
            if vm_ok {
                let free = (vm.free_count as u64).checked_mul(page_size as u64);
                let available = crate::hw::macos_available_bytes(
                    vm.free_count as u64,
                    vm.inactive_count as u64,
                    page_size as u64,
                    total,
                );
                let used =
                    (vm.active_count as u64 + vm.wire_count as u64).checked_mul(page_size as u64);
                for (target, value, name) in [
                    (&mut result.memory.free, free, "mac.vm.free"),
                    (
                        &mut result.memory.available,
                        available,
                        "mac.vm.free_plus_inactive",
                    ),
                    (&mut result.memory.used, used, "mac.vm.active_plus_wired"),
                ] {
                    *target = match value {
                        Some(v) if v <= total => Metric::bytes(v, "host", name),
                        _ => Metric::unavailable("bytes", "host", "invalid_counter", name),
                    };
                }
            } else {
                for m in [
                    &mut result.memory.free,
                    &mut result.memory.used,
                    &mut result.memory.available,
                ] {
                    *m = Metric::unavailable("bytes", "host", "source_error", "mac.vm");
                }
            }
        } else {
            result.memory.total =
                Metric::unavailable("bytes", "host", "source_error", "mac.hw.memsize");
        }
        let mut ticks = [0u32; 4];
        let mut tick_count = libc::HOST_CPU_LOAD_INFO_COUNT;
        let cpu_ok = unsafe {
            libc::host_statistics(
                port,
                libc::HOST_CPU_LOAD_INFO,
                ticks.as_mut_ptr().cast(),
                &mut tick_count,
            ) == 0
        };
        unsafe {
            mach_port_deallocate(libc::mach_task_self(), port);
        }
        if self
            .previous
            .as_ref()
            .is_some_and(|(_, t, _)| t.elapsed().as_millis() < 200)
        {
            result.cpu = self.last_cpu.clone().unwrap_or_else(|| {
                Metric::unavailable(
                    "percent_machine",
                    "host",
                    "baseline_required",
                    "mac.host_cpu",
                )
            });
            return result;
        }
        let stamp = now();
        let instant = Instant::now();
        let mut cpu = Metric::unavailable(
            "percent_machine",
            "host",
            if cpu_ok {
                "baseline_required"
            } else {
                "source_error"
            },
            "mac.host_cpu",
        );
        if cpu_ok {
            if let Some((old, prior, start)) =
                self.previous.replace((ticks, instant, stamp.clone()))
            {
                if !super::valid_interval(
                    &start,
                    &stamp,
                    instant.duration_since(prior).as_secs_f64(),
                ) {
                    self.reset_process_baselines = true;
                }
                let diffs: Option<Vec<u32>> = ticks
                    .iter()
                    .zip(old)
                    .map(|(a, b)| a.checked_sub(b))
                    .collect();
                if let Some(d) = diffs {
                    let total = d.iter().map(|v| *v as u64).sum::<u64>();
                    if total > 0
                        && super::valid_interval(
                            &start,
                            &stamp,
                            instant.duration_since(prior).as_secs_f64(),
                        )
                    {
                        cpu.value = serde_json::Number::from_f64(
                            100.0 * (total - d[2] as u64) as f64 / total as f64,
                        );
                        cpu.status = "measured".into();
                        cpu.reason = None;
                        cpu.observed_at = stamp.clone();
                        cpu.interval_start = Some(start);
                        cpu.interval_end = Some(stamp);
                        cpu.duration_ms =
                            Some(instant.duration_since(prior).as_secs_f64() * 1000.0);
                    }
                }
            }
        } else {
            self.previous = None;
        }
        if result.logical_cpu_count.is_none() {
            cpu = Metric::unavailable(
                "percent_machine",
                "host",
                "source_error",
                "mac.hw.logicalcpu",
            );
        }
        self.last_cpu = Some(cpu.clone());
        result.cpu = cpu;
        result
    }

    pub fn gpu(&mut self) -> SourceResult {
        objc2::rc::autoreleasepool(|_| {
            let mut result = SourceResult::unavailable("gpu");
            let devices = MTLCopyAllDevices();
            let statistics = gpu_statistics();
            for i in 0..devices.count().min(32) {
                let device = devices.objectAtIndex(i);
                let shared = device.hasUnifiedMemory();
                let key = format!("metal:{:x}", device.registryID());
                let kind = if shared { "shared" } else { "dedicated" };
                let missing = Metric::unavailable(
                    "bytes",
                    "device",
                    "unsupported_counter",
                    "mac.metal.device_wide_memory",
                );
                let counters = statistics
                    .as_ref()
                    .ok()
                    .and_then(|all| all.get(&device.registryID()));
                let utilization = match counters.and_then(|c| c.get("utilization")) {
                    Some(value) => {
                        Metric::device_percent(*value as f64, "mac.iokit.device_utilization")
                    }
                    None => Metric::unavailable(
                        "percent_device",
                        "device",
                        statistics
                            .as_ref()
                            .err()
                            .copied()
                            .unwrap_or("unsupported_counter"),
                        "mac.iokit.device_utilization",
                    ),
                };
                let used = if shared {
                    match counters.and_then(|c| c.get("used")) {
                        Some(value) if *value >= 0 => {
                            Metric::bytes(*value as u64, "device", "mac.iokit.in_use_system_memory")
                        }
                        Some(_) => Metric::unavailable(
                            "bytes",
                            "device",
                            "invalid_counter",
                            "mac.iokit.in_use_system_memory",
                        ),
                        None => Metric::unavailable(
                            "bytes",
                            "device",
                            statistics
                                .as_ref()
                                .err()
                                .copied()
                                .unwrap_or("unsupported_counter"),
                            "mac.iokit.in_use_system_memory",
                        ),
                    }
                } else {
                    missing.clone()
                };
                result.devices.push(Device {
                    utilization: Some(utilization),
                    device_key: key.clone(),
                    name: device.name().to_string(),
                    vendor: "unknown".into(),
                    memory_kind: kind.into(),
                    inventory_device_id: None,
                    memory: vec![MemoryView {
                        kind: kind.into(),
                        pool_id: if shared {
                            "host:ram".into()
                        } else {
                            format!("{key}:dedicated")
                        },
                        backing_pool_id: shared.then(|| "host:ram".into()),
                        total: missing.clone(),
                        used,
                        free: missing,
                    }],
                });
            }
            result.device_discovery = Some(Discovery {
                status: if devices.count() > 32 {
                    "error"
                } else {
                    "measured"
                }
                .into(),
                reason: (devices.count() > 32).then(|| "enumeration_limit".into()),
                observed_at: now(),
            });
            result
        })
    }
}
