<script lang="ts">
  import type { Snippet } from 'svelte';
  import type { StageTask } from '../prima-deployment.svelte';

  let {
    stage,
    index,
    active = false,
    retrying = false,
    onRetry,
    children,
  }: {
    stage: StageTask;
    index: number;
    active?: boolean;
    retrying?: boolean;
    onRetry?: (stageName: string) => void;
    children?: Snippet;
  } = $props();

  const STAGE_TITLES: Record<string, string> = {
    materialize: 'Materialize Placement',
    asset_sync: 'Model & Runtime Asset Sync',
    node_profile: 'Node Hardware Profiling',
    link_profile: 'Ring-TCP Link Profiling',
    plan_qualification: 'Plan Qualification',
  };

  const title = $derived(STAGE_TITLES[stage.stage_name] || stage.stage_name);
  const pct = $derived(
    typeof stage.progress_fraction === 'number'
      ? Math.min(100, Math.max(0, Math.round(stage.progress_fraction * 100)))
      : stage.status === 'completed'
      ? 100
      : undefined
  );

  let userToggled = $state<boolean | null>(null);
  const defaultOpen = $derived(stage.status === 'failed' || stage.status === 'interrupted' || stage.status === 'running' || active);
  const isOpen = $derived(userToggled !== null ? userToggled : defaultOpen);

  function toggleOpen() {
    userToggled = !isOpen;
  }

  function formatDuration(started?: number | null, finished?: number | null): string | null {
    if (!started) return null;
    const end = finished || Date.now() / 1000;
    const sec = Math.max(0, end - started);
    if (sec < 60) return `${sec.toFixed(1)}s`;
    return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
  }

  const duration = $derived(formatDuration(stage.started_at, stage.finished_at));
</script>

<div
  class="stage-item"
  class:failed={stage.status === 'failed' || stage.status === 'interrupted'}
  class:running={stage.status === 'running'}
  class:completed={stage.status === 'completed'}
>
  <div class="stage-header-row">
    <button
      type="button"
      class="stage-toggle-btn"
      aria-expanded={isOpen}
      onclick={toggleOpen}
    >
      <span class="step-num">{index + 1}</span>
      <span class="stage-headings">
        <strong class="stage-title">{title}</strong>
        {#if retrying}
          <span class="stage-subtitle">Retrying…</span>
        {:else if stage.progress_label}
          <span class="stage-subtitle">{stage.progress_label}</span>
        {/if}
      </span>
      <span class="spacer"></span>
      {#if duration}<span class="duration-text">{duration}</span>{/if}
      {#if stage.attempt > 1}<span class="attempt-chip">Attempt {stage.attempt}</span>{/if}

      {#if stage.status === 'completed'}
        <span class="badge badge-success">✓ Completed</span>
      {:else if stage.status === 'running'}
        <span class="badge badge-info">Running {pct !== undefined ? `${pct}%` : '…'}</span>
      {:else if stage.status === 'failed' || stage.status === 'interrupted'}
        <span class="badge badge-danger">✕ Failed</span>
      {:else if stage.status === 'skipped'}
        <span class="badge badge-subtle">Skipped</span>
      {:else}
        <span class="badge badge-pending">Pending</span>
      {/if}

      <span class="chevron" class:open={isOpen}>▼</span>
    </button>

    {#if (stage.status === 'failed' || stage.status === 'interrupted') && onRetry}
      <div class="stage-action-wrap">
        <button
          type="button"
          class="btn btn-retry"
          disabled={retrying}
          onclick={() => onRetry(stage.stage_name)}
        >
          {retrying ? 'Retrying…' : 'Retry stage'}
        </button>
      </div>
    {/if}
  </div>

  {#if stage.status === 'running' && pct !== undefined}
    <div class="stage-progress">
      <progress max="100" value={pct} aria-valuenow={pct} aria-label={title + ' progress'}></progress>
    </div>
  {/if}

  {#if stage.error_message}
    <div class="stage-error" role="alert">
      <span>{stage.error_message}</span>
    </div>
  {/if}

  {#if isOpen && children}
    <div class="stage-body">
      {@render children()}
    </div>
  {/if}
</div>

<style>
  .stage-item {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 8px;
    overflow: hidden;
    transition: border-color 0.15s ease;
  }
  .stage-item.running {
    border-color: var(--info);
  }
  .stage-item.completed {
    border-color: color-mix(in srgb, var(--accent) 30%, var(--line));
  }
  .stage-item.failed {
    border-color: var(--danger);
  }
  .stage-header-row {
    display: flex;
    align-items: center;
    background: transparent;
  }
  .stage-toggle-btn {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 14px;
    background: transparent;
    border: none;
    color: inherit;
    text-align: left;
    flex: 1;
    cursor: pointer;
    min-height: 48px;
    font-family: inherit;
  }
  .stage-toggle-btn:hover {
    background: var(--raised);
  }
  .step-num {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    background: var(--raised);
    border: 1px solid var(--line);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    font-weight: 600;
    color: var(--muted);
    flex-shrink: 0;
  }
  .stage-item.completed .step-num {
    background: var(--green-wash);
    color: var(--accent);
    border-color: var(--accent);
  }
  .stage-item.running .step-num {
    background: var(--blue-wash);
    color: var(--info);
    border-color: var(--info);
  }
  .stage-item.failed .step-num {
    background: var(--red-wash);
    color: var(--danger);
    border-color: var(--danger);
  }
  .stage-headings {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .stage-title {
    font-size: 13px;
    font-weight: 600;
  }
  .stage-subtitle {
    font-size: 11px;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .spacer {
    flex: 1;
  }
  .duration-text {
    font-size: 11px;
    color: var(--muted);
    flex-shrink: 0;
  }
  .attempt-chip {
    font-size: 10px;
    padding: 1px 5px;
    border-radius: 4px;
    background: var(--raised);
    color: var(--muted);
    border: 1px solid var(--line);
    flex-shrink: 0;
  }
  .badge {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 12px;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .badge-success {
    background: var(--green-wash);
    color: var(--accent);
    border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--line));
  }
  .badge-info {
    background: var(--blue-wash);
    color: var(--info);
    border: 1px solid color-mix(in srgb, var(--info) 35%, var(--line));
  }
  .badge-danger {
    background: var(--red-wash);
    color: var(--danger);
    border: 1px solid color-mix(in srgb, var(--danger) 35%, var(--line));
  }
  .badge-subtle {
    background: var(--raised);
    color: var(--muted);
  }
  .badge-pending {
    background: var(--raised);
    color: var(--muted);
  }
  .chevron {
    font-size: 9px;
    color: var(--muted);
    transition: transform 0.2s ease;
    display: inline-block;
    flex-shrink: 0;
  }
  .chevron.open {
    transform: rotate(180deg);
  }
  .stage-action-wrap {
    padding-right: 14px;
    flex-shrink: 0;
  }
  .btn-retry {
    padding: 3px 10px;
    font-size: 12px;
    min-height: 32px;
    background: var(--warning);
    color: var(--accent-ink);
    border: none;
    font-weight: 600;
    border-radius: 4px;
    cursor: pointer;
  }
  .btn-retry:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
  .stage-progress {
    padding: 0 14px 6px;
  }
  progress {
    width: 100%;
    height: 4px;
    border-radius: 2px;
    accent-color: var(--accent);
  }
  .stage-error {
    margin: 0 14px 10px;
    padding: 8px 10px;
    background: var(--red-wash);
    border-left: 3px solid var(--danger);
    font-size: 12px;
    color: var(--text);
  }
  .stage-body {
    padding: 10px 14px 14px;
    border-top: 1px solid var(--line);
    background: var(--raised);
  }
</style>
