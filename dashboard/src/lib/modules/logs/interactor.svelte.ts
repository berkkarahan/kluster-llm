import { errors } from '../../shared/errors';
import type { LiveLogs } from '../../live/entities';
import type { LiveLogListener } from '../../live/updates';
import type { LogEntry, LogSnapshot, LogSource, LogTag, RefreshMode } from './entities';

type LogLivePort = {
  setLogListener(listener: LiveLogListener | null): boolean;
  setLogVisibilityListener(listener: (() => void) | null, expected?: () => void): boolean;
  readonly deliveryHealth: string;
};

const offlineLive: LogLivePort = {
  setLogListener: () => false,
  setLogVisibilityListener: () => false,
  deliveryHealth: 'loading'
};

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
const SOURCES = new Set<LogSource>(['agent', 'llama', 'rpc', 'unknown']);
const GAP_REASONS = new Set(['retention', 'batch_limit', 'feed_reset']);

function tagForSource(source: string): LogTag | null {
  if (source === 'agent') return 'ui';
  if (source === 'llama' || source === 'rpc') return 'inference';
  if (source === 'unknown') return 'unclassified';
  return null;
}

function withTag(entry: Omit<LogEntry, 'tag'> & { tag?: LogTag }, index: number): LogEntry {
  const expected = tagForSource(entry.source);
  if (!expected) {
    throw new Error(`Invalid log snapshot: line ${index} has an invalid source`);
  }
  if (entry.tag != null && entry.tag !== expected) {
    throw new Error(`Invalid log snapshot: line ${index} has a tag that does not match its source`);
  }
  return entry.tag === expected ? entry as LogEntry : { ...entry, tag: expected };
}

function validateSnapshot(value: unknown): LogSnapshot {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Invalid log snapshot: expected an object');
  }
  const data = value as Record<string, unknown>;
  if (!Array.isArray(data.lines)) throw new Error('Invalid log snapshot: lines must be an array');
  const lines = data.lines as unknown[];
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
  for (const [index, entry] of lines.entries()) {
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
    lines[index] = withTag(row as Omit<LogEntry, 'tag'> & { tag?: LogTag }, index);
  }
  return data as unknown as LogSnapshot;
}

/**
 * Page-owned log-trail state and lifecycle.
 *
 * Polls the `GET /v2/logs` snapshot endpoint on a fixed cadence, keeps
 * a bounded recent buffer, and deduplicates by stable `event_id`. Clear is
 * display-only: it never mutates stored logs, and before the first successful
 * snapshot it is a no-op so an in-flight load is not discarded.
 */
export class LogTrail {
  entries = $state<LogEntry[]>([]);
  /** '' means All nodes; otherwise a stable node identity. */
  selectedNodeId = $state<string>('');
  /** '' means All tags; otherwise one producer tag. */
  selectedTag = $state<'' | LogTag>('');
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
  continuityId = $state<string | null>(null);

  // ponytail: ID-only seen set for one mounted visit; it grows with observed
  // events. Swap for producer sequence watermarks if day-long high-volume
  // sessions make bounded identity memory matter.
  seenIds = new Set<string>();
  private generation = 0;
  private inFlight = false;
  private disposed = false;
  paused = $state(false);
  private timer: ReturnType<typeof setTimeout> | null = null;
  private fallbackTimer: ReturnType<typeof setTimeout> | null = null;
  private abort: AbortController | null = null;
  private deadline = 0;
  private hasCleared = false;
  private onVisibility: (() => void) | null = null;
  private visibilityListener = () => this.handleVisibility();
  private usingLive = false;
  private denied = false;
  private liveListener = {
    after: () => this.cursor,
    continuity: () => this.continuityId,
    onLogs: (logs: LiveLogs) => this.receiveLive(logs)
  };

  constructor(private readonly live: LogLivePort = offlineLive) {}

  start(): void {
    if (this.disposed) return;
    if (this.paused) {
      if (this.cursor === null) void this.read(true);
      return;
    }
    if (typeof document !== 'undefined' && !this.live.setLogVisibilityListener(this.visibilityListener)) {
      this.onVisibility = this.visibilityListener;
      document.addEventListener('visibilitychange', this.onVisibility);
    }
    this.deadline = nowMs() + REFRESH_MS[this.refreshMode];
    this.usingLive = this.refreshMode === 'live' && this.live.setLogListener(this.liveListener);
    if (this.usingLive) {
      this.armLiveFallback();
      return;
    }
    void this.read();
    this.arm();
  }

  stop(): void {
    this.disposed = true;
    this.generation += 1;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (this.fallbackTimer) clearTimeout(this.fallbackTimer);
    this.fallbackTimer = null;
    this.abort?.abort();
    this.abort = null;
    this.inFlight = false;
    if (this.onVisibility && typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', this.onVisibility);
    }
    this.live.setLogVisibilityListener(null, this.visibilityListener);
    this.onVisibility = null;
    if (this.usingLive) this.live.setLogListener(null);
    this.usingLive = false;
    this.entries = [];
    this.seenIds.clear();
    this.cursor = null;
    this.continuityId = null;
    this.hasCleared = false;
  }

  setMode(mode: RefreshMode): void {
    if (mode === this.refreshMode) return;
    this.refreshMode = mode;
    if (this.paused) return;
    if (mode === 'live') this.denied = false;
    this.deadline = nowMs() + REFRESH_MS[mode];
    this.cancelActiveRead();
    if (mode === 'live') {
      this.usingLive = this.live.setLogListener(this.liveListener);
      if (this.usingLive) {
        if (this.timer) clearTimeout(this.timer);
        this.timer = null;
        this.armLiveFallback();
        return;
      }
    } else if (this.usingLive) {
      this.live.setLogListener(null);
      this.usingLive = false;
      if (this.fallbackTimer) clearTimeout(this.fallbackTimer);
      this.fallbackTimer = null;
    }
    this.arm();
  }

  setNode(id: string): void {
    this.selectedNodeId = id;
  }

  setEnabled(enabled: boolean): void {
    if (this.disposed || this.paused === !enabled) return;
    this.paused = !enabled;
    if (enabled) {
      this.start();
      return;
    }
    this.generation += 1;
    this.abort?.abort();
    this.abort = null;
    this.inFlight = false;
    if (this.timer) clearTimeout(this.timer);
    if (this.fallbackTimer) clearTimeout(this.fallbackTimer);
    this.timer = this.fallbackTimer = null;
    if (this.usingLive) this.live.setLogListener(null);
    this.usingLive = false;
    if (this.cursor === null) void this.read(true);
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
    if (this.usingLive) this.live.setLogListener(this.liveListener);
  }

  visibleEntries(): LogEntry[] {
    const id = this.selectedNodeId;
    const tag = this.selectedTag;
    return this.entries.filter((entry) => {
      if (id && entry.node_id !== id) return false;
      if (tag && entry.tag !== tag) return false;
      return true;
    });
  }

  private handleVisibility(): void {
    if (this.disposed || this.paused || typeof document === 'undefined') return;
    if (document.visibilityState === 'hidden') {
      if (this.timer) clearTimeout(this.timer);
      this.timer = null;
      this.cancelActiveRead();
      if (this.fallbackTimer) clearTimeout(this.fallbackTimer);
      this.fallbackTimer = null;
      return;
    }
    this.deadline = nowMs() + REFRESH_MS[this.refreshMode];
    if (this.usingLive) {
      this.armLiveFallback();
      return;
    }
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

  private armLiveFallback(): void {
    if (this.disposed || this.paused || !this.usingLive || this.fallbackTimer
      || (typeof document !== 'undefined' && document.visibilityState === 'hidden')) return;
    const tick = () => {
      this.fallbackTimer = null;
      if (this.disposed || !this.usingLive || (typeof document !== 'undefined' && document.visibilityState === 'hidden')) return;
      if (!this.denied && (this.live.deliveryHealth === 'periodic-refresh'
        || this.live.deliveryHealth === 'unavailable')) void this.read();
      this.fallbackTimer = setTimeout(tick, 2000);
    };
    this.fallbackTimer = setTimeout(tick, 2000);
  }

  private arm(): void {
    if (this.disposed || this.paused) return;
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

  private async read(retainedHistory = false): Promise<void> {
    const errorGeneration = errors.generation;
    if (this.disposed || (this.paused && !retainedHistory) || this.inFlight || (this.usingLive && this.denied)) return;
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
      const liveFallback = this.usingLive && this.refreshMode === 'live';
      const params = liveFallback ? new URLSearchParams({ topics: '', logs: '1' })
        : new URLSearchParams({ n: String(this.cursor === null ? INITIAL_N : POLL_N) });
      if (this.cursor !== null) params.set('after', this.cursor);
      if (liveFallback && this.continuityId) params.set('continuity_id', this.continuityId);
      let response = await fetch(`${liveFallback ? '/v2/events' : '/v2/logs'}?${params}`, { signal: controller.signal });
      if (liveFallback && (response.status === 401 || response.status === 403)) this.denied = true;
      let legacyHandoff = false;
      if (liveFallback && !response.ok && response.status !== 401 && response.status !== 403) {
        const legacy = new URLSearchParams({ n: String(this.cursor === null ? INITIAL_N : POLL_N) });
        if (this.cursor && !this.continuityId) legacy.set('after', this.cursor);
        response = await fetch(`/v2/logs?${legacy}`, { signal: controller.signal });
        if (response.status === 401 || response.status === 403) this.denied = true;
        legacyHandoff = this.continuityId !== null;
      }
      if (!response.ok) throw new Error(`logs ${response.status}`);
      const raw = await response.json();
      if (liveFallback) {
        if (raw?.logs?.availability === 'denied') this.denied = true;
        if (!legacyHandoff && raw?.version !== 1 && raw?.lines === undefined) {
          throw new Error('Incremental logs unsupported');
        }
        if (raw?.version === 1 && (raw.logs?.availability !== 'available' || !raw.logs.snapshot)) {
          throw new Error(raw?.logs?.error?.message ?? 'Live logs unavailable');
        }
      }
      const data = validateSnapshot(raw?.version === 1 ? raw.logs.snapshot : raw);
      if (this.disposed || gen !== this.generation) return;
      if (nowMs() - started >= REQUEST_TIMEOUT_MS) throw new Error('Log read timed out; retrying');
      this.commit(legacyHandoff ? { ...data, reset: 'origin_changed', gap: true, gap_reason: 'feed_reset' } : data);
      if (legacyHandoff) this.continuityId = null;
    } catch (error) {
      if (this.disposed || gen !== this.generation) return;
      if (errorGeneration !== errors.generation) return;
      this.errorText = timedOut ? 'Log read timed out; retrying' : String(error);
      errors.observe('logs', this.errorText, '', false, errorGeneration);
      this.state = 'error';
    } finally {
      clearTimeout(requestTimer);
      if (gen === this.generation) {
        this.inFlight = false;
        if (this.abort === controller) this.abort = null;
      }
    }
  }

  private receiveLive(result: LiveLogs): void {
    if (this.disposed || this.paused || !this.usingLive) return;
    if (result.availability === 'denied') this.denied = true;
    if (result.availability !== 'available' || !result.snapshot) {
      this.errorText = result.error?.message ?? 'Live logs unavailable';
      errors.observe('logs', this.errorText);
      this.state = 'error';
      return;
    }
    try {
      this.commit(validateSnapshot(result.snapshot));
    } catch (error) {
      this.errorText = String(error);
      errors.observe('logs', this.errorText, '', false);
      this.state = 'error';
    }
  }

  private commit(data: LogSnapshot & { continuity_id?: string }): void {
    const continuityChanged = Boolean(this.continuityId && data.continuity_id
      && this.continuityId !== data.continuity_id);
    const isReset = Boolean(data.reset) || continuityChanged;
    const baselineOnly = this.hasCleared && isReset;
    const appended: LogEntry[] = [];
    for (const [index, entry] of (data.lines ?? []).entries()) {
      if (!entry || !entry.event_id) continue;
      const tagged = withTag(entry, index);
      if (this.seenIds.has(tagged.event_id)) continue;
      this.seenIds.add(tagged.event_id);
      appended.push(tagged);
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
    if (data.continuity_id) this.continuityId = data.continuity_id;
    this.coverage = data.source === 'local' ? 'local' : 'cluster';
    this.gap = this.gap || gapNow || this.omittedRows > 0;
    this.warning = data.warning ?? '';
    this.errorText = '';
    errors.observe('logs', '');
    this.lastSuccessAt = Date.now();
  }
}
