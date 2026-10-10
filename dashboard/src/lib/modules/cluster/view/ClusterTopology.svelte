<script lang="ts">
  import ErrorMessage from '$lib/shared/ErrorMessage.svelte';
  import Icon from '$lib/shared/Icon.svelte';
  import { formatGib } from '$lib/shared/format-memory';
  import type { ManagedNetwork } from '$lib/modules/network/entities';
import type { UsageNode } from '$lib/modules/usage/entities';

  type FastSubnetPhase = 'hidden' | 'checking' | 'ready' | 'busy';
  type FastSubnetNode = {
    path: 'fast-subnet' | 'router';
    phase: FastSubnetPhase;
    address: string | null;
    error: string;
  };

  type LinkKind = 'connected' | 'disconnected' | 'unmeasured';

  let {
    nodes,
    relationships,
    selectedId,
    offline = false,
    networks = [],
    fastSubnet = { nodes: {}, onReconnect: () => {} },
    onselect
  }: {
    nodes: UsageNode[];
    relationships: { from_node_id: string; to_node_id: string; kind: 'master-worker' }[];
    selectedId: string;
    offline?: boolean;
    networks?: ManagedNetwork[];
    fastSubnet?: {
      nodes: Record<string, FastSubnetNode>;
      onReconnect: (nodeId: string) => void;
    };
    onselect: (id: string) => void;
  } = $props();

  const master = $derived(nodes.find((node) => node.role === 'master') ?? nodes[0] ?? null);
  const workers = $derived(master ? nodes.filter((node) => node.node_id !== master.node_id) : []);

  function measured(value: number | null | undefined, status?: string): number | null {
    if (value == null || !Number.isFinite(value) || status === 'error' || status === 'unsupported') return null;
    return value;
  }

  function memory(node: UsageNode): { used: string; total: string; fraction: number | null; label: string } {
    const snapshot = node.resources;
    if (snapshot) {
      const used = measured(snapshot.host.memory.used.value, snapshot.host.memory.used.status);
      const total = measured(snapshot.host.memory.total.value, snapshot.host.memory.total.status);
      return {
        used: formatGib(used),
        total: formatGib(total),
        fraction: used != null && total ? Math.max(0, Math.min(1, used / total)) : null,
        label: 'Memory'
      };
    }
    const reading = node.reading;
    if (reading) {
      const used = reading.host.used_bytes;
      const total = reading.host.total_bytes ?? node.total_ram_bytes ?? null;
      return {
        used: formatGib(used),
        total: formatGib(total),
        fraction: used != null && total ? Math.max(0, Math.min(1, used / total)) : null,
        label: 'Memory'
      };
    }
    return { used: '—', total: formatGib(node.total_ram_bytes ?? node.vram_bytes), fraction: null, label: 'Memory' };
  }

  function cpu(node: UsageNode): string {
    const value = node.resources
      ? measured(node.resources.host.cpu.value, node.resources.host.cpu.status)
      : node.reading?.host.cpu_percent ?? null;
    return value == null ? '—' : `${Math.round(value)}%`;
  }

  function gpu(node: UsageNode): string {
    const device = node.resources?.devices.find((item) => item.utilization);
    const value = device?.utilization
      ? measured(device.utilization.value, device.utilization.status)
      : node.reading?.devices.find((item) => item.utilization_percent != null)?.utilization_percent ?? null;
    return value == null ? '—' : `${Math.round(value)}%`;
  }

  function deviceName(node: UsageNode): string {
    return node.resources?.devices[0]?.name
      ?? node.devices?.[0]?.name
      ?? node.reading?.devices[0]?.device_id
      ?? (node.backend || 'Host');
  }

  function freshness(node: UsageNode): string {
    const state = node.resources ? node.resource_freshness ?? node.freshness : node.freshness;
    if (!node.connected || offline) return 'disconnected';
    return state || 'unavailable';
  }

  function runtimeLabel(node: UsageNode): string {
    if (node.rpc_status === 'idle') return node.role === 'worker' ? 'Joined · idle' : 'Idle';
    return ({ starting: 'Starting', serving: 'Serving', recovering: 'Recovering', failed: 'Failed', cleanup_pending: 'Cleanup pending' } as Record<string, string>)[node.rpc_status ?? ''] ?? '';
  }

  function linked(workerId: string): boolean {
    const masterId = master?.node_id;
    if (!masterId) return false;
    return relationships.some((edge) =>
      (edge.from_node_id === masterId && edge.to_node_id === workerId)
      || (edge.to_node_id === masterId && edge.from_node_id === workerId)
    );
  }

  function measuredRate(nodeId: string): number | null {
    for (const network of networks) {
      const participant = network.participants.find((item) => item.node_id === nodeId);
      if (participant?.link_speed_mbps != null) return participant.link_speed_mbps;
    }
    return null;
  }

  function pathLabel(nodeId: string): string {
    const path = fastSubnet.nodes[nodeId]?.path;
    if (path === 'fast-subnet') return 'Fast subnet';
    if (path === 'router') return 'Router LAN';
    return '';
  }

  function linkFor(worker: UsageNode): { kind: LinkKind; label: string; rate: string } {
    if (offline || !worker.connected || (master && !master.connected)) {
      return { kind: 'disconnected', label: 'Disconnected', rate: 'Unmeasured' };
    }
    const rate = measuredRate(worker.node_id);
    const rateText = rate == null ? 'Unmeasured' : `${rate} Mb/s`;
    if (!linked(worker.node_id)) return { kind: 'unmeasured', label: 'Unmeasured', rate: rateText };
    return { kind: 'connected', label: 'Connected', rate: rateText };
  }
</script>

{#snippet nodeCard(node: UsageNode, masterCard = false)}
  {@const ram = memory(node)}
  <button
    type="button"
    class="topology-node"
    class:topology-master={masterCard}
    class:is-selected={selectedId === node.node_id}
    data-node-id={node.node_id}
    aria-pressed={selectedId === node.node_id}
    onclick={() => onselect(node.node_id)}
  >
    <div class="od-row topology-node-heading">
      <span class="node-icon"><Icon name={masterCard ? 'server' : 'monitor'} /></span>
      <span class="od-field od-fill">
        <strong>{node.host || node.node_id}</strong>
        <span class="topology-node-role">{node.address ? `${node.address} · ` : ''}{node.role} · {node.node_id}{pathLabel(node.node_id) ? ` · ${pathLabel(node.node_id)}` : ''}{node.connected && node.rpc_status ? ` · ${runtimeLabel(node)}` : ''}</span>
      </span>
      <span class="topology-health" class:offline={!node.connected || offline}>
        <Icon name={node.connected && !offline ? 'circleCheck' : 'alert'} small />
      </span>
    </div>
    <div class="od-row topology-device">
      <Icon name="chip" small />
      <span class="od-fill device-label">{deviceName(node)}</span>
    </div>
    <div class="resource" data-memory-reading>
      <div class="resource-label">
        <span>{ram.label}</span>
        <strong>{ram.used} / {ram.total}</strong>
      </div>
      <div class="bar" style={`--value: ${(ram.fraction ?? 0) * 100}%`}>
        <span></span>
      </div>
    </div>
    <div class="topology-node-metrics">
      <div class="od-stat"><span>CPU</span><strong>{cpu(node)}</strong></div>
      <div class="od-stat"><span>GPU</span><strong>{gpu(node)}</strong></div>
    </div>
    <div class="topology-node-footer">
      <span>{freshness(node)}</span>
      <span>{node.assignment ? node.assignment : 'No known assignment'}</span>
    </div>
  </button>
{/snippet}

{#snippet fastSubnetAction(node: UsageNode)}
  {@const offer = fastSubnet.nodes[node.node_id]}
  {#if offer && offer.phase !== 'hidden'}
    <div class="fast-subnet-action" data-fast-subnet={offer.phase}>
      {#if offer.phase === 'checking'}
        <p class="probe-status" role="status"><span class="spinner" aria-hidden="true"></span>Checking fast subnet…</p>
      {:else}
        <button
          class="reconnect-button"
          type="button"
          disabled={offer.phase === 'busy'}
          onclick={() => fastSubnet.onReconnect(node.node_id)}
        >{offer.phase === 'busy' ? 'Reconnecting…' : 'Reconnect with Fast Subnet'}</button>
        <p class="action-hint">
          {#if offer.phase === 'busy'}
            Waiting for the worker to rejoin on the private path.
          {:else if offer.address}
            Control and inference will move to <code>{offer.address}</code>.
          {/if}
        </p>
        <ErrorMessage source={`fast-subnet:${node.node_id}`} message={offer.error} />
      {/if}
    </div>
  {/if}
{/snippet}

<div class="topology-panel panel" data-topology>
  <div class="topology-summary">
    <span><strong>{nodes.length}</strong> {nodes.length === 1 ? 'node' : 'nodes'}</span>
    <span>{workers.length} {workers.length === 1 ? 'worker' : 'workers'}</span>
    <span>Link rate, loss, and RTT stay unmeasured unless the host reports them.</span>
  </div>
  <div class="cluster-diagram">
    <div class="topology-map">
      {#if master}
        <div class="topology-master-slot">
          {@render nodeCard(master, true)}
        </div>
        <span class="topology-trunk" class:offline={offline || !master.connected}></span>
        {#if workers.length}
          <ul class="topology-branches">
            {#each workers as worker (worker.node_id)}
              {@const link = linkFor(worker)}
              <li class="topology-branch" class:offline={link.kind === 'disconnected'} class:unknown={link.kind === 'unmeasured'} data-link-state={link.kind}>
                <span class="topology-stem"></span>
                <button type="button" class="topology-link" class:is-selected={selectedId === worker.node_id} onclick={() => onselect(worker.node_id)}>
                  <span class="topology-link-heading">{master.host || master.node_id} → {worker.host || worker.node_id}</span>
                  <span class="topology-link-values">
                    <strong>{link.label}</strong>
                    <span class="mono">{link.rate}</span>
                  </span>
                  <span class="topology-status {link.kind === 'connected' ? '' : link.kind}">{link.kind === 'connected' ? 'Master-worker path' : link.label}</span>
                </button>
                <div class="topology-worker">
                  {@render nodeCard(worker)}
                  {@render fastSubnetAction(worker)}
                </div>
              </li>
            {/each}
          </ul>
        {:else}
          <p class="topology-map-note">No workers are joined. Paths are unmeasured until a worker connects.</p>
        {/if}
      {/if}
      <div class="topology-legend">
        <span><i class="topology-legend-line"></i> Connected</span>
        <span><i class="topology-legend-line offline"></i> Disconnected</span>
        <span><i class="topology-legend-line limited"></i> Unmeasured</span>
      </div>
    </div>
  </div>
  <div class="cluster-list nodes-grid">
    {#each nodes as node (node.node_id)}
      <div class="topology-worker">
        {@render nodeCard(node, node.role === 'master')}
        {#if node.role === 'worker'}{@render fastSubnetAction(node)}{/if}
      </div>
    {/each}
  </div>
</div>

<style>
  .cluster-list { display: none; margin-top: 16px; }
  .topology-worker {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
    min-width: 0;
    align-items: stretch;
  }
  .topology-worker:has(.fast-subnet-action) {
    grid-template-columns: minmax(238px, 0.85fr) minmax(220px, 1.15fr);
  }
  .fast-subnet-action {
    display: flex;
    min-width: 0;
    flex-direction: column;
    justify-content: center;
    gap: 8px;
    padding: 11px 12px;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: #0f1216;
  }
  .probe-status {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0;
    color: var(--muted);
    font-size: 12px;
  }
  .spinner {
    width: 12px;
    height: 12px;
    flex: none;
    border: 1px solid var(--control);
    border-top-color: var(--text);
    border-radius: 50%;
    animation: spin 0.9s linear infinite;
  }
  .reconnect-button {
    align-self: flex-start;
    min-height: 44px;
    padding: 0 15px;
    border: 1px solid var(--accent);
    border-radius: 4px;
    background: var(--accent);
    color: var(--accent-ink);
    font-weight: 650;
    cursor: pointer;
  }
  .reconnect-button:disabled {
    opacity: 1;
    border-color: var(--control);
    background: var(--raised);
    color: var(--muted);
    cursor: wait;
  }
  .action-hint { margin: 0; color: var(--muted); font-size: 11px; }
  .action-hint code { color: var(--text); font-family: var(--mono); }
  .fast-subnet-action :global(.error-message) {
    align-items: center;
    min-height: 52px;
    margin: 0;
    padding: 7px 7px 7px 10px;
    border: 1px solid #613336;
    border-radius: 4px;
    background: var(--red-wash);
    color: var(--danger);
  }
  .fast-subnet-action :global(.error-dismiss) {
    width: auto;
    min-width: 44px;
    height: auto;
    min-height: 44px;
    border-color: #794145;
    color: var(--danger);
  }
  @media (max-width: 767px) {
    .cluster-diagram { display: none; }
    .cluster-list { display: grid; }
    .topology-worker,
    .topology-worker:has(.fast-subnet-action) { grid-template-columns: minmax(0, 1fr); }
    .reconnect-button { width: 100%; }
  }
  @media (hover: hover) {
    .reconnect-button:hover:not(:disabled) {
      background: #66e1a5;
      border-color: #66e1a5;
      color: var(--accent-ink);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .spinner { animation: none; }
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
