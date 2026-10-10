import { errors } from '../../shared/errors';
import type { LiveInteractor } from '../../live/interactor.svelte';
import type { UsageLive, UsageLifetimes, UsagePage, UsageSeries } from './entities';

export class UsageInteractor {
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

  constructor(readonly live: LiveInteractor) {}

  invalidateHistory() {
    ++this.historySeq;
  }

  resetAfterClusterStop() {
    if (this.liveUsage) this.liveUsage = { ...this.liveUsage, nodes: {}, relationships: [] };
    this.selectedNodeId = '';
  }

  private setRequestError(field: 'usageError' | 'historyError', message: string, generation: number, explicit: boolean) {
    if (generation !== errors.generation) return;
    this[field] = message;
    const source = field === 'usageError' ? 'usage-request' : field.replace('Error', '');
    if (explicit) errors.publish(source, message, generation);
    else errors.observe(source, message, '', false, generation);
  }

  applyUsage(data: UsageLive) {
    errors.observe('recorder', data.recording?.error ?? '');
    for (const node of Object.values(data.nodes)) errors.observe(`collector:${node.node_id}`, node.reading?.collector_error ?? '');
    this.liveUsage = data;
    this.usageError = '';
    errors.observe('usage-request', '', '', false);
    if (!this.selectedNodeId || !this.liveUsage.nodes[this.selectedNodeId]) {
      this.selectedNodeId = Object.keys(this.liveUsage.nodes)[0] ?? this.selectedNodeId;
    }
  }

  async refreshUsage() {
    const errorGeneration = errors.generation;
    if (this.live.updates) {
      this.live.updates.refresh();
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

}

