<script lang="ts">
  let {
    nodeProfiles = {},
    nodes = {},
  }: {
    nodeProfiles?: Record<string, any>;
    nodes?: Record<string, any>;
  } = $props();

  function formatBytes(bytes?: number): string {
    if (!bytes || bytes <= 0) return '—';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
  }

  function formatMs(ms?: number): string {
    if (ms === undefined || ms === null || isNaN(ms)) return '—';
    return `${ms.toFixed(2)} ms`;
  }

  function formatSpeed(mbs?: number): string {
    if (!mbs) return '—';
    return `${mbs.toFixed(0)} MB/s`;
  }
</script>

<div class="node-profile-view">
  {#if Object.keys(nodeProfiles).length > 0}
    <div class="profile-table-wrap">
      <table>
        <thead>
          <tr>
            <th>Node</th>
            <th>Backend / Device</th>
            <th>GPU Layer</th>
            <th>CPU Layer</th>
            <th>Prompt Latency</th>
            <th>Decode Latency</th>
            <th>Usable Memory</th>
            <th>Disk Read</th>
          </tr>
        </thead>
        <tbody>
          {#each Object.entries(nodeProfiles) as [nodeId, prof]}
            {@const node = nodes[nodeId]}
            <tr>
              <td class="font-mono node-cell">
                <strong>{nodeId}</strong>
                {#if node?.role}<span class="role-chip">{node.role}</span>{/if}
              </td>
              <td>
                <span class="device-cell">
                  {prof.device_name || prof.backend || node?.backend || 'CPU'}
                </span>
              </td>
              <td>{formatMs(prof.gpu_layer_ms)}</td>
              <td>{formatMs(prof.cpu_layer_ms)}</td>
              <td>{formatMs(prof.prompt_ms_per_token)}/tok</td>
              <td>{formatMs(prof.decode_ms_per_token)}/tok</td>
              <td>{formatBytes(prof.usable_memory_bytes)}</td>
              <td>{formatSpeed(prof.disk_read_mbs)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:else}
    <p class="unmeasured-notice">
      Node hardware benchmark pending or unmeasured. Profiles measure Vulkan/CPU kernel times and memory throughput across cluster nodes.
    </p>
  {/if}
</div>

<style>
  .node-profile-view {
    display: grid;
    gap: 8px;
    min-width: 0;
  }
  .profile-table-wrap {
    overflow-x: auto;
    border: 1px solid var(--line);
    border-radius: 6px;
    background: var(--surface);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    text-align: left;
    white-space: nowrap;
  }
  th {
    padding: 8px 12px;
    background: var(--raised);
    color: var(--muted);
    font-weight: 500;
    border-bottom: 1px solid var(--line);
  }
  td {
    padding: 8px 12px;
    border-bottom: 1px solid var(--line);
  }
  tr:last-child td {
    border-bottom: none;
  }
  .node-cell {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .role-chip {
    font-size: 10px;
    padding: 1px 4px;
    border-radius: 3px;
    background: var(--surface-2, var(--raised));
    color: var(--muted);
  }
  .device-cell {
    max-width: 160px;
    overflow: hidden;
    text-overflow: ellipsis;
    display: inline-block;
  }
  .unmeasured-notice {
    font-size: 13px;
    color: var(--muted);
    margin: 0;
  }
</style>
