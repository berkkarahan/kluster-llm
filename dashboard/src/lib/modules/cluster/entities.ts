import type { AccelBackend, GpuInfo, RpcStatus, SplitMode } from '../../shared/entities';
import type { DownloadProgress } from '../models/entities';
import type { EffectiveConfig } from '../run/entities';
import type { InferenceActivity } from '../usage/entities';
import type { LoggingStatus } from '../logs/entities';
import type { ManagedNetwork } from '../network/entities';
import type { SoftwareIdentity } from '../node-settings/entities';
import type { RuntimeLibrary } from '../runtimes/entities';

export type { AccelBackend, GpuInfo, RpcStatus, SplitMode };

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
  lan_host?: string | null;
  /** TCP address the master last saw this worker's control connection arrive from. */
  control_source?: string | null;
  gpu_ids?: number[] | null;
  latency_ms?: number | null;
  process_pid?: number | null;
  process_role?: string | null;
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

export type NodeNetworkIdentity = {
  node_id: string;
  hostname?: string;
  role: 'master' | 'worker';
  lan_host?: string;
  rpc_bind?: string;
  private_address?: string | null;
  private_prefix_length?: number | null;
  network_id?: string | null;
  assignment_state?: string | null;
  link_speed_mbps?: number | null;
  updated_at?: string;
};

export type Status = {
  runtimes?: RuntimeLibrary;
  network_directory?: NodeNetworkIdentity[];
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
  /** Node engine mode from config (kllm start/join --engine); drives the Prima settings swap. */
  engine?: 'llamacpp' | 'primacpp';
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

export type ClusterView = 'loading' | 'offline' | 'idle' | 'live';

export type FastSubnetWorker = {
  node_id: string;
  address: string | null;
  reachable: boolean;
};

export type FastSubnetReachability = {
  network_id: string | null;
  subnet: string | null;
  master_address: string | null;
  workers: FastSubnetWorker[];
};
