<script lang="ts">
  import type { PrimaPreparationObserver } from '../preparation.svelte';
  import { composeProgressLabel } from '#lib/shared/format-transfer';

  let { observer }: { observer: PrimaPreparationObserver } = $props();
  const job = $derived(observer.job);
  const progress = $derived(job?.progress);
  const pct = $derived(
    typeof progress?.fraction === 'number'
      ? Math.min(100, Math.max(0, Math.round(progress.fraction * 100)))
      : undefined
  );
  const stages = $derived(Array.isArray(job?.stages) ? job.stages : null);
</script>

{#if job}
  <div class="prep-progress-panel" aria-live="polite">
    <div class="header-row">
      <strong>Preparation: {job.phase}</strong>
      {#if job.status && job.status !== job.phase}
        <span class="status-chip">{job.status}</span>
      {/if}
    </div>

    {#if progress?.label}<p>{progress.label}</p>{/if}
    {#if progress?.steps_total}<p>Step {progress.step} of {progress.steps_total}</p>{/if}

    <progress max="100" value={pct} aria-valuenow={pct} aria-label="Prima preparation and profiling progress"></progress>
    <p>{pct === undefined ? 'Progress not reported' : `${pct}%`} {progress?.detail ?? ''}</p>

    {#if progress?.bytes_total > 0}
      <p>{composeProgressLabel(progress.bytes_done, progress.bytes_total, progress.rate_bps, progress.eta_seconds)}</p>
    {/if}

    {#if stages && stages.length > 0}
      <div class="stages-summary">
        <span class="stages-label">Pipeline stages:</span>
        <div class="stages-chips">
          {#each stages as st}
            <span class="stage-chip" class:completed={st.status === 'completed'} class:running={st.status === 'running'} class:failed={st.status === 'failed'}>
              {st.stage_name}
            </span>
          {/each}
        </div>
      </div>
    {/if}

    {#if observer.cancellationRequested}
      <p>Cancellation requested. {observer.active ? 'Waiting for local settlement.' : 'Local work settled.'} Remote cleanup may await lease expiry.</p>
    {/if}

    {#if observer.settlementUnknown}
      <p>Server did not report local settlement. Cleanup is unknown; refresh status. A terminal phase does not prove remote cleanup.</p>
    {/if}

    {#if observer.active}
      <button class="btn" disabled={observer.cancellationRequested || !!observer.cancelReason} title={observer.cancelReason} onclick={() => observer.cancel()}>
        Cancel preparation
      </button>
      {#if observer.cancelReason}<p>{observer.cancelReason}</p>{/if}
    {/if}

    {#if observer.settlementUnknown || observer.error}
      <button class="btn" onclick={() => observer.refresh()}>Refresh preparation status</button>
    {/if}

    {#if job.error || observer.error}
      <p role="alert" class="error-msg">{job.error || observer.error}</p>
    {/if}

    <details>
      <summary>Participant progress</summary>
      {#each Object.entries(job.nodes ?? {}) as [id, state]}
        {@const node = state as Record<string, any>}
        <p>{id} · {node.progress?.label || node.phase} · {node.progress?.detail ?? ''}</p>
        <progress max="1" value={typeof node.progress?.fraction === 'number' ? node.progress.fraction : undefined} aria-label={'Preparation on ' + id}></progress>
      {/each}
    </details>
  </div>
{/if}

<style>
  .prep-progress-panel {
    display: grid;
    gap: 8px;
    padding: 12px;
    border: 1px solid var(--border);
    border-radius: 8px;
    min-width: 0;
  }
  .header-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .status-chip {
    font-size: 11px;
    padding: 1px 6px;
    border-radius: 10px;
    background: var(--raised);
    color: var(--muted);
  }
  progress {
    width: 100%;
    accent-color: var(--accent);
  }
  p {
    font-size: 13px;
    overflow-wrap: anywhere;
    margin: 0;
  }
  .stages-summary {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    font-size: 12px;
    padding: 4px 0;
  }
  .stages-label {
    color: var(--muted);
  }
  .stages-chips {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
  }
  .stage-chip {
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 4px;
    background: var(--raised);
    color: var(--muted);
    border: 1px solid var(--line);
  }
  .stage-chip.completed {
    background: var(--green-wash);
    color: var(--accent);
    border-color: var(--accent);
  }
  .stage-chip.running {
    background: var(--blue-wash);
    color: var(--info);
    border-color: var(--info);
  }
  .stage-chip.failed {
    background: var(--red-wash);
    color: var(--danger);
    border-color: var(--danger);
  }
  .error-msg {
    color: var(--danger);
  }
  summary {
    cursor: pointer;
    min-height: 44px;
  }
  button {
    min-height: 44px;
  }
</style>
