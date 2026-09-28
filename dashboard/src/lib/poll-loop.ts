/**
 * Responsive polling loop, modeled on Unsloth Studio's download-manager poll
 * loop (`studio/frontend/src/features/hub/download-manager/poll-loop.ts`).
 *
 * Guarantees:
 * - two-tier cadence: `fastMs` while the watched resource is active,
 *   `slowMs` when idle/terminal (cheap keep-alive);
 * - ±20% jitter on every interval so concurrent dashboards don't sync up;
 * - in-flight dedup: a new tick never starts while the previous fetch is
 *   still resolving, so slow endpoints can't pile up requests;
 * - epoch staleness guard: results from a superseded run (or from after
 *   `stop()`) are dropped instead of writing stale state;
 * - visibility-aware backoff: polling pauses while the tab is hidden and
 *   resumes on `visibilitychange`, so background tabs don't hammer the API.
 *
 * Plain TypeScript on purpose — no runes, so it can be instantiated at module
 * scope (SSR-safe) and driven by explicit start/stop from `onMount`.
 */

export type PollTier = 'fast' | 'slow';

export class PollLoop {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private inFlight = false;
  private epoch = 0;
  private running = false;
  private hidden = typeof document !== 'undefined' && document.visibilityState === 'hidden';

  constructor(
    /** One poll: resolve with the decoded payload (or null when unavailable). */
    private readonly fetcher: () => Promise<unknown>,
    /** Interval while active. */
    private readonly fastMs: number,
    /** Interval while idle/terminal. */
    private readonly slowMs: number,
    /** Receives each fresh result; may mutate $state. */
    private readonly onResult: (data: unknown) => void,
    /** Optional tier selector — default: always fast. */
    private readonly tierFor?: (data: unknown) => PollTier
  ) {}

  start(): void {
    if (this.running) return;
    this.running = true;
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', this.onVisibility);
    }
    void this.tick();
  }

  stop(): void {
    this.running = false;
    // Invalidate any in-flight fetch so its result is discarded.
    this.epoch += 1;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', this.onVisibility);
    }
  }

  private onVisibility = (): void => {
    this.hidden = document.visibilityState === 'hidden';
    // Tab became visible again: resume immediately if nothing is pending.
    if (!this.hidden && this.running && !this.timer && !this.inFlight) void this.tick();
  };

  private delay(tier: PollTier): number {
    const base = tier === 'fast' ? this.fastMs : this.slowMs;
    return Math.round(base * (0.8 + Math.random() * 0.4));
  }

  private schedule(tier: PollTier): void {
    if (!this.running || this.hidden) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.tick();
    }, this.delay(tier));
  }

  private async tick(): Promise<void> {
    if (!this.running || this.inFlight) return;
    if (this.hidden) return; // paused; onVisibility resumes us
    this.inFlight = true;
    const epoch = ++this.epoch;
    try {
      const data = await this.fetcher();
      if (epoch !== this.epoch || !this.running) return;
      this.onResult(data);
      const tier = this.tierFor ? this.tierFor(data) : 'fast';
      this.schedule(tier);
    } catch {
      // Network blip: back off to the slow tier, keep the loop alive.
      if (epoch === this.epoch && this.running) this.schedule('slow');
    } finally {
      this.inFlight = false;
    }
  }
}
