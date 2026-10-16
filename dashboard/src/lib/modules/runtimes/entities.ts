export type RuntimeVersion = {
  engine?: 'llamacpp' | 'primacpp'; recipe_digest?: string;
  runtime_id: string; repository: string; commit: string; ref: string;
  ref_type: 'tag' | 'branch' | 'commit'; name: string;
};
export type RuntimeBuild = {
  engine?: 'llamacpp' | 'primacpp'; transport?: string;
  runtime_id: string; build_id: string; backend: string; commit: string;
  size_bytes?: number; platform?: string; architecture?: string;
  mode?: 'prebuilt' | 'source'; prebuilt?: boolean; asset?: string;
  /** Prima managed builds probe the staged server; absent for llama.cpp builds. */
  capabilities?: {
    backends?: string[];
    features?: string[];
    devices?: { backend?: string; name?: string; description?: string }[];
  };
  /**
   * Source-controlled qualification policy for this build; the six Prima gates
   * are the capability contract. llama.cpp builds have none (zero key).
   */
  capability_keys?: string[];
};
export type RuntimeJob = {
  job_id: string | null; runtime_id: string; backend: string; phase: string;
  error?: string | null; created_at?: number;
};
export type RuntimeTarget = {
  version: RuntimeVersion; revision: number; cluster_binding_id: string; master_node_id: string;
};
export type RuntimeNode = {
  node_id: string; name?: string; address?: string | null; role: string; capability: number; phase: string;
  target_revision?: number; prepared?: RuntimeBuild | null; running?: RuntimeBuild | null;
  job?: RuntimeJob | null; error?: string | null; target_state?: RuntimeTargetState;
  target_error?: string | null; channel?: { connected: boolean; error?: string | null };
};
export type RuntimeTargetState = 'current' | 'label_stale' | 'binding_stale' | 'absent';
export type RuntimeScope = `${'windows' | 'linux' | 'darwin'}-vulkan-${'local' | 'distributed'}`;
export type RuntimeLibrary = {
  versions: { version: RuntimeVersion; build: RuntimeBuild | null; inference_supported?: boolean; qualification_errors?: string[];
    scope?: RuntimeScope | null;
    qualified_models?: {sha256: string; name?: string}[];
    qualified_features?: string[]; qualified_placements?: string[];
  }[];
  jobs: RuntimeJob[]; target: RuntimeTarget | null; selected: string | null;
  running: (RuntimeVersion & RuntimeBuild) | null; nodes?: RuntimeNode[];
  target_state?: RuntimeTargetState; target_error?: string | null;
  legacy?: { label: string; phase: string; message: string };
  reconcile_error?: string | null;
  bootstrap?: {repository: string; ref: string};
  control?: {available: boolean; message?: string | null};
  engine_sources?: { engine: 'primacpp'; repository: string; ref: string; ref_type: 'commit';
    release?: string; release_commit?: string; recipe_digest?: string; qualification_errors: string[] }[];
  prima_preparation?: Record<string, any> | null;
};
export type RuntimeSources = {
  repository: string; releases: {tag: string; name: string; prerelease: boolean}[];
  tags: string[]; branches: string[]; warning: string | null;
};
import type {
  HighsCycleCandidate,
  HighsNodeAllocation,
  HighsPrunedNode,
  HighsScheduleReport,
  MemoryPressureSet,
  PlacementStrategyMode,
  SchedulerMode,
} from '../run/entities';

export type {
  HighsCycleCandidate,
  HighsNodeAllocation,
  HighsPrunedNode,
  HighsScheduleReport,
  MemoryPressureSet,
  PlacementStrategyMode,
  SchedulerMode,
};

/** GET /v2/node/prima-devices — the selected Prima build's own device probe. */
export type PrimaDeviceReport = {
  available: boolean;
  reason?: string;
  features?: string[];
  backends?: string[];
  devices?: {
    backend?: string; name?: string; description?: string;
    device_identity?: string; driver_identity?: string; physical_index?: number;
  }[];
};

export type PrimaPlanPreview = {
  runtime_id: string;
  plan_digest: string;
  inventory_digest: string;
  candidate_count: number;
  placement_mode?: SchedulerMode;
  scheduler?: HighsScheduleReport;
  profiling?: {
    status: string;
    predicted_decode_ms: number | null;
    reason?: string;
    scheduler?: HighsScheduleReport;
  };
  prima_topology?: {
    total_layers: number;
    ranks: {
      node_id: string;
      rank: number;
      layers: { start: number; end: number }[];
      devices: string[];
      ngl: number;
      tensor_split: number[];
    }[];
  } | null;
  qualification_errors?: string[];
  network_edges?: Record<string, unknown>[];
  [key: string]: unknown;
};
