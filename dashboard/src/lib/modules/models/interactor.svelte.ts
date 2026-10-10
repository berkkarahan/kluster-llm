import { errors } from '../../shared/errors';
import { pushToast } from '../../shared/toast';
import { apiErrorMessage, type HttpClient } from '../../shared/http.svelte';
import type { LiveProgress } from '../../live/entities';
import type { DownloadJob, HfTokenSource, HfTokenStatus, ModelInfo, ModelSearchHit, ModelSearchResponse } from './entities';

type RunLink = {
  selectionGeneration: number;
  selectModel(id: string): Promise<void>;
  clampContextToModel(): void;
};

export class ModelsInteractor {
  models = $state<ModelInfo[]>([]);
  selectedModel = $state('');
  modelSource = $state('');
  searchResults = $state<ModelSearchHit[]>([]);
  searchTotalVram = $state<number | null>(null);
  searchError = $state('');
  searchWarnings = $state<string[]>([]);
  searchQuery = $state('');
  searching = $state(false);
  hfTokenInput = $state('');
  hfTokenConfigured = $state(false);
  hfTokenSource = $state<HfTokenSource>(null);
  downloadJob = $state<DownloadJob | null>(null);
  private handledDownloadTerminals = new Set<string>();
  private reconciledDownloadTerminals = new Set<string>();
  private selectedDownloadTerminals = new Set<string>();
  private downloadSelectionGenerations = new Map<string, number>();
  private observedDownloadIds = new Set<string>();
  private acceptedDownloadIds = new Set<string>();
  private lastDownloadTerminal: DownloadJob | null = null;
  private runLink: RunLink | null = null;

  constructor(readonly http: HttpClient) {}

  get busy() { return this.http.busy; }
  set busy(value: string) { this.http.busy = value; }
  private get selectionGeneration() { return this.runLink?.selectionGeneration ?? 0; }

  bindRun(run: RunLink) {
    this.runLink = run;
  }

  private async selectModel(id: string) {
    await this.runLink?.selectModel(id);
  }

  private clampContextToModel() {
    this.runLink?.clampContextToModel();
  }

  private async post(path: string, body?: unknown, method: 'POST' | 'DELETE' = 'POST') {
    return this.http.command(path, body, method);
  }

  noteAcceptedDownload(operationId: string) {
    this.acceptedDownloadIds.add(operationId);
    if (this.lastDownloadTerminal?.operation_id === operationId) {
      this.maybeSelectCompletedDownload(this.lastDownloadTerminal);
    }
  }

  private setRequestError(field: 'searchError', message: string, generation: number, explicit: boolean) {
    if (generation !== errors.generation) return;
    this[field] = message;
    if (explicit) errors.publish('search', message, generation);
    else errors.observe('search', message, '', false, generation);
  }

    private applyDownload(job: DownloadJob | null) {
    this.downloadJob = job && typeof job === 'object' && 'state' in job ? job : null;
  }

  applyDownloadRecords(progress: LiveProgress<DownloadJob>) {
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
      await this.http.refresh();
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

}

