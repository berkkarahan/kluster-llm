/**
 * Shared UX machinery for the Llama and Prima runtime settings views: the
 * busy/error action wrapper, build-log access, node-job cancel, and small
 * format helpers. Views keep their own dialogs, drafts, and panels; every
 * instance publishes failures to the shared `errors` store under one source
 * with a generation guard, so dialogs and panels show the same message.
 */
import { errors } from '#lib/shared/errors';
import type { RuntimeJob, RuntimeNode, RuntimeVersion } from '#lib/modules/runtimes/entities';
import type { RuntimesInteractor } from '../../interactor.svelte';

/** Phases that count as an in-flight build. */
const PREPARING_PHASES: readonly string[] = ['queued', 'downloading', 'building', 'verifying'];

export const isPreparingJob = (job: RuntimeJob | null | undefined) =>
  !!job && PREPARING_PHASES.includes(job.phase);

export const shortSha = (value: string | null | undefined) => value?.slice(0, 8) ?? '—';

export const formatDisk = (bytes: number | undefined) =>
  bytes ? `${(bytes / 1024 ** 2).toFixed(0)} MiB` : '—';

export const versionLabel = (version: RuntimeVersion | null | undefined) =>
  version?.name || version?.ref || '—';

export class RuntimeActions {
  busy = $state(false);
  error = $state('');
  errorGeneration = $state(errors.generation);
  logText = $state('');
  #logJob: RuntimeJob | null = null;
  #logNode: RuntimeNode | null = null;

  constructor(
    readonly presenter: RuntimesInteractor,
    readonly section = 'runtime-action'
  ) {}

  /** '' unless the error belongs to the current error generation. */
  get message() {
    return this.errorGeneration === errors.generation ? this.error : '';
  }

  async action(work: () => Promise<unknown>) {
    const generation = errors.generation;
    this.busy = true;
    this.error = '';
    this.errorGeneration = generation;
    errors.observe(this.section, '', '', false, generation);
    try { await work(); }
    catch (failure) {
      if (generation === errors.generation && !(failure instanceof DOMException && failure.name === 'AbortError')) {
        this.error = String(failure);
        errors.publish(this.section, this.error, generation);
      }
    } finally { this.busy = false; }
  }

  /** Open the view's log dialog, then hydrate its textarea from the local or remote job. */
  async showLogs(job: RuntimeJob | null | undefined, node: RuntimeNode | null, open: () => void) {
    if (!job?.job_id) return;
    this.#logJob = job;
    this.#logNode = node;
    this.logText = '';
    open();
    await this.refreshLogs();
  }

  async refreshLogs() {
    if (!this.#logJob?.job_id) return;
    const jobId = this.#logJob.job_id;
    const remoteNodeId = this.#logNode && this.#logNode.node_id !== this.presenter.status?.node_id
      ? this.#logNode.node_id
      : undefined;
    await this.action(async () => {
      const result = remoteNodeId !== undefined
        ? await this.presenter.remoteLogs(remoteNodeId, jobId)
        : await this.presenter.localLogs(jobId);
      this.logText = result.text;
    });
  }

  async cancel(job: RuntimeJob, node: RuntimeNode | null = null) {
    if (!job.job_id) return;
    const remote = node && node.node_id !== this.presenter.status?.node_id;
    await this.action(() => remote
      ? this.presenter.cancelRemote(node!.node_id, job.job_id!)
      : this.presenter.cancelLocal(job.job_id!));
  }
}
