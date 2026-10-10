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

export type HfTokenSource = 'file' | 'env' | null;

export type HfTokenStatus = {
  configured: boolean;
  source: HfTokenSource;
};
