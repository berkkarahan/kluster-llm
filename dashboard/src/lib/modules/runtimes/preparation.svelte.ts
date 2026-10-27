type Job = Record<string, any>;
type Transport = { primaJob(id: string, signal?: AbortSignal): Promise<Job>; cancelPrima(id: string): Promise<unknown> };
const terminal = (job: Job) => ['prepared', 'failed', 'cancelled', 'interrupted'].includes(job.phase);

/** One observer per application, shared by Settings and Deploy. Unsubscribe never cancels work. */
export class PrimaPreparationObserver {
  job = $state.raw<Job | null>(null);
  error = $state('');
  cancellationRequested = $state(false);
  onSettled: (job: Job) => void = () => {};
  private owners = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private read: AbortController | null = null;
  private generation = 0;
  private failures = 0;
  constructor(readonly transport: Transport, readonly canCommand: () => boolean = () => false) {}
  get cancelReason() { return this.canCommand() ? '' : 'Only the master can cancel preparation. Workers can observe progress.'; }
  get active() { return !!this.job && (!terminal(this.job) || this.job.local_work_pending !== false); }
  get settlementUnknown() { return !!this.job && terminal(this.job) && this.job.local_work_pending === undefined; }
  observe(job: Job | null) {
    if (!job?.job_id) return;
    if (this.job && job.job_id !== this.job.job_id && this.active) return;
    if (job.job_id !== this.job?.job_id) { this.abort(); this.cancellationRequested = false; }
    this.job = job;
    if (!this.active) { this.clearTimer(); this.onSettled(job); }
    else this.schedule();
  }
  acquire() {
    ++this.owners;
    if (this.owners === 1) void this.refresh();
    let released = false;
    return () => { if (released) return; released = true; this.owners = Math.max(0,this.owners-1); if (!this.owners) this.abort(); };
  }
  private clearTimer() { if (this.timer) clearTimeout(this.timer); this.timer = null; }
  private abort() { ++this.generation; this.clearTimer(); this.read?.abort(); this.read = null; }
  private schedule() {
    if (!this.owners || !this.active || this.read || this.timer || this.settlementUnknown) return;
    this.timer = setTimeout(() => { this.timer = null; void this.refresh(); }, Math.min(1000 * 2 ** this.failures, 15000));
  }
  async refresh() {
    if (!this.owners || !this.job?.job_id || this.read) return;
    this.clearTimer();
    const id = this.job.job_id, generation = this.generation, controller = new AbortController();
    this.read = controller;
    try {
      const job = await this.transport.primaJob(id, controller.signal);
      if (generation !== this.generation || controller.signal.aborted || this.job?.job_id !== id) return;
      this.failures = 0; this.error = ''; this.observe(job);
    } catch (error) {
      if (!controller.signal.aborted && generation === this.generation) { this.error = String(error); ++this.failures; }
    } finally {
      if (generation === this.generation) { this.read = null; this.schedule(); }
    }
  }
  async cancel() {
    if (!this.canCommand() || !this.active || !this.job || this.cancellationRequested) return;
    this.cancellationRequested = true;
    try {
      const result = await this.transport.cancelPrima(this.job.job_id) as Job | null;
      if (result) this.observe(result);
      else this.cancellationRequested = false; // Authorization changed before dispatch.
    }
    catch (error) { this.error = `Cancellation outcome unknown: ${String(error)}. Refresh status; remote leases may still be retiring.`; }
  }
  stop() { this.owners = 0; this.abort(); }
}
