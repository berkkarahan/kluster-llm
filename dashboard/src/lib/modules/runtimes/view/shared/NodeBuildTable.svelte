<script lang="ts">
  /**
   * Per-node build/job table shared by the Llama and Prima runtime settings
   * views. The Llama variant adds Backend / Running SHA / Target SHA columns;
   * the Prima variant shows the pinned build's staged artifact and disk. The
   * worker-read-only action rules are identical for both engines.
   */
  import ErrorMessage from '#lib/shared/ErrorMessage.svelte';
  import { RuntimeActions, isPreparingJob, shortSha, formatDisk } from './runtimeActions.svelte';
  import type { RuntimeNode } from '#lib/modules/runtimes/entities';
  import type { RuntimesInteractor } from '../../interactor.svelte';

  let {
    nodes,
    presenter,
    actions,
    openLogs,
    onretry,
    variant = 'llama',
    requiredNodes = [],
    targetCommit = null,
    tableLabel
  }: {
    nodes: RuntimeNode[];
    presenter: RuntimesInteractor;
    actions: RuntimeActions;
    openLogs: () => void;
    onretry: (node: RuntimeNode) => unknown;
    variant?: 'llama' | 'prima';
    requiredNodes?: string[];
    targetCommit?: string | null;
    tableLabel?: string;
  } = $props();

  const master = $derived(presenter.status?.role === 'master');
  const worker = $derived(presenter.status?.role === 'worker');
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users must be able to scroll the wide table.) -->
<div class="table-scroll" tabindex="0" role="region" aria-label={tableLabel ?? 'Node builds, scroll for all columns'}><table class={variant === 'llama' ? 'wide' : ''}><thead><tr>
  <th>Node</th>
  {#if variant === 'llama'}<th>Backend</th><th>Running SHA</th><th>Target SHA</th><th>Preparation</th>{:else}<th>Phase</th><th>Staged artifact</th><th>Disk</th>{/if}
  <th>Actions</th>
</tr></thead><tbody>
  {#each nodes as node (node.node_id)}
    <tr>
      <td><strong>{node.name ?? node.node_id}</strong><small>{node.address ? `${node.address} · ` : ''}{node.role.toUpperCase()}{node.node_id === presenter.status?.node_id ? ' · THIS NODE' : ''}{requiredNodes.includes(node.node_id) ? ' · REQUIRED' : ''}</small></td>
      {#if variant === 'llama'}
        <td class="mono">{node.prepared?.backend ?? node.job?.backend ?? '—'}</td>
        <td class="mono">{shortSha(node.running?.commit)}</td>
        <td class="mono">{shortSha(targetCommit)}</td>
      {:else}
        <td class="mono small">{shortSha(node.prepared?.commit ?? null)}</td>
        <td class="mono small muted">{formatDisk(node.prepared?.size_bytes)}</td>
      {/if}
      <td><span class="state-label" class:green={node.phase === 'prepared'} class:warning={['failed', 'offline', 'interrupted', 'cancelled'].includes(node.phase)}>{node.phase.replaceAll('_', ' ')}</span>{#if node.job && isPreparingJob(node.job)}<progress aria-label={`${node.name ?? node.node_id} ${node.job.phase}`}></progress>{/if}{#if node.error}<div class="node-error"><ErrorMessage source={`runtime-node:${node.node_id}`} message={node.error} /></div>{/if}</td>
      <td><div class="table-actions">
        {#if ['failed', 'interrupted', 'cancelled', 'waiting_for_target'].includes(node.phase)}
          <button class="btn" disabled={actions.busy || !(master || node.node_id === presenter.status?.node_id)} onclick={() => onretry(node)}>{node.phase === 'waiting_for_target' ? 'Sync' : 'Retry'}</button>
        {/if}
        <button class="btn" disabled={actions.busy || !node.job?.job_id || node.phase === 'offline' || (worker && node.node_id !== presenter.status?.node_id)} onclick={() => actions.showLogs(node.job, node, openLogs)}>Logs</button>
        {#if node.job && isPreparingJob(node.job)}
          <button class="btn" disabled={actions.busy || (worker && node.node_id !== presenter.status?.node_id)} onclick={() => actions.cancel(node.job!, node)}>Cancel</button>
        {/if}
      </div></td>
    </tr>
  {/each}
</tbody></table></div>
