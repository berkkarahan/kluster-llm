import { formatArguments, parseArgumentText, PARAMETER_FIELDS, parameterBounds, shellQuote } from './configuration';
import type { EffectiveConfig, ExpertFlag, LoadProgress, ParameterDescriptor, PlacementStrategyMode } from './entities';
import { DEFAULT_MAX_CONTEXT, MIN_CONTEXT, normalizeKvCacheType } from './entities';
import type { ModelDefaultsInfo } from '../models/entities';
import { formatMemoryComponent } from '../../shared/format-memory';
import { errors } from '../../shared/errors';
import { pushToast } from '../../shared/toast';
import { apiErrorMessage, type HttpClient } from '../../shared/http.svelte';
import type { LiveInteractor } from '../../live/interactor.svelte';
import type { LiveProgress } from '../../live/entities';
import type { Status } from '../cluster/entities';
import type { ClusterInteractor } from '../cluster/interactor.svelte';
import type { ModelsInteractor } from '../models/interactor.svelte';
import type { NodeSettingsInteractor } from '../node-settings/interactor.svelte';
import type { NetworkInteractor } from '../network/interactor.svelte';

export class RunInteractor {
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
  placementStrategy = $state<PlacementStrategyMode>('auto');
  justEnoughMaster = $state(false);
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
  activeRunConfig = $state<EffectiveConfig | null>(null);
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
  vramWeight = $state(0.6);
  latencyWeight = $state(0.4);
  private settingsBaseline = $state('');
  planResult = $state<Record<string, unknown> | null>(null);
  estimating = $state(false);
  estimateRecalculations = $state(0);
  estimateNote = $state('');
  private lastEstimate: { payload: Record<string, unknown>; result: Record<string, unknown> } | null = null;
  estimateTimer: ReturnType<typeof setTimeout> | null = null;
  private estimateSeq = 0;
  loadProgress = $state<LoadProgress | null>(null);
  selectionGeneration = 0;
  clearErrors: () => void = () => {};
  onStopChat: () => void = () => {};

  constructor(
    readonly http: HttpClient,
    readonly live: LiveInteractor,
    readonly cluster: ClusterInteractor,
    readonly catalog: ModelsInteractor,
    readonly settings: NodeSettingsInteractor,
    readonly network: NetworkInteractor
  ) {}

  get status() { return this.cluster.status; }
  get models() { return this.catalog.models; }
  get selectedModel() { return this.catalog.selectedModel; }
  set selectedModel(value: string) { this.catalog.selectedModel = value; }
  get vramPercent() { return this.settings.vramPercent; }
  get ramPercent() { return this.settings.ramPercent; }
  get device() { return this.settings.device; }
  get selectedNetworkId() { return this.network.selectedNetworkId; }
  set selectedNetworkId(value: string) { this.network.selectedNetworkId = value; }
  get busy() { return this.http.busy; }
  set busy(value: string) { this.http.busy = value; }
  get lastApplyError() { return this.settings.lastApplyError; }
  set lastApplyError(value: string | null) { this.settings.lastApplyError = value; }

  private async post(path: string, body?: unknown, method: 'POST' | 'DELETE' = 'POST') {
    return this.http.command(path, body, method);
  }

  private loadToken() {
    return this.http.loadToken();
  }

  private setRequestError(field: 'estimateError', message: string, generation: number, explicit: boolean) {
    if (generation !== errors.generation) return;
    this[field] = message;
    if (explicit) errors.publish('estimate', message, generation);
    else errors.observe('estimate', message, '', false, generation);
  }

  watchLive() {
    this.live.onStatusFingerprint((status) => this.onStatusFingerprint(status as Status));
  }

  onStatusFingerprint(data: Status) {
    if (data.model_name && !this.selectedModel) this.selectedModel = data.model_name;
    if (!this.selectedModel && data.role === 'master' && data.last_plan) {
      this.planResult = data.last_plan as Record<string, unknown>;
    }
    if (this.selectedModel && data.role === 'master') this.scheduleEstimate();
  }

  onNodeConfigSaved() {
    ++this.estimateSeq;
    this.estimating = false;
    this.planResult = null;
    this.scheduleEstimate();
  }

  invalidateEstimate() {
    ++this.estimateSeq;
    if (this.estimateTimer) clearTimeout(this.estimateTimer);
    this.estimateTimer = null;
    this.estimating = false;
  }

  stopTimers() {
    if (this.estimateTimer) clearTimeout(this.estimateTimer);
    this.estimateTimer = null;
  }

  applyLoad(progress: LiveProgress<LoadProgress>) {
    const selected = progress.current ?? progress.last_terminal;
    if (!selected && (this.loadProgress?.phase === 'starting' || this.loadProgress?.phase === 'loading')) {
      this.loadProgress = { ...this.loadProgress, phase: 'unknown' };
    } else this.loadProgress = selected;
    errors.observe('load', selected?.phase === 'error' ? selected.error ?? 'unknown error' : '', selected?.operation_id ?? '');
  }

  async stopInference() {
    this.clearErrors();
    this.onStopChat();
    this.cluster.clearingLifecycle = true;
    try {
      await this.post('/v2/inference/runs/stop');
    } finally { this.cluster.clearingLifecycle = false; }
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
    return RunInteractor.SAMPLING_FALLBACKS[key] ?? 0;
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
    if (ps.placement_strategy === 'auto' || ps.placement_strategy === 'capacity_first' || ps.placement_strategy === 'priority') {
      this.placementStrategy = ps.placement_strategy;
      if (this.placementStrategy === 'priority' && !this.nodePriority?.length) {
        this.nodePriority = [...new Set(Object.keys(this.status?.members ?? {}))];
      } else if (this.placementStrategy !== 'priority') {
        this.nodePriority = null;
      }
    } else {
      this.placementStrategy = this.nodePriority?.length ? 'priority' : 'auto';
    }
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
    if (typeof ps.just_enough_master === 'boolean') this.justEnoughMaster = ps.just_enough_master;
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

  setPlacementStrategy(mode: PlacementStrategyMode) {
    const normalized: PlacementStrategyMode =
      mode === 'capacity_first' || mode === 'priority' ? mode : 'auto';
    this.placementStrategy = normalized;
    this.nodeShares = null;
    this.nodePriority = normalized === 'priority'
      ? [...new Set([...(this.nodePriority ?? []), ...Object.keys(this.status?.members ?? {})])]
      : null;
    this.scheduleEstimate();
  }

  setPriorityPlanning(enabled: boolean) {
    this.setPlacementStrategy(enabled ? 'priority' : 'auto');
  }

  moveNodePriority(node: string, direction: -1 | 1) {
    const order = [...new Set([...(this.nodePriority ?? []), ...Object.keys(this.status?.members ?? {})])];
    const index = order.indexOf(node);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target], order[index]];
    this.nodePriority = order;
    this.placementStrategy = 'priority';
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
    this.placementStrategy = 'auto';
    this.justEnoughMaster = false;
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
    if (this.justEnoughMaster) tokens.push('--just-enough-master');
    for (const flag of parseArgumentText(this.extraArgsText)) tokens.push(`--extra-arg=${shellQuote(JSON.stringify(flag))}`);
    return tokens.join(' ');
  }

  planPayload() {
    const extra = parseArgumentText(this.extraArgsText);
    const effectiveStrategy: PlacementStrategyMode = this.nodePriority?.length ? 'priority' : this.placementStrategy;
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
      placement_strategy: effectiveStrategy,
      just_enough_master: this.justEnoughMaster,
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

}

