use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use thiserror::Error;

const DEFAULT_HEADROOM: f64 = 0.80;
/// Rough bytes-per-token for q8_0 KV estimate (K+V), conservative.
const KV_BYTES_PER_TOKEN_PER_LAYER: f64 = 256.0;

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
    /// If true, unified memory (e.g. Apple Silicon) — VRAM unknown uses RAM budget.
    #[pyo3(get)]
    pub unified_memory: bool,
}

#[pymethods]
impl NodeCapacity {
    #[new]
    #[pyo3(signature = (node_id, total_ram_bytes, available_ram_bytes, vram_bytes=None, backend="unknown".to_string(), unified_memory=false))]
    fn new(
        node_id: String,
        total_ram_bytes: u64,
        available_ram_bytes: u64,
        vram_bytes: Option<u64>,
        backend: String,
        unified_memory: bool,
    ) -> Self {
        Self {
            node_id,
            total_ram_bytes,
            available_ram_bytes,
            vram_bytes,
            backend,
            unified_memory,
        }
    }
}

#[derive(Clone, Debug)]
pub struct PlanRequest {
    pub nodes: Vec<NodeCapacity>,
    pub model_size_bytes: u64,
    pub context: u32,
    pub headroom: f64,
    pub tensor_split_override: Option<Vec<f64>>,
    /// Preferred device: "cpu" | "gpu" (default gpu).
    pub device: String,
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
            headroom,
            tensor_split_override,
            device,
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

#[derive(Clone, Debug)]
pub(crate) struct NodeDeviceBudget {
    budget: u64,
    effective: String,
    fallback: bool,
}

fn ram_budget(node: &NodeCapacity, headroom: f64) -> u64 {
    (node.total_ram_bytes as f64 * headroom) as u64
}

fn gpu_usable(node: &NodeCapacity) -> bool {
    if node.unified_memory || node.backend == "metal" {
        return true;
    }
    matches!(node.vram_bytes, Some(v) if v > 0)
}

fn gpu_budget(node: &NodeCapacity, headroom: f64) -> u64 {
    let ram = ram_budget(node, headroom);
    if node.unified_memory || node.backend == "metal" {
        return ram;
    }
    let vram = ((node.vram_bytes.unwrap_or(0) as f64) * headroom) as u64;
    let spill = (node.available_ram_bytes.max(1) as f64 * (1.0 - headroom)).min(ram as f64) as u64 / 4;
    vram.saturating_add(spill)
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
    // preferred == gpu
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

fn estimate_kv(model_size_bytes: u64, context: u32, layers: u32) -> u64 {
    let _ = model_size_bytes;
    ((context as f64) * (layers as f64) * KV_BYTES_PER_TOKEN_PER_LAYER) as u64
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

    let layers = estimate_layers(req.model_size_bytes);
    let kv = estimate_kv(req.model_size_bytes, req.context, layers);
    let needed = req.model_size_bytes.saturating_add(kv);

    let mut budgets = Vec::with_capacity(req.nodes.len());
    let mut node_effective = Vec::with_capacity(req.nodes.len());
    let mut node_fallbacks = Vec::with_capacity(req.nodes.len());
    for n in &req.nodes {
        let nb = node_budget_for_device(n, req.headroom, &preferred)?;
        budgets.push(nb.budget);
        node_effective.push(nb.effective);
        node_fallbacks.push(nb.fallback);
    }
    let total_budget: u64 = budgets.iter().sum();
    if total_budget < needed {
        return Err(PlanError::Message(format!(
            "cluster budget too small: need ~{} MB (model+KV), have ~{} MB",
            needed / (1024 * 1024),
            total_budget / (1024 * 1024)
        )));
    }

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
        budgets
            .iter()
            .map(|b| *b as f64 / total_budget as f64)
            .collect()
    };

    let any_gpu = node_effective.iter().any(|d| d == "gpu");
    let device_effective = if preferred == "cpu" || !any_gpu {
        "cpu"
    } else {
        "gpu"
    };
    let ngl = if device_effective == "cpu" { 0 } else { layers };

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
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(id: &str, ram_gb: u64, vram_gb: Option<u64>, metal: bool) -> NodeCapacity {
        let ram = ram_gb * 1024 * 1024 * 1024;
        NodeCapacity {
            node_id: id.into(),
            total_ram_bytes: ram,
            available_ram_bytes: ram / 2,
            vram_bytes: vram_gb.map(|g| g * 1024 * 1024 * 1024),
            backend: if metal { "metal".into() } else { "vulkan".into() },
            unified_memory: metal,
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
            headroom: 0.80,
            tensor_split_override: None,
            device: device.into(),
        }
    }

    #[test]
    fn proportional_split_mac_and_worker() {
        let plan = plan_split(&req(
            vec![
                node("master", 24, None, true),
                node("worker", 32, Some(12), false),
            ],
            5 * 1024 * 1024 * 1024,
            "gpu",
        ))
        .unwrap();
        assert_eq!(plan.split_mode, "layer");
        assert_eq!(plan.tensor_split.len(), 2);
        let sum: f64 = plan.tensor_split.iter().sum();
        assert!((sum - 1.0).abs() < 1e-6);
        assert!(plan.tensor_split[0] > plan.tensor_split[1]);
        assert_eq!(plan.device_effective, "gpu");
        assert!(plan.ngl > 0);
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
        // RAM headroom 32*0.8 GiB
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
        let ram = 32u64 * 1024 * 1024 * 1024;
        let headroom = 0.80;
        let spill = ((ram / 2) as f64 * (1.0 - headroom)).min((ram as f64) * headroom) as u64 / 4;
        let expected = ((vram as f64) * headroom) as u64 + spill;
        assert_eq!(plan.budgets_bytes[0], expected);
    }

    #[test]
    fn refuses_insufficient_budget() {
        let err = plan_split(&req(
            vec![node("m", 4, None, true)],
            40 * 1024 * 1024 * 1024,
            "gpu",
        ));
        assert!(err.is_err());
    }
}
