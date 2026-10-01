//! Scoped resource observations. No inference capacity/budget policy belongs here.
use chrono::{DateTime, SecondsFormat, Utc};
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::Number;
use std::collections::{HashMap, HashSet};
use std::time::Instant;

#[cfg(target_os = "macos")]
mod macos;
#[cfg(target_os = "windows")]
mod windows;

const MAX_FRAME: usize = 65536;

pub fn now() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Micros, true)
}

pub fn valid_interval(start: &str, end: &str, seconds: f64) -> bool {
    let (Ok(a), Ok(b)) = (
        DateTime::parse_from_rfc3339(start),
        DateTime::parse_from_rfc3339(end),
    ) else {
        return false;
    };
    let wall = (b - a).num_microseconds().unwrap_or(0) as f64 / 1e6;
    (0.2..=86400.0).contains(&seconds)
        && wall > 0.0
        && (wall - seconds).abs() <= (seconds * 0.05).max(0.05)
}

fn process_percent(cpu_ns: u64, seconds: f64, cores: u32) -> Option<f64> {
    if !seconds.is_finite() || seconds <= 0.0 || cores == 0 {
        return None;
    }
    let value = 100.0 * cpu_ns as f64 / (seconds * 1e9 * cores as f64);
    (value.is_finite() && (0.0..=100.0).contains(&value)).then_some(value)
}

pub struct ProcessCounters {
    pub token: String,
    pub started: String,
    pub cpu_ns: u64,
    pub rss: Option<u64>,
}

pub fn process_counters(pid: u32) -> Result<ProcessCounters, String> {
    #[cfg(target_os = "windows")]
    {
        windows::process(pid)
    }
    #[cfg(target_os = "macos")]
    {
        macos::process(pid)
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        let _ = pid;
        Err("unsupported_counter".into())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Metric {
    pub value: Option<Number>,
    pub unit: String,
    pub scope: String,
    pub status: String,
    pub reason: Option<String>,
    pub detail: Option<String>,
    pub observed_at: String,
    pub interval_start: Option<String>,
    pub interval_end: Option<String>,
    pub duration_ms: Option<f64>,
    pub source: String,
}

impl Metric {
    pub fn unavailable(unit: &str, scope: &str, reason: &str, source: &str) -> Self {
        Self {
            value: None,
            unit: unit.into(),
            scope: scope.into(),
            status: if reason == "unsupported_counter" {
                "unsupported"
            } else if reason == "baseline_required" {
                "initializing"
            } else {
                "error"
            }
            .into(),
            reason: Some(reason.into()),
            detail: None,
            observed_at: now(),
            interval_start: None,
            interval_end: None,
            duration_ms: None,
            source: source.into(),
        }
    }

    pub fn bytes(value: u64, scope: &str, source: &str) -> Self {
        let mut m = Self::unavailable("bytes", scope, "unsupported_counter", source);
        m.value = Some(value.into());
        m.status = "measured".into();
        m.reason = None;
        m
    }

    pub fn device_percent(value: f64, source: &str) -> Self {
        let mut metric = Self::unavailable("percent_device", "device", "invalid_counter", source);
        if value.is_finite() && (0.0..=100.0).contains(&value) {
            metric.value = serde_json::Number::from_f64(value);
            metric.status = "measured".into();
            metric.reason = None;
        }
        metric
    }

    fn validate(&self) -> Result<(), String> {
        if !matches!(self.scope.as_str(), "host" | "process" | "device")
            || !matches!(
                self.unit.as_str(),
                "bytes" | "percent_machine" | "percent_device"
            )
            || !matches!(
                self.status.as_str(),
                "measured" | "initializing" | "unsupported" | "error"
            )
        {
            return Err("invalid metric scope, unit or status".into());
        }
        if self.observed_at.len() > 40
            || !self.observed_at.ends_with('Z')
            || DateTime::parse_from_rfc3339(&self.observed_at).is_err()
        {
            return Err("invalid observation timestamp".into());
        }
        if self
            .detail
            .as_ref()
            .is_some_and(|s| s.chars().count() > 256)
            || self.source.is_empty()
            || self.source.len() > 256
        {
            return Err("invalid diagnostic length".into());
        }
        if self.status == "measured" {
            let n = self
                .value
                .as_ref()
                .ok_or("measured metric requires value")?;
            if self.reason.is_some() {
                return Err("measured metric cannot have failure reason".into());
            }
            if self.unit == "bytes" {
                if n.as_u64().is_none() {
                    return Err("bytes must be unsigned integer".into());
                }
                if self.interval_start.is_some()
                    || self.interval_end.is_some()
                    || self.duration_ms.is_some()
                {
                    return Err("memory has no CPU interval".into());
                }
            } else {
                if !n
                    .as_f64()
                    .is_some_and(|v| v.is_finite() && (0.0..=100.0).contains(&v))
                {
                    return Err("invalid CPU percentage".into());
                }
                // Driver GPU gauges do not expose a sampling interval. Do not invent one.
                if self.unit == "percent_device"
                    && self.scope == "device"
                    && self.interval_start.is_none()
                    && self.interval_end.is_none()
                    && self.duration_ms.is_none()
                {
                    return Ok(());
                }
                let start = DateTime::parse_from_rfc3339(
                    self.interval_start
                        .as_deref()
                        .ok_or("missing CPU interval")?,
                )
                .map_err(|_| "invalid interval")?;
                let end = DateTime::parse_from_rfc3339(
                    self.interval_end.as_deref().ok_or("missing CPU interval")?,
                )
                .map_err(|_| "invalid interval")?;
                if end <= start
                    || self.interval_end.as_deref() != Some(self.observed_at.as_str())
                    || !self
                        .interval_start
                        .as_deref()
                        .is_some_and(|s| s.ends_with('Z'))
                    || !self
                        .duration_ms
                        .is_some_and(|v| v.is_finite() && (200.0..=86400000.0).contains(&v))
                {
                    return Err("invalid CPU interval".into());
                }
            }
        } else {
            let reason = self
                .reason
                .as_deref()
                .ok_or("unavailable metric requires reason")?;
            if !matches!(
                reason,
                "baseline_required"
                    | "interval_too_short"
                    | "counter_reset"
                    | "clock_discontinuity"
                    | "unsupported_counter"
                    | "permission_denied"
                    | "process_exited"
                    | "process_identity_mismatch"
                    | "identity_unverified"
                    | "device_removed"
                    | "ambiguous_pool"
                    | "timeout"
                    | "source_busy"
                    | "source_quarantined"
                    | "invalid_counter"
                    | "native_unavailable"
                    | "enumeration_limit"
                    | "source_error"
            ) || self.value.is_some()
                || self.interval_start.is_some()
                || self.interval_end.is_some()
                || self.duration_ms.is_some()
            {
                return Err("invalid unavailable metric".into());
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Memory {
    pub pool_id: String,
    pub total: Metric,
    pub used: Metric,
    pub free: Metric,
    pub available: Metric,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Host {
    pub logical_cpu_count: Option<u32>,
    pub cpu: Metric,
    pub memory: Memory,
}

impl Host {
    fn unavailable() -> Self {
        let m = Metric::unavailable("bytes", "host", "unsupported_counter", "native.host");
        Self {
            logical_cpu_count: None,
            cpu: Metric::unavailable(
                "percent_machine",
                "host",
                "unsupported_counter",
                "native.host",
            ),
            memory: Memory {
                pool_id: "host:ram".into(),
                total: m.clone(),
                used: m.clone(),
                free: m.clone(),
                available: m,
            },
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Discovery {
    pub status: String,
    pub reason: Option<String>,
    pub observed_at: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryView {
    pub kind: String,
    pub pool_id: String,
    pub backing_pool_id: Option<String>,
    pub total: Metric,
    pub used: Metric,
    pub free: Metric,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Device {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub utilization: Option<Metric>,
    pub device_key: String,
    pub name: String,
    pub vendor: String,
    pub memory_kind: String,
    pub inventory_device_id: Option<String>,
    pub memory: Vec<MemoryView>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProcessSelection {
    pub pid: u32,
    pub role: String,
    pub start_token: String,
    pub started_at: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProcessReading {
    pub pid: u32,
    pub role: String,
    pub start_token: String,
    pub started_at: String,
    pub node_id: String,
    pub lifetime_id: String,
    pub state: String,
    pub identity_verified: bool,
    pub cpu: Metric,
    pub rss: Metric,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    version: u32,
    generation: String,
    request_id: u64,
    node_id: String,
    managed_processes: Vec<ProcessSelection>,
    reply_budget_bytes: usize,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceResult {
    pub host: Option<Host>,
    pub devices: Vec<Device>,
    pub device_discovery: Option<Discovery>,
    pub processes: Vec<ProcessReading>,
}

impl SourceResult {
    fn unavailable(source: &str) -> Self {
        Self {
            host: (source == "host").then(Host::unavailable),
            devices: vec![],
            device_discovery: (source == "gpu").then(|| Discovery {
                status: "unsupported".into(),
                reason: Some("unsupported_counter".into()),
                observed_at: now(),
            }),
            processes: vec![],
        }
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.devices.len() > 32 || self.processes.len() > 8 {
            return Err("source collection limit".into());
        }
        if let Some(h) = &self.host {
            if h.memory.pool_id != "host:ram" || h.logical_cpu_count == Some(0) {
                return Err("invalid host identity".into());
            }
            validate_metric(&h.cpu, "percent_machine", "host")?;
            for m in [
                &h.memory.total,
                &h.memory.used,
                &h.memory.free,
                &h.memory.available,
            ] {
                validate_metric(m, "bytes", "host")?;
            }
            if let Some(total) = h.memory.total.value.as_ref().and_then(|v| v.as_u64()) {
                for m in [&h.memory.used, &h.memory.free, &h.memory.available] {
                    if m.value
                        .as_ref()
                        .and_then(|v| v.as_u64())
                        .is_some_and(|v| v > total)
                    {
                        return Err("host counter exceeds physical memory".into());
                    }
                }
            }
        }
        if let Some(d) = &self.device_discovery {
            if !matches!(d.status.as_str(), "measured" | "error" | "unsupported")
                || (d.status == "measured") != d.reason.is_none()
                || !d.observed_at.ends_with('Z')
                || DateTime::parse_from_rfc3339(&d.observed_at).is_err()
            {
                return Err("invalid discovery status".into());
            }
            if let Some(reason) = &d.reason {
                Metric::unavailable("bytes", "device", reason, "native.discovery").validate()?;
            }
        }
        let mut seen = HashSet::new();
        let mut pools = HashMap::new();
        for d in &self.devices {
            if let Some(utilization) = &d.utilization {
                validate_metric(utilization, "percent_device", "device")?;
            }
            if d.device_key.is_empty()
                || d.device_key.len() > 128
                || !seen.insert(&d.device_key)
                || d.name.chars().count() > 256
                || d.memory.len() > 2
                || !matches!(
                    d.memory_kind.as_str(),
                    "dedicated" | "shared" | "mixed" | "unknown"
                )
            {
                return Err("invalid device".into());
            }
            if d.vendor.len() > 128 || d.inventory_device_id.as_ref().is_some_and(|v| v.len() > 64)
            {
                return Err("invalid device metadata".into());
            }
            let mut kinds = HashSet::new();
            for v in &d.memory {
                if !matches!(v.kind.as_str(), "shared" | "dedicated")
                    || !kinds.insert(&v.kind)
                    || v.pool_id.is_empty()
                    || v.pool_id.len() > 256
                    || (v.kind == "shared"
                        && (v.pool_id != "host:ram"
                            || v.backing_pool_id.as_deref() != Some("host:ram")))
                    || (v.kind == "dedicated"
                        && (v.pool_id == "host:ram" || v.backing_pool_id.is_some()))
                {
                    return Err("invalid memory view".into());
                }
                let metadata = (&v.kind, &v.backing_pool_id);
                if pools
                    .insert(&v.pool_id, metadata)
                    .is_some_and(|old| old != metadata)
                {
                    return Err("conflicting memory pool definition".into());
                }
                for m in [&v.total, &v.used, &v.free] {
                    validate_metric(m, "bytes", "device")?;
                }
            }
        }
        let mut seen = HashSet::new();
        for p in &self.processes {
            if p.pid == 0
                || !seen.insert(p.pid)
                || !matches!(p.role.as_str(), "master" | "worker")
                || p.node_id.is_empty()
                || p.node_id.chars().count() > 128
                || p.start_token.is_empty()
                || p.start_token.len() > 128
                || !matches!(p.state.as_str(), "running" | "exited" | "unavailable")
                || !p.started_at.ends_with('Z')
                || DateTime::parse_from_rfc3339(&p.started_at).is_err()
                || p.lifetime_id != format!("{}:{}:{}", p.node_id, p.pid, p.started_at)
            {
                return Err("invalid process identity".into());
            }
            validate_metric(&p.cpu, "percent_machine", "process")?;
            validate_metric(&p.rss, "bytes", "process")?;
            if p.identity_verified != (p.state == "running")
                || (!p.identity_verified && (p.cpu.value.is_some() || p.rss.value.is_some()))
            {
                return Err("unverified or ended process has running measurements".into());
            }
        }
        Ok(())
    }
}

fn validate_metric(metric: &Metric, unit: &str, scope: &str) -> Result<(), String> {
    metric.validate()?;
    if metric.unit != unit || metric.scope != scope {
        return Err("metric has wrong owner/unit".into());
    }
    Ok(())
}

pub fn validate_source_json(source: &str, payload: &str) -> Result<(), String> {
    if payload.len() > MAX_FRAME {
        return Err("source payload exceeds limit".into());
    }
    let result: SourceResult = serde_json::from_str(payload).map_err(|e| e.to_string())?;
    if (source == "host"
        && (result.host.is_none()
            || result.device_discovery.is_some()
            || !result.devices.is_empty()))
        || (source == "gpu"
            && (result.host.is_some()
                || result.device_discovery.is_none()
                || !result.processes.is_empty()))
        || !matches!(source, "host" | "gpu")
    {
        return Err("source payload has wrong scope".into());
    }
    result.validate()
}

#[pyclass(name = "_UsageCollector")]
pub struct UsageCollector {
    source: String,
    processes: HashMap<u32, (String, u64, Instant, String, Metric, u32)>,
    devices: HashMap<String, Device>,
    #[cfg(target_os = "windows")]
    platform: windows::Collector,
    #[cfg(target_os = "macos")]
    platform: macos::Collector,
}

#[pymethods]
impl UsageCollector {
    #[new]
    fn new(source: &str) -> PyResult<Self> {
        if !matches!(source, "host" | "gpu") {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "source must be host or gpu",
            ));
        }
        Ok(Self {
            source: source.into(),
            processes: HashMap::new(),
            devices: HashMap::new(),
            #[cfg(target_os = "windows")]
            platform: windows::Collector::default(),
            #[cfg(target_os = "macos")]
            platform: macos::Collector::default(),
        })
    }

    fn collect_json(&mut self, py: Python<'_>, request_json: &str) -> PyResult<String> {
        if request_json.len() > MAX_FRAME {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "request exceeds 64 KiB",
            ));
        }
        let request: Request = serde_json::from_str(request_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let result = py.allow_threads(|| self.collect(request));
        result.map_err(pyo3::exceptions::PyValueError::new_err)
    }
}

impl UsageCollector {
    fn collect(&mut self, request: Request) -> Result<String, String> {
        if request.version != 1
            || request.generation.len() != 32
            || !request
                .generation
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || request.request_id == 0
            || request.node_id.is_empty()
            || request.node_id.chars().count() > 128
            || request.managed_processes.len() > 8
            || !(1..=MAX_FRAME).contains(&request.reply_budget_bytes)
            || (self.source == "gpu" && !request.managed_processes.is_empty())
        {
            return Err("invalid request metadata".into());
        }
        let mut pids = HashSet::new();
        for p in &request.managed_processes {
            if p.pid == 0
                || !pids.insert(p.pid)
                || !matches!(p.role.as_str(), "master" | "worker")
                || p.start_token.is_empty()
                || p.start_token.len() > 128
                || DateTime::parse_from_rfc3339(&p.started_at).is_err()
            {
                return Err("invalid process selection".into());
            }
        }
        let mut result = SourceResult::unavailable(&self.source);
        #[cfg(any(target_os = "windows", target_os = "macos"))]
        if self.source == "host" {
            result.host = Some(self.platform.host());
            if self.platform.reset_process_baselines {
                self.processes.clear();
            }
        }
        if self.source == "gpu" {
            #[cfg(any(target_os = "windows", target_os = "macos"))]
            {
                result = self.platform.gpu();
            }
            observe_devices(&mut self.devices, &mut result);
        }
        self.processes.retain(|pid, _| pids.contains(pid));
        for p in request.managed_processes {
            let mut reading = ProcessReading {
                lifetime_id: format!("{}:{}:{}", request.node_id, p.pid, p.started_at),
                node_id: request.node_id.clone(),
                pid: p.pid,
                role: p.role,
                start_token: p.start_token,
                started_at: p.started_at,
                state: "unavailable".into(),
                identity_verified: false,
                cpu: Metric::unavailable(
                    "percent_machine",
                    "process",
                    "unsupported_counter",
                    "native.process",
                ),
                rss: Metric::unavailable(
                    "bytes",
                    "process",
                    "unsupported_counter",
                    "native.process",
                ),
            };
            match process_counters(reading.pid) {
                Ok(raw)
                    if raw.token == reading.start_token && raw.started == reading.started_at =>
                {
                    reading.state = "running".into();
                    reading.identity_verified = true;
                    reading.rss = raw
                        .rss
                        .map(|v| Metric::bytes(v, "process", "native.process.resident"))
                        .unwrap_or_else(|| {
                            Metric::unavailable(
                                "bytes",
                                "process",
                                "permission_denied",
                                "native.process.resident",
                            )
                        });
                    reading.cpu = Metric::unavailable(
                        "percent_machine",
                        "process",
                        "baseline_required",
                        "native.process.cpu",
                    );
                    let instant = Instant::now();
                    let stamp = now();
                    let cores = result
                        .host
                        .as_ref()
                        .and_then(|h| h.logical_cpu_count)
                        .unwrap_or(0);
                    let mut advance = true;
                    if let Some((token, ticks, last, start, previous_metric, previous_cores)) =
                        self.processes.get(&reading.pid)
                    {
                        let elapsed = instant.duration_since(*last).as_secs_f64();
                        if token == &raw.token && cores == *previous_cores && elapsed < 0.2 {
                            reading.cpu = previous_metric.clone();
                            advance = false;
                        } else if token == &raw.token
                            && cores > 0
                            && cores == *previous_cores
                            && valid_interval(start, &stamp, elapsed)
                        {
                            if let Some(delta) = raw.cpu_ns.checked_sub(*ticks) {
                                if let Some(percent) = process_percent(delta, elapsed, cores) {
                                    reading.cpu.value = serde_json::Number::from_f64(percent);
                                    reading.cpu.status = "measured".into();
                                    reading.cpu.reason = None;
                                    reading.cpu.observed_at = stamp.clone();
                                    reading.cpu.interval_start = Some(start.clone());
                                    reading.cpu.interval_end = Some(stamp.clone());
                                    reading.cpu.duration_ms = Some(elapsed * 1000.0);
                                }
                            }
                        }
                    }
                    if advance {
                        self.processes.insert(
                            reading.pid,
                            (
                                raw.token,
                                raw.cpu_ns,
                                instant,
                                stamp,
                                reading.cpu.clone(),
                                cores,
                            ),
                        );
                    }
                }
                raw => {
                    self.processes.remove(&reading.pid);
                    let reason = match raw {
                        Ok(_) => "process_identity_mismatch".into(),
                        Err(reason) => reason,
                    };
                    if matches!(
                        reason.as_str(),
                        "process_exited" | "process_identity_mismatch"
                    ) {
                        reading.state = "exited".into();
                    }
                    reading.cpu = Metric::unavailable(
                        "percent_machine",
                        "process",
                        &reason,
                        "native.process.cpu",
                    );
                    reading.rss =
                        Metric::unavailable("bytes", "process", &reason, "native.process.resident");
                }
            }
            result.processes.push(reading);
        }
        result.validate()?;
        let encoded = serde_json::to_string(&result).map_err(|e| e.to_string())?;
        if encoded.len() > MAX_FRAME {
            return Err("native source payload exceeds 64 KiB".into());
        }
        Ok(encoded)
    }
}

fn observe_devices(previous: &mut HashMap<String, Device>, result: &mut SourceResult) {
    let complete = result
        .device_discovery
        .as_ref()
        .is_some_and(|d| d.status == "measured");
    let current: HashMap<String, Device> = result
        .devices
        .iter()
        .map(|d| (d.device_key.clone(), d.clone()))
        .collect();
    for (key, old) in previous.iter() {
        if current.contains_key(key) {
            continue;
        }
        if result.devices.len() == 32 {
            result.device_discovery = Some(Discovery {
                status: "error".into(),
                reason: Some("enumeration_limit".into()),
                observed_at: now(),
            });
            break;
        }
        let mut unavailable = old.clone();
        let reason = if complete {
            "device_removed"
        } else {
            "source_error"
        };
        if let Some(metric) = &mut unavailable.utilization {
            *metric = Metric::unavailable("percent_device", "device", reason, &metric.source);
        }
        for view in &mut unavailable.memory {
            for metric in [&mut view.total, &mut view.used, &mut view.free] {
                *metric = Metric::unavailable("bytes", "device", reason, &metric.source);
            }
        }
        result.devices.push(unavailable);
    }
    if complete {
        *previous = current;
    } else {
        let old = std::mem::replace(previous, current);
        for (key, device) in old {
            if previous.len() == 32 {
                break;
            }
            previous.entry(key).or_insert(device);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zero_is_measured_but_failed_values_are_not() {
        let mut m = Metric::bytes(0, "host", "test");
        assert!(m.validate().is_ok());
        m.status = "error".into();
        m.reason = Some("timeout".into());
        assert!(m.validate().is_err());
        m.value = None;
        assert!(m.validate().is_ok());
    }

    #[test]
    fn device_percent_preserves_zero_and_rejects_invalid_counters() {
        for value in [0.0, 43.5, 100.0] {
            let metric = Metric::device_percent(value, "test.gpu");
            assert_eq!(metric.status, "measured");
            assert_eq!(metric.value.as_ref().and_then(|v| v.as_f64()), Some(value));
            assert!(metric.validate().is_ok());
        }
        for value in [-1.0, 100.1, f64::NAN, f64::INFINITY] {
            let metric = Metric::device_percent(value, "test.gpu");
            assert_eq!(metric.reason.as_deref(), Some("invalid_counter"));
            assert!(metric.value.is_none());
            assert!(metric.validate().is_ok());
        }
    }

    #[test]
    fn strict_request_rejects_duplicate_and_unknown_fields() {
        assert!(serde_json::from_str::<Request>(r#"{"version":1,"version":1}"#).is_err());
        assert!(serde_json::from_str::<Request>(r#"{"unknown":1}"#).is_err());
    }

    #[test]
    fn discontinuous_clock_intervals_reset_sampling() {
        assert!(valid_interval(
            "2026-09-30T00:00:00Z",
            "2026-09-30T00:00:02Z",
            2.0
        ));
        assert!(!valid_interval(
            "2026-09-30T00:00:00Z",
            "2026-09-30T00:01:00Z",
            2.0
        ));
        assert!(!valid_interval(
            "2026-09-30T00:00:02Z",
            "2026-09-30T00:00:00Z",
            2.0
        ));
    }

    #[test]
    fn process_cpu_uses_whole_machine_capacity_and_rejects_invalid_intervals() {
        assert_eq!(process_percent(2_000_000_000, 2.0, 8), Some(12.5));
        assert_eq!(process_percent(0, 2.0, 8), Some(0.0));
        assert_eq!(process_percent(2_000_000_000, 0.0, 8), None);
        assert_eq!(process_percent(2_000_000_000, 2.0, 0), None);
        assert_eq!(process_percent(4_000_000_000, 2.0, 1), None);
    }

    #[test]
    fn removed_devices_are_reported_once_and_rediscovered_without_old_values() {
        let missing = Metric::unavailable("bytes", "device", "unsupported_counter", "test");
        let device = Device {
            utilization: None,
            device_key: "gpu:1".into(),
            name: "test".into(),
            vendor: "test".into(),
            memory_kind: "dedicated".into(),
            inventory_device_id: None,
            memory: vec![MemoryView {
                kind: "dedicated".into(),
                pool_id: "gpu:1:dedicated".into(),
                backing_pool_id: None,
                total: Metric::bytes(1024, "device", "test"),
                used: Metric::bytes(0, "device", "test"),
                free: missing,
            }],
        };
        let mut result = SourceResult::unavailable("gpu");
        result.device_discovery = Some(Discovery {
            status: "measured".into(),
            reason: None,
            observed_at: now(),
        });
        let mut previous = HashMap::from([(device.device_key.clone(), device.clone())]);
        observe_devices(&mut previous, &mut result);
        assert_eq!(
            result.devices[0].memory[0].used.reason.as_deref(),
            Some("device_removed")
        );
        assert!(result.validate().is_ok());
        result.devices.clear();
        observe_devices(&mut previous, &mut result);
        assert!(result.devices.is_empty());
        result.devices.push(device);
        observe_devices(&mut previous, &mut result);
        assert_eq!(
            result.devices[0].memory[0]
                .used
                .value
                .as_ref()
                .and_then(|n| n.as_u64()),
            Some(0)
        );
    }

    #[test]
    fn partial_discovery_cannot_accumulate_unbounded_previous_devices() {
        let mut previous = HashMap::new();
        for batch in 0..3 {
            let mut result = SourceResult::unavailable("gpu");
            result.devices = (0..32)
                .map(|index| Device {
                    utilization: None,
                    device_key: format!("gpu:{batch}:{index}"),
                    name: "test".into(),
                    vendor: "unknown".into(),
                    memory_kind: "unknown".into(),
                    inventory_device_id: None,
                    memory: vec![],
                })
                .collect();
            observe_devices(&mut previous, &mut result);
            assert!(previous.len() <= 32);
            assert!(result.devices.len() <= 32);
        }
    }
}
