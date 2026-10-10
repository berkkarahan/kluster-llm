import { errors } from '../../shared/errors';
import { pushToast } from '../../shared/toast';
import type { HttpClient } from '../../shared/http.svelte';
import type { LiveInteractor } from '../../live/interactor.svelte';
import type { AccelBackend, GpuInfo } from '../../shared/entities';
import type { ClusterInteractor } from '../cluster/interactor.svelte';
import type { HomePaths } from './entities';
import type { Status } from '../cluster/entities';

const ACCEL_LABELS: Record<AccelBackend, string> = {
  metal: 'Metal',
  cuda: 'CUDA',
  rocm: 'ROCm',
  vulkan: 'Vulkan'
};

function isAccelBackend(v: unknown): v is AccelBackend {
  return v === 'metal' || v === 'cuda' || v === 'rocm' || v === 'vulkan';
}

export class NodeSettingsInteractor {
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
  device = $state<'cpu' | 'gpu'>('gpu');
  vramPercent = $state(80);
  ramPercent = $state(80);
  home = $state<HomePaths | null>(null);
  private gpusLoaded = false;
  private configGpuIds: number[] | null = null;
  private selectionHydrated = false;
  lastApplyError: string | null = null;
  lastApplyWarning: string | null = null;
  onConfigSaved: () => void = () => {};
  onCpuDevice: () => void = () => {};

  constructor(
    readonly http: HttpClient,
    readonly live: LiveInteractor,
    readonly cluster: ClusterInteractor
  ) {}

  get busy() { return this.http.busy; }
  set busy(value: string) { this.http.busy = value; }
  get status() { return this.cluster.status; }
  private refresh() { return this.live.refresh(); }

  applyAccel(data: Status) {
    if (isAccelBackend(data.accel_backend)) this.accelBackend = data.accel_backend;
    else if (data.accel_backend === null) this.accelBackend = null;
  }

  onStatusFingerprint(data: Status) {
    const err = data.apply_error ?? null;
    const warn = data.apply_warning ?? null;
    if (err && err !== this.lastApplyError) pushToast('danger', err, 4000, { source: 'apply', snapshot: true });
    if (warn && warn !== this.lastApplyWarning) pushToast('warn', warn);
    this.lastApplyError = err;
    this.lastApplyWarning = warn;
    if (!this.selectionHydrated) this.hydrateGpuSelection();
  }

  watchLive() {
    this.live.onStatusFingerprint((status) => this.onStatusFingerprint(status as Status));
  }

  async loadHome() {
    try {
      const r = await fetch('/v2/node/home');
      if (r.ok) this.home = (await r.json()) as HomePaths;
    } catch {
      /* ignore — data directory stays informational */
    }
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

  /**
   * Auto selection keeps discrete accelerators. Integrated adapters (iGPU /
   * APU graphics) share host RAM and must not inflate the VRAM total while a
   * discrete card is present; a machine with only integrated devices keeps them.
   */
  defaultGpuIds(): number[] {
    const discrete = this.gpus.filter((g) => !g.integrated).map((g) => g.index);
    return discrete.length > 0 ? discrete : this.gpus.map((g) => g.index);
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
      this.selectedGpuIds = picked.length > 0 ? picked : this.defaultGpuIds();
    } else {
      this.selectedGpuIds = this.defaultGpuIds();
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
    const all = this.defaultGpuIds();
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
      this.onConfigSaved();
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
      this.onCpuDevice();
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

  private recoveryCommand() {
    return (
      this.gpuInstallHint || 'python scripts/install.py && python scripts/install.py --check'
    );
  }

  gpuStartWarning() {
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

}

