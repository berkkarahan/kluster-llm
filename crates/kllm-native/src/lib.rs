mod hw;
mod planner;
mod process;

use pyo3::prelude::*;
use pyo3::types::PyDict;

use hw::CapacitySnapshot;
use planner::{NodeCapacity, PlanRequest, PlanResult};
use process::{LaunchConfig, LaunchResult, ProcessHandle};

#[pyfunction]
fn probe_capacity() -> PyResult<CapacitySnapshot> {
    Ok(hw::probe_capacity())
}

#[pyfunction]
fn plan_split(request: &Bound<'_, PyDict>) -> PyResult<PlanResult> {
    let req = PlanRequest::from_pydict(request)?;
    planner::plan_split(&req).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(e.to_string())
    })
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
fn start_master(config: &Bound<'_, PyDict>) -> PyResult<LaunchResult> {
    let cfg = LaunchConfig::master_from_pydict(config)?;
    process::start_process(&cfg).map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(e.to_string())
    })
}

#[pyfunction]
fn start_worker(config: &Bound<'_, PyDict>) -> PyResult<LaunchResult> {
    let cfg = LaunchConfig::worker_from_pydict(config)?;
    process::start_process(&cfg).map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(e.to_string())
    })
}

#[pyfunction]
fn stop_process(pid: u32, force: bool) -> PyResult<bool> {
    Ok(process::stop_process(pid, force))
}

#[pyfunction]
fn process_alive(pid: u32) -> PyResult<bool> {
    Ok(process::process_alive(pid))
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(probe_capacity, m)?)?;
    m.add_function(wrap_pyfunction!(plan_split, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_llama_server, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_rpc_server, m)?)?;
    m.add_function(wrap_pyfunction!(start_master, m)?)?;
    m.add_function(wrap_pyfunction!(start_worker, m)?)?;
    m.add_function(wrap_pyfunction!(stop_process, m)?)?;
    m.add_function(wrap_pyfunction!(process_alive, m)?)?;
    m.add_class::<CapacitySnapshot>()?;
    m.add_class::<NodeCapacity>()?;
    m.add_class::<PlanResult>()?;
    m.add_class::<LaunchResult>()?;
    m.add_class::<ProcessHandle>()?;
    Ok(())
}
