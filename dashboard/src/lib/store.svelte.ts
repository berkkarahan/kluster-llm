import type { ChatMessage, ClusterView, GpuInfo, HfTokenSource, HfTokenStatus, ModelInfo, Status } from './types';
import { DEFAULT_MAX_CONTEXT, MIN_CONTEXT, normalizeKvCacheType } from './types';
import { pushToast } from './toast';

let fingerprint = '';

function fp(s: Status | null): string {
  if (!s) return '';
  return JSON.stringify({
    role: s.role,
    members: s.members,
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
  connected = $state(false);
  statusSeen = $state(false);
  models = $state<ModelInfo[]>([]);
  apiToken = $state('');
  busy = $state('');
  joinKeyInput = $state('');
  rpcBindInput = $state('');
  overlay = $state<'none' | 'wireguard'>('none');
  gpus = $state<GpuInfo[]>([]);
  gpuBackend = $state('cpu');
  selectedGpuIds = $state<number[]>([]);
  modelSource = $state('');
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
  remember = $state(true);
  advancedOpen = $state(false);
  fieldErrors = $state<Record<string, string>>({});
  device = $state<'cpu' | 'gpu'>('gpu');
  vramPercent = $state(80);
  ramPercent = $state(80);
  planResult = $state<Record<string, unknown> | null>(null);
  chat = $state<ChatMessage[]>([]);
  chatInput = $state('');
  chatStreaming = $state(false);
  private timer: ReturnType<typeof setInterval> | null = null;
  private abort: AbortController | null = null;
  private gpusLoaded = false;
  private configGpuIds: number[] | null = null;
  private selectionHydrated = false;
  private lastApplyError: string | null = null;
  private lastApplyWarning: string | null = null;

  viewState(): ClusterView {
    if (!this.statusSeen) return 'loading';
    if (!this.connected) return 'offline';
    const role = this.status?.role;
    if (role === 'master' || role === 'worker') return 'live';
    return 'idle';
  }

  start() {
    void this.refresh();
    void this.loadToken();
    void this.loadHfToken();
    void this.loadModels();
    void this.loadConfig();
    void this.loadGpus();
    this.timer = setInterval(() => void this.refresh(), 1000);
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  async refresh() {
    try {
      const r = await fetch('/v1/status');
      if (!r.ok) throw new Error(`status ${r.status}`);
      const data = (await r.json()) as Status;
      this.statusSeen = true;
      this.status = data;
      this.connected = true;
      const live = data.role === 'master' || data.role === 'worker';
      if (live) {
        if (data.join_key) this.joinKeyInput = data.join_key;
        if (data.rpc_bind) this.rpcBindInput = data.rpc_bind;
        if (data.overlay === 'none' || data.overlay === 'wireguard') this.overlay = data.overlay;
      }
      const next = fp(data);
      if (next !== fingerprint) {
        fingerprint = next;
        if (data.model_name && !this.selectedModel) this.selectedModel = data.model_name;
        if (data.last_plan) this.planResult = data.last_plan as Record<string, unknown>;
        else if (data.role === 'idle') this.planResult = null;
        const err = data.apply_error ?? null;
        const warn = data.apply_warning ?? null;
        if (err && err !== this.lastApplyError) pushToast('danger', err);
        if (warn && warn !== this.lastApplyWarning) pushToast('warn', warn);
        this.lastApplyError = err;
        this.lastApplyWarning = warn;
        if (!this.selectionHydrated) this.hydrateGpuSelection();
      }
    } catch {
      this.connected = false;
    }
  }

  async loadToken() {
    try {
      const r = await fetch('/v1/api-token');
      if (!r.ok) return;
      const data = await r.json();
      this.apiToken = data.api_token ?? '';
    } catch {
      /* ignore */
    }
  }

  async loadHfToken() {
    try {
      const r = await fetch('/v1/hf-token');
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
    this.busy = '/v1/hf-token';
    try {
      const r = await fetch('/v1/hf-token', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token })
      });
      const text = await r.text();
      if (!r.ok) {
        pushToast('danger', text || `hf-token ${r.status}`);
        throw new Error(text);
      }
      pushToast('ok', token ? 'hf token saved' : 'hf token cleared');
      await this.loadHfToken();
    } finally {
      this.busy = '';
    }
  }

  async loadModels() {
    try {
      const r = await fetch('/v1/models');
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

  applyParameterSet(ps: Record<string, unknown>) {
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
    if (typeof ps.threads === 'number') this.threads = ps.threads;
    if (Array.isArray(ps.extra_args)) this.extraArgsText = (ps.extra_args as string[]).join(' ');
    if (typeof ps.remember === 'boolean') this.remember = ps.remember;
    if (typeof ps.advanced_open === 'boolean') this.advancedOpen = ps.advanced_open;
    if (ps.device === 'cpu' || ps.device === 'gpu') this.device = ps.device;
  }

  async selectModel(id: string) {
    this.selectedModel = id;
    try {
      const r = await fetch(`/v1/model-settings/${encodeURIComponent(id)}`);
      if (!r.ok) {
        this.clampContextToModel();
        return;
      }
      const data = await r.json();
      if (data.parameter_set) this.applyParameterSet(data.parameter_set);
      else this.clampContextToModel();
    } catch {
      this.clampContextToModel();
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
    this.speculative = 'auto';
    this.draftModel = '';
    this.vision = false;
    this.mmproj = '';
    this.chatTemplate = 'default';
    this.chatTemplateText = '';
    this.mmapMlock = 'auto';
    this.checkpoints = 'auto';
    this.cacheRam = 'auto';
    this.threads = 8;
    this.extraArgsText = '';
    this.advancedOpen = false;
    this.fieldErrors = {};
    this.planResult = null;
  }

  planRunBlockedReason(): string | null {
    if (!this.models.length) return 'Add a local model before planning or running.';
    if (this.status?.role !== 'master') return 'Start as cluster master before Plan & Run.';
    const members = this.status?.members ?? {};
    if (Object.keys(members).length === 0) return 'Register at least one cluster member.';
    return null;
  }

  validateRunSettings(): boolean {
    const err: Record<string, string> = {};
    const maxCtx = this.selectedModelMaxContext();
    if (!this.contextAuto && this.context < MIN_CONTEXT)
      err.context = `Context must be at least ${MIN_CONTEXT} when Auto is off.`;
    else if (!this.contextAuto && this.context > maxCtx)
      err.context = `Context must be ≤ ${maxCtx} for this model.`;
    if (this.threads < 1) err.threads = 'Threads must be at least 1.';
    if (this.vramPercent < 10 || this.vramPercent > 100) err.vramPercent = 'GPU memory % must be 10–100.';
    if (this.ramPercent < 10 || this.ramPercent > 100) err.ramPercent = 'RAM % must be 10–100.';
    this.fieldErrors = err;
    return Object.keys(err).length === 0;
  }

  planPayload() {
    const extra = this.extraArgsText
      .split(/\s+/)
      .map((s) => s.trim())
      .filter(Boolean);
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
      vision: this.vision,
      mmproj: this.mmproj || null,
      device: this.device,
      vram_percent: this.device === 'gpu' ? this.vramPercent : undefined,
      ram_percent: this.device === 'cpu' ? this.ramPercent : undefined,
      chat_template: this.chatTemplate,
      chat_template_text: this.chatTemplateText || null,
      mmap_mlock: this.mmapMlock,
      checkpoints: this.checkpoints,
      cache_ram: this.cacheRam,
      threads: this.threads,
      extra_args: extra,
      remember: this.remember,
      advanced_open: this.advancedOpen
    };
    return body;
  }

  async loadConfig() {
    try {
      const r = await fetch('/v1/config');
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

  async loadGpus() {
    try {
      const r = await fetch('/v1/gpus');
      if (!r.ok) return;
      const data = await r.json();
      this.gpuBackend = data.backend ?? 'cpu';
      const list = (data.gpus ?? []) as GpuInfo[];
      this.gpus = list;
      if (!this.gpusLoaded) {
        this.gpusLoaded = true;
        this.hydrateGpuSelection();
      }
    } catch {
      /* ignore */
    }
  }

  toggleGpu(index: number) {
    if (this.selectedGpuIds.includes(index)) {
      if (this.selectedGpuIds.length <= 1) return;
      this.selectedGpuIds = this.selectedGpuIds.filter((i) => i !== index);
    } else {
      this.selectedGpuIds = [...this.selectedGpuIds, index].sort((a, b) => a - b);
    }
  }

  async saveConfig(partial?: {
    device?: 'cpu' | 'gpu';
    gpu_ids?: number[] | null;
    vram_percent?: number;
    ram_percent?: number;
  }) {
    const device = partial?.device ?? this.device;
    this.device = device;
    const body: Record<string, unknown> = {
      device,
      vram_percent: partial?.vram_percent ?? this.vramPercent,
      ram_percent: partial?.ram_percent ?? this.ramPercent
    };
    if (device === 'gpu') {
      const ids = partial?.gpu_ids !== undefined ? partial.gpu_ids : this.selectedGpuIds;
      body.gpu_ids = ids && ids.length > 0 ? ids : null;
      this.configGpuIds = (body.gpu_ids as number[] | null) ?? null;
    }
    this.busy = '/v1/config';
    try {
      const r = await fetch('/v1/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      const text = await r.text();
      if (!r.ok) {
        pushToast('danger', text || `config ${r.status}`);
        throw new Error(text);
      }
      try {
        const data = text ? JSON.parse(text) : {};
        if (typeof data.vram_percent === 'number') this.vramPercent = data.vram_percent;
        if (typeof data.ram_percent === 'number') this.ramPercent = data.ram_percent;
      } catch {
        /* ignore */
      }
      pushToast('ok', 'config saved');
      await this.refresh();
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
    }
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

  async post(path: string, body?: unknown) {
    this.busy = path;
    try {
      const r = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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
        pushToast('danger', typeof data === 'string' ? data : JSON.stringify(data));
        throw new Error(String(text));
      }
      pushToast('ok', `${path} ok`);
      await this.refresh();
      await this.loadModels();
      return data;
    } finally {
      this.busy = '';
    }
  }

  async startMaster() {
    const body: Record<string, unknown> = {
      key: this.joinKeyInput || null,
      rpc_bind: this.rpcBindInput || null,
      overlay: this.overlay
    };
    if (this.gpus.length > 0) {
      body.gpu_ids =
        this.selectedGpuIds.length > 0 ? this.selectedGpuIds : this.gpus.map((g) => g.index);
    }
    await this.post('/v1/start', body);
  }

  async stopCluster() {
    await this.post('/v1/stop');
  }

  async joinCluster() {
    const body: Record<string, unknown> = {
      key: this.joinKeyInput,
      master_port: 18765,
      rpc_port: 50052,
      rpc_bind: this.rpcBindInput || null,
      overlay: this.overlay
    };
    if (this.gpus.length > 0) {
      body.gpu_ids =
        this.selectedGpuIds.length > 0 ? this.selectedGpuIds : this.gpus.map((g) => g.index);
    }
    await this.post('/v1/join', body);
  }

  async leaveCluster() {
    await this.post('/v1/leave');
  }

  async downloadModel() {
    await this.post('/v1/models/download', { source: this.modelSource });
    this.modelSource = '';
  }

  async distributeModel() {
    if (!this.selectedModel) return;
    await this.post('/v1/models/distribute', { name: this.selectedModel });
  }

  async deleteModel(name: string) {
    if (!name) return;
    if (!confirm(`Delete ${name} locally and on workers?`)) return;
    await this.post('/v1/models/delete', { name });
    if (this.selectedModel === name) this.selectedModel = this.models[0]?.id ?? '';
  }

  async plan() {
    if (this.planRunBlockedReason()) return;
    if (!this.validateRunSettings()) return;
    this.planResult = (await this.post('/v1/plan', this.planPayload())) as Record<string, unknown>;
  }

  async run() {
    if (this.planRunBlockedReason()) return;
    if (!this.validateRunSettings()) return;
    if (this.planResult?.fit_status === 'overflow') {
      pushToast('danger', String(this.planResult.hint ?? 'overflow — adjust context and re-plan'));
      return;
    }
    const data = (await this.post('/v1/run', this.planPayload())) as Record<string, unknown>;
    const plan = (data.plan ?? {}) as Record<string, unknown>;
    if (plan.fit_status === 'tight' || data.warning) {
      pushToast('warn', String(data.warning ?? plan.hint ?? 'tight fit'));
    }
  }

  stopChat() {
    this.abort?.abort();
    this.abort = null;
    this.chatStreaming = false;
  }

  async sendChat() {
    const content = this.chatInput.trim();
    if (!content || this.chatStreaming) return;
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
          stream: true
        }),
        signal: this.abort.signal
      });
      if (!r.ok || !r.body) {
        const t = await r.text();
        pushToast('danger', t || `chat ${r.status}`);
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
      if ((e as Error).name !== 'AbortError') pushToast('danger', String(e));
    } finally {
      this.chatStreaming = false;
      this.abort = null;
    }
  }
}

export const appStore = new AppStore();
