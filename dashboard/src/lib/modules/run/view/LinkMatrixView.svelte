<script lang="ts">
  import type { LinkMatrixEntry } from '../prima-deployment.svelte';

  let {
    linkMatrix = [],
    nodes = {},
  }: {
    linkMatrix?: LinkMatrixEntry[];
    nodes?: Record<string, any>;
  } = $props();

  const nodeIds = $derived(
    Object.keys(nodes).length > 0
      ? Object.keys(nodes).sort()
      : Array.from(new Set(linkMatrix.flatMap(l => [l.source, l.target]))).sort()
  );

  function findLink(source: string, target: string): LinkMatrixEntry | undefined {
    return linkMatrix.find(l => l.source === source && l.target === target);
  }

  function formatBw(bytesPerSec?: number): string {
    if (!bytesPerSec || bytesPerSec <= 0) return '—';
    const gbps = (bytesPerSec * 8) / 1e9;
    const gbSec = bytesPerSec / 1e9;
    if (gbSec >= 1.0) {
      return `${gbSec.toFixed(2)} GB/s (${gbps.toFixed(1)} Gbps)`;
    }
    const mbSec = bytesPerSec / 1e6;
    return `${mbSec.toFixed(1)} MB/s`;
  }

  function formatLatency(ms?: number): string {
    if (ms === undefined || ms === null || isNaN(ms)) return '—';
    return `${ms.toFixed(2)} ms`;
  }
</script>

<div class="link-matrix-view">
  {#if nodeIds.length === 1}
    <div class="single-node-banner">
      <span class="info-badge">Single-node deployment</span>
      <p>Ring-TCP network link profiling is skipped for single-node placements (intra-host memory bandwidth is used).</p>
    </div>
  {:else if nodeIds.length > 1 && linkMatrix.length > 0}
    <div class="matrix-table-wrap">
      <table>
        <thead>
          <tr>
            <th class="matrix-corner">Source \ Target</th>
            {#each nodeIds as targetId}
              <th class="font-mono">{targetId}</th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#each nodeIds as sourceId}
            <tr>
              <th class="font-mono source-header">{sourceId}</th>
              {#each nodeIds as targetId}
                {@const link = findLink(sourceId, targetId)}
                {#if sourceId === targetId}
                  <td class="self-cell">
                    <span class="cell-label">Local Bus</span>
                    <span class="cell-detail">0.0 ms</span>
                  </td>
                {:else if link}
                  <td class="link-cell" class:fast={link.latency_ms < 1.0} class:slow={link.latency_ms > 10.0}>
                    <div class="link-latency">{formatLatency(link.latency_ms)}</div>
                    <div class="link-bw">{formatBw(link.bandwidth_bytes_s)}</div>
                  </td>
                {:else}
                  <td class="missing-cell">
                    <span>Unmeasured</span>
                  </td>
                {/if}
              {/each}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <div class="matrix-legend">
      <span>Legend: Directed Ring-TCP latency &amp; bandwidth between host pair</span>
    </div>
  {:else}
    <p class="unmeasured-notice">
      Ring-TCP link benchmarks pending or unmeasured. Profiling measures pairwise latency and socket streaming speed over LAN / WireGuard or dedicated links.
    </p>
  {/if}
</div>

<style>
  .link-matrix-view {
    display: grid;
    gap: 8px;
    min-width: 0;
  }
  .single-node-banner {
    padding: 10px 12px;
    background: var(--raised);
    border: 1px solid var(--line);
    border-radius: 6px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .single-node-banner p {
    margin: 0;
    font-size: 12px;
    color: var(--muted);
  }
  .info-badge {
    font-size: 11px;
    font-weight: 600;
    color: var(--info);
  }
  .matrix-table-wrap {
    overflow-x: auto;
    border: 1px solid var(--line);
    border-radius: 6px;
    background: var(--surface);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    text-align: center;
  }
  th {
    padding: 8px 12px;
    background: var(--raised);
    color: var(--muted);
    font-weight: 500;
    border: 1px solid var(--line);
    white-space: nowrap;
  }
  .source-header {
    text-align: left;
  }
  td {
    padding: 8px 10px;
    border: 1px solid var(--line);
    min-width: 110px;
  }
  .self-cell {
    background: var(--surface-2, var(--raised));
    color: var(--muted);
  }
  .cell-label {
    display: block;
    font-size: 11px;
  }
  .cell-detail {
    font-size: 10px;
    opacity: 0.7;
  }
  .link-cell {
    background: var(--surface);
  }
  .link-cell.fast {
    background: color-mix(in srgb, var(--accent) 8%, var(--surface));
  }
  .link-cell.slow {
    background: color-mix(in srgb, var(--warning) 12%, var(--surface));
  }
  .link-latency {
    font-weight: 600;
    color: var(--text);
  }
  .link-bw {
    font-size: 11px;
    color: var(--muted);
  }
  .missing-cell {
    color: var(--muted);
    font-style: italic;
  }
  .matrix-legend {
    font-size: 11px;
    color: var(--muted);
  }
  .unmeasured-notice {
    font-size: 13px;
    color: var(--muted);
    margin: 0;
  }
</style>
