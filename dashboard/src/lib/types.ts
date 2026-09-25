export type AccelBackend = 'metal' | 'cuda' | 'rocm' | 'vulkan';

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
  unified_memory?: boolean;
  binary_version?: string;
  vram_bytes?: number | null;
  total_ram_bytes?: number;
  available_ram_bytes?: number;
  control_port?: number;
  rpc_port?: number;
  gpu_ids?: number[] | null;
  latency_ms?: number | null;
};

export type DownloadProgress = {
  filename: string;
  bytes: number;
  total: number | null;
  pct: number;
};

/** Live download job view from /v1/models/download-progress. */
export type DownloadJob = {
  state: 'idle' | 'running' | 'complete' | 'error' | 'cancelled';
  filename?: string;
  bytes?: number;
  total?: number | null;
  pct?: number;
  phase?: string;
  rate_bps?: number | null;
  eta_seconds?: number | null;
  error?: string | null;
};

/** Live inference load view from /v1/load-progress. */
export type LoadProgress = {
  phase: 'starting' | 'loading' | 'ready' | 'error' | null;
  model: string | null;
  bytes_loaded: number;
  bytes_total: number;
  fraction: number;
  rate_bps?: number | null;
  eta_seconds?: number | null;
  error?: string;
};

/** Per-model run settings as stored by PUT /v1/model-settings/{model}. */
export type ParameterSet = Record<string, unknown>;

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
  role: string;
  node_id: string;
  join_key?: string | null;
  master_host?: string | null;
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
  device?: 'cpu' | 'gpu';
  vram_percent?: number;
  ram_percent?: number;
  config_gpu_ids?: number[] | null;
  accel_backend?: AccelBackend | null;
  gpu_ids?: number[] | null;
  rpc_bind?: string | null;
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
  device_allocations?: DeviceAllocation[];
  device_order?: string[];
  estimate_confidence?: string;
  resolved_launch_settings?: Record<string, unknown>;
  cpu_layers?: number;
  cpu_alloc_bytes?: number;
  [key: string]: unknown;
};

export type ClusterView = 'loading' | 'offline' | 'idle' | 'live';

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
  repo_id: string;
  filename: string;
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
