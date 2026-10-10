<script lang="ts">
  import { onMount, tick } from 'svelte';
  import type { UsageLive, UsageNode } from '$lib/modules/usage/entities';
  import { formatGib } from '$lib/shared/format-memory';

  let { nodes, relationships, selectedId, onselect }: {
    nodes: Record<string, UsageNode>;
    relationships: UsageLive['relationships'];
    selectedId: string;
    onselect: (id: string) => void;
  } = $props();

  const CARD_W = 264;
  const GAP_X = 32;
  const GAP_Y = 24;
  const MAX_SCALE = 4;
  const FIT_PAD = 24;

  let graph = $state<HTMLDivElement | undefined>(undefined);
  let scale = $state(1);
  let x = $state(0);
  let y = $state(0);
  let auto = $state(true);
  let heights = $state<Record<string, number>>({});
  let viewport = $state({ w: 0, h: 0 });
  let dragging: { x: number; y: number } | null = null;

  const ids = $derived(Object.keys(nodes).sort((a, b) =>
    (nodes[a]?.role === 'master' ? 0 : 1) - (nodes[b]?.role === 'master' ? 0 : 1) || a.localeCompare(b)));

  const columns = $derived.by(() => {
    const master = ids.filter((id) => nodes[id]?.role === 'master');
    const workers = ids.filter((id) => nodes[id]?.role !== 'master');
    const workerCols = workers.length <= 2 ? 1 : 2;
    const cols: string[][] = [];
    if (master.length) cols.push(master);
    for (let c = 0; c < workerCols; c++) cols.push([]);
    const offset = master.length ? 1 : 0;
    workers.forEach((id, i) => cols[offset + (i % workerCols)].push(id));
    return cols.filter((col) => col.length > 0);
  });

  const layout = $derived.by(() => {
    const pos: Record<string, { x: number; y: number; h: number }> = {};
    let width = 0;
    let height = 0;
    columns.forEach((col, ci) => {
      let cursor = 0;
      for (const id of col) {
        const h = heights[id] ?? estimateHeight(nodes[id]);
        pos[id] = { x: ci * (CARD_W + GAP_X), y: cursor, h };
        cursor += h + GAP_Y;
      }
      height = Math.max(height, Math.max(0, cursor - GAP_Y));
      width = Math.max(width, ci * (CARD_W + GAP_X) + CARD_W);
    });
    return { pos, width, height };
  });

  function finitePercent(value: number | null | undefined): number | null {
    return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null;
  }

  function percentText(value: number | null | undefined): string {
    const v = finitePercent(value);
    return v == null ? 'unavailable' : `${Math.round(v)}%`;
  }

  function availabilityText(node: UsageNode, field: string): string {
    const state = node.reading?.availability?.[field];
    return state && state !== 'measured' ? state : 'unavailable';
  }

  function cpuText(node: UsageNode): string {
    const value = finitePercent(node.resources ? node.resources.host.cpu.value : node.reading?.host.cpu_percent);
    if (value != null) return `${Math.round(value)}%`;
    if (node.resources) return node.resources.host.cpu.status;
    return node.reading ? availabilityText(node, 'host_cpu') : 'unavailable';
  }

  function ramFill(node: UsageNode): number {
    const used = node.resources ? node.resources.host.memory.used.value : node.reading?.host.used_bytes;
    const total = node.resources ? node.resources.host.memory.total.value : node.reading?.host.total_bytes;
    if (typeof used !== 'number' || !Number.isFinite(used) || used < 0) return 0;
    if (typeof total !== 'number' || !Number.isFinite(total) || total <= 0) return 0;
    return Math.min(100, Math.max(0, (used / total) * 100));
  }

  function ramText(node: UsageNode): string {
    const used = node.resources ? node.resources.host.memory.used.value : node.reading?.host.used_bytes;
    const total = node.resources ? node.resources.host.memory.total.value : node.reading?.host.total_bytes;
    const usedOk = typeof used === 'number' && Number.isFinite(used) && used >= 0;
    const totalOk = typeof total === 'number' && Number.isFinite(total) && total > 0;
    const shared = node.unified_memory ? ' · shared' : '';
    if (usedOk && totalOk) {
      const pct = Math.min(100, Math.max(0, ((used as number) / (total as number)) * 100));
      return `${formatGib(used)} / ${formatGib(total)} · ${Math.round(pct)}%${shared}`;
    }
    if (usedOk) return `${formatGib(used)} · percentage unavailable${shared}`;
    if (node.reading) return `${availabilityText(node, 'host_memory')}${shared}`;
    const reported = node.total_ram_bytes;
    if (typeof reported === 'number' && Number.isFinite(reported) && reported > 0)
      return `reported ${formatGib(reported)}${shared}`;
    return 'unavailable';
  }

  type DeviceRow = { id: string; label: string; util: string; mem: string; reason: string | null;
    memoryRows?: { label: string; value: string }[] };

  function deviceRows(node: UsageNode): DeviceRow[] {
    if (node.resources) return node.resources.devices.map((device) => ({
      id: device.device_key, label: device.name,
      util: device.utilization?.status === 'measured' ? percentText(device.utilization.value) : device.utilization?.status ?? 'unsupported',
      memoryRows: device.memory.flatMap((memory) => memory.kind === 'dedicated'
        ? [{ label: 'Dedicated', value: memory.used.value != null
            ? `${formatGib(memory.used.value)} / ${formatGib(memory.total.value)}${memory.free.value != null ? ` · ${formatGib(memory.free.value)} free` : ''}`
            : memory.used.status }]
        : [{ label: 'GPU shared RAM', value: memory.used.value != null ? `${formatGib(memory.used.value)} used` : memory.used.status },
           ...(memory.backing_pool_id === 'host:ram' && node.resources!.host.memory.available.value != null
             ? [{ label: 'Host available', value: `${formatGib(node.resources!.host.memory.available.value)} · shared pool` }] : [])]),
      mem: '', reason: null
    }));
    const rows = new Map<string, DeviceRow>();
    for (const device of node.devices ?? []) {
      const id = String(device.index);
      const capacity = device.vram_bytes;
      rows.set(id, {
        id,
        label: device.name ? `GPU ${id} · ${device.name}` : `GPU ${id}`,
        util: 'unavailable',
        mem: typeof capacity === 'number' && Number.isFinite(capacity) && capacity > 0
          ? `reported ${formatGib(capacity)} capacity${node.unified_memory ? ' · shared with host RAM' : ''}` : 'unavailable',
        reason: null
      });
    }
    for (const device of node.reading?.devices ?? []) {
      const id = String(device.device_id);
      const prior = rows.get(id);
      const capacity = device.total_bytes ?? node.devices?.find((gpu) => String(gpu.index) === id)?.vram_bytes;
      rows.set(id, {
        id,
        label: prior?.label ?? `GPU ${id}`,
        util: percentText(device.utilization_percent),
        mem: (device.used_bytes != null
          ? capacity != null ? `${formatGib(device.used_bytes)} / ${formatGib(capacity)}` : `${formatGib(device.used_bytes)} used`
          : capacity != null ? `reported ${formatGib(capacity)} capacity` : 'unavailable')
          + (node.unified_memory ? ' · shared with host RAM' : ''),
        reason: device.unavailable_reason ?? null
      });
    }
    return [...rows.values()].sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }));
  }

  function gpuAbsence(node: UsageNode): string {
    if (node.resources) return node.resources.device_discovery.status === 'measured'
      ? 'No GPUs reported' : `GPU discovery ${node.resources.device_discovery.status}: ${node.resources.device_discovery.reason ?? 'unavailable'}`;
    return (node.backend ?? '').toLowerCase().includes('cpu')
      ? 'CPU backend · no GPUs reported'
      : 'GPU information unavailable';
  }

  function stateText(node: UsageNode): string {
    const connection = node.connected ? 'connected' : 'disconnected';
    if (node.resources) return `${connection} · ${node.resource_freshness ?? 'unavailable'}${node.resource_age_seconds != null ? ` · ${Math.round(node.resource_age_seconds)}s ago` : ''}`;
    const age = typeof node.age_seconds === 'number' ? ` · ${Math.round(node.age_seconds)}s ago` : '';
    return `${connection} · ${node.freshness}${age}`;
  }

  function estimateHeight(node: UsageNode | undefined): number {
    const devices = node ? deviceRows(node) : [];
    return 126 + (devices.length ? devices.reduce((sum, device) =>
      sum + (device.memoryRows ? 25 + (1 + device.memoryRows.length) * 20 : 20), 0) : 20);
  }

  function applyFit() {
    const el = graph;
    if (!el || !layout.width || !layout.height) return;
    const vw = el.clientWidth;
    const vh = el.clientHeight;
    if (!vw || !vh) return;
    const next = Math.min(1, (vw - FIT_PAD * 2) / layout.width, (vh - FIT_PAD * 2) / layout.height);
    scale = Math.max(Number.EPSILON, next);
    x = (vw - layout.width * scale) / 2;
    y = (vh - layout.height * scale) / 2;
  }

  function zoomBy(factor: number) {
    const el = graph;
    if (!el) return;
    auto = false;
    const next = Math.min(MAX_SCALE, Math.max(Number.EPSILON, scale * factor));
    if (next === scale) return;
    const cx = (el.clientWidth / 2 - x) / scale;
    const cy = (el.clientHeight / 2 - y) / scale;
    scale = next;
    x = el.clientWidth / 2 - cx * scale;
    y = el.clientHeight / 2 - cy * scale;
  }

  function panBy(dx: number, dy: number) {
    auto = false;
    x += dx;
    y += dy;
  }

  function fit() {
    auto = true;
    applyFit();
  }

  function drag(event: PointerEvent) {
    const el = graph;
    if (!el) return;
    if ((event.target as Element | null)?.closest?.('.node-map-card')) return;
    auto = false;
    dragging = { x: event.clientX - x, y: event.clientY - y };
    try { el.setPointerCapture(event.pointerId); } catch { /* not capturable */ }
  }

  function move(event: PointerEvent) {
    if (!dragging) return;
    x = event.clientX - dragging.x;
    y = event.clientY - dragging.y;
  }

  function endDrag(event: PointerEvent) {
    if (!dragging) return;
    dragging = null;
    try { graph?.releasePointerCapture(event.pointerId); } catch { /* already released */ }
  }

  onMount(() => {
    const el = graph;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const sync = () => { viewport = { w: el.clientWidth, h: el.clientHeight }; };
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(el);
    return () => observer.disconnect();
  });

  // Measure rendered card heights so row spacing and Fit match the real content.
  $effect(() => {
    void nodes;
    void ids.length;
    if (typeof window === 'undefined') return;
    void tick().then(() => {
      const el = graph;
      if (!el) return;
      const next: Record<string, number> = {};
      for (const card of el.querySelectorAll<HTMLElement>('.node-map-card')) {
        const id = card.dataset.nodeId;
        if (id && card.offsetHeight > 0) next[id] = card.offsetHeight;
      }
      const keys = Object.keys(next);
      if (!keys.length) return;
      const previous = heights;
      const same = keys.length === Object.keys(previous).length
        && keys.every((id) => Math.abs((previous[id] ?? 0) - next[id]) <= 0.5);
      if (!same) heights = next;
    });
  });

  // Automatic fit: refit on initial layout, resize or geometry change until the user navigates.
  $effect(() => {
    if (!auto) return;
    void layout.width;
    void layout.height;
    void viewport.w;
    void viewport.h;
    applyFit();
  });
</script>

{#snippet summary(node: UsageNode)}
  <span class="node-summary">
    <span class="node-summary-head">
      <span class="node-name">{node.host}</span>
      <span class="node-role-chip">{node.role}</span>
    </span>
    <span class="node-id">{node.address ? `${node.address} · ${node.node_id}` : node.node_id}</span>
    <span class="node-state">{stateText(node)}</span>
    <span class="node-metric"><span class="k">Host CPU</span><span class="v">{cpuText(node)}</span></span>
    <span class="node-metric"><span class="k">Host RAM</span><span class="v">{ramText(node)}</span></span>
    {#each deviceRows(node) as device (device.id)}
      {#if device.memoryRows}
        <span class="node-device-name">{device.label}</span>
        <span class="node-metric"><span class="k">GPU utilization</span><span class="v">{device.util}</span></span>
        {#each device.memoryRows as memory}
          <span class="node-metric"><span class="k">{memory.label}</span><span class="v">{memory.value}</span></span>
        {/each}
      {:else}<span class="node-metric node-metric-device">
        <span class="k">{device.label}</span>
        <span class="v">{device.util} · {device.mem}{device.reason ? ` (${device.reason})` : ''}</span>
      </span>
      {/if}
    {/each}
    {#if !deviceRows(node).length}
      <span class="node-metric node-metric-device"><span class="k">GPU</span><span class="v">{gpuAbsence(node)}</span></span>
    {/if}
    <span class="node-assignment">{node.assignment
      ? `${node.connected ? 'Assigned' : 'Last known assignment'}: ${node.assignment}`
      : 'No known assignment'}</span>
  </span>
{/snippet}

<div class="node-map panel p-3" aria-label="Cluster node map" data-od-id="live-node-map">
  <div class="node-map-controls flex flex-wrap items-center gap-2 mb-2">
    <span class="mono text-xs">Cluster nodes</span>
    <button class="btn" onclick={() => zoomBy(1.2)} aria-label="Zoom in">+</button>
    <button class="btn" onclick={() => zoomBy(1 / 1.2)} aria-label="Zoom out">−</button>
    <button class="btn" onclick={fit}>Fit</button>
    <button class="btn" onclick={() => panBy(-50, 0)} aria-label="Pan left">←</button>
    <button class="btn" onclick={() => panBy(50, 0)} aria-label="Pan right">→</button>
    <button class="btn" onclick={() => panBy(0, -50)} aria-label="Pan up">↑</button>
    <button class="btn" onclick={() => panBy(0, 50)} aria-label="Pan down">↓</button>
  </div>

  <div bind:this={graph} class="node-map-graph relative border" style="border-color: var(--border)"
    role="group" aria-label="Cluster node map canvas"
    onpointerdown={drag} onpointermove={move} onpointerup={endDrag} onpointercancel={endDrag}>
    <div class="node-map-world" style={`transform: translate(${x}px, ${y}px) scale(${scale});`}>
      <svg class="node-map-edges" width={Math.max(1, layout.width)} height={Math.max(1, layout.height)}
        viewBox={`0 0 ${Math.max(1, layout.width)} ${Math.max(1, layout.height)}`} aria-hidden="true">
        {#each relationships as edge (`${edge.from_node_id}:${edge.to_node_id}`)}
          {@const from = layout.pos[edge.from_node_id]}
          {@const to = layout.pos[edge.to_node_id]}
          {#if from && to}
            <line x1={from.x + CARD_W / 2} y1={from.y + from.h / 2}
              x2={to.x + CARD_W / 2} y2={to.y + to.h / 2}
              stroke="var(--muted)" stroke-width="1.5" />
          {/if}
        {/each}
      </svg>
      {#each ids as id (id)}
        {@const node = nodes[id]}
        <button class="node-map-card panel mono text-left"
          class:active={selectedId === id}
          class:memory-known={ramFill(node) > 0}
          data-node-id={id}
          style={`left: ${layout.pos[id].x}px; top: ${layout.pos[id].y}px; width: ${CARD_W}px; --memory-fill: ${ramFill(node)}%;`}
          aria-pressed={selectedId === id}
          onclick={() => onselect(id)}>
          {@render summary(node)}
        </button>
      {/each}
    </div>
  </div>

  <p class="mono text-xs mt-2 node-map-relationships" style="color: var(--muted)">
    {#if relationships.length}
      {relationships.map((edge) => `${edge.from_node_id} → ${edge.to_node_id} (master–worker membership)`).join(' · ')}
    {:else}No registered master–worker relationships{/if}
  </p>

  <div class="node-map-list mt-2" aria-label="Node list">
    {#each ids as id (id)}
      <button class="btn h-auto py-2 text-left" aria-pressed={selectedId === id} onclick={() => onselect(id)}>
        {@render summary(nodes[id])}
      </button>
    {/each}
  </div>
</div>
