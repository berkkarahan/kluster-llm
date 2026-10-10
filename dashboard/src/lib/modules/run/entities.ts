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
  node_gpu_names?: Record<string, string[]>;
  device_order?: string[];
  estimate_confidence?: string;
  resolved_launch_settings?: Record<string, unknown>;
  cpu_layers?: number;
  cpu_alloc_bytes?: number;
  [key: string]: unknown;
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
