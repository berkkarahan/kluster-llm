#![cfg(feature = "python-bindings")]
mod gguf;
mod network;
mod hw;
mod planner;
mod process;
mod usage;

use pyo3::prelude::*;
use pyo3::types::PyDict;

use hw::{CapacitySnapshot, GpuDevice};
use planner::{NodeCapacity, PlanRequest, PlanResult};
use process::{LaunchConfig, LaunchResult, ProcessHandle};

#[pyfunction]
fn _process_identity(py: Python<'_>, pid: u32) -> PyResult<(String, String)> {
    py.detach(|| usage::process_counters(pid))
        .map(|p| (p.token, p.started))
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

#[pyfunction]
fn _validate_source_json(source: &str, payload: &str) -> PyResult<()> {
    usage::validate_source_json(source, payload).map_err(pyo3::exceptions::PyValueError::new_err)
}

#[pyfunction]
fn _read_gguf_metadata_json(
    py: Python<'_>,
    path: String,
    include_template: bool,
) -> Option<String> {
    py.detach(|| gguf::inference_metadata_json(&path, include_template))
}

#[pyfunction]
fn probe_capacity(py: Python<'_>) -> PyResult<CapacitySnapshot> {
    Ok(py.detach(hw::probe_capacity))
}

#[pyfunction]
fn list_gpus(py: Python<'_>) -> PyResult<(Vec<GpuDevice>, String)> {
    let (devices, backend) = py.detach(hw::list_gpus);
    Ok((devices, backend.as_str().to_string()))
}

#[pyfunction]
fn _probe_capacity_and_gpus(
    py: Python<'_>,
) -> PyResult<(CapacitySnapshot, Vec<GpuDevice>, String)> {
    let (snapshot, devices, backend) = py.detach(hw::probe_capacity_with_gpus);
    Ok((snapshot, devices, backend.as_str().to_string()))
}

#[pyfunction]
fn plan_split(request: &Bound<'_, PyDict>) -> PyResult<PlanResult> {
    let req = PlanRequest::from_pydict(request)?;
    planner::plan_split_entry(&req)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (repo_root=None))]
fn resolve_llama_server(repo_root: Option<String>) -> PyResult<Option<String>> {
    Ok(process::resolve_llama_server(repo_root.as_deref()))
}

#[pyfunction]
#[pyo3(signature = (repo_root=None))]
fn resolve_rpc_server(repo_root: Option<String>) -> PyResult<Option<String>> {
    Ok(process::resolve_rpc_server(repo_root.as_deref()))
}

#[pyfunction]
fn start_master(py: Python<'_>, config: &Bound<'_, PyDict>) -> PyResult<LaunchResult> {
    let cfg = LaunchConfig::master_from_pydict(config)?;
    py.detach(|| process::start_process(&cfg))
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

#[pyfunction]
fn start_worker(py: Python<'_>, config: &Bound<'_, PyDict>) -> PyResult<LaunchResult> {
    let cfg = LaunchConfig::worker_from_pydict(config)?;
    py.detach(|| process::start_process(&cfg))
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

#[pyfunction]
fn stop_process(py: Python<'_>, pid: u32, force: bool) -> PyResult<bool> {
    Ok(py.detach(|| process::stop_process(pid, force)))
}

#[pyfunction]
fn process_alive(py: Python<'_>, pid: u32) -> PyResult<bool> {
    Ok(py.detach(|| process::process_alive(pid)))
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("RESOURCE_SCHEMA_VERSION", 2u32)?;
    m.add("RUNTIME_SELECTION_VERSION", 1u32)?;
    m.add_function(wrap_pyfunction!(network::arp_conflict::probe_ipv4_conflict, m)?)?;
    #[cfg(unix)]
    {
        m.add_class::<network::arp_conflict::IPv4ConflictObserver>()?;
        m.add_function(wrap_pyfunction!(network::arp_conflict::start_ipv4_conflict_observer, m)?)?;
    }
    m.add_class::<network::address_events::AddressObserver>()?;
    m.add_function(wrap_pyfunction!(network::address_events::start_address_observer, m)?)?;
    m.add_class::<network::relay::NetworkBridge>()?;
    m.add_function(wrap_pyfunction!(network::relay::start_network_bridge, m)?)?;
    m.add_function(wrap_pyfunction!(network::rpc_probe::probe_network_rpc, m)?)?;
    m.add_class::<usage::UsageCollector>()?;
    m.add_function(wrap_pyfunction!(_process_identity, m)?)?;
    m.add_function(wrap_pyfunction!(_validate_source_json, m)?)?;
    m.add_function(wrap_pyfunction!(_read_gguf_metadata_json, m)?)?;
    m.add_function(wrap_pyfunction!(probe_capacity, m)?)?;
    m.add_function(wrap_pyfunction!(list_gpus, m)?)?;
    m.add_function(wrap_pyfunction!(_probe_capacity_and_gpus, m)?)?;
    m.add_function(wrap_pyfunction!(plan_split, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_llama_server, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_rpc_server, m)?)?;
    m.add_function(wrap_pyfunction!(start_master, m)?)?;
    m.add_function(wrap_pyfunction!(start_worker, m)?)?;
    m.add_function(wrap_pyfunction!(stop_process, m)?)?;
    m.add_function(wrap_pyfunction!(process_alive, m)?)?;
    m.add_class::<CapacitySnapshot>()?;
    m.add_class::<GpuDevice>()?;
    m.add_class::<NodeCapacity>()?;
    m.add_class::<PlanResult>()?;
    m.add_class::<LaunchResult>()?;
    m.add_class::<ProcessHandle>()?;
    Ok(())
}
