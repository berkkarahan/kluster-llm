<script lang="ts">
  import Icon from '$lib/components/Icon.svelte';
  import { formatGib } from '$lib/format-memory';
  import type { ManagedNetwork, UsageNode } from '$lib/types';

  type LinkKind = 'connected' | 'disconnected' | 'unmeasured';

  let {
    nodes,
    relationships,
    selectedId,
    offline = false,
    networks = [],
    onselect
  }: {
    nodes: UsageNode[];
    relationships: { from_node_id: string; to_node_id: string; kind: 'master-worker' }[];
    selectedId: string;
    offline?: boolean;
    networks?: ManagedNetwork[];
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
        <span class="topology-node-role">{node.role} · {node.node_id}</span>
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
                {@render nodeCard(worker)}
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
      {@render nodeCard(node, node.role === 'master')}
    {/each}
  </div>
</div>

<style>
  .cluster-list { display: none; margin-top: 16px; }
  @media (max-width: 767px) {
    .cluster-diagram { display: none; }
    .cluster-list { display: grid; }
  }
</style>
