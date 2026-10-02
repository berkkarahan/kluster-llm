<script lang="ts">
  import ErrorMessage from '$lib/components/ErrorMessage.svelte';
  /**
   * HF / URL download job strip. State comes from appStore.downloadJob
   * (shared dashboard live feed); cancel is non-blocking.
   */
  import { appStore } from '$lib/store.svelte';
  import { errors } from '$lib/errors';
  import { composeProgressLabel, formatGb } from '$lib/format-transfer';

  const job = $derived(appStore.downloadJob);
  const running = $derived(job?.state === 'running');
  const terminal = $derived(
    !!job && job.state !== 'running' && job.state !== 'idle'
  );

  const phaseLabel = $derived.by(() => {
    if (!job) return '';
    if (job.state === 'running') {
      const ph = job.phase ?? 'downloading';
      return ph === 'downloading' ? 'Downloading…' : `${ph}…`;
    }
    if (job.state === 'complete') return 'Download complete';
    if (job.state === 'error') return 'Download failed';
    if (job.state === 'cancelled') return 'Download cancelled';
    if (job.state === 'unknown') return 'Download outcome unknown';
    return job.state;
  });

  const chipClass = $derived.by(() => {
    if (!job) return '';
    if (job.state === 'running' || job.state === 'complete') return 'chip-ok';
    if (job.state === 'error') return 'chip-danger';
    if (job.state === 'unknown') return 'chip-warn';
    return 'chip-muted';
  });

  const pct = $derived(job ? Math.min(100, Math.max(0, job.pct ?? 0)) : 0);
  const detail = $derived(
    job ? composeProgressLabel(job.bytes, job.total ?? null, job.rate_bps, job.eta_seconds) : ''
  );
</script>

{#if (running || terminal) && (job?.state !== 'error' || !$errors.started || $errors.entries.download?.visible)}
  <div class="flex flex-col gap-2">
    <div class="flex items-center gap-2">
      {#if running}<span class="spinner" aria-hidden="true"></span>{/if}
      <span class="chip {chipClass}">{phaseLabel}</span>
      <span class="ml-auto mono text-[11px]" style="color: var(--muted)">{job?.filename ?? ''}</span>
    </div>

    {#if running}
      <div
        class="progress"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
        aria-label="Model download"
      >
        <span style:width={`${pct}%`}></span>
      </div>
      <div class="mono text-[11px]" style="color: var(--muted)">
        {detail} · {Math.round(pct)}%
      </div>
      <button class="btn btn-danger self-start" type="button" onclick={() => appStore.cancelDownload()}
        >Cancel download</button
      >
    {:else if job?.state === 'complete'}
      <p class="mono text-[11px]" style="color: var(--accent)">
        {formatGb(job.bytes)} on disk — open Plan &amp; Run to load into {appStore.device === 'gpu'
          ? 'VRAM'
          : 'RAM'}.
      </p>
    {:else if job?.state === 'error'}
      <ErrorMessage source="download" message={job.error ?? 'unknown error'} identity={job.operation_id} />
    {:else if job?.state === 'cancelled'}
      <p class="mono text-[11px]" style="color: var(--muted)">Cancelled — nothing was saved.</p>
    {:else}
      <p class="mono text-[11px]" style="color: var(--warn)">Reconnect or inspect the model list to determine the final outcome.</p>
    {/if}
  </div>
{/if}
