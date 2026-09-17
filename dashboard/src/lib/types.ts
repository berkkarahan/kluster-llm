export type GpuInfo = {
  index: number;
  name: string;
  vram_bytes?: number | null;
};

export type Member = {
  node_id: string;
  host: string;
  role?: string;
  backend?: string;
  vram_bytes?: number | null;
  total_ram_bytes?: number;
  available_ram_bytes?: number;
  control_port?: number;
  rpc_port?: number;
  gpu_ids?: number[] | null;
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
  gpu_ids?: number[] | null;
  rpc_bind?: string | null;
  overlay?: 'none' | 'wireguard' | string;
  wg_address?: string | null;
  overlay_status?: string | null;
  netsec_warnings?: string[];
};

export type ModelInfo = {
  id: string;
  filename?: string;
  size_bytes?: number;
  loaded?: boolean;
};

export type ChatMessage = {
  role: 'user' | 'assistant' | 'system';
  content: string;
};
