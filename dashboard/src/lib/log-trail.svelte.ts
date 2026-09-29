import type { LogEntry, LogSnapshot, LogSource, RefreshMode } from './types';

export const REFRESH_MODES: RefreshMode[] = ['live', '5s', '30s', '1m'];

export const REFRESH_MS: Record<RefreshMode, number> = {
  live: 1000,
  '5s': 5000,
  '30s': 30000,
  '1m': 60000
};

/** Fixed dispatch-through-decode deadline for one snapshot read. */
export const REQUEST_TIMEOUT_MS = 5000;
/** Newest accepted entries kept in the DOM; older rows are dropped with a notice. */
export const MAX_ROWS = 2000;
const INITIAL_N = 200;
const POLL_N = 5000;

export type LogTrailState = 'loading' | 'ready' | 'empty' | 'cleared' | 'error';

function nowMs(): number {
  return typeof performance !== 'undefined' && typeof performance.now === 'function'
    ? performance.now()
    : Date.now();
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SOURCES = new Set<LogSource>(['agent', 'llama', 'rpc']);
const GAP_REASONS = new Set(['retention', 'batch_limit', 'feed_reset']);

function validateSnapshot(value: unknown): LogSnapshot {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Invalid log snapshot: expected an object');
  }
  const data = value as Record<string, unknown>;
  if (!Array.isArray(data.lines)) throw new Error('Invalid log snapshot: lines must be an array');
  if (data.source !== 'cluster' && data.source !== 'local') {
    throw new Error('Invalid log snapshot: source must be cluster or local');
  }
  if (typeof data.cursor !== 'string') {
    throw new Error('Incremental logs unsupported: snapshot is missing its cursor');
  }
  if (data.cursor.length > 128) throw new Error('Invalid log snapshot: cursor is too long');
  const [epoch, sequence, extra] = data.cursor.split(':');
  if (!UUID.test(epoch ?? '') || !/^\d+$/.test(sequence ?? '') || extra !== undefined) {
    throw new Error('Invalid log snapshot: cursor must use canonical UUID:sequence format');
  }
  if (data.reset !== null && data.reset !== 'origin_changed' && data.reset !== 'restart') {
    throw new Error('Invalid log snapshot: unsupported reset reason');
  }
  if (typeof data.gap !== 'boolean') throw new Error('Invalid log snapshot: gap must be boolean');
  if (data.gap_reason !== null && !GAP_REASONS.has(String(data.gap_reason))) {
    throw new Error('Invalid log snapshot: unsupported gap reason');
  }
  if (data.warning !== undefined && typeof data.warning !== 'string') {
    throw new Error('Invalid log snapshot: warning must be text');
  }
  for (const [index, entry] of data.lines.entries()) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
      throw new Error(`Invalid log snapshot: line ${index} must be an object`);
    }
    const row = entry as Record<string, unknown>;
    if (typeof row.event_id !== 'string' || !UUID.test(row.event_id)) {
      throw new Error(`Invalid log snapshot: line ${index} has an invalid event ID`);
    }
    if (typeof row.ts !== 'number' || !Number.isFinite(row.ts)) {
      throw new Error(`Invalid log snapshot: line ${index} has an invalid timestamp`);
    }
    if (typeof row.node_id !== 'string' || !row.node_id) {
      throw new Error(`Invalid log snapshot: line ${index} has no node ID`);
    }
    if (typeof row.source !== 'string' || !SOURCES.has(row.source as LogSource)) {
      throw new Error(`Invalid log snapshot: line ${index} has an invalid source`);
    }
    if (typeof row.line !== 'string') throw new Error(`Invalid log snapshot: line ${index} has no message`);
  }
  return data as unknown as LogSnapshot;
}

/**
 * Page-owned log-trail state and lifecycle.
 *
 * Polls the existing `GET /v1/logs` snapshot endpoint on a fixed cadence, keeps
 * a bounded recent buffer, and deduplicates by stable `event_id`. Clear is
 * display-only: it never mutates stored logs, and before the first successful
 * snapshot it is a no-op so an in-flight load is not discarded.
 */
export class LogTrail {
  entries = $state<LogEntry[]>([]);
  /** '' means All nodes; otherwise a stable node identity. */
  selectedNodeId = $state<string>('');
  refreshMode = $state<RefreshMode>('live');
  state = $state<LogTrailState>('loading');
  coverage = $state<'cluster' | 'local'>('cluster');
  gap = $state(false);
  warning = $state('');
  errorText = $state('');
  lastSuccessAt = $state<number | null>(null);
  omittedRows = $state(0);
  /** Last committed receiver-local checkpoint; null until the first success. */
  cursor = $state<string | null>(null);

  // ponytail: ID-only seen set for one mounted visit; it grows with observed
  // events. Swap for producer sequence watermarks if day-long high-volume
  // sessions make bounded identity memory matter.
  seenIds = new Set<string>();
  private generation = 0;
  private inFlight = false;
  private disposed = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private abort: AbortController | null = null;
  private deadline = 0;
  private hasCleared = false;
  private onVisibility: (() => void) | null = null;

  start(): void {
    if (this.disposed) return;
    if (typeof document !== 'undefined') {
      this.onVisibility = () => this.handleVisibility();
      document.addEventListener('visibilitychange', this.onVisibility);
    }
    this.deadline = nowMs() + REFRESH_MS[this.refreshMode];
    void this.read();
    this.arm();
  }

  stop(): void {
    this.disposed = true;
    this.generation += 1;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.abort?.abort();
    this.abort = null;
    this.inFlight = false;
    if (this.onVisibility && typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', this.onVisibility);
    }
    this.onVisibility = null;
    this.entries = [];
    this.seenIds.clear();
    this.cursor = null;
    this.hasCleared = false;
  }

  setMode(mode: RefreshMode): void {
    if (mode === this.refreshMode) return;
    this.refreshMode = mode;
    this.deadline = nowMs() + REFRESH_MS[mode];
    this.cancelActiveRead();
    this.arm();
  }

  setNode(id: string): void {
    this.selectedNodeId = id;
  }

  /** Display-only clear. No-op until a snapshot has been committed. */
  clear(): void {
    if (this.cursor === null) return;
    this.entries = [];
    this.omittedRows = 0;
    this.gap = false;
    this.hasCleared = true;
    this.generation += 1;
    this.abort?.abort();
    this.abort = null;
    this.inFlight = false;
    this.state = 'cleared';
  }

  visibleEntries(): LogEntry[] {
    const id = this.selectedNodeId;
    if (!id) return this.entries;
    return this.entries.filter((entry) => entry.node_id === id);
  }

  private handleVisibility(): void {
    if (this.disposed || typeof document === 'undefined') return;
    if (document.visibilityState === 'hidden') {
      if (this.timer) clearTimeout(this.timer);
      this.timer = null;
      this.cancelActiveRead();
      return;
    }
    this.deadline = nowMs() + REFRESH_MS[this.refreshMode];
    void this.read();
    this.arm();
  }

  private cancelActiveRead(): void {
    if (!this.inFlight) return;
    this.generation += 1;
    this.abort?.abort();
    this.abort = null;
    this.inFlight = false;
  }

  private arm(): void {
    if (this.disposed) return;
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
    if (this.timer) clearTimeout(this.timer);
    const interval = REFRESH_MS[this.refreshMode];
    let delay = this.deadline - nowMs();
    if (delay <= 0) {
      const missed = Math.floor((nowMs() - this.deadline) / interval) + 1;
      this.deadline += missed * interval;
      delay = this.deadline - nowMs();
    }
    this.timer = setTimeout(() => {
      this.timer = null;
      this.deadline += REFRESH_MS[this.refreshMode];
      void this.read();
      this.arm();
    }, Math.max(0, delay));
  }

  private async read(): Promise<void> {
    if (this.disposed || this.inFlight) return;
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
    this.inFlight = true;
    const gen = ++this.generation;
    const controller = new AbortController();
    this.abort = controller;
    let timedOut = false;
    const started = nowMs();
    const requestTimer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, REQUEST_TIMEOUT_MS);
    try {
      const params = new URLSearchParams({ n: String(this.cursor === null ? INITIAL_N : POLL_N) });
      if (this.cursor !== null) params.set('after', this.cursor);
      const response = await fetch(`/v1/logs?${params.toString()}`, { signal: controller.signal });
      if (!response.ok) throw new Error(`logs ${response.status}`);
      const data = validateSnapshot(await response.json());
      if (this.disposed || gen !== this.generation) return;
      if (nowMs() - started >= REQUEST_TIMEOUT_MS) throw new Error('Log read timed out; retrying');
      this.commit(data);
    } catch (error) {
      if (this.disposed || gen !== this.generation) return;
      this.errorText = timedOut ? 'Log read timed out; retrying' : String(error);
      this.state = 'error';
    } finally {
      clearTimeout(requestTimer);
      if (gen === this.generation) {
        this.inFlight = false;
        if (this.abort === controller) this.abort = null;
      }
    }
  }

  private commit(data: LogSnapshot): void {
    const isReset = Boolean(data.reset);
    const baselineOnly = this.hasCleared && isReset;
    const appended: LogEntry[] = [];
    for (const entry of data.lines ?? []) {
      if (!entry || !entry.event_id) continue;
      if (this.seenIds.has(entry.event_id)) continue;
      this.seenIds.add(entry.event_id);
      appended.push(entry);
    }
    let gapNow = Boolean(data.gap) || isReset;
    if (!baselineOnly && appended.length) {
      this.entries = [...this.entries, ...appended];
      if (this.entries.length > MAX_ROWS) {
        const trimmed = this.entries.length - MAX_ROWS;
        this.entries = this.entries.slice(-MAX_ROWS);
        this.omittedRows += trimmed;
        gapNow = true;
      }
    }
    this.state = this.entries.length ? 'ready' : this.hasCleared ? 'cleared' : 'empty';
    if (data.cursor) this.cursor = data.cursor;
    this.coverage = data.source === 'local' ? 'local' : 'cluster';
    this.gap = gapNow || this.omittedRows > 0;
    this.warning = data.warning ?? '';
    this.errorText = '';
    this.lastSuccessAt = Date.now();
  }
}
