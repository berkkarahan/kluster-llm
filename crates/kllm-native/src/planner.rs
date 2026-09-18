use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use thiserror::Error;

use crate::hw::METAL_VRAM_FRACTION;

const DEFAULT_HEADROOM: f64 = 0.80;
const DEFAULT_VRAM_WEIGHT: f64 = 0.6;
const DEFAULT_LATENCY_WEIGHT: f64 = 0.4;
/// When estimated need exceeds this fraction of budget (but not budget), status is `tight`.
pub const TIGHT_RATIO: f64 = 0.90;
/// Base KV bytes/token/layer for a single byte-width of K or V.
/// With q8_0+q8_0 (1+1): 128*2 = 256 — matches the historical constant.
const KV_BASE_BYTES_PER_TOKEN_PER_LAYER: f64 = 128.0;
/// Fixed overhead when vision is on but mmproj size is unknown.
const VISION_OVERHEAD_BYTES: u64 = 512 * 1024 * 1024;
const MAX_AUTO_CONTEXT: u32 = 131_072;
const AUTO_CONTEXT_STEP: u32 = 512;

#[derive(Error, Debug)]
pub enum PlanError {
    #[error("{0}")]
    Message(String),
}

#[pyclass]
#[derive(Clone, Debug)]
pub struct NodeCapacity {
    #[pyo3(get)]
    pub node_id: String,
    #[pyo3(get)]
    pub total_ram_bytes: u64,
    #[pyo3(get)]
    pub available_ram_bytes: u64,
    #[pyo3(get)]
    pub vram_bytes: Option<u64>,
    #[pyo3(get)]
    pub backend: String,
    /// If true, unified memory (e.g. Apple Silicon).
    #[pyo3(get)]
    pub unified_memory: bool,
    #[pyo3(get)]
    pub latency_ms: Option<f64>,
    #[pyo3(get)]
    pub is_master: bool,
}

#[pymethods]
impl NodeCapacity {
    #[new]
    #[pyo3(signature = (node_id, total_ram_bytes, available_ram_bytes, vram_bytes=None, backend="unknown".to_string(), unified_memory=false, latency_ms=None, is_master=false))]
    fn new(
        node_id: String,
        total_ram_bytes: u64,
        available_ram_bytes: u64,
        vram_bytes: Option<u64>,
        backend: String,
        unified_memory: bool,
        latency_ms: Option<f64>,
        is_master: bool,
    ) -> Self {
        Self {
            node_id,
            total_ram_bytes,
            available_ram_bytes,
            vram_bytes,
            backend,
            unified_memory,
            latency_ms,
            is_master,
        }
    }
}

#[derive(Clone, Debug)]
pub struct PlanRequest {
    pub nodes: Vec<NodeCapacity>,
    pub model_size_bytes: u64,
    pub context: u32,
    pub context_auto: bool,
    pub headroom: f64,
    pub tensor_split_override: Option<Vec<f64>>,
    /// Preferred device: "cpu" | "gpu" (default gpu).
    pub device: String,
    pub vram_weight: f64,
    pub latency_weight: f64,
    /// KV cache dtype for keys (default q8_0).
    pub cache_type_k: String,
    /// KV cache dtype for values (default q8_0).
    pub cache_type_v: String,
    /// Parallel slots; multiplies KV. Default 1 (treat "auto" as 1 for estimate).
    pub parallel_slots: u32,
    pub mmproj_size_bytes: u64,
    pub draft_model_size_bytes: u64,
    pub vision: bool,
}

impl PlanRequest {
    pub fn from_pydict(dict: &Bound<'_, PyDict>) -> PyResult<Self> {
        let model_size_bytes: u64 = dict
            .get_item("model_size_bytes")?
            .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err("model_size_bytes"))?
            .extract()?;
        let context: u32 = dict
            .get_item("context")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or(4096);
        let context_auto: bool = dict
            .get_item("context_auto")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or(false);
        let headroom: f64 = dict
            .get_item("headroom")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or(DEFAULT_HEADROOM);
        let device: String = dict
            .get_item("device")?
            .map(|v| v.extract::<String>())
            .transpose()?
            .unwrap_or_else(|| "gpu".into())
            .to_ascii_lowercase();
        if device != "cpu" && device != "gpu" {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "device must be 'cpu' or 'gpu'",
            ));
        }
        let vram_weight: f64 = dict
            .get_item("vram_weight")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or(DEFAULT_VRAM_WEIGHT);
        let latency_weight: f64 = dict
            .get_item("latency_weight")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or(DEFAULT_LATENCY_WEIGHT);
        let cache_type_k: String = dict
            .get_item("cache_type_k")?
            .map(|v| v.extract::<String>())
            .transpose()?
            .unwrap_or_else(|| "q8_0".into());
        let cache_type_v: String = dict
            .get_item("cache_type_v")?
            .map(|v| v.extract::<String>())
            .transpose()?
            .unwrap_or_else(|| "q8_0".into());
        let parallel_slots: u32 = dict
            .get_item("parallel_slots")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or(1)
            .max(1);
        let mmproj_size_bytes: u64 = dict
            .get_item("mmproj_size_bytes")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or(0);
        let draft_model_size_bytes: u64 = dict
            .get_item("draft_model_size_bytes")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or(0);
        let vision: bool = dict
            .get_item("vision")?
            .map(|v| v.extract())
            .transpose()?
            .unwrap_or(false);

        let nodes_obj = dict
            .get_item("nodes")?
            .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err("nodes"))?;
        let nodes_list = nodes_obj.downcast::<PyList>()?;
        let mut nodes = Vec::with_capacity(nodes_list.len());
        for item in nodes_list.iter() {
            if let Ok(nc) = item.extract::<PyRef<NodeCapacity>>() {
                nodes.push(nc.clone());
                continue;
            }
            let d = item.downcast::<PyDict>()?;
            let vram: Option<u64> = d
                .get_item("vram_bytes")?
                .map(|v| {
                    if v.is_none() {
                        Ok(None)
                    } else {
                        v.extract().map(Some)
                    }
                })
                .transpose()?
                .flatten();
            let backend = d
                .get_item("backend")?
                .map(|v| v.extract::<String>())
                .transpose()?
                .unwrap_or_else(|| "unknown".into());
            let unified = backend == "metal"
                || d.get_item("unified_memory")?
                    .map(|v| v.extract::<bool>())
                    .transpose()?
                    .unwrap_or(false);
            let latency_ms: Option<f64> = d
                .get_item("latency_ms")?
                .map(|v| {
                    if v.is_none() {
                        Ok(None)
                    } else {
                        v.extract().map(Some)
                    }
                })
                .transpose()?
                .flatten();
            let is_master = d
                .get_item("is_master")?
                .map(|v| v.extract::<bool>())
                .transpose()?
                .unwrap_or(false)
                || d.get_item("role")?
                    .map(|v| v.extract::<String>())
                    .transpose()?
                    .map(|r| r == "master")
                    .unwrap_or(false);
            nodes.push(NodeCapacity {
                node_id: d
                    .get_item("node_id")?
                    .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err("node_id"))?
                    .extract()?,
                total_ram_bytes: d
                    .get_item("total_ram_bytes")?
                    .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err("total_ram_bytes"))?
                    .extract()?,
                available_ram_bytes: d
                    .get_item("available_ram_bytes")?
                    .map(|v| v.extract())
                    .transpose()?
                    .unwrap_or(0),
                vram_bytes: vram,
                backend,
                unified_memory: unified,
                latency_ms,
                is_master,
            });
        }

        let tensor_split_override = dict
            .get_item("tensor_split")?
            .map(|v| v.extract::<Vec<f64>>())
            .transpose()?;

        Ok(Self {
            nodes,
            model_size_bytes,
            context,
            context_auto,
            headroom,
            tensor_split_override,
            device,
            vram_weight,
            latency_weight,
            cache_type_k,
            cache_type_v,
            parallel_slots,
            mmproj_size_bytes,
            draft_model_size_bytes,
            vision,
        })
    }
}

#[pyclass]
#[derive(Clone, Debug)]
pub struct PlanResult {
    #[pyo3(get)]
    pub tensor_split: Vec<f64>,
    #[pyo3(get)]
    pub budgets_bytes: Vec<u64>,
    #[pyo3(get)]
    pub node_ids: Vec<String>,
    #[pyo3(get)]
    pub total_budget_bytes: u64,
    #[pyo3(get)]
    pub model_size_bytes: u64,
    #[pyo3(get)]
    pub kv_estimate_bytes: u64,
    #[pyo3(get)]
    pub estimated_layers: u32,
    #[pyo3(get)]
    pub ngl: u32,
    #[pyo3(get)]
    pub split_mode: String,
    #[pyo3(get)]
    pub device_requested: String,
    #[pyo3(get)]
    pub device_effective: String,
    /// Per-node effective device, parallel to node_ids ("cpu" | "gpu").
    #[pyo3(get)]
    pub node_effective_devices: Vec<String>,
    /// Per-node fallback flag, parallel to node_ids.
    #[pyo3(get)]
    pub node_fallbacks: Vec<bool>,
    #[pyo3(get)]
    pub fit_status: String,
    #[pyo3(get)]
    pub estimated_bytes: u64,
    #[pyo3(get)]
    pub budget_bytes: u64,
    #[pyo3(get)]
    pub hint: String,
    #[pyo3(get)]
    pub context_effective: u32,
    #[pyo3(get)]
    pub node_alloc_bytes: Vec<u64>,
    #[pyo3(get)]
    pub node_scores: Vec<f64>,
    #[pyo3(get)]
    pub node_latency_ms: Vec<f64>,
    #[pyo3(get)]
    pub vram_alloc_bytes: u64,
    #[pyo3(get)]
    pub ram_alloc_bytes: u64,
}

#[pymethods]
impl PlanResult {
    fn tensor_split_csv(&self) -> String {
        self.tensor_split
            .iter()
            .map(|v| format!("{v:.4}"))
            .collect::<Vec<_>>()
            .join(",")
    }

    fn __repr__(&self) -> String {
        format!(
            "PlanResult(split={:?}, budget={}, model={}, kv={}, device={}->{}",
            self.tensor_split,
            self.total_budget_bytes,
            self.model_size_bytes,
            self.kv_estimate_bytes,
            self.device_requested,
            self.device_effective
        )
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SackTier {
    Vram,
    Ram,
}

#[derive(Clone, Debug)]
struct Sack {
    node_idx: usize,
    tier: SackTier,
    capacity: u64,
    latency_ms: f64,
}

fn ram_budget(node: &NodeCapacity, headroom: f64) -> u64 {
    (node.total_ram_bytes as f64 * headroom) as u64
}

fn master_ram_fallback_budget(node: &NodeCapacity, headroom: f64) -> u64 {
    let cap = node.available_ram_bytes.min(node.total_ram_bytes);
    (cap as f64 * headroom) as u64
}

pub fn effective_vram_bytes(node: &NodeCapacity) -> u64 {
    if let Some(v) = node.vram_bytes {
        if v > 0 {
            return v;
        }
    }
    if node.unified_memory || node.backend == "metal" {
        return (node.total_ram_bytes as f64 * METAL_VRAM_FRACTION) as u64;
    }
    0
}

fn gpu_usable(node: &NodeCapacity) -> bool {
    if node.unified_memory || node.backend == "metal" {
        return true;
    }
    effective_vram_bytes(node) > 0
}

fn gpu_budget(node: &NodeCapacity, headroom: f64) -> u64 {
    ((effective_vram_bytes(node) as f64) * headroom) as u64
}

#[derive(Clone, Debug)]
pub(crate) struct NodeDeviceBudget {
    budget: u64,
    effective: String,
    fallback: bool,
}

/// Budget a node for the preferred device, falling back when GPU is unavailable.
pub(crate) fn node_budget_for_device(
    node: &NodeCapacity,
    headroom: f64,
    preferred: &str,
) -> Result<NodeDeviceBudget, PlanError> {
    if preferred == "cpu" {
        return Ok(NodeDeviceBudget {
            budget: ram_budget(node, headroom),
            effective: "cpu".into(),
            fallback: false,
        });
    }
    if gpu_usable(node) {
        Ok(NodeDeviceBudget {
            budget: gpu_budget(node, headroom),
            effective: "gpu".into(),
            fallback: false,
        })
    } else {
        Ok(NodeDeviceBudget {
            budget: ram_budget(node, headroom),
            effective: "cpu".into(),
            fallback: true,
        })
    }
}

fn normalize_weights(vram_weight: f64, latency_weight: f64) -> Result<(f64, f64), PlanError> {
    if vram_weight < 0.0 || latency_weight < 0.0 {
        return Err(PlanError::Message(
            "vram_weight and latency_weight must be >= 0".into(),
        ));
    }
    let sum = vram_weight + latency_weight;
    if sum <= f64::EPSILON {
        return Err(PlanError::Message(
            "vram_weight and latency_weight cannot both be zero".into(),
        ));
    }
    Ok((vram_weight / sum, latency_weight / sum))
}

fn node_latency(node: &NodeCapacity) -> f64 {
    node.latency_ms.unwrap_or(0.0).max(0.0)
}

fn score_sack(
    sack: &Sack,
    max_cap_in_tier: u64,
    max_lat: f64,
    wv: f64,
    wl: f64,
) -> f64 {
    let cap_score = if max_cap_in_tier > 0 {
        sack.capacity as f64 / max_cap_in_tier as f64
    } else {
        0.0
    };
    let lat_score = if max_lat <= f64::EPSILON {
        1.0
    } else {
        (1.0 - sack.latency_ms / max_lat).clamp(0.0, 1.0)
    };
    wv * cap_score + wl * lat_score
}

fn build_sacks(nodes: &[NodeCapacity], headroom: f64, preferred: &str) -> Vec<Sack> {
    let mut sacks = Vec::new();
    if preferred == "cpu" {
        for (i, n) in nodes.iter().enumerate() {
            sacks.push(Sack {
                node_idx: i,
                tier: SackTier::Ram,
                capacity: ram_budget(n, headroom),
                latency_ms: node_latency(n),
            });
        }
        return sacks;
    }

    for (i, n) in nodes.iter().enumerate() {
        if gpu_usable(n) {
            sacks.push(Sack {
                node_idx: i,
                tier: SackTier::Vram,
                capacity: gpu_budget(n, headroom),
                latency_ms: node_latency(n),
            });
        } else {
            sacks.push(Sack {
                node_idx: i,
                tier: SackTier::Ram,
                capacity: ram_budget(n, headroom),
                latency_ms: node_latency(n),
            });
        }
        if n.is_master && !n.unified_memory && n.backend != "metal" && gpu_usable(n) {
            sacks.push(Sack {
                node_idx: i,
                tier: SackTier::Ram,
                capacity: master_ram_fallback_budget(n, headroom),
                latency_ms: node_latency(n),
            });
        }
    }
    sacks
}

fn greedy_fill(
    sacks: &[Sack],
    needed: u64,
    wv: f64,
    wl: f64,
) -> (Vec<u64>, Vec<u64>, u64, u64, Vec<f64>) {
    let n_nodes = sacks
        .iter()
        .map(|s| s.node_idx)
        .max()
        .map(|m| m + 1)
        .unwrap_or(0);
    let mut device_alloc = vec![0u64; n_nodes];
    let mut ram_alloc = vec![0u64; n_nodes];
    let mut remaining = needed;

    let max_lat = sacks
        .iter()
        .map(|s| s.latency_ms)
        .fold(0.0_f64, f64::max);

    let mut scores_by_sack: Vec<f64> = vec![0.0; sacks.len()];

    for tier in [SackTier::Vram, SackTier::Ram] {
        if remaining == 0 {
            break;
        }
        let tier_sacks: Vec<usize> = sacks
            .iter()
            .enumerate()
            .filter(|(_, s)| s.tier == tier && s.capacity > 0)
            .map(|(i, _)| i)
            .collect();
        if tier_sacks.is_empty() {
            continue;
        }
        let max_cap = tier_sacks
            .iter()
            .map(|&i| sacks[i].capacity)
            .max()
            .unwrap_or(0);
        let mut order: Vec<(usize, f64)> = tier_sacks
            .iter()
            .map(|&i| {
                let sc = score_sack(&sacks[i], max_cap, max_lat, wv, wl);
                scores_by_sack[i] = sc;
                (i, sc)
            })
            .collect();
        order.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

        for (idx, _) in order {
            if remaining == 0 {
                break;
            }
            let sack = &sacks[idx];
            let take = sack.capacity.min(remaining);
            if take == 0 {
                continue;
            }
            if sack.tier == SackTier::Vram {
                device_alloc[sack.node_idx] = device_alloc[sack.node_idx].saturating_add(take);
            } else {
                ram_alloc[sack.node_idx] = ram_alloc[sack.node_idx].saturating_add(take);
            }
            remaining = remaining.saturating_sub(take);
        }
    }

    let vram_total: u64 = device_alloc.iter().sum();
    let ram_total: u64 = ram_alloc.iter().sum();
    (device_alloc, ram_alloc, vram_total, ram_total, scores_by_sack)
}

fn estimate_layers(model_size_bytes: u64) -> u32 {
    let mb = model_size_bytes / (1024 * 1024);
    if mb < 6000 {
        32
    } else if mb < 12_000 {
        48
    } else {
        80
    }
}

/// Approximate element width in bytes for a KV cache dtype.
fn kv_dtype_bytes(dtype: &str) -> f64 {
    match dtype.to_ascii_lowercase().as_str() {
        "f32" => 4.0,
        "f16" | "bf16" => 2.0,
        "q8_0" => 1.0,
        "q5_0" | "q5_1" => 0.625,
        "q4_0" | "q4_1" | "iq4_nl" => 0.5,
        _ => 1.0, // unknown → treat like q8_0
    }
}

fn estimate_kv(
    context: u32,
    layers: u32,
    cache_type_k: &str,
    cache_type_v: &str,
    parallel_slots: u32,
) -> u64 {
    let width = kv_dtype_bytes(cache_type_k) + kv_dtype_bytes(cache_type_v);
    let slots = parallel_slots.max(1) as f64;
    ((context as f64)
        * (layers as f64)
        * KV_BASE_BYTES_PER_TOKEN_PER_LAYER
        * width
        * slots) as u64
}

fn estimate_needed(req: &PlanRequest, kv: u64) -> u64 {
    let mut needed = req.model_size_bytes.saturating_add(kv);
    needed = needed.saturating_add(req.mmproj_size_bytes);
    needed = needed.saturating_add(req.draft_model_size_bytes);
    if req.vision && req.mmproj_size_bytes == 0 {
        needed = needed.saturating_add(VISION_OVERHEAD_BYTES);
    }
    needed
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FitStatus {
    Fits,
    Tight,
    Overflow,
}

impl FitStatus {
    fn as_str(self) -> &'static str {
        match self {
            FitStatus::Fits => "fits",
            FitStatus::Tight => "tight",
            FitStatus::Overflow => "overflow",
        }
    }
}

pub fn classify_fit(needed: u64, budget: u64) -> (FitStatus, String) {
    if budget == 0 {
        return (
            FitStatus::Overflow,
            "cluster budget is zero; add workers or reduce context".into(),
        );
    }
    if needed > budget {
        return (
            FitStatus::Overflow,
            format!(
                "need ~{} MB but cluster budget is ~{} MB; reduce context or add capacity",
                needed / (1024 * 1024),
                budget / (1024 * 1024)
            ),
        );
    }
    let tight_line = (budget as f64 * TIGHT_RATIO) as u64;
    if needed > tight_line {
        return (
            FitStatus::Tight,
            format!(
                "usage ~{} MB is above {:.0}% of ~{} MB budget; run may be tight",
                needed / (1024 * 1024),
                TIGHT_RATIO * 100.0,
                budget / (1024 * 1024)
            ),
        );
    }
    (FitStatus::Fits, String::new())
}

fn auto_context_candidates(max_ctx: u32) -> Vec<u32> {
    let cap = max_ctx.min(MAX_AUTO_CONTEXT).max(AUTO_CONTEXT_STEP);
    let mut pow = AUTO_CONTEXT_STEP;
    while pow * 2 <= cap {
        pow *= 2;
    }
    let mut out = Vec::new();
    let mut c = pow;
    while c >= AUTO_CONTEXT_STEP {
        if c <= cap {
            out.push(c);
        }
        if c == AUTO_CONTEXT_STEP {
            break;
        }
        c /= 2;
    }
    if out.is_empty() {
        out.push(AUTO_CONTEXT_STEP);
    }
    out
}

fn total_sack_capacity(sacks: &[Sack]) -> u64 {
    sacks.iter().map(|s| s.capacity).sum()
}

pub fn plan_split(req: &PlanRequest) -> Result<PlanResult, PlanError> {
    if req.nodes.is_empty() {
        return Err(PlanError::Message("at least one node is required".into()));
    }
    if !(0.1..=1.0).contains(&req.headroom) {
        return Err(PlanError::Message("headroom must be between 0.1 and 1.0".into()));
    }
    let preferred = req.device.to_ascii_lowercase();
    if preferred != "cpu" && preferred != "gpu" {
        return Err(PlanError::Message("device must be 'cpu' or 'gpu'".into()));
    }
    let (wv, wl) = normalize_weights(req.vram_weight, req.latency_weight)?;

    let layers = estimate_layers(req.model_size_bytes);
    let kv = estimate_kv(
        req.context,
        layers,
        &req.cache_type_k,
        &req.cache_type_v,
        req.parallel_slots,
    );
    let needed = estimate_needed(req, kv);

    let sacks = build_sacks(&req.nodes, req.headroom, &preferred);
    let total_budget = total_sack_capacity(&sacks);
    let (fit_status, hint) = classify_fit(needed, total_budget);

    let mut node_effective = Vec::with_capacity(req.nodes.len());
    let mut node_fallbacks = Vec::with_capacity(req.nodes.len());
    let mut budgets = Vec::with_capacity(req.nodes.len());
    for n in &req.nodes {
        let nb = node_budget_for_device(n, req.headroom, &preferred)?;
        node_effective.push(nb.effective);
        node_fallbacks.push(nb.fallback);
        budgets.push(nb.budget);
    }

    let (device_alloc, ram_alloc, vram_alloc_total, ram_alloc_total, _sack_scores) =
        greedy_fill(&sacks, needed, wv, wl);

    let tensor_split = if let Some(ref override_split) = req.tensor_split_override {
        if override_split.len() != req.nodes.len() {
            return Err(PlanError::Message(format!(
                "tensor_split length {} != nodes {}",
                override_split.len(),
                req.nodes.len()
            )));
        }
        let sum: f64 = override_split.iter().sum();
        if (sum - 1.0).abs() > 0.02 {
            return Err(PlanError::Message(format!(
                "tensor_split must sum to ~1.0 (got {sum})"
            )));
        }
        override_split.clone()
    } else {
        let node_totals: Vec<u64> = device_alloc
            .iter()
            .zip(ram_alloc.iter())
            .map(|(d, r)| d.saturating_add(*r))
            .collect();
        let total: u64 = node_totals.iter().sum();
        if total == 0 {
            vec![0.0; req.nodes.len()]
        } else {
            node_totals
                .iter()
                .map(|a| *a as f64 / total as f64)
                .collect()
        }
    };

    let any_gpu = node_effective.iter().any(|d| d == "gpu") && vram_alloc_total > 0;
    let device_effective = if preferred == "cpu" || !any_gpu {
        "cpu"
    } else {
        "gpu"
    };
    let ngl = if device_effective == "cpu" {
        0
    } else if needed == 0 {
        0
    } else {
        ((layers as u64)
            .saturating_mul(vram_alloc_total)
            .min((layers as u64).saturating_mul(needed))
            / needed) as u32
    };

    let cluster_max_cap = sacks.iter().map(|s| s.capacity).max().unwrap_or(0);
    let max_lat = req
        .nodes
        .iter()
        .map(node_latency)
        .fold(0.0_f64, f64::max);
    let node_scores: Vec<f64> = req
        .nodes
        .iter()
        .enumerate()
        .map(|(i, n)| {
            let node_cap = sacks
                .iter()
                .filter(|s| s.node_idx == i)
                .map(|s| s.capacity)
                .max()
                .unwrap_or(0);
            if node_cap == 0 || cluster_max_cap == 0 {
                return 0.0;
            }
            score_sack(
                &Sack {
                    node_idx: i,
                    tier: SackTier::Vram,
                    capacity: node_cap,
                    latency_ms: node_latency(n),
                },
                cluster_max_cap,
                max_lat,
                wv,
                wl,
            )
        })
        .collect();

    let node_latency_ms: Vec<f64> = req.nodes.iter().map(node_latency).collect();
    let node_alloc_bytes: Vec<u64> = device_alloc
        .iter()
        .zip(ram_alloc.iter())
        .map(|(d, r)| d.saturating_add(*r))
        .collect();

    Ok(PlanResult {
        tensor_split,
        budgets_bytes: budgets,
        node_ids: req.nodes.iter().map(|n| n.node_id.clone()).collect(),
        total_budget_bytes: total_budget,
        model_size_bytes: req.model_size_bytes,
        kv_estimate_bytes: kv,
        estimated_layers: layers,
        ngl,
        split_mode: "layer".into(),
        device_requested: preferred,
        device_effective: device_effective.into(),
        node_effective_devices: node_effective,
        node_fallbacks,
        fit_status: fit_status.as_str().into(),
        estimated_bytes: needed,
        budget_bytes: total_budget,
        hint,
        context_effective: req.context,
        node_alloc_bytes,
        node_scores,
        node_latency_ms,
        vram_alloc_bytes: vram_alloc_total,
        ram_alloc_bytes: ram_alloc_total,
    })
}

pub fn plan_split_auto_context(req: &PlanRequest) -> Result<PlanResult, PlanError> {
    if !req.context_auto {
        return plan_split(req);
    }
    let candidates = auto_context_candidates(req.context.max(AUTO_CONTEXT_STEP));
    let mut best_tight: Option<PlanResult> = None;
    for ctx in candidates {
        let mut sub = req.clone();
        sub.context = ctx;
        sub.context_auto = false;
        let plan = plan_split(&sub)?;
        match plan.fit_status.as_str() {
            "fits" => return Ok(plan),
            "tight" if best_tight.is_none() => best_tight = Some(plan),
            _ => {}
        }
    }
    if let Some(plan) = best_tight {
        return Ok(plan);
    }
    let mut fallback = req.clone();
    fallback.context = AUTO_CONTEXT_STEP;
    fallback.context_auto = false;
    plan_split(&fallback)
}

pub fn plan_split_entry(req: &PlanRequest) -> Result<PlanResult, PlanError> {
    if req.context_auto {
        plan_split_auto_context(req)
    } else {
        plan_split(req)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(id: &str, ram_gb: u64, vram_gb: Option<u64>, metal: bool) -> NodeCapacity {
        node_with_latency(id, ram_gb, vram_gb, metal, None, false)
    }

    fn node_with_latency(
        id: &str,
        ram_gb: u64,
        vram_gb: Option<u64>,
        metal: bool,
        latency_ms: Option<f64>,
        is_master: bool,
    ) -> NodeCapacity {
        let ram = ram_gb * 1024 * 1024 * 1024;
        NodeCapacity {
            node_id: id.into(),
            total_ram_bytes: ram,
            available_ram_bytes: ram / 2,
            vram_bytes: vram_gb.map(|g| g * 1024 * 1024 * 1024),
            backend: if metal { "metal".into() } else { "vulkan".into() },
            unified_memory: metal,
            latency_ms,
            is_master,
        }
    }

    fn req(
        nodes: Vec<NodeCapacity>,
        model_size_bytes: u64,
        device: &str,
    ) -> PlanRequest {
        PlanRequest {
            nodes,
            model_size_bytes,
            context: 4096,
            context_auto: false,
            headroom: 0.80,
            tensor_split_override: None,
            device: device.into(),
            vram_weight: DEFAULT_VRAM_WEIGHT,
            latency_weight: DEFAULT_LATENCY_WEIGHT,
            cache_type_k: "q8_0".into(),
            cache_type_v: "q8_0".into(),
            parallel_slots: 1,
            mmproj_size_bytes: 0,
            draft_model_size_bytes: 0,
            vision: false,
        }
    }

    #[test]
    fn kv_q8_matches_legacy_256() {
        let layers = 32u32;
        let ctx = 4096u32;
        let kv = estimate_kv(ctx, layers, "q8_0", "q8_0", 1);
        assert_eq!(kv, (ctx as u64) * (layers as u64) * 256);
    }

    #[test]
    fn kv_f16_is_double_q8() {
        let q8 = estimate_kv(4096, 32, "q8_0", "q8_0", 1);
        let f16 = estimate_kv(4096, 32, "f16", "f16", 1);
        assert_eq!(f16, q8 * 2);
    }

    #[test]
    fn kv_scales_with_parallel_slots() {
        let one = estimate_kv(2048, 48, "q8_0", "q8_0", 1);
        let two = estimate_kv(2048, 48, "q8_0", "q8_0", 2);
        assert_eq!(two, one * 2);
    }

    #[test]
    fn estimate_includes_mmproj_and_draft() {
        let mut r = req(vec![node("m", 64, None, true)], 1024 * 1024 * 1024, "gpu");
        r.mmproj_size_bytes = 100 * 1024 * 1024;
        r.draft_model_size_bytes = 200 * 1024 * 1024;
        let plan = plan_split(&r).unwrap();
        let layers = estimate_layers(r.model_size_bytes);
        let kv = estimate_kv(r.context, layers, "q8_0", "q8_0", 1);
        let expected = r
            .model_size_bytes
            .saturating_add(kv)
            .saturating_add(r.mmproj_size_bytes)
            .saturating_add(r.draft_model_size_bytes);
        assert_eq!(plan.estimated_bytes, expected);
    }

    #[test]
    fn vision_without_mmproj_adds_overhead() {
        let mut r = req(vec![node("m", 64, None, true)], 512 * 1024 * 1024, "gpu");
        r.vision = true;
        let plan = plan_split(&r).unwrap();
        let base = {
            let mut b = r.clone();
            b.vision = false;
            plan_split(&b).unwrap().estimated_bytes
        };
        assert_eq!(plan.estimated_bytes, base + VISION_OVERHEAD_BYTES);
    }

    #[test]
    fn knapsack_prefers_vram_before_ram() {
        let mut w = node("worker", 64, Some(8), false);
        w.backend = "cpu".into();
        w.unified_memory = false;
        w.vram_bytes = None;
        let plan = plan_split(&req(vec![w], 2 * 1024 * 1024 * 1024, "gpu")).unwrap();
        assert_eq!(plan.device_effective, "cpu");
        assert_eq!(plan.ngl, 0);
    }

    #[test]
    fn knapsack_mac_and_worker_split() {
        let plan = plan_split(&req(
            vec![
                node_with_latency("master", 24, None, true, Some(0.0), true),
                node_with_latency("worker", 32, Some(12), false, Some(5.0), false),
            ],
            5 * 1024 * 1024 * 1024,
            "gpu",
        ))
        .unwrap();
        assert_eq!(plan.split_mode, "layer");
        assert_eq!(plan.tensor_split.len(), 2);
        let sum: f64 = plan.tensor_split.iter().sum();
        assert!((sum - 1.0).abs() < 1e-6);
        assert_eq!(plan.device_effective, "gpu");
        assert!(plan.ngl > 0);
        assert!(plan.vram_alloc_bytes > 0);
    }

    #[test]
    fn ram_only_alloc_produces_nonzero_split() {
        let mut a = node("a", 32, None, false);
        a.backend = "cpu".into();
        a.vram_bytes = None;
        let mut b = node("b", 16, None, false);
        b.backend = "cpu".into();
        b.vram_bytes = None;
        let plan = plan_split(&req(vec![a, b], 4 * 1024 * 1024 * 1024, "gpu")).unwrap();
        let sum: f64 = plan.tensor_split.iter().sum();
        assert!((sum - 1.0).abs() < 1e-6);
        assert!(plan.tensor_split[0] > plan.tensor_split[1]);
        assert!(plan.ram_alloc_bytes > 0);
    }

    #[test]
    fn node_scores_reflect_relative_capacity() {
        let plan = plan_split(&req(
            vec![
                node_with_latency("big", 32, Some(16), false, Some(0.0), false),
                node_with_latency("small", 32, Some(4), false, Some(0.0), false),
            ],
            2 * 1024 * 1024 * 1024,
            "gpu",
        ))
        .unwrap();
        assert!(plan.node_scores[0] > plan.node_scores[1]);
    }

    #[test]
    fn metal_legacy_vram_budget() {
        let n = node("m", 24, None, true);
        let vram = effective_vram_bytes(&n);
        assert_eq!(vram, (24u64 * 1024 * 1024 * 1024 * 80) / 100);
        assert_eq!(gpu_budget(&n, 0.80), (vram as f64 * 0.80) as u64);
    }

    #[test]
    fn gpu_unknown_vram_falls_back_to_cpu() {
        let plan = plan_split(&req(
            vec![node("w", 16, None, false)],
            1_000_000,
            "gpu",
        ))
        .unwrap();
        assert_eq!(plan.device_requested, "gpu");
        assert_eq!(plan.device_effective, "cpu");
        assert_eq!(plan.ngl, 0);
        assert_eq!(plan.node_effective_devices, vec!["cpu".to_string()]);
        assert_eq!(plan.node_fallbacks, vec![true]);
    }

    #[test]
    fn cpu_preferred_forces_ram_and_ngl_zero() {
        let plan = plan_split(&req(
            vec![node("w", 32, Some(12), false)],
            1_000_000,
            "cpu",
        ))
        .unwrap();
        assert_eq!(plan.device_requested, "cpu");
        assert_eq!(plan.device_effective, "cpu");
        assert_eq!(plan.ngl, 0);
        assert_eq!(plan.node_fallbacks, vec![false]);
        assert_eq!(
            plan.budgets_bytes[0],
            (32u64 * 1024 * 1024 * 1024) * 80 / 100
        );
    }

    #[test]
    fn gpu_budget_scales_vram_by_headroom() {
        let plan = plan_split(&req(
            vec![node("w", 32, Some(10), false)],
            1_000_000,
            "gpu",
        ))
        .unwrap();
        assert_eq!(plan.device_effective, "gpu");
        let vram = 10u64 * 1024 * 1024 * 1024;
        let expected = ((vram as f64) * 0.80) as u64;
        assert_eq!(plan.budgets_bytes[0], expected);
    }

    #[test]
    fn insufficient_budget_returns_overflow() {
        let plan = plan_split(&req(
            vec![node("m", 4, None, true)],
            40 * 1024 * 1024 * 1024,
            "gpu",
        ))
        .unwrap();
        assert_eq!(plan.fit_status, "overflow");
        assert!(plan.estimated_bytes > plan.budget_bytes);
    }

    #[test]
    fn tight_ratio_threshold() {
        let budget = 1000u64;
        let (s, _) = classify_fit(950, budget);
        assert_eq!(s, FitStatus::Tight);
        let (s2, _) = classify_fit(899, budget);
        assert_eq!(s2, FitStatus::Fits);
        let (s3, _) = classify_fit(1001, budget);
        assert_eq!(s3, FitStatus::Overflow);
    }

    #[test]
    fn auto_context_prefers_fits() {
        let mut r = req(vec![node("m", 64, None, true)], 512 * 1024 * 1024, "gpu");
        r.context_auto = true;
        r.context = 4096;
        let plan = plan_split_auto_context(&r).unwrap();
        assert!(plan.context_effective >= 512);
        assert!(plan.fit_status == "fits" || plan.fit_status == "tight");
    }

    #[test]
    fn latency_prefers_lower_rtt_when_vram_equal() {
        let vram = Some(8u64 * 1024 * 1024 * 1024);
        let plan = plan_split(&req(
            vec![
                node_with_latency("fast", 32, vram, false, Some(1.0), false),
                node_with_latency("slow", 32, vram, false, Some(50.0), false),
            ],
            4 * 1024 * 1024 * 1024,
            "gpu",
        ))
        .unwrap();
        assert!(plan.tensor_split[0] >= plan.tensor_split[1]);
    }

    #[test]
    fn weight_normalization() {
        let (a, b) = normalize_weights(0.6, 0.4).unwrap();
        assert!((a - 0.6).abs() < 1e-9);
        assert!((b - 0.4).abs() < 1e-9);
        assert!(normalize_weights(0.0, 0.0).is_err());
    }
}
