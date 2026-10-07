export type AccelBackend = 'metal' | 'cuda' | 'rocm' | 'vulkan';
export type SplitMode = 'layer' | 'row' | 'tensor';
export type RpcStatus = 'idle' | 'starting' | 'serving' | 'recovering' | 'failed' | 'cleanup_pending';

export type GpuInfo = {
  index: number;
  name: string;
  vram_bytes?: number | null;
  device?: string;
  backend?: string;
  free_bytes?: number;
};

export type Member = {
  node_id: string;
  host: string;
  role?: string;
  backend?: string;
  actual_backend?: string;
  requested_backend?: string | null;
  devices?: GpuInfo[];
  rpc_devices?: { index: number; device: string; backend: string; memory_kind: 'gpu' | 'ram' }[];
  rpc_capabilities?: Member['rpc_devices'];
  rpc_control_version?: number;
  rpc_status?: RpcStatus;
  rpc_error?: string | null;
  unified_memory?: boolean;
  binary_version?: string;
  vram_bytes?: number | null;
  total_ram_bytes?: number;
  available_ram_bytes?: number;
  control_port?: number;
  rpc_port?: number;
  gpu_ids?: number[] | null;
  latency_ms?: number | null;
  process_pid?: number | null;
  process_role?: string | null;
};

export type DownloadProgress = {
  filename: string;
  bytes: number;
  total: number | null;
  pct: number;
};

/** Live download job view from /v2/models/downloads/current. */
export type DownloadJob = {
  state: 'idle' | 'running' | 'complete' | 'error' | 'cancelled' | 'unknown';
  filename?: string;
  bytes?: number;
  total?: number | null;
  pct?: number;
  phase?: string;
  rate_bps?: number | null;
  eta_seconds?: number | null;
  error?: string | null;
  operation_id?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
  finished_at?: string | null;
};

/** Live inference load view from /v2/inference/load. */
export type LoadProgress = {
  phase: 'starting' | 'loading' | 'ready' | 'error' | 'cancelled' | 'unknown' | null;
  model: string | null;
  bytes_loaded: number;
  bytes_total: number;
  fraction: number;
  rate_bps?: number | null;
  eta_seconds?: number | null;
  error?: string;
  operation_id?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
  finished_at?: string | null;
};

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

/** Per-model run settings as stored by PUT /v2/inference/model-settings/{model}. */
export type ParameterSet = Record<string, unknown>;

export type ParameterDescriptor = {
  id: string; cli: string; section: string; scope: string; memory_affecting: boolean;
  available: boolean; unavailable_reason?: string | null; applies: string;
  type?: string; default?: unknown; minimum?: number; maximum?: number;
  enum?: string[]; choices?: string[];
  anyOf?: { type?: string; minimum?: number; maximum?: number; enum?: string[] }[];
};

export type ExpertFlag = { flag: string; arguments: string[]; description: string };
export type EffectiveConfig = {
  schema_version: number; model: string; process_pid?: number; run_id?: string;
  requested: ParameterSet; values: ParameterSet; sources: Record<string, string>;
  adjustments: Record<string, string>; generation_defaults: ParameterSet;
};

export type ModelSettingsDoc = {
  model: string;
  parameter_set: ParameterSet;
  updated_at: string | null;
};

export type UpdateWorkerProgress = {
  node_id?: string | null;
  host?: string | null;
  phase: string;
  error?: string | null;
  git_head?: string | null;
  llama_release?: string | null;
  rebuild?: string | null;
};

export type UpdateProgress = {
  phase?: string;
  pct?: number;
  target_git_head?: string | null;
  target_llama_release?: string | null;
  workers: UpdateWorkerProgress[];
  error?: string | null;
};

export type SoftwareIdentity = {
  git_head?: string | null;
  llama_release?: string | null;
};

export type HomePaths = {
  home: string;
  models: string;
  logs: string;
};

export type HfTokenSource = 'file' | 'env' | null;

export type HfTokenStatus = {
  configured: boolean;
  source: HfTokenSource;
};

export type Status = {
  runtimes?: import('./runtime-types').RuntimeLibrary;
  active_config?: EffectiveConfig | null;
  managed_networks?: ManagedNetwork[];
  feature_flags?: Record<string, boolean>;
  logging?: LoggingStatus;
  role: string;
  node_id: string;
  join_key?: string | null;
  master_host?: string | null;
  master_port?: number;
  members: Record<string, Member>;
  process_pid?: number | null;
  process_role?: string | null;
  last_plan?: Record<string, unknown> | null;
  model_name?: string | null;
  capacity?: Record<string, unknown>;
  control_port?: number;
  rpc_port?: number;
  infer_port?: number;
  inference_up?: boolean;
  inference_activity?: InferenceActivity | null;
  device?: 'cpu' | 'gpu';
  vram_percent?: number;
  ram_percent?: number;
  config_gpu_ids?: number[] | null;
  accel_backend?: AccelBackend | null;
  split_mode?: SplitMode;
  gpu_ids?: number[] | null;
  rpc_bind?: string | null;
  rpc_lifecycle?: { status: RpcStatus; workers?: string[]; retries?: number; error?: string | null };
  overlay?: 'none' | 'wireguard' | string;
  wg_address?: string | null;
  overlay_status?: string | null;
  netsec_warnings?: string[];
  apply_error?: string | null;
  apply_warning?: string | null;
  /** Live GGUF download; null/absent when idle. */
  download?: DownloadProgress | null;
  /** Live cluster software update on master; null/absent when idle. */
  update?: UpdateProgress | null;
  /** Local checkout identity (git HEAD + llama-release pin). */
  software?: SoftwareIdentity | null;
  rpc_cache?: { enabled: boolean; path: string; exists: boolean; persistent: boolean };
};

export type DeviceAllocation = {
  memory_kind?: 'gpu' | 'ram';
  node_id: string;
  device: string;
  backend: string;
  layer_start: number;
  layer_end: number;
  layers: number;
  output_layer: boolean;
  estimated_bytes: number;
  budget_bytes: number;
};

export type PlacementPlan = {
  required_nodes?: string[];
  node_priority?: string[];
  node_ram_budget_bytes?: Record<string, number>;
  node_ram_alloc_bytes?: Record<string, number>;
  node_vram_alloc_bytes?: Record<string, number>;
  device_allocations?: DeviceAllocation[];
  device_order?: string[];
  estimate_confidence?: string;
  resolved_launch_settings?: Record<string, unknown>;
  cpu_layers?: number;
  cpu_alloc_bytes?: number;
  [key: string]: unknown;
};

export type ClusterView = 'loading' | 'offline' | 'idle' | 'live';

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

export type LogSource = 'agent' | 'llama' | 'rpc' | 'unknown';

/** Producer tag. `ui` is the control application, `inference` is model serving, `unclassified` is an unknown producer. */
export type LogTag = 'ui' | 'inference' | 'unclassified';

/** One recorded cluster log line from GET /v2/logs. */
export type LogEntry = {
  event_id: string;
  ts: number;
  node_id: string;
  source: LogSource;
  tag: LogTag;
  line: string;
};

/** Bounded recent snapshot plus a resumable receiver-local checkpoint. */
/** Stable producer identity used only by explicitly negotiated log streams. */
export type LogPosition = { slot: number; generation: number; sequence: number };
export type PositionedLogEvent = LogEntry & { position: LogPosition };
export type PositionFrontier = {
  domain_id: string;
  frontier_revision: number;
  retired_through: { slot: number; generation: number; sequence: number }[];
};

export type LogSnapshot = {
  lines: LogEntry[];
  source: 'cluster' | 'local';
  warning?: string;
  cursor: string;
  reset: 'origin_changed' | 'restart' | null;
  gap: boolean;
  gap_reason: 'retention' | 'batch_limit' | 'feed_reset' | null;
};

export type RefreshMode = 'live' | '5s' | '30s' | '1m';

export type LiveAvailability = 'loading' | 'available' | 'unavailable' | 'denied';
export type LiveCoverage = 'local' | 'cluster' | 'local_with_cluster_view';
export type DeliveryHealth = 'loading' | 'live' | 'reconnecting' | 'periodic-refresh' | 'unavailable' | 'paused';
export type LiveAreaName = 'status' | 'usage' | 'load' | 'download';

export type LiveError = { code: string; message: string };

export type LiveArea<T> = {
  revision: number;
  sampled_at: string | null;
  availability: LiveAvailability;
  coverage: LiveCoverage;
  upstream_last_success_at: string | null;
  data: T | null;
  error: LiveError | null;
};

export type LiveAreaHealth = {
  availability: LiveAvailability;
  coverage: LiveCoverage;
  last_success_at: string | null;
  error: LiveError | null;
};

export type LiveLogSnapshot = LogSnapshot & {
  continuity_id: string;
  discontinuity: { id: string; reason: string; detected_at: string } | null;
};

export type LiveLogs = {
  availability: LiveAvailability;
  sampled_at: string | null;
  snapshot: LiveLogSnapshot | null;
  error: LiveError | null;
};

export type LiveProgress<T> = { current: T | null; last_terminal: T | null };

export type LiveSnapshot = {
  version: 1;
  transport: 'websocket';
  epoch: string;
  server_time: string;
  areas: Partial<{
    status: LiveArea<Status>;
    usage: LiveArea<UsageLive>;
    load: LiveArea<LiveProgress<LoadProgress>>;
    download: LiveArea<LiveProgress<DownloadJob>>;
  }>;
  logs: LiveLogs | null;
};

export type ModelDefaultsInfo = {
  sampling?: Record<string, number>;
  chat_template_kwargs?: Record<string, unknown>;
  reasoning?: Record<string, unknown>;
  sources?: Record<string, string>;
  architecture?: string;
  context_length?: number;
  reasoning_format?: string;
};

export type ModelInfo = {
  id: string;
  filename?: string;
  size_bytes?: number;
  loaded?: boolean;
  /** GGUF `{arch}.context_length` when readable */
  max_context?: number;
  /** GGUF + Unsloth/LM Studio resolved defaults */
  defaults?: ModelDefaultsInfo;
};

export type ModelSearchFit = 'fits' | 'overflow' | 'unknown';

export type ModelSearchHit = {
  repo_id: string | null;
  filename: string;
  /** Full repository-relative Hub path. Absent or null on older servers and direct URLs. */
  path?: string | null;
  /** Effective Hub revision. Absent or null on older servers and direct URLs. */
  revision?: string | null;
  quant: string | null;
  size_bytes: number;
  gated: boolean;
  source: string;
  fit: ModelSearchFit;
};

export type ModelSearchResponse = {
  query: string;
  total_vram_bytes: number | null;
  results: ModelSearchHit[];
  /** Sanitized partial keyword-search failures. Omitted or empty means none. */
  warnings?: string[];
};

export const MIN_CONTEXT = 512;
export const CONTEXT_STEP = 512;
/** Fallback slider max when model metadata is unavailable */
export const DEFAULT_MAX_CONTEXT = 131_072;

export type ChatMessage = {
  role: 'user' | 'assistant' | 'system';
  content: string;
};

/** llama.cpp --cache-type-k / --cache-type-v allowlist */
export const KV_CACHE_TYPES = [
  'f32',
  'f16',
  'bf16',
  'q8_0',
  'q4_0',
  'q4_1',
  'iq4_nl',
  'q5_0',
  'q5_1'
] as const;

export type KvCacheType = (typeof KV_CACHE_TYPES)[number];

export function normalizeKvCacheType(value: string): KvCacheType {
  return (KV_CACHE_TYPES as readonly string[]).includes(value)
    ? (value as KvCacheType)
    : 'q8_0';
}


export type NetworkAdapter = {
  adapter_id: string;
  os_interface: string;
  inventory_revision: string;
  connected: boolean;
  link_speed_mbps: number | null;
  dhcp: boolean | null;
  permission: boolean;
  physical_ethernet: boolean;
  addresses: string[];
};
export type NetworkIdentity = {
  node_id: string;
  key_id: string;
  public_key: string;
  cluster_binding_id: string | null;
  permission: boolean;
  paired_peers: Array<{node_id: string; key_id: string; expected_role: string}>;
};
export type NetworkInventory = {
  node_id: string;
  adapters: NetworkAdapter[];
  selection?: string | null;
  error_code?: string;
};
export type ManagedNetwork = {
  network_id: string;
  subnet: string;
  revision: number;
  desired_state: string;
  observed_state: string;
  current_operation_id: string | null;
  participants: Array<{
    node_id: string; adapter_id: string; address: string; prefix_length: number;
    state: string; ownership: string; link_speed_mbps: number | null;
    logical_rpc_endpoint: string | null; verification_freshness: string;
    error?: {code: string; message: string} | null;
  }>;
  blocking_runs: Array<{run_id: string; state: string}>;
};
