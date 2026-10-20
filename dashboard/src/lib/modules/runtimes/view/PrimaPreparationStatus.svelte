<script lang="ts">
  import type { PrimaPreparationObserver } from '../preparation.svelte';
  import { composeProgressLabel } from '#lib/shared/format-transfer';
  let { observer }: { observer: PrimaPreparationObserver } = $props();
  const job = $derived(observer.job);
  const progress = $derived(job?.progress);
  const pct = $derived(typeof progress?.fraction === 'number' ? Math.min(100,Math.max(0,Math.round(progress.fraction*100))) : undefined);
</script>
{#if job}
  <div class="prep-progress-panel" aria-live="polite">
    <strong>Preparation: {job.phase}</strong>
    {#if progress?.label}<p>{progress.label}</p>{/if}
    {#if progress?.steps_total}<p>Step {progress.step} of {progress.steps_total}</p>{/if}
    <progress max="100" value={pct} aria-valuenow={pct} aria-label="Prima preparation and profiling progress"></progress>
    <p>{pct === undefined ? 'Progress not reported' : `${pct}%`} {progress?.detail ?? ''}</p>
    {#if progress?.bytes_total > 0}<p>{composeProgressLabel(progress.bytes_done,progress.bytes_total,progress.rate_bps,progress.eta_seconds)}</p>{/if}
    {#if observer.cancellationRequested}<p>Cancellation requested. {observer.active ? 'Waiting for local settlement.' : 'Local work settled.'} Remote cleanup may await lease expiry.</p>{/if}
    {#if observer.settlementUnknown}<p>Server did not report local settlement. Cleanup is unknown; refresh status. A terminal phase does not prove remote cleanup.</p>{/if}
    {#if observer.active}
      <button class="btn" disabled={observer.cancellationRequested || !!observer.cancelReason} title={observer.cancelReason} onclick={() => observer.cancel()}>Cancel preparation</button>
      {#if observer.cancelReason}<p>{observer.cancelReason}</p>{/if}
    {/if}
    {#if observer.settlementUnknown || observer.error}<button class="btn" onclick={() => observer.refresh()}>Refresh preparation status</button>{/if}
    {#if job.error || observer.error}<p role="alert">{job.error || observer.error}</p>{/if}
    <details><summary>Participant progress</summary>
      {#each Object.entries(job.nodes ?? {}) as [id, state]}
        {@const node = state as Record<string, any>}
        <p>{id} · {node.progress?.label || node.phase} · {node.progress?.detail ?? ''}</p>
        <progress max="1" value={typeof node.progress?.fraction === 'number' ? node.progress.fraction : undefined} aria-label={'Preparation on '+id}></progress>
      {/each}
    </details>
  </div>
{/if}
<style>
  .prep-progress-panel { display:grid;gap:8px;padding:12px;border:1px solid var(--border);border-radius:8px;min-width:0; }
  progress { width:100%;accent-color:var(--accent); } p {font-size:13px;overflow-wrap:anywhere;} summary {cursor:pointer;min-height:44px;} button {min-height:44px;}
</style>
