<script lang="ts">
  import { composeProgressLabel } from '#lib/shared/format-transfer';

  let {
    nodes = {},
    bytesDone = 0,
    bytesTotal = 0,
  }: {
    nodes?: Record<string, any>;
    bytesDone?: number;
    bytesTotal?: number;
  } = $props();

  function formatBytes(bytes: number): string {
    if (!bytes || bytes <= 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(i > 1 ? 1 : 0)} ${sizes[i]}`;
  }

  function nodePct(node: any): number | undefined {
    const fraction = node.progress?.fraction;
    if (typeof fraction === 'number') {
      return Math.min(100, Math.max(0, Math.round(fraction * 100)));
    }
    if (node.status === 'prepared' || node.phase === 'prepared') return 100;
    return undefined;
  }

  function isCached(node: any): boolean {
    return (
      (node.status === 'prepared' || node.phase === 'prepared') &&
      (!node.progress?.bytes_done || node.progress?.bytes_done === 0 || node.progress?.rate_bps === 0)
    );
  }
</script>

<div class="asset-sync-view">
  {#if bytesTotal > 0}
    <div class="summary-line">
      <span class="metric-label">Total Asset Transfer:</span>
      <span class="metric-val">{formatBytes(bytesDone)} / {formatBytes(bytesTotal)}</span>
    </div>
  {/if}

  <div class="node-list">
    {#each Object.entries(nodes) as [nodeId, node]}
      {@const pct = nodePct(node)}
      {@const cached = isCached(node)}
      <div class="node-card" class:failed={node.status === 'failed' || node.phase === 'failed'}>
        <div class="node-header">
          <div class="node-title">
            <span class="node-id font-mono">{nodeId}</span>
            {#if node.role}<span class="role-chip">{node.role}</span>{/if}
            {#if node.backend}<span class="backend-chip">{node.backend}</span>{/if}
          </div>
          <div class="node-status">
            {#if cached}
              <span class="status-badge cached">Cached</span>
            {:else if node.status === 'running' || node.phase === 'preparing'}
              <span class="status-badge transferring">Syncing</span>
            {:else if node.status === 'prepared' || node.phase === 'prepared'}
              <span class="status-badge completed">Staged</span>
            {:else if node.status === 'failed' || node.phase === 'failed'}
              <span class="status-badge failed">Failed</span>
            {:else}
              <span class="status-badge pending">{node.status || 'Pending'}</span>
            {/if}
          </div>
        </div>

        <div class="progress-wrap">
          <progress
            max="100"
            value={pct}
            aria-valuenow={pct}
            aria-label={'Asset transfer on ' + nodeId}
          ></progress>
          <div class="progress-labels">
            <span>{pct !== undefined ? `${pct}%` : 'Progress pending'}</span>
            {#if node.progress?.bytes_total > 0}
              <span>
                {composeProgressLabel(
                  node.progress.bytes_done,
                  node.progress.bytes_total,
                  node.progress.rate_bps,
                  node.progress.eta_seconds
                )}
              </span>
            {:else if cached}
              <span>Reused from local model cache</span>
            {:else if node.progress?.label}
              <span>{node.progress.label}</span>
            {/if}
          </div>
        </div>

        {#if node.error || node.progress?.detail}
          <p class="node-detail" class:error-text={node.status === 'failed' || node.phase === 'failed'}>
            {node.error || node.progress?.detail}
          </p>
        {/if}
      </div>
    {/each}

    {#if Object.keys(nodes).length === 0}
      <p class="empty-notice">No cluster participants recorded for asset distribution.</p>
    {/if}
  </div>
</div>

<style>
  .asset-sync-view {
    display: grid;
    gap: 12px;
  }
  .summary-line {
    display: flex;
    justify-content: space-between;
    font-size: 13px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--line);
  }
  .metric-label {
    color: var(--muted);
  }
  .metric-val {
    font-weight: 600;
  }
  .node-list {
    display: grid;
    gap: 10px;
  }
  .node-card {
    padding: 10px 12px;
    background: var(--raised);
    border: 1px solid var(--line);
    border-radius: 6px;
    display: grid;
    gap: 8px;
  }
  .node-card.failed {
    border-color: var(--danger);
    background: var(--red-wash);
  }
  .node-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }
  .node-title {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .node-id {
    font-size: 13px;
    font-weight: 600;
  }
  .role-chip, .backend-chip {
    font-size: 11px;
    padding: 1px 6px;
    border-radius: 4px;
    background: var(--surface);
    color: var(--muted);
    border: 1px solid var(--line);
  }
  .status-badge {
    font-size: 11px;
    font-weight: 500;
    padding: 2px 8px;
    border-radius: 10px;
  }
  .status-badge.cached {
    background: var(--green-wash);
    color: var(--accent);
    border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--line));
  }
  .status-badge.transferring {
    background: var(--blue-wash);
    color: var(--info);
    border: 1px solid color-mix(in srgb, var(--info) 35%, var(--line));
  }
  .status-badge.completed {
    background: var(--green-wash);
    color: var(--accent);
  }
  .status-badge.failed {
    background: var(--red-wash);
    color: var(--danger);
  }
  .status-badge.pending {
    background: var(--surface);
    color: var(--muted);
  }
  .progress-wrap {
    display: grid;
    gap: 4px;
  }
  progress {
    width: 100%;
    height: 8px;
    border-radius: 4px;
    accent-color: var(--accent);
  }
  .progress-labels {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--muted);
  }
  .node-detail {
    margin: 0;
    font-size: 12px;
    color: var(--muted);
  }
  .node-detail.error-text {
    color: var(--danger);
  }
  .empty-notice {
    font-size: 13px;
    color: var(--muted);
    margin: 0;
  }
</style>
