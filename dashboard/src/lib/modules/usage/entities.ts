import type { GpuInfo, RpcStatus } from '../../shared/entities';

/** Live serving state sampled from llama-server slots on the master. */
export type InferenceActivity = {
  phase: 'idle' | 'starting' | 'loading_model' | 'processing_input' | 'generating' | 'ready' | 'error';
  active_requests: number;
  prompt_tokens: number;
  prompt_tokens_processed: number;
  output_tokens: number;
  slots: number;
  server_ready: boolean;
  load_fraction?: number;
  detail?: string | null;
};

export type UsageDevice = {
  device_id: string;
  scope: 'device';
  utilization_percent: number | null;
  used_bytes: number | null;
  total_bytes: number | null;
  temperature_c: number | null;
  power_w: number | null;
  unavailable_reason?: string | null;
};

export type UsageReading = {
  lifetime_id: string;
  pid: number;
  process_role: 'master' | 'worker';
  process_started_at: string;
  observed_at: string;
  received_at?: string;
  process: { cpu_percent_machine: number | null; rss_bytes: number | null };
  host: { cpu_percent: number | null; used_bytes: number | null; total_bytes: number | null };
  devices: UsageDevice[];
  availability: Record<string, 'measured' | 'initializing' | 'unsupported' | 'error'>;
  collector_error?: string | null;
};

export type RecorderStatus = {
  recording: 'recording' | 'interrupted' | 'failed';
  error?: string | null;
  last_successful_save: string | null;
  earliest_retained?: string | null;
  latest_retained?: string | null;
  storage_truncated?: boolean;
};

export type ResourceMetric = {
  value: number | null;
  unit: 'bytes' | 'percent_machine' | 'percent_device';
  scope: 'host' | 'process' | 'device';
  status: 'measured' | 'initializing' | 'unsupported' | 'error';
  reason: string | null;
  detail: string | null;
  observed_at: string;
  interval_start: string | null;
  interval_end: string | null;
  duration_ms: number | null;
  source: string;
};

export type ResourceSnapshot = {
  schema_version: 1 | 2; node_id: string; generation: string; sequence: number; snapshot_id: string;
  requested_at: string; completed_at: string;
  host: { logical_cpu_count: number | null; cpu: ResourceMetric;
    memory: { pool_id: string; total: ResourceMetric; used: ResourceMetric; free: ResourceMetric; available: ResourceMetric } };
  devices: { device_key: string; name: string; vendor: string; memory_kind: 'dedicated' | 'shared' | 'mixed' | 'unknown';
    utilization?: ResourceMetric | null;
    inventory_device_id: string | null; memory: { kind: 'dedicated' | 'shared'; pool_id: string; backing_pool_id: string | null;
      total: ResourceMetric; used: ResourceMetric; free: ResourceMetric }[] }[];
  device_discovery: { status: 'measured' | 'error' | 'unsupported'; reason: string | null; observed_at: string };
  processes: { pid: number; role: 'master' | 'worker'; start_token: string; started_at: string; node_id: string;
    lifetime_id: string; state: 'running' | 'exited' | 'unavailable'; identity_verified: boolean; cpu: ResourceMetric; rss: ResourceMetric }[];
  totals: Record<'dedicated_capacity' | 'dedicated_used' | 'physical_capacity', {
    value: number | null; unit: 'bytes'; status: 'complete' | 'partial' | 'unavailable';
    included_pool_ids: string[]; missing_pool_ids: string[]; observed_at: string[] }>;
  collector: { kind: 'native' | 'legacy'; platform: string };
};

export type LoggingStatus = {
  enabled: boolean; generation: string; status: 'applied' | 'partial';
  nodes: Record<string, { status: 'applied' | 'pending' | 'unsupported' | 'error' | 'unreachable' }>;
};

export type UsageNode = {
  node_id: string;
  host: string;
  address?: string | null;
  role: 'master' | 'worker';
  rpc_status?: RpcStatus;
  backend?: string | null;
  devices?: GpuInfo[];
  unified_memory?: boolean;
  total_ram_bytes?: number | null;
  vram_bytes?: number | null;
  assignment?: string | null;
  connected: boolean;
  process_state?: 'running' | 'ended' | 'unobserved' | 'unavailable' | 'no managed process';
  freshness: 'fresh' | 'stale' | 'unavailable';
  age_seconds?: number | null;
  reading: UsageReading | null;
  resources?: ResourceSnapshot | null;
  resource_age_seconds?: number | null;
  resource_freshness?: 'fresh' | 'stale' | 'unavailable';
};

export type UsageLive = {
  resource_schema_versions?: number[];
  nodes: Record<string, UsageNode>;
  relationships: { from_node_id: string; to_node_id: string; kind: 'master-worker' }[];
  recording: RecorderStatus;
};

export type UsageLifetime = {
  lifetime_id: string;
  pid: number;
  role: 'master' | 'worker';
  started_at: string;
  ended_at: string | null;
  state: 'running' | 'ended' | 'unobserved';
  model?: string | null;
  associations?: { model: string; observed_from: string; observed_until: string | null }[];
};

export type UsageCoverage = RecorderStatus & {
  coverage: 'recorded' | 'unrecorded';
  earliest_retained: string | null;
  latest_retained: string | null;
  storage_truncated: boolean;
};

export type UsageLifetimes = UsageCoverage & {
  node_id: string;
  host: string;
  connected: boolean;
  lifetimes: UsageLifetime[];
  retained_node_ids: string[];
};

export type UsageBucket = {
  start: string;
  end: string;
  count: number;
  process_cpu_min: number | null;
  process_cpu_max: number | null;
  process_rss_min: number | null;
  process_rss_max: number | null;
  host_cpu_min: number | null;
  host_cpu_max: number | null;
  host_used_bytes_min: number | null;
  host_used_bytes_max: number | null;
  devices: {
    device_id: string;
    scope: 'device';
    total_bytes: number | null;
    unavailable_reason?: string | null;
    utilization_percent_min?: number | null;
    utilization_percent_max?: number | null;
    used_bytes_min?: number | null;
    used_bytes_max?: number | null;
    temperature_c_min?: number | null;
    temperature_c_max?: number | null;
    power_w_min?: number | null;
    power_w_max?: number | null;
  }[];
};

export type UsageSeries = UsageCoverage & {
  window_start: string;
  window_end: string;
  time_zone: 'UTC';
  buckets: UsageBucket[];
};

export type UsagePage = UsageCoverage & {
  window_start: string;
  window_end: string;
  time_zone: 'UTC';
  readings: UsageReading[];
  next_cursor: string | null;
};
