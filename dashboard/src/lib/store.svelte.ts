import { formatArguments, parseArgumentText, PARAMETER_FIELDS, parameterBounds, shellQuote } from './configuration';
import type { ParameterDescriptor, ExpertFlag, EffectiveConfig } from './types';
import type {
    AccelBackend,
    ChatMessage,
    ClusterView,
    DownloadJob,
    GpuInfo,
    HfTokenSource,
    HfTokenStatus,
    LoadProgress,
    ModelDefaultsInfo,
    ModelInfo,
    ModelSearchHit,
    ModelSearchResponse,
    Status,
    Member,
    UsageLive,
    UsageLifetimes,
    UsageSeries,
    UsagePage,
    FastSubnetReachability
  } from './types';
import { DEFAULT_MAX_CONTEXT, MIN_CONTEXT, normalizeKvCacheType } from './types';
import { formatMemoryComponent } from './format-memory';
import { pushToast } from './toast';
import { errors } from './errors';
import { DashboardUpdates } from './live-updates';
import type { LiveLogListener } from './live-updates';
import type { DeliveryHealth, LiveArea, LiveAreaHealth, LiveAreaName, LiveProgress } from './types';

const ACCEL_LABELS: Record<AccelBackend, string> = {
  metal: 'Metal',
  cuda: 'CUDA',
  rocm: 'ROCm',
  vulkan: 'Vulkan'
};

function isAccelBackend(v: unknown): v is AccelBackend {
  return v === 'metal' || v === 'cuda' || v === 'rocm' || v === 'vulkan';
}

let fingerprint = '';

function v2ErrorDetailMessage(details: unknown): string {
  if (typeof details === 'string') return details;
  if (Array.isArray(details)) {
    return details
      .map((item) => (typeof item === 'object' && item !== null && typeof (item as { msg?: unknown }).msg === 'string'
        ? String((item as { msg: string }).msg) : ''))
      .filter(Boolean)
      .slice(0, 3)
      .join('; ');
  }
  if (typeof details === 'object' && details !== null) {
    const detail = details as { code?: unknown; message?: unknown };
    if (typeof detail.message === 'string' && detail.message) return detail.message;
    return Object.values(details as Record<string, unknown>)
      .filter((value): value is string => typeof value === 'string' && value.length > 0)
      .join('; ');
  }
  return '';
}

function apiErrorMessage(data: unknown, text: string, status: number, fallback: string): string {
  if (typeof data === 'object' && data !== null && 'error' in data) {
    const error = (data as { error?: { message?: unknown; details?: unknown } }).error;
    // The v2 envelope keeps the actionable text in details; message alone is
    // the generic "The request could not be completed." placeholder.
    const details = v2ErrorDetailMessage(error?.details);
    if (details) return details;
    if (typeof error?.message === 'string') return error.message;
  }
  if (typeof data === 'object' && data !== null && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === 'object' && detail !== null && 'message' in detail && typeof detail.message === 'string') {
      return `${'code' in detail ? String(detail.code) + ': ' : ''}${detail.message}`;
    }
    return String(detail);
  }
  if (typeof data === 'string') return data;
  return text || `${fallback} ${status}`;
}

/**
 * Plan-relevant projection of a member. Deliberately excludes liveness/telemetry
 * (`last_seen`, `latency_ms`, `available_ram_bytes`) that changes on every 1–2 s
 * heartbeat; including it made status polling re-plan and blank the Plan & Run
 * panel once per poll. Identity/capacity/backend changes still trip it.
 */
function memberFingerprint(m: Member): unknown {
  return {
    role: m.role,
    backend: m.backend,
    actual_backend: m.actual_backend,
    requested_backend: m.requested_backend,
    unified_memory: m.unified_memory,
    rpc_devices: m.rpc_devices,
    rpc_capabilities: m.rpc_capabilities,
    rpc_status: m.rpc_status,
    total_ram_bytes: m.total_ram_bytes,
    vram_bytes: m.vram_bytes,
    gpu_ids: m.gpu_ids,
    host: m.host,
    rpc_port: m.rpc_port,
    devices: (m.devices ?? []).map((d) => ({
      index: d.index,
      name: d.name,
      device: d.device,
      backend: d.backend,
      vram_bytes: d.vram_bytes
    }))
  };
}

function fp(s: Status | null): string {
  if (!s) return '';
  return JSON.stringify({
    role: s.role,
    runtime_target: s.runtimes?.target?.version.runtime_id ?? s.runtimes?.selected,
    runtime_prepared: (s.runtimes?.nodes ?? []).map(n => [n.node_id, n.prepared?.runtime_id, n.prepared?.build_id]),
    members: Object.fromEntries(
      Object.entries(s.members ?? {}).map(([id, m]) => [id, memberFingerprint(m)])
    ),
    model_name: s.model_name,
    inference_up: s.inference_up,
    last_plan: s.last_plan,
    process_pid: s.process_pid,
    rpc_bind: s.rpc_bind,
    overlay: s.overlay,
    wg_address: s.wg_address,
    overlay_status: s.overlay_status,
    netsec_warnings: s.netsec_warnings,
    gpu_ids: s.gpu_ids,
    apply_error: s.apply_error,
    apply_warning: s.apply_warning
  });
}

class AppStore {
  status = $state<Status | null>(null);
  liveUsage = $state<UsageLive | null>(null);
  usageError = $state('');
  selectedNodeId = $state('');
  selectedLifetimeId = $state('');
  usageWindow = $state<'15m' | '1h' | '24h'>('15m');
  usageLifetimes = $state<UsageLifetimes | null>(null);
  usageSeries = $state<UsageSeries | null>(null);
  usagePage = $state<UsagePage | null>(null);
  historyError = $state('');
  private historySeq = 0;
  connected = $state(false);
  deliveryHealth = $state<DeliveryHealth>('loading');
  deliveryError = $state('');
  areaHealth = $state<Partial<Record<LiveAreaName, LiveAreaHealth>>>({});
  statusSeen = $state(false);
  models = $state<ModelInfo[]>([]);
  apiToken = $state('');
  busy = $state('');
  fastSubnetReachability = $state<FastSubnetReachability | null>(null);
  fastSubnetProbe = $state<'idle' | 'checking' | 'done' | 'failed'>('idle');
  reconnectingNodeId = $state('');
  fastSubnetErrors = $state<Record<string, string>>({});
  joinKeyInput = $state('');
  masterHostInput = $state('');
  rpcBindInput = $state('');
  overlay = $state<'none' | 'wireguard'>('none');
  gpus = $state<GpuInfo[]>([]);
  gpuProbeError = $state('');
  gpuInstallHint = $state('');
  gpuBackendWarning = $state('');
  installedBackends = $state<AccelBackend[]>([]);
  gpuBackend = $state('cpu');
  gpuUnifiedMemory = $state<boolean | null>(null);
  probedBackend = $state('cpu');
  allowedBackends = $state<AccelBackend[]>([]);
  accelBackend = $state<AccelBackend | null>(null);
  selectedGpuIds = $state<number[]>([]);
  modelSource = $state('');
  searchResults = $state<ModelSearchHit[]>([]);
  searchTotalVram = $state<number | null>(null);
  searchError = $state('');
  searchWarnings = $state<string[]>([]);
  /** Accepted query for the open results, empty state, or error. Edits to the search field do not change it. */
  searchQuery = $state('');
  searching = $state(false);
  hfTokenInput = $state('');
  hfTokenConfigured = $state(false);
  hfTokenSource = $state<HfTokenSource>(null);
  selectedModel = $state('');
  context = $state(4096);
  contextAuto = $state(false);
  cacheTypeK = $state('q8_0');
  cacheTypeV = $state('q8_0');
  parallelSlots = $state<number | 'auto'>('auto');
  batchSize = $state<number | 'auto'>('auto');
  microBatch = $state<number | 'auto'>('auto');
  moeOffload = $state<number | 'auto'>('auto');
  flashAttention = $state<'auto' | 'on' | 'off'>('auto');
  nodeShares = $state<Record<string, number> | null>(null);
  nodePriority = $state<string[] | null>(null);
  requiredNodes = $state<string[]>([]);
  threadsMax = $state(8);
  templateEmbedded = $state(false);
  modelTemplateText = $state<string | null>(null);
  templatePresets = $state<{ id: string; label: string; text?: string }[]>([]);
  estimateError = $state('');
  distributableBytes = $state(0);
  speculative = $state('auto');
  draftModel = $state('');
  vision = $state(false);
  mmproj = $state('');
  chatTemplate = $state('default');
  chatTemplateText = $state('');
  mmapMlock = $state<'auto' | 'mmap' | 'mlock' | 'mmap+mlock' | 'none'>('auto');
  checkpoints = $state<number | 'auto'>('auto');
  cacheRam = $state<number | 'auto'>('auto');
  threads = $state(8);
  extraArgsText = $state('');
  parameters = $state<ParameterDescriptor[]>([]);
  expertFlags = $state<ExpertFlag[]>([]);
  projectors = $state<string[]>([]);
  settingsLoading = $state(false);
  settingsLoadError = $state('');
  savedModelConfig = $state<Record<string, unknown> | null>(null);
  runDevice = $state<'cpu' | 'gpu' | null>(null);
  runGpuIds = $state<number[] | null>(null);
  runVramPercent = $state<number | null>(null);
  runRamPercent = $state<number | null>(null);
  threadsBatch = $state<number | null>(null);
  draftMax = $state<number | null>(null);
  frequencyPenalty = $state<number | null>(null);
  seed = $state<number | null>(null);
  maxOutputTokens = $state<number | null>(null);
  stopSequences = $state<string[] | null>(null);
  stopParseError = $state('');
  reasoningEffort = $state<string | null>(null);
  reasoningBudget = $state<number | null>(null);
  reasoningFormat = $state<string | null>(null);
  chatTemperature = $state<number | null>(null);
  chatThinking = $state<boolean | null>(null);
  chatOutputLimit = $state<number | null>(null);
  chatResponseFormat = $state<'text' | 'json_object' | 'json_schema'>('text');
  chatSchemaText = $state('');
  activeRunConfig = $state<EffectiveConfig | null>(null);
  // Inference overrides — seeded from GGUF / Unsloth-style defaults (always numeric in UI)
  temperature = $state<number | null>(null);
  topP = $state<number | null>(null);
  topK = $state<number | null>(null);
  minP = $state<number | null>(null);
  presencePenalty = $state<number | null>(null);
  repeatPenalty = $state<number | null>(null);
  enableThinking = $state<boolean | null>(null);
  remember = $state(false);
  advancedOpen = $state(false);
  fieldErrors = $state<Record<string, string>>({});
  device = $state<'cpu' | 'gpu'>('gpu');
  vramPercent = $state(80);
  ramPercent = $state(80);
  vramWeight = $state(0.6);
  latencyWeight = $state(0.4);
  /** Snapshot of planPayload() after last select/save/reset — drives dirty Save accent. */
  private settingsBaseline = $state('');
  planResult = $state<Record<string, unknown> | null>(null);
  estimating = $state(false);
  estimateRecalculations = $state(0);
  estimateNote = $state('');
  private lastEstimate: { payload: Record<string, unknown>; result: Record<string, unknown> } | null = null;
  private estimateTimer: ReturnType<typeof setTimeout> | null = null;
  private estimateSeq = 0;
  /** Live inference load state (null when idle). */
  loadProgress = $state<LoadProgress | null>(null);
  /** Live HF download job (null when idle). */
  downloadJob = $state<DownloadJob | null>(null);
  chat = $state<ChatMessage[]>([]);
  chatInput = $state('');
  chatStreaming = $state(false);
  private updates: DashboardUpdates | null = null;
  private abort: AbortController | null = null;
  private selectionGeneration = 0;
  private handledDownloadTerminals = new Set<string>();
  private reconciledDownloadTerminals = new Set<string>();
  private selectedDownloadTerminals = new Set<string>();
  private downloadSelectionGenerations = new Map<string, number>();
  private observedDownloadIds = new Set<string>();
  private acceptedDownloadIds = new Set<string>();
  private lastDownloadTerminal: DownloadJob | null = null;
  private gpusLoaded = false;
  private configGpuIds: number[] | null = null;
  private selectionHydrated = false;
  private lastApplyError: string | null = null;
  private lastApplyWarning: string | null = null;
  private clearingLifecycle = false;

  viewState(): ClusterView {
    if (!this.statusSeen) return 'loading';
    if (this.status?.role === 'idle') return 'idle';
    if (!this.connected) return 'offline';
    const role = this.status?.role;
    if (role === 'worker' && this.status?.netsec_warnings?.includes('master heartbeat unreachable')) return 'offline';
    if (role === 'master' || role === 'worker') return 'live';
    return 'idle';
  }

  start() {
    errors.start();
    this.usageError = '';
    this.historyError = '';
    this.searchError = '';
    this.estimateError = '';
    for (const source of ['usage-request', 'history', 'search', 'estimate']) errors.observe(source, '', '', false);
    this.fieldErrors = {};
    this.lastApplyError = null;
    this.statusSeen = false;
    this.connected = false;
    if (!this.updates) {
      this.updates = new DashboardUpdates({
        onArea: (topic, area) => this.applyLiveArea(topic, area),
        onAreaHealth: (topic, area) => this.applyLiveHealth(topic, area),
        onHealth: (health, error) => {
          const lostConnection = this.connected && health === 'unavailable';
          errors.observe('delivery', error ?? '', '', false);
          if (lostConnection) this.clearErrors();
          this.deliveryHealth = health;
          this.deliveryError = error ?? '';
          if (health === 'unavailable') {
            this.connected = false;
            this.statusSeen = true;
          }
        }
      });
    }
    this.updates.start();
    void this.loadToken();
    void this.loadHfToken();
    void this.loadModels();
    void this.loadConfig();
    void this.loadGpus();
  }

  stop() {
    this.apiToken = '';
    this.updates?.stop();
    if (this.estimateTimer) clearTimeout(this.estimateTimer);
    this.estimateTimer = null;
  }

  setLogListener(listener: LiveLogListener | null): boolean {
    this.updates?.setLogListener(listener);
    return this.updates !== null;
  }

  setLogVisibilityListener(listener: (() => void) | null, expected?: () => void): boolean {
    this.updates?.setLogVisibilityListener(listener, expected);
    return this.updates !== null;
  }

  setUsageInterest(active: boolean) {
    this.updates?.setUsageInterest(active);
  }

  private applyLiveArea(topic: LiveAreaName, area: LiveArea<unknown>) {
    if (area.availability !== 'available' || area.data === null) return;
    if (topic === 'status') this.applyStatus(area.data as Status);
    else if (topic === 'usage') this.applyUsage(area.data as UsageLive);
    else if (topic === 'load') {
      const progress = area.data as LiveProgress<LoadProgress>;
      const selected = progress.current ?? progress.last_terminal;
      if (!selected && (this.loadProgress?.phase === 'starting' || this.loadProgress?.phase === 'loading')) {
        this.loadProgress = { ...this.loadProgress, phase: 'unknown' };
      } else this.loadProgress = selected;
      errors.observe('load', selected?.phase === 'error' ? selected.error ?? 'unknown error' : '', selected?.operation_id ?? '');
    } else if (topic === 'download') {
      const progress = area.data as LiveProgress<DownloadJob>;
      this.applyDownloadRecords(progress);
    }
  }

  private applyLiveHealth(topic: LiveAreaName, area: LiveArea<unknown>) {
    errors.observe(`area:${topic}`, area.error?.message ?? (area.availability === 'denied' || area.availability === 'unavailable' ? 'source unavailable' : ''));
    const previous = this.areaHealth[topic];
    this.areaHealth[topic] = {
      availability: area.availability,
      coverage: area.coverage,
      last_success_at: area.availability === 'available' ? area.sampled_at : previous?.last_success_at ?? null,
      error: area.error
    };
  }

  private applyStatus(data: Status) {
    const initial = !this.statusSeen;
    const previous = initial ? null : this.status;
    if (!this.clearingLifecycle && previous && ((previous.role !== 'idle' && data.role === 'idle')
      || ((previous.inference_up || previous.process_role === 'master' && previous.process_pid)
        && !data.inference_up && !data.process_pid)
      || (previous.role === 'worker' && previous.members?.[previous.node_id] && !data.members?.[previous.node_id]))) {
      this.clearErrors();
    }
    errors.observe('apply', data.apply_error ?? '');
    errors.observe('inference-activity', data.inference_activity?.phase === 'error'
      ? data.inference_activity.detail ?? 'Inference failed' : '');
    errors.observe('update', data.update?.error ?? '');
    for (const worker of previous?.update?.workers ?? []) {
      if (!data.update?.workers.some(next => (next.node_id ?? next.host) === (worker.node_id ?? worker.host))) {
        errors.observe(`update-worker:${worker.node_id ?? worker.host}`, '');
      }
    }
    for (const worker of data.update?.workers ?? []) {
      errors.observe(`update-worker:${worker.node_id ?? worker.host}`, worker.error ?? '', '', initial);
    }
    this.statusSeen = true;
    if (this.status?.model_name !== data.model_name || this.status?.active_config?.run_id !== data.active_config?.run_id) {
      this.chatTemperature = null;
      this.chatThinking = null;
      this.chatOutputLimit = null;
    }
    this.activeRunConfig = data.active_config ?? null;
    this.status = data;
    this.connected = true;
    const live = data.role === 'master' || data.role === 'worker';
    if (live) {
      if (data.join_key) this.joinKeyInput = data.join_key;
      if (data.master_host) this.masterHostInput = data.master_host;
      if (data.rpc_bind) this.rpcBindInput = data.rpc_bind;
      if (data.overlay === 'none' || data.overlay === 'wireguard') this.overlay = data.overlay;
    }
    if (isAccelBackend(data.accel_backend)) this.accelBackend = data.accel_backend;
    else if (data.accel_backend === null) this.accelBackend = null;
    const next = fp(data);
    if (next !== fingerprint) {
      fingerprint = next;
      if (data.model_name && !this.selectedModel) this.selectedModel = data.model_name;
      if (!this.selectedModel && data.role === 'master' && data.last_plan) {
        this.planResult = data.last_plan as Record<string, unknown>;
      }
      if (this.selectedModel && data.role === 'master') this.scheduleEstimate();
      const err = data.apply_error ?? null;
      const warn = data.apply_warning ?? null;
      if (err && err !== this.lastApplyError) pushToast('danger', err, 4000, { source: 'apply', snapshot: true });
      if (warn && warn !== this.lastApplyWarning) pushToast('warn', warn);
      this.lastApplyError = err;
      this.lastApplyWarning = warn;
      if (!this.selectionHydrated) this.hydrateGpuSelection();
    }
  }

  private applyUsage(data: UsageLive) {
    errors.observe('recorder', data.recording?.error ?? '');
    for (const node of Object.values(data.nodes)) errors.observe(`collector:${node.node_id}`, node.reading?.collector_error ?? '');
    this.liveUsage = data;
    this.usageError = '';
    errors.observe('usage-request', '', '', false);
    if (!this.selectedNodeId || !this.liveUsage.nodes[this.selectedNodeId]) {
      this.selectedNodeId = Object.keys(this.liveUsage.nodes)[0] ?? this.selectedNodeId;
    }
  }

  private applyDownload(job: DownloadJob | null) {
    this.downloadJob = job && typeof job === 'object' && 'state' in job ? job : null;
  }

  private applyDownloadRecords(progress: LiveProgress<DownloadJob>) {
    const current = progress.current;
    const terminal = progress.last_terminal;
    const displayed = current ?? terminal;
    errors.observe('download', displayed?.state === 'error' ? displayed.error ?? 'unknown error' : '', displayed?.operation_id ?? '');
    if (current?.operation_id) this.observedDownloadIds.add(current.operation_id);
    this.lastDownloadTerminal = terminal;
    const tracked = this.downloadJob;
    if (!current && tracked?.state === 'running'
      && (!terminal || (tracked.operation_id && tracked.operation_id !== terminal.operation_id))) {
      this.applyDownload({ ...tracked, state: 'unknown' });
    } else {
      this.applyDownload(current ?? terminal);
    }
    if (terminal?.state === 'complete' && !terminal.operation_id && tracked?.state === 'running'
      && !tracked.operation_id && terminal.filename === tracked.filename) {
      void this.loadModels();
    }
    if (terminal?.state === 'complete' && terminal.operation_id && !this.handledDownloadTerminals.has(terminal.operation_id)) {
      const id = terminal.operation_id;
      this.handledDownloadTerminals.add(id);
      this.downloadSelectionGenerations.set(id, this.selectionGeneration);
      void this.loadModels().then(() => {
        this.reconciledDownloadTerminals.add(id);
        this.maybeSelectCompletedDownload(terminal);
      });
    }
  }

  private maybeSelectCompletedDownload(job: DownloadJob) {
    const id = job.operation_id;
    if (!id || !job.filename || this.selectedDownloadTerminals.has(id)
      || !this.reconciledDownloadTerminals.has(id)
      || (!this.observedDownloadIds.has(id) && !this.acceptedDownloadIds.has(id))) return;
    if (this.downloadSelectionGenerations.get(id) !== this.selectionGeneration
      || !this.models.some((model) => model.id === job.filename)) return;
    this.selectedDownloadTerminals.add(id);
    void this.selectModel(job.filename);
  }

  async refresh() {
    if (this.updates) {
      this.updates.refresh();
      return;
    }
    return this.refreshDirect();
  }

  private async refreshDirect() {
    const errorGeneration = errors.generation;
    try {
      const r = await fetch('/v2/node');
      if (!r.ok) throw new Error(`status ${r.status}`);
      const data = (await r.json()) as Status;
      if (errorGeneration === errors.generation) this.applyStatus(data);
    } catch {
      if (errorGeneration !== errors.generation) return;
      if (this.connected) this.clearErrors();
      this.statusSeen = true;
      this.connected = false;
    }
  }

  async refreshUsage() {
    const errorGeneration = errors.generation;
    if (this.updates) {
      this.updates.refresh();
      return;
    }
    try {
      const r = await fetch('/v2/telemetry');
      if (!r.ok) {
        const payload = await r.json().catch(() => null);
        const detail = payload?.error ?? payload?.detail;
        const message = detail && typeof detail === 'object'
          ? `${detail.message ?? `usage ${r.status}`} · last saved ${detail.last_successful_save ?? 'never'}`
          : typeof detail === 'string' ? detail : `usage ${r.status}`;
        throw new Error(message);
      }
      const data = (await r.json()) as UsageLive;
      if (errorGeneration === errors.generation) this.applyUsage(data);
    } catch (error) {
      this.setRequestError('usageError', String(error), errorGeneration, false);
      if (this.liveUsage) this.liveUsage = { ...this.liveUsage, nodes: Object.fromEntries(
        Object.entries(this.liveUsage.nodes).map(([id, node]) => [id, {
          ...node, freshness: node.reading ? 'stale' as const : 'unavailable' as const
        }])
      ) };
    }
  }

  async selectUsageNode(nodeId: string) {
    const errorGeneration = errors.generation;
    this.selectedNodeId = nodeId;
    this.selectedLifetimeId = '';
    this.usageLifetimes = null;
    this.usageSeries = null;
    this.usagePage = null;
    const seq = ++this.historySeq;
    if (!nodeId) return;
    try {
      const r = await fetch(`/v2/telemetry/lifetimes?node_id=${encodeURIComponent(nodeId)}`);
      if (!r.ok) throw new Error(`history ${r.status}`);
      if (seq !== this.historySeq) return;
      this.usageLifetimes = (await r.json()) as UsageLifetimes;
      this.selectedLifetimeId = this.usageLifetimes.lifetimes[0]?.lifetime_id ?? '';
      this.historyError = '';
      errors.observe('history', '', '', false);
      if (this.selectedLifetimeId) await this.loadUsageHistory();
    } catch (error) {
      if (seq === this.historySeq) this.setRequestError('historyError', String(error), errorGeneration, true);
    }
  }

  async selectUsageLifetime(id: string) {
    this.selectedLifetimeId = id;
    await this.loadUsageHistory();
  }

  async selectUsageWindow(window: '15m' | '1h' | '24h') {
    this.usageWindow = window;
    await this.loadUsageHistory();
  }

  async loadUsageHistory() {
    const errorGeneration = errors.generation;
    const nodeId = this.selectedNodeId;
    const lifeId = this.selectedLifetimeId;
    const seq = ++this.historySeq;
    this.usageSeries = null;
    this.usagePage = null;
    if (!nodeId || !lifeId) return;
    const params = new URLSearchParams({ node_id: nodeId, lifetime_id: lifeId, window: this.usageWindow });
    try {
      const [series, page] = await Promise.all([
        fetch(`/v2/telemetry/series?${params}`), fetch(`/v2/telemetry/readings?${params}`)
      ]);
      if (!series.ok || !page.ok) throw new Error(`history ${series.status}/${page.status}`);
      if (seq !== this.historySeq) return;
      this.usageSeries = (await series.json()) as UsageSeries;
      this.usagePage = (await page.json()) as UsagePage;
      this.historyError = '';
      errors.observe('history', '', '', false);
    } catch (error) {
      if (seq === this.historySeq) this.setRequestError('historyError', String(error), errorGeneration, true);
    }
  }

  async nextUsagePage() {
    const errorGeneration = errors.generation;
    const cursor = this.usagePage?.next_cursor;
    if (!cursor) return;
    const seq = this.historySeq;
    const params = new URLSearchParams({ node_id: this.selectedNodeId, lifetime_id: this.selectedLifetimeId,
      window: this.usageWindow, cursor });
    try {
      const r = await fetch(`/v2/telemetry/readings?${params}`);
      if (!r.ok) throw new Error(`history ${r.status}`);
      const page = (await r.json()) as UsagePage;
      if (seq !== this.historySeq || this.usagePage?.next_cursor !== cursor) return;
      this.usagePage = { ...page, readings: [...(this.usagePage?.readings ?? []), ...page.readings] };
    } catch (error) {
      if (seq === this.historySeq) this.setRequestError('historyError', String(error), errorGeneration, true);
    }
  }

  async networkRequest<T>(path: string, method = 'GET', value?: unknown, signal?: AbortSignal): Promise<T> {
    if (!this.apiToken) await this.loadToken();
    const response = await fetch(path, {
      method, signal,
      headers: { Authorization: `Bearer ${this.apiToken}`, 'Content-Type': 'application/json' },
      ...(value === undefined ? {} : { body: JSON.stringify(value) })
    });
    let data;
    try { data = await response.json(); }
    catch {
      throw new Error(`Network request ${method} ${path} returned ${response.status}: ${response.ok ? 'Invalid JSON response' : response.statusText || 'Request failed'}`);
    }
    if (!response.ok) {
      const detail = data?.error?.details ?? data?.detail ?? data?.error;
      throw new Error(typeof detail === 'object' && detail !== null
        ? `${detail.code ?? response.status}: ${detail.message ?? 'Network request failed'}`
        : String(detail ?? `Network request ${response.status}`));
    }
    return data as T;
  }

  async refreshRuntimes(signal?: AbortSignal) {
    const generation = errors.generation;
    const data = await this.networkRequest<import('./runtime-types').RuntimeLibrary>('/v2/node/runtimes', 'GET', undefined, signal);
    if (generation === errors.generation && this.status) this.status = { ...this.status, runtimes: data };
    return data;
  }

  async runtimeAction(path: string, method: string, value?: unknown) {
    await this.networkRequest(path, method, value);
    await this.refreshRuntimes();
  }

  async loadToken() {
    try {
      const r = await fetch('/v2/credentials/api-token');
      if (!r.ok) return;
      const data = await r.json();
      this.apiToken = data.api_token ?? '';
    } catch {
      /* ignore */
    }
  }

  async loadHfToken() {
    try {
      const r = await fetch('/v2/credentials/huggingface');
      if (!r.ok) return;
      const data = (await r.json()) as HfTokenStatus;
      this.hfTokenConfigured = !!data.configured;
      this.hfTokenSource = data.source ?? null;
    } catch {
      /* ignore */
    }
  }

  async saveHfToken() {
    await this.putHfToken(this.hfTokenInput);
    this.hfTokenInput = '';
  }

  async clearHfToken() {
    this.hfTokenInput = '';
    await this.putHfToken('');
  }

  private async putHfToken(token: string) {
    const errorGeneration = errors.generation;
    this.busy = '/v2/credentials/huggingface';
    try {
      const r = await fetch('/v2/credentials/huggingface', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token })
      });
      const text = await r.text();
      if (!r.ok) {
        throw new Error(text);
      }
      pushToast('ok', token ? 'hf token saved' : 'hf token cleared');
      await this.loadHfToken();
    } catch (error) {
      pushToast('danger', String(error), 4000, { generation: errorGeneration });
      throw error;
    } finally {
      this.busy = '';
    }
  }

  async loadModels() {
    try {
      const r = await fetch('/v2/models');
      if (!r.ok) return;
      const data = await r.json();
      this.models = data.data ?? [];
      if (!this.selectedModel && this.models[0]) {
        await this.selectModel(this.models[0].id);
      } else {
        this.clampContextToModel();
      }
    } catch {
      /* ignore */
    }
  }

  selectedModelMaxContext(): number {
    const m = this.models.find((x) => x.id === this.selectedModel);
    const n = m?.max_context;
    if (typeof n === 'number' && n >= MIN_CONTEXT) return n;
    return DEFAULT_MAX_CONTEXT;
  }

  clampContextToModel() {
    const max = this.selectedModelMaxContext();
    if (this.context > max) this.context = max;
    if (this.context < MIN_CONTEXT) this.context = MIN_CONTEXT;
  }


  selectedModelDefaults(): ModelDefaultsInfo | null {
    const m = this.models.find((x) => x.id === this.selectedModel);
    return m?.defaults ?? null;
  }

  /** Fallback sampling defaults when GGUF metadata has no entry (Unsloth-style seeds). */
  static readonly SAMPLING_FALLBACKS: Record<string, number> = {
    temperature: 0.7,
    top_p: 0.95,
    top_k: 40,
    min_p: 0.05,
    presence_penalty: 0,
    repeat_penalty: 1.1
  };

  samplingDefault(key: string): number {
    const v = this.selectedModelDefaults()?.sampling?.[key];
    if (typeof v === 'number') return v;
    return AppStore.SAMPLING_FALLBACKS[key] ?? 0;
  }

  autoSamplingHint(key: string): string {
    return `auto ${this.samplingDefault(key)}`;
  }

  autoThinkingHint(): string {
    const kw = this.selectedModelDefaults()?.chat_template_kwargs;
    if (!kw || !('enable_thinking' in kw)) return 'auto';
    return `auto ${kw.enable_thinking ? 'on' : 'off'}`;
  }

  /** True when Plan & Run fields differ from the last saved / loaded baseline. */
  settingsDirty(): boolean {
    if (!this.selectedModel) return false;
    return this.settingsFingerprint() !== this.settingsBaseline;
  }

  private settingsFingerprint(): string {
    // Exclude remember / advanced_open — UI chrome, not run parameters.
    let body: Record<string, unknown>;
    try { body = this.planPayload(); }
    catch { return JSON.stringify({ invalidExtra: this.extraArgsText, model: this.selectedModel }); }
    delete body.remember;
    delete body.advanced_open;
    return JSON.stringify(body);
  }

  markSettingsClean() {
    this.settingsBaseline = this.settingsFingerprint();
  }

  applyParameterSet(ps: Record<string, unknown>) {
    this.draftModel = typeof ps.draft_model === 'string' ? ps.draft_model : '';
    this.mmproj = typeof ps.mmproj === 'string' ? ps.mmproj : '';
    this.chatTemplateText = typeof ps.chat_template_text === 'string' ? ps.chat_template_text : '';
    this.runDevice = ps.device === 'cpu' || ps.device === 'gpu' ? ps.device : null;
    this.runGpuIds = Array.isArray(ps.gpu_ids) ? ps.gpu_ids.map(Number) : null;
    this.runVramPercent = typeof ps.vram_percent === 'number' ? ps.vram_percent : null;
    this.runRamPercent = typeof ps.ram_percent === 'number' ? ps.ram_percent : null;
    for (const key of ['threads_batch', 'draft_max', 'frequency_penalty', 'seed', 'max_output_tokens',
      'stop', 'reasoning_effort', 'reasoning_budget', 'reasoning_format']) {
      const field = PARAMETER_FIELDS[key];
      (this as unknown as Record<string, unknown>)[field] = ps[key] ?? null;
    }
    if (typeof ps.context === 'number') this.context = ps.context;
    if (typeof ps.context_auto === 'boolean') this.contextAuto = ps.context_auto;
    this.clampContextToModel();
    if (typeof ps.cache_type_k === 'string') this.cacheTypeK = normalizeKvCacheType(ps.cache_type_k);
    if (typeof ps.cache_type_v === 'string') this.cacheTypeV = normalizeKvCacheType(ps.cache_type_v);
    if (ps.parallel_slots === 'auto' || typeof ps.parallel_slots === 'number')
      this.parallelSlots = ps.parallel_slots as number | 'auto';
    if (ps.batch_size === 'auto' || typeof ps.batch_size === 'number')
      this.batchSize = ps.batch_size as number | 'auto';
    if (ps.micro_batch === 'auto' || typeof ps.micro_batch === 'number')
      this.microBatch = ps.micro_batch as number | 'auto';
    if (ps.moe_offload === 'auto' || typeof ps.moe_offload === 'number')
      this.moeOffload = ps.moe_offload as number | 'auto';
    if (ps.flash_attention === 'auto' || ps.flash_attention === 'on' || ps.flash_attention === 'off')
      this.flashAttention = ps.flash_attention;
    this.nodeShares = null;
    this.nodePriority = Array.isArray(ps.node_priority) && ps.node_priority.length
      ? [...new Set(ps.node_priority.filter((node): node is string => typeof node === 'string' && !!node))]
      : ps.node_shares && typeof ps.node_shares === 'object' && !Array.isArray(ps.node_shares)
        ? Object.keys(ps.node_shares).sort((a, b) => Number((ps.node_shares as Record<string, number>)[b]) - Number((ps.node_shares as Record<string, number>)[a])) : null;
    this.requiredNodes = Array.isArray(ps.required_nodes)
      ? [...new Set(ps.required_nodes.filter((node): node is string => typeof node === 'string' && !!node))] : [];
    if (typeof ps.speculative === 'string') this.speculative = ps.speculative;
    if (typeof ps.draft_model === 'string') this.draftModel = ps.draft_model;
    if (typeof ps.vision === 'boolean') this.vision = ps.vision;
    if (typeof ps.mmproj === 'string') this.mmproj = ps.mmproj ?? '';
    if (typeof ps.chat_template === 'string') this.chatTemplate = ps.chat_template;
    if (typeof ps.chat_template_text === 'string') this.chatTemplateText = ps.chat_template_text;
    if (typeof ps.mmap_mlock === 'string') this.mmapMlock = ps.mmap_mlock as typeof this.mmapMlock;
    if (ps.checkpoints === 'auto' || typeof ps.checkpoints === 'number')
      this.checkpoints = ps.checkpoints as number | 'auto';
    if (ps.cache_ram === 'auto' || typeof ps.cache_ram === 'number')
      this.cacheRam = ps.cache_ram as number | 'auto';
    if (typeof ps.threads === 'number') this.threads = Math.min(this.threadsMax, ps.threads);
    if (Array.isArray(ps.extra_args)) this.extraArgsText = formatArguments(ps.extra_args as string[]);
    if (typeof ps.remember === 'boolean') this.remember = ps.remember;
    if (typeof ps.advanced_open === 'boolean') this.advancedOpen = ps.advanced_open;
    if (typeof ps.vram_weight === 'number') this.vramWeight = ps.vram_weight;
    if (typeof ps.latency_weight === 'number') this.latencyWeight = ps.latency_weight;
    if (typeof ps.temperature === 'number') this.temperature = ps.temperature;
    else this.temperature = null;
    if (typeof ps.top_p === 'number') this.topP = ps.top_p;
    else this.topP = null;
    if (typeof ps.top_k === 'number') this.topK = ps.top_k;
    else this.topK = null;
    if (typeof ps.min_p === 'number') this.minP = ps.min_p;
    else this.minP = null;
    if (typeof ps.presence_penalty === 'number') this.presencePenalty = ps.presence_penalty;
    else this.presencePenalty = null;
    if (typeof ps.repeat_penalty === 'number') this.repeatPenalty = ps.repeat_penalty;
    else this.repeatPenalty = null;
    if (typeof ps.enable_thinking === 'boolean') this.enableThinking = ps.enable_thinking;
    else if (ps.enable_thinking === null) this.enableThinking = null;
  }

  async selectModel(id: string) {
    const generation = ++this.selectionGeneration;
    this.selectedModel = id;
    this.resetRunSettings();
    this.savedModelConfig = null;
    this.settingsLoadError = '';
    this.settingsLoading = true;
    this.parameters = [];
    this.expertFlags = [];
    this.projectors = [];
    this.templateEmbedded = false;
    this.modelTemplateText = null;
    this.templatePresets = [];
    try {
      await this.loadRunCapabilities();
      if (generation !== this.selectionGeneration) return;
      const r = await fetch(`/v2/inference/model-settings/${encodeURIComponent(id)}`);
      if (generation !== this.selectionGeneration) return;
      if (!r.ok) throw new Error('Could not load saved settings. Retry or use defaults.');
      const data = await r.json();
      if (generation !== this.selectionGeneration) return;
      this.savedModelConfig = data.parameter_set ?? {};
      this.applyParameterSet(this.savedModelConfig ?? {});
      this.markSettingsClean();
    } catch (error) {
      if (generation !== this.selectionGeneration) return;
      this.settingsLoadError = String(error);
    } finally {
      if (generation === this.selectionGeneration) {
        this.settingsLoading = false;
        this.scheduleEstimate();
      }
    }
  }

  revertSavedSettings() {
    this.resetRunSettings();
    if (this.savedModelConfig) this.applyParameterSet(this.savedModelConfig);
    this.markSettingsClean();
    this.scheduleEstimate();
  }

  useDefaultSettings() {
    this.settingsLoadError = '';
    this.resetRunSettings();
    this.scheduleEstimate();
  }

  async loadRunCapabilities() {
    try {
      const model = this.selectedModel;
      const r = await fetch(`/v2/inference/capabilities?model=${encodeURIComponent(model)}`);
      if (!r.ok) return;
      const info = await r.json();
      if (model !== this.selectedModel) return;
      if (typeof info.threads_max === 'number' && info.threads_max > 0) {
        this.threadsMax = info.threads_max;
        this.threads = Math.min(this.threads, this.threadsMax);
      }
      this.parameters = Array.isArray(info.parameters) ? info.parameters : [];
      this.expertFlags = Array.isArray(info.expert_flags) ? info.expert_flags : [];
      this.projectors = Array.isArray(info.projectors) ? info.projectors : [];
      this.templateEmbedded = !!info.template_embedded;
      this.modelTemplateText = typeof info.template_text === 'string' ? info.template_text : null;
      this.templatePresets = Array.isArray(info.templates) ? info.templates : [];
    } catch { /* Selection resets capabilities; never reuse another model's metadata. */ }
  }

  setNodeShare(node: string, share: number) {
    const ids = Object.keys(this.status?.members ?? {});
    if (!ids.includes(node) || !ids.length) return;
    const prev = this.nodeShares ?? Object.fromEntries(ids.map((id) => [id, 1 / ids.length]));
    const others = ids.filter((id) => id !== node);
    const value = others.length ? Math.max(0, Math.min(1, share)) : 1;
    const total = others.reduce((n, id) => n + (prev[id] ?? 0), 0);
    const next = Object.fromEntries(ids.map((id) => [id, id === node ? value :
      (1 - value) * (total ? (prev[id] ?? 0) / total : 1 / others.length)]));
    this.nodeShares = next;
    this.scheduleEstimate();
  }

  setPriorityPlanning(enabled: boolean) {
    this.nodeShares = null;
    this.nodePriority = enabled ? [...new Set([...(this.nodePriority ?? []), ...Object.keys(this.status?.members ?? {})])] : null;
    this.scheduleEstimate();
  }

  moveNodePriority(node: string, direction: -1 | 1) {
    const order = [...new Set([...(this.nodePriority ?? []), ...Object.keys(this.status?.members ?? {})])];
    const index = order.indexOf(node);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target], order[index]];
    this.nodePriority = order;
    this.nodeShares = null;
    this.scheduleEstimate();
  }

  removeNodePriority(node: string) {
    this.nodePriority = (this.nodePriority ?? []).filter((id) => id !== node);
    this.requiredNodes = this.requiredNodes.filter((id) => id !== node);
    this.scheduleEstimate();
  }

  setNodeRequired(node: string, required: boolean) {
    this.requiredNodes = required ? [...new Set([...this.requiredNodes, node])] : this.requiredNodes.filter((id) => id !== node);
    this.scheduleEstimate();
  }

  /** Apply GGUF / fallback sampling seeds into the slider-backed fields. */
  seedSamplingDefaults() {
    this.temperature = null;
    this.topP = null;
    this.topK = null;
    this.minP = null;
    this.presencePenalty = null;
    this.repeatPenalty = null;
  }

  /** Persist the current run settings for this model. */
  async saveModelSettings() {
    const errorGeneration = errors.generation;
    if (!this.selectedModel) return;
    const model = this.selectedModel;
    const generation = this.selectionGeneration;
    if (!this.validateRunSettings(true)) {
      pushToast('danger', 'Fix validation errors before saving', 4000, { generation: errorGeneration });
      return;
    }
    const body = this.planPayload();
    delete body.remember;
    delete body.advanced_open;
    delete body.settings_source;
    this.busy = '/v2/inference/model-settings';
    try {
      const r = await fetch(`/v2/inference/model-settings/${encodeURIComponent(model)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parameter_set: body })
      });
      const text = await r.text();
      let data: unknown = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text;
      }
      if (!r.ok) {
        pushToast('danger', apiErrorMessage(data, text, r.status, 'save settings'), 4000, { generation: errorGeneration });
        return;
      }
      pushToast('ok', `settings saved for ${model}`);
      if (generation !== this.selectionGeneration) return;
      const saved = data as { parameter_set?: Record<string, unknown> };
      this.savedModelConfig = saved.parameter_set ?? body;
      this.applyParameterSet(this.savedModelConfig);
      this.markSettingsClean();
    } catch (e) {
      pushToast('danger', String(e), 4000, { generation: errorGeneration });
    } finally {
      this.busy = '';
    }
  }

  resetRunSettings() {
    this.context = Math.min(4096, this.selectedModelMaxContext());
    this.contextAuto = false;
    this.cacheTypeK = 'q8_0';
    this.cacheTypeV = 'q8_0';
    this.parallelSlots = 'auto';
    this.batchSize = 'auto';
    this.microBatch = 'auto';
    this.moeOffload = 'auto';
    this.flashAttention = 'auto';
    this.nodeShares = null;
    this.nodePriority = null;
    this.requiredNodes = [];
    this.speculative = 'auto';
    this.draftModel = '';
    this.vision = false;
    this.mmproj = '';
    this.chatTemplate = 'default';
    this.chatTemplateText = '';
    this.mmapMlock = 'auto';
    this.checkpoints = 'auto';
    this.cacheRam = 'auto';
    this.threads = Math.min(8, this.threadsMax);
    this.extraArgsText = '';
    this.runDevice = null;
    this.runGpuIds = null;
    this.runVramPercent = null;
    this.runRamPercent = null;
    this.threadsBatch = null;
    this.draftMax = null;
    this.frequencyPenalty = null;
    this.seed = null;
    this.maxOutputTokens = null;
    this.stopSequences = null;
    this.stopParseError = '';
    this.reasoningEffort = null;
    this.reasoningBudget = null;
    this.reasoningFormat = null;
    this.remember = false;
    this.seedSamplingDefaults();
    this.enableThinking = null;
    this.advancedOpen = false;
    this.fieldErrors = {};
    this.vramWeight = 0.6;
    this.latencyWeight = 0.4;
    this.planResult = null;
    this.distributableBytes = 0;
  }

  planRunBlockedReason(): string | null {
    if (this.settingsLoading) return 'Loading model configuration…';
    if (this.settingsLoadError) return this.settingsLoadError;
    if (!this.models.length) return 'Add a local model before planning or running.';
    if (this.status?.role !== 'master') return 'Start as cluster master before Plan & Run.';
    const members = this.status?.members ?? {};
    if (Object.keys(members).length === 0) return 'Register at least one cluster member.';
    return null;
  }

  validateRunSettings(explicit = false): boolean {
    const err: Record<string, string> = {};
    if (this.stopParseError) err.stop = this.stopParseError;
    const maxCtx = this.selectedModelMaxContext();
    if (!this.contextAuto && this.context < MIN_CONTEXT)
      err.context = `Context must be at least ${MIN_CONTEXT} when Auto is off.`;
    else if (!this.contextAuto && this.context > maxCtx)
      err.context = `Context must be ≤ ${maxCtx} for this model.`;
    if (this.threads < 1) err.threads = 'Threads must be at least 1.';
    if (this.threads > this.threadsMax) err.threads = `Threads must be at most ${this.threadsMax} on the master.`;
    if (this.vramPercent < 10 || this.vramPercent > 100) err.vramPercent = 'GPU memory % must be 10–100.';
    if (this.ramPercent < 10 || this.ramPercent > 100) err.ramPercent = 'RAM % must be 10–100.';
    if (this.vramWeight < 0 || this.vramWeight > 1) err.vramWeight = 'VRAM weight must be 0–1.';
    if (this.latencyWeight < 0 || this.latencyWeight > 1) err.latencyWeight = 'Latency weight must be 0–1.';
    if (this.vramWeight + this.latencyWeight <= 0)
      err.placementWeights = 'VRAM and latency weights cannot both be zero.';
    try { parseArgumentText(this.extraArgsText); } catch (error) { err.extra_args = String(error); }
    for (const descriptor of this.parameters) {
      const value = this.parameterValue(descriptor.id);
      if (value === null || value === undefined || value === 'auto') continue;
      const bounds = parameterBounds(descriptor);
      if (typeof value === 'number' && (!Number.isFinite(value) || bounds.integer && !Number.isInteger(value)
        || bounds.min !== undefined && value < bounds.min || bounds.max !== undefined && value > bounds.max)) {
        err[descriptor.id] = `${descriptor.id}: enter a valid value${bounds.min !== undefined ? ` ≥ ${bounds.min}` : ''}${bounds.max !== undefined ? ` ≤ ${bounds.max}` : ''}.`;
      }
    }
    if (this.maxOutputTokens === 0) err.max_output_tokens = 'Response limit must be positive or -1 for unlimited.';
    const fields = new Set([...Object.keys(this.fieldErrors), ...Object.keys(err)]);
    this.fieldErrors = err;
    for (const field of fields) {
      if (explicit && err[field]) errors.publish(`validation:${field}`, err[field]);
      else errors.observe(`validation:${field}`, err[field] ?? '', '', false);
    }
    if (Object.keys(err).some((key) => key !== 'context')) this.advancedOpen = true;
    return Object.keys(err).length === 0;
  }

  parameterValue(name: string): unknown {
    const field = PARAMETER_FIELDS[name];
    return field ? (this as unknown as Record<string, unknown>)[field] : undefined;
  }

  setParameter(name: string, value: unknown) {
    const field = PARAMETER_FIELDS[name];
    if (!field) return;
    (this as unknown as Record<string, unknown>)[field] = value;
    this.scheduleEstimate();
  }

  parameterDescriptor(name: string): ParameterDescriptor | undefined {
    const descriptor = this.parameters.find(item => item.id === name);
    if (descriptor && this.chatTemplate !== 'default'
      && descriptor.unavailable_reason !== 'Not supported by the installed llama-server') {
      const text = this.chatTemplate === 'custom' ? this.chatTemplateText
        : this.templatePresets.find(item => item.id === this.chatTemplate)?.text ?? '';
      if (['reasoning_effort', 'reasoning_budget', 'enable_thinking'].includes(name)) {
        const available = name === 'reasoning_effort' ? text.includes('reasoning_effort')
          : name === 'enable_thinking' ? text.includes('enable_thinking')
          : text.includes('<think>') && text.includes('</think>');
        const choices = name === 'reasoning_effort' ? ['default', ...['minimal', 'low', 'medium', 'high', 'xhigh', 'max']
          .filter(level => text.includes(`'${level}'`) || text.includes(`"${level}"`))] : descriptor.choices;
        return { ...descriptor, choices, available, unavailable_reason: available ? null : 'Selected template does not declare this control' };
      }
    }
    return descriptor;
  }

  effectiveConfig(): EffectiveConfig | null {
    return (this.planResult?.effective_config as EffectiveConfig | undefined) ?? null;
  }

  async copyConfiguration(format: 'json' | 'cli') {
    const body = this.networkPlanPayload();
    delete body.remember;
    delete body.advanced_open;
    const text = format === 'json' ? JSON.stringify(body, null, 2) : this.configurationCommand(body);
    try { await navigator.clipboard.writeText(text); pushToast('ok', `${format.toUpperCase()} copied`); }
    catch { pushToast('danger', 'Clipboard unavailable; select the configuration text to copy.'); }
  }

  configurationCommand(body = this.networkPlanPayload()): string {
    const tokens = ['kllm run', '--settings defaults', `--model ${shellQuote(this.selectedModel)}`];
    for (const item of this.parameters) {
      const value = body[item.id];
      if (value === null || value === undefined || item.id === 'extra_args') continue;
      if (typeof value === 'boolean') {
        if (item.id === 'enable_thinking') tokens.push(value ? '--enable-thinking' : '--no-enable-thinking');
        else if (item.id === 'vision') tokens.push(value ? '--vision' : '--no-vision');
        else if (item.id === 'context_auto') tokens.push(value ? '--context-auto' : '--no-context-auto');
        else if (value) tokens.push(item.cli);
      } else if (Array.isArray(value)) for (const element of value) tokens.push(`${item.cli} ${shellQuote(element)}`);
      else if (item.id === 'node_shares' && typeof value === 'object')
        for (const [node, share] of Object.entries(value)) tokens.push(`--node-share ${shellQuote(`${node}=${share}`)}`);
      else if (typeof value !== 'object') tokens.push(`${item.cli} ${shellQuote(value)}`);
    }
    if (this.selectedNetworkId) tokens.push(`--network-id ${shellQuote(this.selectedNetworkId)}`);
    for (const flag of parseArgumentText(this.extraArgsText)) tokens.push(`--extra-arg=${shellQuote(JSON.stringify(flag))}`);
    return tokens.join(' ');
  }

  planPayload() {
    const extra = parseArgumentText(this.extraArgsText);
    const body: Record<string, unknown> = {
      model: this.selectedModel,
      context: this.context,
      context_auto: this.contextAuto,
      cache_type_k: this.cacheTypeK,
      cache_type_v: this.cacheTypeV,
      speculative: this.speculative,
      draft_model: this.draftModel || null,
      parallel_slots: this.parallelSlots,
      batch_size: this.batchSize,
      micro_batch: this.microBatch,
      moe_offload: this.moeOffload,
      flash_attention: this.flashAttention,
      node_shares: this.nodeShares,
      node_priority: this.nodePriority ? [...new Set([...this.nodePriority, ...Object.keys(this.status?.members ?? {})])] : [],
      required_nodes: this.nodeShares ? [] : [...this.requiredNodes],
      vision: this.vision,
      mmproj: this.mmproj || null,
      device: this.runDevice,
      gpu_ids: this.runGpuIds,
      settings_source: 'defaults',
      split_mode: 'layer',
      vram_percent: this.runVramPercent,
      ram_percent: this.runRamPercent,
      vram_weight: this.vramWeight,
      latency_weight: this.latencyWeight,
      chat_template: this.chatTemplate,
      chat_template_text: this.chatTemplateText || null,
      mmap_mlock: this.mmapMlock,
      checkpoints: this.checkpoints,
      cache_ram: this.cacheRam,
      threads: this.threads,
      threads_batch: this.threadsBatch,
      draft_max: this.draftMax,
      seed: this.seed,
      max_output_tokens: this.maxOutputTokens,
      frequency_penalty: this.frequencyPenalty,
      stop: this.stopSequences,
      reasoning_effort: this.reasoningEffort,
      reasoning_budget: this.reasoningBudget,
      reasoning_format: this.reasoningFormat,
      extra_args: extra,
      temperature: this.temperature,
      top_p: this.topP,
      top_k: this.topK,
      min_p: this.minP,
      presence_penalty: this.presencePenalty,
      repeat_penalty: this.repeatPenalty,
      enable_thinking: this.enableThinking,
      remember: this.remember,
      advanced_open: this.advancedOpen
    };
    return body;
  }

  async loadConfig() {
    try {
      const r = await fetch('/v2/node/config');
      if (!r.ok) return;
      const data = await r.json();
      if (data.device === 'cpu' || data.device === 'gpu') this.device = data.device;
      if (typeof data.vram_percent === 'number') this.vramPercent = data.vram_percent;
      if (typeof data.ram_percent === 'number') this.ramPercent = data.ram_percent;
      if (Array.isArray(data.gpu_ids)) {
        this.configGpuIds = data.gpu_ids.map((n: number) => Number(n));
      } else if (data.gpu_ids === null || data.gpu_ids === undefined) {
        this.configGpuIds = null;
      }
      if (isAccelBackend(data.accel_backend)) this.accelBackend = data.accel_backend;
      else if (data.accel_backend === null || data.accel_backend === undefined) {
        this.accelBackend = null;
      }
      this.hydrateGpuSelection();
    } catch {
      /* ignore */
    }
  }

  /** One-shot GPU checkbox init. Never re-applied after hydrate (avoids fighting toggles). */
  private hydrateGpuSelection() {
    if (this.selectionHydrated || !this.gpusLoaded || this.gpus.length === 0) return;
    const available = new Set(this.gpus.map((g) => g.index));
    if (this.status?.gpu_ids && this.status.gpu_ids.length > 0) {
      const fromStatus = this.status.gpu_ids.filter((i) => available.has(i));
      if (fromStatus.length > 0) {
        this.selectedGpuIds = fromStatus;
        this.selectionHydrated = true;
        return;
      }
    }
    if (this.configGpuIds && this.configGpuIds.length > 0) {
      const picked = this.configGpuIds.filter((i) => available.has(i));
      this.selectedGpuIds = picked.length > 0 ? picked : this.gpus.map((g) => g.index);
    } else {
      this.selectedGpuIds = this.gpus.map((g) => g.index);
    }
    this.selectionHydrated = true;
  }

  private applyGpusPayload(data: Record<string, unknown>) {
    errors.observe('gpu-probe', typeof data.error === 'string' ? data.error : '');
    this.gpuProbeError = typeof data.error === 'string' ? data.error : '';
    this.gpuInstallHint = typeof data.install_hint === 'string' ? data.install_hint : '';
    this.gpuBackendWarning =
      typeof data.backend_warning === 'string' ? data.backend_warning : '';
    this.gpuBackend = typeof data.backend === 'string' ? data.backend : 'cpu';
    this.gpuUnifiedMemory = typeof data.unified_memory === 'boolean' ? data.unified_memory : null;
    this.probedBackend =
      typeof data.probed_backend === 'string' ? data.probed_backend : this.gpuBackend;
    const allowed = Array.isArray(data.allowed_backends)
      ? (data.allowed_backends as unknown[]).filter(isAccelBackend)
      : [];
    this.allowedBackends = allowed;
    const installed = Array.isArray(data.installed_backends)
      ? (data.installed_backends as unknown[]).filter(isAccelBackend)
      : [];
    this.installedBackends = installed;
    const list = (data.gpus ?? []) as GpuInfo[];
    this.gpus = list;
    // Prefer saved preference when installed; else effective backend from probe resolution.
    if (this.accelBackend && installed.includes(this.accelBackend)) {
      /* keep */
    } else if (isAccelBackend(this.gpuBackend) && installed.includes(this.gpuBackend)) {
      this.accelBackend = this.gpuBackend;
    } else if (installed.length > 0) {
      this.accelBackend = installed[0];
    } else {
      this.accelBackend = null;
    }
  }

  async loadGpus() {
    const errorGeneration = errors.generation;
    try {
      const r = await fetch('/v2/node/devices');
      if (!r.ok) return;
      const data = await r.json();
      if (errorGeneration === errors.generation) this.applyGpusPayload(data);
      if (!this.gpusLoaded) {
        this.gpusLoaded = true;
        this.hydrateGpuSelection();
        return;
      }
      await this.reconcileGpuSelection();
    } catch {
      /* ignore */
    }
  }

  /**
   * After the first hydrate, keep checkbox selection aligned with live inventory:
   * drop vanished indices; if none remain, select all current devices and persist.
   */
  private async reconcileGpuSelection() {
    if (this.gpus.length === 0) {
      if (this.selectedGpuIds.length > 0) this.selectedGpuIds = [];
      return;
    }
    const available = new Set(this.gpus.map((g) => g.index));
    const kept = this.selectedGpuIds.filter((i) => available.has(i));
    if (kept.length > 0 && kept.length === this.selectedGpuIds.length) return;
    if (kept.length > 0) {
      this.selectedGpuIds = kept;
      await this.saveConfig({ gpu_ids: kept });
      return;
    }
    const all = this.gpus.map((g) => g.index);
    this.selectedGpuIds = all;
    await this.saveConfig({ gpu_ids: all });
  }

  toggleGpu(index: number) {
    if (this.selectedGpuIds.includes(index)) {
      if (this.selectedGpuIds.length <= 1) return;
      this.selectedGpuIds = this.selectedGpuIds.filter((i) => i !== index);
    } else {
      this.selectedGpuIds = [...this.selectedGpuIds, index].sort((a, b) => a - b);
    }
  }

  accelBackendLabel(backend: AccelBackend): string {
    return ACCEL_LABELS[backend] ?? backend;
  }

  async saveConfig(partial?: {
    device?: 'cpu' | 'gpu';
    gpu_ids?: number[] | null;
    vram_percent?: number;
    ram_percent?: number;
    accel_backend?: AccelBackend | null;
  }) {
    const errorGeneration = errors.generation;
    const device = partial?.device ?? this.device;
    this.device = device;
    const body: Record<string, unknown> = {
      device,
      split_mode: 'layer',
      vram_percent: partial?.vram_percent ?? this.vramPercent,
      ram_percent: partial?.ram_percent ?? this.ramPercent
    };
    if (device === 'gpu') {
      const ids = partial?.gpu_ids !== undefined ? partial.gpu_ids : this.selectedGpuIds;
      body.gpu_ids = ids && ids.length > 0 ? ids : null;
      this.configGpuIds = (body.gpu_ids as number[] | null) ?? null;
    }
    if (partial && 'accel_backend' in partial) {
      body.accel_backend = partial.accel_backend ?? null;
      this.accelBackend = partial.accel_backend ?? null;
    } else if (this.accelBackend) {
      body.accel_backend = this.accelBackend;
    }
    this.busy = '/v2/node/config';
    try {
      const r = await fetch('/v2/node/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      const text = await r.text();
      if (!r.ok) {
        throw new Error(text || `config ${r.status}`);
      }
      try {
        const data = text ? JSON.parse(text) : {};
        if (typeof data.vram_percent === 'number') this.vramPercent = data.vram_percent;
        if (typeof data.ram_percent === 'number') this.ramPercent = data.ram_percent;
        if (isAccelBackend(data.accel_backend)) this.accelBackend = data.accel_backend;
        else if (data.accel_backend === null) this.accelBackend = null;
      } catch {
        /* ignore */
      }
      pushToast('ok', 'config saved');
      await this.refresh();
      await this.loadGpus();
      ++this.estimateSeq;
      this.estimating = false;
      this.planResult = null;
      this.scheduleEstimate();
    } catch (error) {
      pushToast('danger', String(error), 4000, { generation: errorGeneration });
      throw error;
    } finally {
      this.busy = '';
    }
  }

  async setDevice(device: 'cpu' | 'gpu') {
    // Do not silently apply the opposing budget field when toggling GPU↔CPU.
    if (device === 'gpu') {
      await this.saveConfig({ device, vram_percent: this.vramPercent });
    } else {
      await this.saveConfig({ device, ram_percent: this.ramPercent });
      this.nodeShares = null;
      this.scheduleEstimate();
    }
  }

  async setAccelBackend(backend: AccelBackend | null) {
    await this.saveConfig({ accel_backend: backend });
  }

  async setVramPercent(value: number) {
    const n = Math.max(10, Math.min(100, Math.round(value)));
    this.vramPercent = n;
    await this.saveConfig({ vram_percent: n });
  }

  async setRamPercent(value: number) {
    const n = Math.max(10, Math.min(100, Math.round(value)));
    this.ramPercent = n;
    await this.saveConfig({ ram_percent: n });
  }

  async persistGpuSelection() {
    if (this.device !== 'gpu') return;
    await this.saveConfig({ gpu_ids: this.selectedGpuIds });
  }

  async post(path: string, body?: unknown, method: 'POST' | 'DELETE' = 'POST') {
    const errorGeneration = errors.generation;
    this.busy = path;
    try {
      const r = await fetch(path, {
        method,
        headers: { 'Content-Type': 'application/json', ...(this.apiToken ? {Authorization: `Bearer ${this.apiToken}`} : {}) },
        body: body === undefined ? undefined : JSON.stringify(body)
      });
      const text = await r.text();
      let data: unknown = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text;
      }
      if (!r.ok) {
        throw new Error(apiErrorMessage(data, text, r.status, path));
      }
      if (path === '/v2/models/downloads' && data && typeof data === 'object'
        && typeof (data as { operation_id?: unknown }).operation_id === 'string') {
        const operationId = (data as { operation_id: string }).operation_id;
        this.acceptedDownloadIds.add(operationId);
        if (this.lastDownloadTerminal?.operation_id === operationId) {
          this.maybeSelectCompletedDownload(this.lastDownloadTerminal);
        }
      }
      pushToast('ok', `${path} ok`);
      await this.refresh();
      await this.loadModels();
      return data;
    } catch (error) {
      pushToast('danger', String(error), 4000, { generation: errorGeneration });
      throw error;
    } finally {
      this.busy = '';
    }
  }

  private recoveryCommand() {
    return (
      this.gpuInstallHint || 'python scripts/install.py && python scripts/install.py --check'
    );
  }

  private gpuStartWarning() {
    if (this.device !== 'gpu' || this.gpus.length > 0) return;
    if (this.gpuProbeError) {
      pushToast('warn', `${this.gpuProbeError}. Run: ${this.recoveryCommand()}`);
      return;
    }
    if (this.gpuBackendWarning) {
      pushToast('warn', `${this.gpuBackendWarning}. Run: ${this.recoveryCommand()}`);
      return;
    }
    pushToast('warn', 'No local GPUs detected — inference will fall back to CPU layers.');
  }

  async startMaster() {
    await this.loadGpus();
    this.gpuStartWarning();
    const body: Record<string, unknown> = {
      key: this.joinKeyInput || null,
      rpc_bind: this.rpcBindInput || null,
      overlay: this.overlay
    };
    if (this.gpus.length > 0) {
      body.gpu_ids =
        this.selectedGpuIds.length > 0 ? this.selectedGpuIds : this.gpus.map((g) => g.index);
    }
    await this.post('/v2/cluster', body);
  }

  async stopCluster() {
    this.clearErrors();
    this.clearingLifecycle = true;
    try {
      await this.post('/v2/cluster', undefined, 'DELETE');
      this.resetClusterView();
    } finally { this.clearingLifecycle = false; }
  }

  private setRequestError(field: 'usageError' | 'historyError' | 'searchError' | 'estimateError',
    message: string, generation: number, explicit: boolean) {
    if (generation !== errors.generation) return;
    this[field] = message;
    const source = field === 'usageError' ? 'usage-request' : field.replace('Error', '');
    if (explicit) errors.publish(source, message, generation);
    else errors.observe(source, message, '', false, generation);
  }

  clearErrors() {
    errors.clear();
    ++this.historySeq;
    ++this.estimateSeq;
    if (this.estimateTimer) clearTimeout(this.estimateTimer);
    this.estimateTimer = null;
    this.estimating = false;
  }

  private resetClusterView() {
    if (this.status) {
      this.status = {
        ...this.status,
        role: 'idle',
        members: {},
        join_key: null,
        master_host: null,
      };
    }
    if (this.liveUsage) {
      this.liveUsage = { ...this.liveUsage, nodes: {}, relationships: [] };
    }
    // Drop the last resolved bind so the next Start/Join sends no override and
    // the agent picks its default (internal private subnet first).
    this.rpcBindInput = '';
    this.selectedNodeId = '';
  }

  async joinCluster() {
    await this.loadGpus();
    this.gpuStartWarning();
    const masterHost = this.masterHostInput.trim();
    const body: Record<string, unknown> = {
      key: this.joinKeyInput,
      master_port: 18765,
      rpc_port: 50052,
      rpc_bind: this.rpcBindInput || null,
      overlay: this.overlay
    };
    if (masterHost) body.master_host = masterHost;
    if (this.gpus.length > 0) {
      body.gpu_ids =
        this.selectedGpuIds.length > 0 ? this.selectedGpuIds : this.gpus.map((g) => g.index);
    }
    await this.post('/v2/cluster/membership', body);
  }

  async leaveCluster() {
    this.clearErrors();
    this.clearingLifecycle = true;
    try {
      await this.post('/v2/cluster/membership', undefined, 'DELETE');
      this.resetClusterView();
    } finally { this.clearingLifecycle = false; }
  }

  async probeFastSubnetOnce() {
    if (this.fastSubnetProbe !== 'idle' || this.status?.role !== 'master') return;
    this.fastSubnetProbe = 'checking';
    try {
      const response = await fetch('/v2/cluster/fast-subnet/reachability');
      const text = await response.text();
      let data: unknown = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = null;
      }
      const reachability = data as FastSubnetReachability | null;
      if (!response.ok || !reachability || !Array.isArray(reachability.workers)) {
        this.fastSubnetProbe = 'failed';
        this.fastSubnetReachability = null;
        return;
      }
      this.fastSubnetReachability = reachability;
      this.fastSubnetProbe = 'done';
    } catch {
      this.fastSubnetProbe = 'failed';
      this.fastSubnetReachability = null;
    }
  }

  async reconnectWorkerFastSubnet(nodeId: string) {
    const source = `fast-subnet:${nodeId}`;
    const errorGeneration = errors.generation;
    errors.observe(source, '');
    this.fastSubnetErrors = { ...this.fastSubnetErrors, [nodeId]: '' };
    this.reconnectingNodeId = nodeId;
    try {
      const response = await fetch('/v2/cluster/fast-subnet/reconnect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node_id: nodeId })
      });
      const text = await response.text();
      let data: unknown = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text;
      }
      if (!response.ok) {
        throw new Error(apiErrorMessage(data, text, response.status, 'reconnect'));
      }
      pushToast('ok', 'Worker rejoined on the fast subnet');
      await this.refresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.fastSubnetErrors = { ...this.fastSubnetErrors, [nodeId]: message };
      errors.publish(source, message, errorGeneration);
      pushToast('danger', message, 4000, { generation: errorGeneration });
    } finally {
      if (this.reconnectingNodeId === nodeId) this.reconnectingNodeId = '';
    }
  }

  async updateWorkers() {
    const errorGeneration = errors.generation;
    errors.observe('update', '');
    for (const worker of this.status?.update?.workers ?? []) errors.observe(`update-worker:${worker.node_id ?? worker.host}`, '');
    this.busy = '/v2/cluster/updates';
    try {
      const r = await fetch('/v2/cluster/updates', { method: 'POST' });
      const text = await r.text();
      let data: unknown = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text;
      }
      if (!r.ok) {
        const msg = apiErrorMessage(data, text, r.status, 'update');
        throw new Error(msg);
      }
      pushToast('ok', 'worker update started');
      await this.refresh();
      return data;
    } catch (error) {
      pushToast('danger', String(error), 4000, { generation: errorGeneration });
      throw error;
    } finally {
      this.busy = '';
    }
  }

  async searchModels(): Promise<ModelSearchResponse | null> {
    const errorGeneration = errors.generation;
    const query = this.modelSource.trim();
    if (!query || this.searching) return null;
    this.busy = '/v2/models/search';
    this.searching = true;
    this.searchError = '';
    errors.observe('search', '', '', false);
    this.searchResults = [];
    this.searchTotalVram = null;
    this.searchWarnings = [];
    this.searchQuery = query;
    try {
      const r = await fetch('/v2/models/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query })
      });
      const text = await r.text();
      let data: unknown = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text;
      }
      if (!r.ok) {
        const msg = apiErrorMessage(data, text, r.status, 'search');
        this.setRequestError('searchError', msg, errorGeneration, true);
        this.searchResults = [];
        this.searchTotalVram = null;
        this.searchWarnings = [];
        pushToast('danger', msg, 4000, { source: 'search', generation: errorGeneration });
        throw new Error(msg);
      }
      const body = data as ModelSearchResponse;
      this.searchResults = body.results ?? [];
      this.searchTotalVram = body.total_vram_bytes ?? null;
      this.searchWarnings = Array.isArray(body.warnings) ? body.warnings : [];
      if (typeof body.query === 'string' && body.query.trim()) this.searchQuery = body.query;
      return body;
    } catch (e) {
      if (!this.searchError) {
        const msg = String(e);
        this.setRequestError('searchError', msg, errorGeneration, true);
        pushToast('danger', msg, 4000, { source: 'search', generation: errorGeneration });
      }
      this.searchResults = [];
      this.searchTotalVram = null;
      this.searchWarnings = [];
      throw e;
    } finally {
      this.busy = '';
      this.searching = false;
    }
  }

  async downloadModel(source?: string) {
    const src = source !== undefined ? source : this.modelSource.trim();
    if (!src) return;
    this.modelSource = '';
    await this.post('/v2/models/downloads', { source: src });
  }

  /** Cancel the in-flight HF download. */
  async cancelDownload() {
    const errorGeneration = errors.generation;
    if (!this.downloadJob || this.downloadJob.state !== 'running') return;
    this.busy = '/v2/models/downloads/cancel';
    try {
      const r = await fetch('/v2/models/downloads/cancel', { method: 'POST' });
      const text = await r.text();
      if (!r.ok) {
        pushToast('danger', text || `cancel ${r.status}`, 4000, { generation: errorGeneration });
        return;
      }
      await this.refresh();
    } catch (e) {
      pushToast('danger', String(e), 4000, { generation: errorGeneration });
    } finally {
      this.busy = '';
    }
  }

  async deleteModel(name: string) {
    if (!name) return;
    if (!confirm(`Delete ${name} from this node's model store?`)) return;
    await this.post('/v2/models/deletions', { name });
    if (this.selectedModel === name) this.selectedModel = this.models[0]?.id ?? '';
  }

  selectedNetworkId = $state('');

  networkPlanPayload(): Record<string, unknown> {
    return {...this.planPayload(), ...(this.selectedNetworkId ? {network_id: this.selectedNetworkId} : {})};
  }

  async plan() {
    if (this.planRunBlockedReason()) return;
    if (!this.validateRunSettings()) return;
    if (this.estimateTimer) clearTimeout(this.estimateTimer);
    this.estimateTimer = null;
    ++this.estimateSeq;
    this.planResult = (await this.post('/v2/inference/plans', { ...this.networkPlanPayload(), remember: false })) as Record<string, unknown>;
  }

  /** Debounced silent plan for live Estimated VRAM (no toast / busy). */
  scheduleEstimate(delayMs = 300) {
    this.planResult = null;
    this.estimateError = '';
    errors.observe('estimate', '', '', false);
    this.estimateNote = '';
    ++this.estimateSeq;
    if (this.estimateTimer) clearTimeout(this.estimateTimer);
    this.estimateTimer = setTimeout(() => {
      this.estimateTimer = null;
      void this.refreshEstimate();
    }, delayMs);
  }

  async refreshEstimate() {
    const errorGeneration = errors.generation;
    if (this.planRunBlockedReason()) return;
    if (!this.selectedModel) return;
    if (!this.validateRunSettings()) return;
    if (this.busy) return;
    const seq = ++this.estimateSeq;
    const payload: Record<string, unknown> = { ...this.networkPlanPayload(), remember: false };
    this.estimating = true;
    try {
      const r = await fetch('/v2/inference/plans?preview=true', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const text = await r.text();
      let data: unknown = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text;
      }
      if (seq !== this.estimateSeq) return;
      if (!r.ok) {
        this.planResult = null;
        this.setRequestError('estimateError', apiErrorMessage(data, text, r.status, 'Estimate'), errorGeneration, false);
        return;
      }
      this.estimateError = '';
      errors.observe('estimate', '', '', false);
      this.planResult = data as Record<string, unknown>;
      errors.observe('plan-overflow', this.planResult.fit_status === 'overflow'
        ? String(this.planResult.hint ?? 'Model + context exceed cluster budget — lower context or pick a smaller quant.')
        : '', '', false, errorGeneration);
      ++this.estimateRecalculations;
      this.estimateNote = 'Preview recalculated.';
      const previous = this.lastEstimate;
      const resolved = this.planResult.resolved_launch_settings as Record<string, unknown> | undefined;
      const previousResolved = previous?.result.resolved_launch_settings as Record<string, unknown> | undefined;
      if (previous && previous.payload.model === payload.model
        && (previous.payload.cache_type_k !== payload.cache_type_k || previous.payload.cache_type_v !== payload.cache_type_v)) {
        const before = Number(previous.result.kv_estimate_bytes);
        const after = Number(this.planResult.kv_estimate_bytes);
        if (Number.isFinite(before) && Number.isFinite(after) && before > after) {
          this.estimateNote += ` KV cache decreased by ${formatMemoryComponent(before - after)}.`;
          if (previous.result.weights_estimate_bytes === this.planResult.weights_estimate_bytes
            && previous.result.checkpoint_estimate_bytes === this.planResult.checkpoint_estimate_bytes) {
            this.estimateNote += ' Model weights and host checkpoints are unchanged.';
          }
        } else if (resolved?.context !== previousResolved?.context) {
          this.estimateNote += ' Resolved context changed; compare KV types at a fixed context to see quantization savings.';
        } else if (resolved?.cache_type_k === previousResolved?.cache_type_k && resolved?.cache_type_v === previousResolved?.cache_type_v) {
          this.estimateNote += ' Effective K/V types are unchanged; check extra arguments for cache-type overrides.';
        }
      }
      if (previous && previous.payload.model === payload.model && previous.payload.batch_size !== payload.batch_size
        && previous.result.estimated_bytes === this.planResult.estimated_bytes
        && resolved?.micro_batch != null && resolved.micro_batch === previousResolved?.micro_batch) {
        this.estimateNote += ` Total unchanged: logical batch changed, while physical micro-batch remains ${resolved.micro_batch}.`;
      }
      this.lastEstimate = { payload, result: this.planResult };
      if (typeof (data as Record<string, unknown>).distributable_bytes === 'number')
        this.distributableBytes = (data as Record<string, number>).distributable_bytes;
    } catch {
      if (seq === this.estimateSeq) { this.planResult = null; this.setRequestError('estimateError', 'Estimate unavailable', errorGeneration, false); }
    } finally {
      if (seq === this.estimateSeq) this.estimating = false;
    }
  }

  setAutoInt(field: 'parallelSlots' | 'batchSize' | 'microBatch' | 'moeOffload' | 'checkpoints' | 'cacheRam', mode: 'auto' | 'edit') {
    const defaults: Record<typeof field, number> = {
      parallelSlots: 1,
      batchSize: 512,
      microBatch: 128,
      moeOffload: 0,
      checkpoints: 0,
      cacheRam: 0
    };
    if (mode === 'auto') {
      this[field] = 'auto';
    } else if (this[field] === 'auto') {
      this[field] = field === 'microBatch' && typeof this.batchSize === 'number'
        ? Math.min(defaults[field], this.batchSize) : defaults[field];
    }
    this.scheduleEstimate();
  }

  setSpeculativeMode(mode: 'auto' | 'edit') {
    this.speculative = mode === 'auto' ? 'auto' : 'draft';
    if (mode === 'auto') this.draftModel = '';
    this.scheduleEstimate();
  }

  async run() {
    const errorGeneration = errors.generation;
    if (this.planRunBlockedReason()) return;
    if (!this.validateRunSettings(true)) return;
    if (this.estimating || !this.planResult || this.estimateError) return;
    if (this.planResult?.fit_status === 'overflow') {
      pushToast('danger', String(this.planResult.hint ?? 'overflow — adjust context and re-plan'), 4000, { generation: errorGeneration });
      return;
    }
    if (this.estimateTimer) clearTimeout(this.estimateTimer);
    this.estimateTimer = null;
    ++this.estimateSeq;
    errors.observe('apply', '');
    this.lastApplyError = null;
    if (this.selectedNetworkId) await this.loadToken();
    const data = (await this.post('/v2/inference/runs', this.networkPlanPayload())) as Record<string, unknown>;
    this.activeRunConfig = (data.effective_config ?? null) as EffectiveConfig | null;
    if (this.remember) {
      this.savedModelConfig = this.planPayload();
      this.markSettingsClean();
    }
    const plan = (data.plan ?? {}) as Record<string, unknown>;
    if (Object.keys(plan).length) {
      this.planResult = plan;
    }
    if (plan.fit_status === 'tight' || data.warning) {
      pushToast('warn', String(data.warning ?? plan.hint ?? 'tight fit'));
    }
  }

  stopChat() {
    this.abort?.abort();
    this.abort = null;
    this.chatStreaming = false;
  }

  async stopInference() {
    this.clearErrors();
    this.stopChat();
    this.clearingLifecycle = true;
    try {
      await this.post('/v2/inference/runs/stop');
    } finally { this.clearingLifecycle = false; }
  }

  async sendChat() {
    const errorGeneration = errors.generation;
    const content = this.chatInput.trim();
    if (!content || this.chatStreaming || !this.status?.inference_up) return;
    if (this.chatResponseFormat === 'json_schema') {
      try {
        const schema = JSON.parse(this.chatSchemaText);
        if (!schema || typeof schema !== 'object' || Array.isArray(schema)) throw new Error('Schema must be a JSON object.');
      } catch (error) { pushToast('danger', `Invalid output schema: ${String(error)}`); return; }
    }
    if (!this.apiToken) await this.loadToken();
    this.chat = [...this.chat, { role: 'user', content }, { role: 'assistant', content: '' }];
    this.chatInput = '';
    this.chatStreaming = true;
    this.abort = new AbortController();
    try {
      const r = await fetch('/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${this.apiToken}`
        },
        body: JSON.stringify({
          model: this.status?.model_name || this.selectedModel || 'default',
          messages: this.chat.slice(0, -1),
          stream: true,
          ...(this.chatTemperature !== null ? { temperature: this.chatTemperature } : {}),
          ...(this.chatOutputLimit !== null ? { max_tokens: this.chatOutputLimit } : {}),
          ...(this.chatThinking !== null ? { chat_template_kwargs: { enable_thinking: this.chatThinking } } : {}),
          ...(this.chatResponseFormat === 'json_object' ? { response_format: { type: 'json_object' } } : {}),
          ...(this.chatResponseFormat === 'json_schema' ? { response_format: {
            type: 'json_schema', json_schema: { name: 'chat_output', strict: true, schema: JSON.parse(this.chatSchemaText) }
          } } : {})
        }),
        signal: this.abort.signal
      });
      if (!r.ok || !r.body) {
        const t = await r.text();
        pushToast('danger', t || `chat ${r.status}`, 4000, { generation: errorGeneration });
        this.chatStreaming = false;
        return;
      }
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split('\n');
        buf = parts.pop() ?? '';
        for (const line of parts) {
          if (!line.startsWith('data: ')) continue;
          const payload = line.slice(6).trim();
          if (payload === '[DONE]') continue;
          try {
            const json = JSON.parse(payload);
            const delta = json.choices?.[0]?.delta?.content;
            if (delta) {
              const copy = [...this.chat];
              const last = copy[copy.length - 1];
              if (last?.role === 'assistant') {
                copy[copy.length - 1] = { ...last, content: last.content + delta };
                this.chat = copy;
              }
            }
          } catch {
            /* ignore partial */
          }
        }
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') pushToast('danger', String(e), 4000, { generation: errorGeneration });
    } finally {
      this.chatStreaming = false;
      this.abort = null;
    }
  }
}

export const appStore = new AppStore();
