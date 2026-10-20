<script lang="ts">
  import type { PrimaDeployment, StageTask } from '../prima-deployment.svelte';
  import type { RunInteractor } from '../interactor.svelte';
  import StageItem from './StageItem.svelte';
  import AssetSyncNodeProgress from './AssetSyncNodeProgress.svelte';
  import NodeProfileResults from './NodeProfileResults.svelte';
  import LinkMatrixView from './LinkMatrixView.svelte';
  import StageDiagnosticsPanel from './StageDiagnosticsPanel.svelte';

  let {
    deployment,
    presenter,
    onReview,
  }: {
    deployment: PrimaDeployment;
    presenter: RunInteractor;
    onReview: () => void;
  } = $props();

  const DEFAULT_STAGES: StageTask[] = [
    { stage_name: 'materialize', stage_order: 1, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
    { stage_name: 'asset_sync', stage_order: 2, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
    { stage_name: 'node_profile', stage_order: 3, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
    { stage_name: 'link_profile', stage_order: 4, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
    { stage_name: 'plan_qualification', stage_order: 5, status: 'pending', attempt: 1, progress_fraction: 0, progress_label: 'Pending', bytes_done: 0, bytes_total: 0, error_message: null },
  ];

  const activeRun = $derived(deployment.activeRun);
  const stages = $derived(activeRun?.stages && activeRun.stages.length > 0 ? activeRun.stages : DEFAULT_STAGES);

  const completedStages = $derived(stages.filter(s => s.status === 'completed' || s.status === 'skipped').length);
  const totalStages = $derived(stages.length);
  const overallPct = $derived(Math.round((completedStages / totalStages) * 100));

  const runStatus = $derived(activeRun?.status || (activeRun?.phase === 'prepared' ? 'completed' : 'idle'));
  const isRunning = $derived(runStatus === 'running' || runStatus === 'queued' || deployment.preparing);
  const isCompleted = $derived(runStatus === 'completed' || activeRun?.phase === 'prepared');
  const isFailed = $derived(runStatus === 'failed' || runStatus === 'interrupted');

  const failedStage = $derived(deployment.failedStage);

  const nodeProfiles = $derived(
    deployment.coverage?.node_profiles ||
    deployment.latestPlan?.profiling?.node_profiles ||
    {}
  );

  const linkMatrix = $derived(
    deployment.coverage?.link_matrix ||
    deployment.latestPlan?.profiling?.link_matrix ||
    []
  );

  const nodes = $derived(activeRun?.nodes || presenter.status?.members || {});

  function handleRetry(stageName: string) {
    void deployment.retryStage(stageName);
  }

  function handleRefreshLogs() {
    if (activeRun?.run_id) {
      void deployment.fetchLogs(activeRun.run_id);
    }
  }

  $effect(() => {
    if (isFailed && activeRun?.run_id && !deployment.diagnosticsLogs) {
      void deployment.fetchLogs(activeRun.run_id);
    }
  });
</script>

<div class="stepper-container" role="region" aria-label="Prepare & Profile Pipeline">
  <div class="stepper-header">
    <div class="header-left">
      <h3>Prepare &amp; Profile pipeline</h3>
      <span class="status-chip" class:running={isRunning} class:completed={isCompleted} class:failed={isFailed}>
        {runStatus}
      </span>
    </div>
    <div class="progress-indicator">
      <span>{completedStages} / {totalStages} stages completed ({overallPct}%)</span>
    </div>
  </div>

  <div class="stepper-progress-bar">
    <progress max="100" value={overallPct} aria-valuenow={overallPct} aria-label="Overall pipeline progress"></progress>
  </div>

  <div class="stages-list">
    {#each stages as stage, idx (stage.stage_name)}
      <StageItem
        {stage}
        index={idx}
        active={activeRun?.current_stage === stage.stage_name}
        retrying={deployment.retryingStage === stage.stage_name}
        onRetry={handleRetry}
      >
        {#snippet children()}
          {#if stage.stage_name === 'materialize'}
            <div class="stage-info">
              <p>Placement manifest compiled for target model, companion models, and LoRA adapters.</p>
              {#if activeRun?.manifest_digest}
                <span class="digest-line font-mono">Manifest digest: {activeRun.manifest_digest}</span>
              {/if}
            </div>
          {:else if stage.stage_name === 'asset_sync'}
            <AssetSyncNodeProgress
              {nodes}
              bytesDone={stage.bytes_done}
              bytesTotal={stage.bytes_total}
            />
          {:else if stage.stage_name === 'node_profile'}
            <NodeProfileResults
              {nodeProfiles}
              {nodes}
            />
          {:else if stage.stage_name === 'link_profile'}
            <LinkMatrixView
              {linkMatrix}
              {nodes}
            />
          {:else if stage.stage_name === 'plan_qualification'}
            <div class="qualification-summary">
              {#if activeRun?.qualification}
                <div class="metric-row">
                  <span>Objective step time:</span>
                  <strong>{activeRun.qualification.objective_step_ms ? `${activeRun.qualification.objective_step_ms.toFixed(1)} ms` : 'Optimal'}</strong>
                </div>
                <div class="metric-row">
                  <span>Predicted decode latency:</span>
                  <strong>{activeRun.qualification.predicted_decode_ms_tok ? `${activeRun.qualification.predicted_decode_ms_tok.toFixed(2)} ms/tok` : 'Optimal'}</strong>
                </div>
                <div class="metric-row">
                  <span>Fit status:</span>
                  <strong>{activeRun.qualification.fit_status || 'fits'}</strong>
                </div>
              {:else if deployment.latestPlan?.highs_scheduler}
                <div class="metric-row">
                  <span>HiGHS scheduler status:</span>
                  <strong>{deployment.latestPlan.highs_scheduler.status || 'Solved'}</strong>
                </div>
              {:else}
                <p>Plan qualification calculates zero-trust memory bounds and schedule feasibilities.</p>
              {/if}
            </div>
          {/if}
        {/snippet}
      </StageItem>
    {/each}
  </div>

  {#if isFailed || activeRun?.error || deployment.diagnosticsLogs?.length}
    <StageDiagnosticsPanel
      error={activeRun?.error || failedStage?.error_message}
      stageName={failedStage?.stage_name}
      logs={deployment.diagnosticsLogs || []}
      onRefreshLogs={handleRefreshLogs}
    />
  {/if}

  <div class="stepper-controls">
    {#if isRunning}
      <button class="btn btn-danger" onclick={() => deployment.cancelRun()}>
        Cancel active run
      </button>
    {:else}
      <button
        class="btn"
        disabled={!!deployment.prepareActionReason || isRunning}
        onclick={() => deployment.prepare(false)}
      >
        Prepare files only
      </button>
      <button
        class="btn btn-primary"
        disabled={!!deployment.prepareActionReason || isRunning}
        onclick={() => deployment.prepare(true)}
      >
        Prepare &amp; profile
      </button>
    {/if}

    {#if deployment.canRetryStage}
      <button
        class="btn btn-warning"
        disabled={!!deployment.retryingStage || isRunning}
        onclick={() => deployment.retryStage()}
      >
        Retry failed stage ({failedStage?.stage_name || 'stage'})
      </button>
    {/if}

    <button
      class="btn btn-accent"
      disabled={!!deployment.reviewActionReason || !!deployment.review}
      onclick={onReview}
    >
      Review deployment
    </button>
  </div>
</div>

<style>
  .stepper-container {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 16px;
    display: grid;
    gap: 14px;
  }
  .stepper-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }
  .header-left {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  h3 {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
  }
  .status-chip {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 12px;
    background: var(--raised);
    color: var(--muted);
    text-transform: capitalize;
  }
  .status-chip.running {
    background: var(--blue-wash);
    color: var(--info);
    border: 1px solid color-mix(in srgb, var(--info) 35%, var(--line));
  }
  .status-chip.completed {
    background: var(--green-wash);
    color: var(--accent);
    border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--line));
  }
  .status-chip.failed {
    background: var(--red-wash);
    color: var(--danger);
    border: 1px solid color-mix(in srgb, var(--danger) 35%, var(--line));
  }
  .progress-indicator {
    font-size: 12px;
    color: var(--muted);
  }
  .stepper-progress-bar progress {
    width: 100%;
    height: 6px;
    border-radius: 3px;
    accent-color: var(--accent);
  }
  .stages-list {
    display: grid;
    gap: 10px;
  }
  .stage-info {
    font-size: 12px;
    color: var(--muted);
    display: grid;
    gap: 4px;
  }
  .stage-info p {
    margin: 0;
  }
  .digest-line {
    font-size: 11px;
    opacity: 0.8;
  }
  .qualification-summary {
    display: grid;
    gap: 6px;
    font-size: 12px;
  }
  .metric-row {
    display: flex;
    justify-content: space-between;
    padding: 4px 0;
    border-bottom: 1px solid var(--line);
  }
  .metric-row:last-child {
    border-bottom: none;
  }
  .stepper-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    padding-top: 6px;
    border-top: 1px solid var(--line);
  }
  .btn-primary {
    background: var(--accent);
    color: var(--accent-ink);
    font-weight: 600;
  }
  .btn-warning {
    background: var(--warning);
    color: var(--accent-ink);
    font-weight: 600;
  }
  .btn-danger {
    background: var(--danger);
    color: var(--accent-ink);
    font-weight: 600;
  }
  button {
    min-height: 40px;
  }
</style>
