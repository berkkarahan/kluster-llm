"""SQLite declarations for Prima Prepare & Profile lifecycle, stages, and measurements."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)

schema = MetaData()

# 1. Top-Level Profiling Run Lifecycle
profiling_runs = Table(
    "profiling_runs",
    schema,
    Column("run_id", Text, primary_key=True),
    Column("request_id", Text, nullable=False, unique=True),
    Column("request_digest", Text, nullable=False),
    Column("runtime_id", Text, nullable=False),
    Column("model_path", Text, nullable=False),
    Column("model_sha256", Text, nullable=False),
    Column("network_id", Text, nullable=True),
    Column("status", Text, nullable=False),  # queued, running, completed, failed, cancelled, interrupted
    Column("current_stage", Text, nullable=False),  # materialize, asset_sync, node_profile, link_profile, plan_qualification
    Column("include_profile", Boolean, nullable=False, default=False),
    Column("plan_digest", Text, nullable=False),
    Column("manifest_digest", Text, nullable=True),
    Column("command_owner", Text, nullable=False),  # JSON
    Column("settings_json", Text, nullable=False),  # JSON
    Column("manifest_json", Text, nullable=True),  # JSON
    Column("created_at", Float, nullable=False),
    Column("started_at", Float, nullable=True),
    Column("updated_at", Float, nullable=False),
    Column("finished_at", Float, nullable=True),
    Column("expires_at", Float, nullable=True),
    Column("error_message", Text, nullable=True),
    Column("summary_json", Text, nullable=True),  # JSON
)
Index("ix_profiling_runs_status", profiling_runs.c.status)
Index("ix_profiling_runs_model", profiling_runs.c.model_sha256)
Index("ix_profiling_runs_runtime", profiling_runs.c.runtime_id)
Index("ix_profiling_runs_created", profiling_runs.c.created_at)

# 2. Sequential & Resumable Run Stages
profiling_stages = Table(
    "profiling_stages",
    schema,
    Column("id", Text, primary_key=True),  # Format: {run_id}:{stage_name}
    Column("run_id", Text, ForeignKey("profiling_runs.run_id", ondelete="CASCADE"), nullable=False),
    Column("stage_name", Text, nullable=False),
    Column("stage_order", Integer, nullable=False),
    Column("status", Text, nullable=False),  # pending, running, completed, failed, skipped, cancelled
    Column("attempt", Integer, nullable=False, default=1),
    Column("max_attempts", Integer, nullable=False, default=3),
    Column("progress_fraction", Float, nullable=False, default=0.0),
    Column("progress_label", Text, nullable=False, default=""),
    Column("bytes_done", Integer, nullable=False, default=0),
    Column("bytes_total", Integer, nullable=False, default=0),
    Column("started_at", Float, nullable=True),
    Column("updated_at", Float, nullable=False),
    Column("finished_at", Float, nullable=True),
    Column("error_message", Text, nullable=True),
    UniqueConstraint("run_id", "stage_name", name="uq_run_stage"),
)
Index("ix_profiling_stages_run_order", profiling_stages.c.run_id, profiling_stages.c.stage_order)

# 3. Participating Cluster Nodes
profiling_nodes = Table(
    "profiling_nodes",
    schema,
    Column("id", Text, primary_key=True),  # Format: {run_id}:{node_id}
    Column("run_id", Text, ForeignKey("profiling_runs.run_id", ondelete="CASCADE"), nullable=False),
    Column("node_id", Text, nullable=False),
    Column("role", Text, nullable=False),  # master, worker
    Column("rank_index", Integer, nullable=False),
    Column("assigned_address", Text, nullable=False),
    Column("backend", Text, nullable=False),
    Column("devices_json", Text, nullable=False, default="[]"),
    Column("status", Text, nullable=False),  # pending, preparing, prepared, failed, cancelled
    Column("remote_job_id", Text, nullable=True),
    Column("updated_at", Float, nullable=False),
    UniqueConstraint("run_id", "node_id", name="uq_run_node"),
)

# 4. Granular Per-Node Stage Tasks
profiling_node_stage_tasks = Table(
    "profiling_node_stage_tasks",
    schema,
    Column("id", Text, primary_key=True),  # Format: {run_id}:{stage_name}:{node_id}
    Column("run_id", Text, ForeignKey("profiling_runs.run_id", ondelete="CASCADE"), nullable=False),
    Column("stage_name", Text, nullable=False),
    Column("node_id", Text, nullable=False),
    Column("status", Text, nullable=False),  # pending, running, completed, failed, cancelled
    Column("progress_fraction", Float, nullable=False, default=0.0),
    Column("bytes_done", Integer, nullable=False, default=0),
    Column("bytes_total", Integer, nullable=False, default=0),
    Column("details_json", Text, nullable=False, default="{}"),
    Column("started_at", Float, nullable=True),
    Column("finished_at", Float, nullable=True),
    Column("error_message", Text, nullable=True),
    UniqueConstraint("run_id", "stage_name", "node_id", name="uq_run_stage_node"),
)

# 5. Authoritative Measured Hardware & Kernel Profiles
measured_node_profiles = Table(
    "measured_node_profiles",
    schema,
    Column("id", Text, primary_key=True),  # Format: {run_id}:{node_id}
    Column("run_id", Text, ForeignKey("profiling_runs.run_id", ondelete="CASCADE"), nullable=False),
    Column("node_id", Text, nullable=False),
    Column("profile_key", Text, nullable=False),
    Column("measured_at", Float, nullable=False),
    Column("backend", Text, nullable=False),
    Column("measurement_kind", Text, nullable=False),  # kernel_estimate, end_to_end
    Column("cpu_layer_ms", Float, nullable=False),
    Column("gpu_layer_ms", Float, nullable=True),
    Column("disk_read_bytes_s", Float, nullable=True),
    Column("disk_random_read_bytes_s", Float, nullable=True),
    Column("memory_available_bytes", Integer, nullable=False),
    Column("peak_memory_bytes", Integer, nullable=True),
    Column("prefill_ms", Float, nullable=True),
    Column("decode_ms", Float, nullable=True),
    Column("gpu_layers", Integer, nullable=True),
    Column("total_layers", Integer, nullable=True),
    Column("profile_json", Text, nullable=False),  # Full DeviceProfile JSON
    UniqueConstraint("run_id", "node_id", name="uq_run_measured_profile"),
)
Index("ix_measured_node_profiles_key", measured_node_profiles.c.profile_key)

# 6. Pairwise Inter-Node Ring-TCP Link Profiles
measured_link_profiles = Table(
    "measured_link_profiles",
    schema,
    Column("id", Text, primary_key=True),  # Format: {run_id}:{source_node_id}->{target_node_id}
    Column("run_id", Text, ForeignKey("profiling_runs.run_id", ondelete="CASCADE"), nullable=False),
    Column("source_node_id", Text, nullable=False),
    Column("target_node_id", Text, nullable=False),
    Column("grant_id", Text, nullable=False),
    Column("network_id", Text, nullable=True),
    Column("source_address", Text, nullable=False),
    Column("target_address", Text, nullable=False),
    Column("latency_ms", Float, nullable=False),
    Column("bandwidth_bytes_s", Float, nullable=False),
    Column("samples", Integer, nullable=False, default=3),
    Column("sample_seconds_json", Text, nullable=False),
    Column("measured_at", Float, nullable=False),
    Column("receipt_json", Text, nullable=False),
    UniqueConstraint("run_id", "source_node_id", "target_node_id", name="uq_run_link"),
)
Index("ix_measured_link_profiles_nodes", measured_link_profiles.c.source_node_id, measured_link_profiles.c.target_node_id)

# 7. Persistent Structured Diagnostic & Progress Logs
profiling_logs = Table(
    "profiling_logs",
    schema,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Text, ForeignKey("profiling_runs.run_id", ondelete="CASCADE"), nullable=False),
    Column("stage_name", Text, nullable=True),
    Column("node_id", Text, nullable=True),
    Column("timestamp", Float, nullable=False),
    Column("level", Text, nullable=False),  # DEBUG, INFO, WARN, ERROR
    Column("category", Text, nullable=False),  # lifecycle, asset, kernel, network, planner
    Column("message", Text, nullable=False),
    Column("details_json", Text, nullable=True, default="{}"),
)
Index("ix_profiling_logs_run_time", profiling_logs.c.run_id, profiling_logs.c.timestamp)

# 8. Finalized Plan Qualification & HiGHS Schedule Results
profiling_qualification_results = Table(
    "profiling_qualification_results",
    schema,
    Column("run_id", Text, ForeignKey("profiling_runs.run_id", ondelete="CASCADE"), primary_key=True),
    Column("fit_status", Text, nullable=False),  # fit, overflow, unfeasible
    Column("predicted_decode_ms", Float, nullable=True),
    Column("predicted_weight_read_ms", Float, nullable=True),
    Column("scheduler_strategy", Text, nullable=False),
    Column("scheduler_report_json", Text, nullable=False),
    Column("calculated_at", Float, nullable=False),
    Column("qualification_passed", Boolean, nullable=False, default=False),
    Column("qualification_errors_json", Text, nullable=False, default="[]"),
)
