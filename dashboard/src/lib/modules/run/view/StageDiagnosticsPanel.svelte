<script lang="ts">
  let {
    error = null,
    logs = [],
    stageName = null,
    onRefreshLogs,
  }: {
    error?: string | null;
    logs?: any[];
    stageName?: string | null;
    onRefreshLogs?: () => void;
  } = $props();

  let selectedStage = $state<string>('all');
  let selectedLevel = $state<string>('ALL');

  function getRemediationHint(errMsg?: string | null): string | null {
    if (!errMsg) return null;
    const lower = errMsg.toLowerCase();
    if (lower.includes('required nodes are unavailable')) {
      return 'Cluster nodes rejected as unavailable. Verify that worker nodes are connected, healthy, and reachable across LAN / WireGuard in the Cluster view.';
    }
    if (lower.includes('timed out') || lower.includes('timeout')) {
      return 'Profiling or transfer timed out. Ensure firewall permits Ring-TCP traffic on port 50052 between nodes and network bandwidth is stable.';
    }
    if (lower.includes('memory') || lower.includes('exceed') || lower.includes('budget') || lower.includes('overflow')) {
      return 'Placement exceeds available node memory. Reduce requested context length or change layer assignments.';
    }
    if (lower.includes('qualification') || lower.includes('highs')) {
      return 'Plan qualification gates failed. The solver could not guarantee a stable schedule within device memory constraints.';
    }
    if (lower.includes('digest') || lower.includes('manifest')) {
      return 'Plan digest or manifest mismatch. Re-calculate deployment plan for current draft settings.';
    }
    return null;
  }

  const filteredLogs = $derived(
    logs.filter(log => {
      if (selectedStage !== 'all' && log.stage_name && log.stage_name !== selectedStage) return false;
      if (selectedLevel !== 'ALL' && log.level && log.level !== selectedLevel) return false;
      return true;
    })
  );

  const hint = $derived(getRemediationHint(error));
</script>

<div class="diagnostics-panel" role="region" aria-label="Diagnostics and Logs">
  {#if error}
    <div class="error-alert" role="alert">
      <div class="alert-header">
        <span class="alert-icon">⚠️</span>
        <strong>{stageName ? `Stage '${stageName}' failed` : 'Preparation failure'}</strong>
      </div>
      <p class="error-text font-mono">{error}</p>
      {#if hint}
        <div class="hint-box">
          <span class="hint-label">Remediation:</span>
          <span class="hint-text">{hint}</span>
        </div>
      {/if}
    </div>
  {/if}

  <details class="logs-details">
    <summary>
      <span>Structured diagnostics &amp; execution logs ({logs.length} entries)</span>
    </summary>

    <div class="logs-controls">
      <div class="filter-group">
        <label for="diag-stage-filter">Stage:</label>
        <select id="diag-stage-filter" bind:value={selectedStage} class="field-select">
          <option value="all">All stages</option>
          <option value="materialize">materialize</option>
          <option value="asset_sync">asset_sync</option>
          <option value="node_profile">node_profile</option>
          <option value="link_profile">link_profile</option>
          <option value="plan_qualification">plan_qualification</option>
        </select>
      </div>

      <div class="filter-group">
        <label for="diag-level-filter">Level:</label>
        <select id="diag-level-filter" bind:value={selectedLevel} class="field-select">
          <option value="ALL">ALL</option>
          <option value="INFO">INFO</option>
          <option value="WARN">WARN</option>
          <option value="ERROR">ERROR</option>
        </select>
      </div>

      {#if onRefreshLogs}
        <button class="btn btn-sm" onclick={onRefreshLogs}>Refresh logs</button>
      {/if}
    </div>

    <div class="logs-container">
      {#if filteredLogs.length > 0}
        <pre class="log-pre font-mono">
{#each filteredLogs as log}{`[${new Date(log.timestamp * 1000).toISOString().slice(11, 19)}] [${log.level || 'INFO'}] (${log.stage_name || 'run'}/${log.node_id || 'master'}): ${log.message}\n`}{/each}
        </pre>
      {:else}
        <p class="no-logs">No log entries matching filter criteria.</p>
      {/if}
    </div>
  </details>
</div>

<style>
  .diagnostics-panel {
    display: grid;
    gap: 10px;
    margin-top: 4px;
  }
  .error-alert {
    padding: 12px;
    background: var(--red-wash);
    border: 1px solid var(--danger);
    border-radius: 6px;
    display: grid;
    gap: 8px;
  }
  .alert-header {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--danger);
  }
  .error-text {
    margin: 0;
    font-size: 12px;
    color: var(--text);
    white-space: pre-wrap;
    word-break: break-word;
  }
  .hint-box {
    padding: 8px 10px;
    background: var(--surface);
    border-radius: 4px;
    font-size: 12px;
    display: flex;
    gap: 6px;
  }
  .hint-label {
    font-weight: 600;
    color: var(--warning);
  }
  .hint-text {
    color: var(--muted);
  }
  .logs-details {
    border: 1px solid var(--line);
    border-radius: 6px;
    background: var(--surface);
    padding: 8px 12px;
  }
  summary {
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    color: var(--muted);
  }
  summary:hover {
    color: var(--text);
  }
  .logs-controls {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 0;
    flex-wrap: wrap;
    border-bottom: 1px solid var(--line);
  }
  .filter-group {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
  }
  .field-select {
    background: var(--raised);
    color: var(--text);
    border: 1px solid var(--line);
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 12px;
  }
  .btn-sm {
    padding: 2px 8px;
    font-size: 11px;
    min-height: 28px;
  }
  .logs-container {
    max-height: 240px;
    overflow-y: auto;
    margin-top: 8px;
    background: var(--bg);
    border-radius: 4px;
    padding: 8px;
  }
  .log-pre {
    margin: 0;
    font-size: 11px;
    line-height: 1.4;
    white-space: pre-wrap;
    word-break: break-all;
    color: var(--muted);
  }
  .no-logs {
    font-size: 12px;
    color: var(--muted);
    margin: 0;
    padding: 8px;
  }
</style>
