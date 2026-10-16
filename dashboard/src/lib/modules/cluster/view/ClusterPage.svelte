<script lang="ts">
  import { createApp } from '#lib/app.svelte';
  import { routes } from '#lib/shared/navigation';
  import ErrorMessage from '#lib/shared/ErrorMessage.svelte';
  import LoadProgressPanel from '#lib/modules/run/view/LoadProgressPanel.svelte';
  import InferenceActivity from '#lib/modules/usage/view/InferenceActivity.svelte';
  import ManagedNetwork from '#lib/modules/network/view/ManagedNetwork.svelte';
  import ClusterTopology from '#lib/modules/cluster/view/ClusterTopology.svelte';
  import Icon from '#lib/shared/Icon.svelte';
  import NodeUsageDetails from '#lib/modules/usage/view/NodeUsageDetails.svelte';
  import UsageHistory from '#lib/modules/usage/view/UsageHistory.svelte';
  import { addressInSubnet, observedControlAddress } from '#lib/modules/cluster/fast-subnet';
  import { formatGib } from '#lib/shared/format-memory';
  import { onMount, tick } from 'svelte';
  import type { App } from '#lib/app.svelte';
  let { cluster = createApp().cluster, usage = createApp().usage, settings = createApp().settings, run = createApp().run, network = createApp().network }: {
    cluster?: App['cluster'];
    usage?: App['usage'];
    settings?: App['settings'];
    run?: App['run'];
    network?: App['network'];
  } = $props();

  const members = $derived(Object.values(cluster.status?.members ?? {}));
  const view = $derived(cluster.viewState());
  const idle = $derived(view === 'idle');
  const isMaster = $derived(cluster.status?.role === 'master');
  const isWorker = $derived(cluster.status?.role === 'worker');
  const startJoinDisabled = $derived(!idle || !!cluster.busy);
  const inferenceActivity = $derived(cluster.status?.inference_activity);
  const memberIds = $derived(new Set(Object.keys(cluster.status?.members ?? {})));
  const networkDirectory = $derived(Object.fromEntries(
    (cluster.status?.network_directory ?? []).map((entry) => [entry.node_id, entry])));
  const displayNodes = $derived.by(() => {
    const usageNodes = usage.liveUsage?.nodes ?? {};
    if (view !== 'live' && view !== 'offline') return {};
    const members = cluster.status?.members ?? {};
    const current = Object.fromEntries(Object.entries(members).map(([id, member]) => {
      const existing = usageNodes[id];
      const identity = networkDirectory[id];
      const rpc_status = member.role === 'master' ? cluster.status?.rpc_lifecycle?.status : member.rpc_status;
      const identityFields = {
        host: identity?.hostname || existing?.host || member.host || id,
        address: identity?.private_address || identity?.rpc_bind || existing?.address || member.host || null,
      };
      if (existing) return [id, { ...existing, ...identityFields, rpc_status }];
      return [id, {
        node_id: id,
        ...identityFields,
        role: member.role === 'worker' ? 'worker' as const : 'master' as const,
        rpc_status,
        backend: member.actual_backend ?? member.backend ?? null,
        devices: member.devices,
        unified_memory: !!member.unified_memory,
        total_ram_bytes: member.total_ram_bytes ?? null,
        vram_bytes: member.vram_bytes ?? null,
        connected: true,
        freshness: 'unavailable' as const,
        reading: null,
      }];
    }));
    if (view === 'offline') {
      return Object.fromEntries(Object.entries({ ...usageNodes, ...current }).map(([id, node]) =>
        [id, { ...node, connected: false, freshness: node.reading ? 'stale' as const : 'unavailable' as const }]
      ));
    }
    return current;
  });
  const displayRelationships = $derived(
    view === 'live'
      ? (usage.liveUsage?.relationships ?? []).filter((edge) =>
          memberIds.has(edge.from_node_id) && memberIds.has(edge.to_node_id))
      : []
  );
  const selectedNode = $derived(displayNodes[usage.selectedNodeId] ?? null);
  const nodeCount = $derived(Object.keys(displayNodes).length);
  const recording = $derived(usage.liveUsage?.recording ?? null);
  const netsecWarnings = $derived(cluster.status?.netsec_warnings ?? []);

  let inspectorDialog = $state<HTMLDialogElement | undefined>(undefined);
  let inspectorMode = $state<'details' | 'history' | null>(null);
  let loadedHistoryNode = '';

  async function openInspector(mode: 'details' | 'history') {
    inspectorMode = mode;
    if (mode === 'history') await ensureHistory();
    await tick();
    const dialog = inspectorDialog;
    if (dialog && !dialog.open) dialog.showModal();
  }

  function closeInspector() {
    inspectorDialog?.close();
  }

  // Native modal dialogs restore focus to their trigger on close; the dialog only owns content state.
  function handleDialogClose() {
    inspectorMode = null;
  }

  // History is fetched only when requested (and re-fetched when the selected node changes while open).
  async function ensureHistory() {
    const nodeId = usage.selectedNodeId;
    if (!nodeId) return;
    if (loadedHistoryNode === nodeId && usage.selectedLifetimeId && !usage.historyError) {
      await usage.loadUsageHistory();
      return;
    }
    loadedHistoryNode = nodeId;
    await usage.selectUsageNode(nodeId);
  }

  $effect(() => {
    const nodeId = usage.selectedNodeId;
    if (inspectorMode !== 'history' || !nodeId || loadedHistoryNode === nodeId) return;
    loadedHistoryNode = nodeId;
    void usage.selectUsageNode(nodeId);
  });

  function assignedToInference(nodeId: string): boolean {
    const plan = cluster.status?.last_plan;
    const allocations = plan?.device_allocations;
    if (Array.isArray(allocations) && allocations.length) {
      return allocations.some((allocation) => (
        typeof allocation === 'object'
        && allocation !== null
        && (allocation as { node_id?: unknown }).node_id === nodeId
      ));
    }
    const nodeIds = plan?.node_ids;
    return Array.isArray(nodeIds) ? nodeIds.includes(nodeId) : false;
  }

  const topologyNodes = $derived(Object.values(displayNodes));
  const readyFastSubnet = $derived(
    (cluster.status?.managed_networks ?? []).find((network) => network.observed_state === 'ready') ?? null
  );
  const fastSubnetNodes = $derived.by(() => {
    const offers: Record<string, {
      path: 'fast-subnet' | 'router';
      phase: 'hidden' | 'checking' | 'ready' | 'busy';
      address: string | null;
      error: string;
    }> = {};
    if (!isMaster || view === 'offline' || !readyFastSubnet) return offers;
    const reachable = new Map(
      (cluster.fastSubnetReachability?.workers ?? [])
        .filter((worker) => worker.reachable && worker.address)
        .map((worker) => [worker.node_id, worker.address])
    );
    for (const node of topologyNodes) {
      if (node.role !== 'worker' || !node.connected) continue;
      const observed = observedControlAddress(cluster.status?.members?.[node.node_id]);
      if (!observed || !readyFastSubnet.subnet) continue;
      const onSubnet = addressInSubnet(observed, readyFastSubnet.subnet);
      const address = reachable.get(node.node_id) ?? null;
      const busy = cluster.reconnectingNodeId === node.node_id;
      let phase: 'hidden' | 'checking' | 'ready' | 'busy' = 'hidden';
      if (!onSubnet && cluster.fastSubnetProbe === 'checking') phase = 'checking';
      else if (!onSubnet && address && (cluster.fastSubnetProbe === 'done' || busy)) {
        phase = busy ? 'busy' : 'ready';
      }
      offers[node.node_id] = {
        path: onSubnet ? 'fast-subnet' : 'router',
        phase,
        address,
        error: cluster.fastSubnetErrors[node.node_id] ?? ''
      };
    }
    return offers;
  });
  let fastSubnetProbed = false;
  const connectedCount = $derived(topologyNodes.filter((node) => node.connected).length);
  const memoryUsed = $derived.by(() => {
    let total = 0;
    let any = false;
    for (const node of topologyNodes) {
      const value = node.resources?.host.memory.used.value ?? node.reading?.host.used_bytes ?? null;
      if (value != null && Number.isFinite(value)) {
        total += value;
        any = true;
      }
    }
    return any ? total : null;
  });
  const chartPoints = $derived(
    (usage.usageSeries?.buckets ?? [])
      .map((bucket) => bucket.host_cpu_max)
      .filter((value): value is number => value != null && Number.isFinite(value))
  );
  const activity = $derived(cluster.status?.inference_activity);
  let recentLogs = $state<{ ts: number; node_id: string; line: string }[]>([]);
  let logsNote = $state('');

  function chartPath(values: number[]): string {
    if (values.length < 2) return '';
    const width = 640;
    const height = 120;
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    return values.map((value, index) => {
      const x = (index / (values.length - 1)) * width;
      const y = height - ((value - min) / span) * (height - 8) - 4;
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`;
    }).join(' ');
  }

  $effect(() => {
    if (fastSubnetProbed || cluster.status?.role !== 'master') return;
    const ready = (cluster.status.managed_networks ?? []).some((network) => network.observed_state === 'ready');
    if (!ready) return;
    fastSubnetProbed = true;
    void cluster.probeFastSubnetOnce();
  });

  onMount(() => {
    let disposed = false;
    const load = async () => {
      try {
        const response = await fetch('/v2/logs?n=5');
        if (!response.ok) {
          if (!disposed) logsNote = 'Recent logs unavailable';
          return;
        }
        const data = await response.json();
        if (!Array.isArray(data?.lines)) {
          if (!disposed) logsNote = 'Recent logs unavailable';
          return;
        }
        if (!disposed) {
          recentLogs = data.lines.slice(-5).reverse();
          logsNote = '';
        }
      } catch {
        if (!disposed) logsNote = 'Recent logs unavailable';
      }
    };
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  });
</script>

<div class="stack cluster-page">
  <header class="page-heading">
    <div>
      <p class="eyebrow">Cluster</p>
      <h1 id="page-title">Local cluster</h1>
      <p>Members, measured usage, and the master-to-worker paths this node can see.</p>
    </div>
    <div class="actions">
      <button class="btn btn-accent" type="button" disabled={startJoinDisabled} onclick={() => cluster.startMaster()}>Start</button>
      <button class="btn btn-danger" type="button" disabled={!!cluster.busy || !isMaster} onclick={() => cluster.stopCluster()}>Stop</button>
      <a class="btn" href={routes.run}>Deploy</a>
    </div>
  </header>

  <section class="panel panel-pad stack-sm" aria-label="Cluster membership">
    <div class="od-cluster" style="align-items: end">
      <label class="field od-fill" style="flex-basis: 240px">
        Join key
        <input class="field mono" bind:value={cluster.joinKeyInput} autocomplete="off" spellcheck="false" />
      </label>
      <button class="btn" type="button" disabled={startJoinDisabled || !cluster.joinKeyInput} onclick={() => cluster.joinCluster()}>Join</button>
      <button class="btn" type="button" disabled={!!cluster.busy || !isWorker} onclick={() => cluster.leaveCluster()}>Leave</button>
      <button class="btn" type="button" disabled={!!cluster.busy || !isMaster || members.filter((member) => member.role === 'worker').length === 0} onclick={() => void cluster.updateWorkers()}>Update workers</button>
    </div>
    <LoadProgressPanel presenter={run} />
    {#if isMaster && cluster.status?.update}
      <p class="mono small muted">update {cluster.status.update.phase ?? '—'}{#if (cluster.status.update.pct ?? 0) > 0} · {Math.round(cluster.status.update.pct ?? 0)}%{/if}</p>
    {/if}
    {#if cluster.status?.join_key}
      <p class="mono small">key <span class="green">{cluster.status.join_key}</span></p>
    {/if}
    <p class="mono small muted">node {cluster.status?.node_id ?? '—'} · rpc {cluster.status?.rpc_bind ?? '—'}</p>
    {#if cluster.status?.gpu_ids?.length}
      <p class="mono small muted">active GPUs {cluster.status.gpu_ids.map((id) => {
        const gpu = settings.gpus.find((item) => item.index === id);
        return gpu ? `${id}:${gpu.name}` : String(id);
      }).join(' · ')}</p>
    {/if}
    {#if usage.usageError}<ErrorMessage source="usage-request" message={usage.usageError} snapshot={false} />{/if}
    {#if cluster.status?.apply_error}<ErrorMessage source="apply" message={cluster.status.apply_error} />{/if}
    {#if cluster.status?.apply_warning}<p class="small warning">{cluster.status.apply_warning}</p>{/if}
    {#if cluster.status?.update?.error}<ErrorMessage source="update" message={cluster.status.update.error} />{/if}
    {#each cluster.status?.update?.workers ?? [] as worker}
      <ErrorMessage source={`update-worker:${worker.node_id ?? worker.host}`} message={worker.error} label={`Update ${worker.host ?? worker.node_id ?? '?'}`} />
    {/each}
    {#each netsecWarnings as warning}<p class="small warning" role="note">{warning}</p>{/each}
  </section>

  <section class="stat-strip" aria-label="Cluster summary">
    <div class="od-stat">
      <span class="stat-label">Members</span>
      <span class="stat-number">{nodeCount || '—'}<span class="unit">{connectedCount} connected</span></span>
      <span class="stat-helper">{cluster.status?.role ?? 'idle'}</span>
    </div>
    <div class="od-stat">
      <span class="stat-label">Inference</span>
      <span class="stat-number words">{cluster.status?.inference_up ? 'Up' : 'Idle'}</span>
      <span class="stat-helper">{cluster.status?.model_name || 'No model loaded'}</span>
    </div>
    <div class="od-stat">
      <span class="stat-label">Measured memory</span>
      <span class="stat-number">{memoryUsed == null ? '—' : (memoryUsed / 1024 ** 3).toFixed(1)}{#if memoryUsed != null}<span class="unit">GiB</span>{/if}</span>
      <span class="stat-helper">Host readings only</span>
    </div>
    <div class="od-stat">
      <span class="stat-label">Last plan</span>
      <span class="stat-number words">{cluster.status?.last_plan ? 'Saved' : 'None'}</span>
      <span class="stat-helper" role="status">{recording ? `Recording: ${recording.recording} · last saved ${recording.last_successful_save ?? 'never'}` : 'Recording: unavailable'}</span>
    </div>
  </section>

  <div class="split">
    <section class="panel run-panel" aria-label="Active run">
      <div class="run-heading">
        <div class="run-model">
          <span class="model-emblem"><Icon name="cube" /></span>
          <div>
            <h2>{cluster.status?.model_name || 'No active run'}</h2>
            <p>{activity?.phase ?? (cluster.status?.inference_up ? 'ready' : 'idle')}</p>
          </div>
        </div>
        {#if activity && activity.phase !== 'idle'}
          <InferenceActivity {activity} />
        {/if}
      </div>
      <div class="run-info">
        <div class="od-stat"><small>Output tokens</small><strong>{activity?.output_tokens ?? '—'}</strong></div>
        <div class="od-stat"><small>Prompt tokens</small><strong>{activity ? `${activity.prompt_tokens_processed}/${activity.prompt_tokens}` : '—'}</strong></div>
        <div class="od-stat"><small>Requests</small><strong>{activity?.active_requests ?? '—'}</strong></div>
      </div>
      <div class="run-foot">
        <span class="mono">{cluster.status?.inference_up ? `proxy :${cluster.status.control_port ?? 18765}/v1` : 'Inference is stopped'}</span>
        <a class="text-button" href={routes.integrations}>Open chat <Icon name="arrow" small /></a>
      </div>
    </section>
    <section class="panel chart-panel" aria-label="Usage history chart">
      <div class="chart-head">
        <div>
          <p class="stat-label">Host CPU history</p>
          <p class="chart-value">{chartPoints.length ? `${Math.round(chartPoints[chartPoints.length - 1])}%` : '—'}<small>{chartPoints.length ? 'latest bucket' : 'not loaded'}</small></p>
        </div>
        <span class="tag">Unmeasured throughput</span>
      </div>
      <svg class="chart" viewBox="0 0 640 120" role="img" aria-label="Host CPU history">
        <line class="chart-grid" x1="0" y1="30" x2="640" y2="30" />
        <line class="chart-grid" x1="0" y1="60" x2="640" y2="60" />
        <line class="chart-grid" x1="0" y1="90" x2="640" y2="90" />
        {#if chartPoints.length > 1}
          <path class="chart-line" d={chartPath(chartPoints)} />
        {/if}
      </svg>
      <p class="usage-note"><Icon name="info" small /> Tokens per second are not sampled. Open a node history to load host CPU buckets.</p>
    </section>
  </div>

  {#if view === 'offline'}
    <p class="notice warn" role="status">Disconnected — keeping last cluster view; readings are not live.</p>
  {/if}
  {#if view === 'loading'}
    <p class="notice">Loading cluster…</p>
  {:else if nodeCount}
    <section class="cluster-block" aria-label="Topology">
      <div class="section-heading">
        <div>
          <h2>Topology</h2>
          <p>Connected, disconnected, and unmeasured master-worker paths. No estimated link rate.</p>
        </div>
        <div class="actions">
          <button class="btn" type="button" disabled={!usage.selectedNodeId} onclick={() => openInspector('details')}>Details</button>
          <button class="btn" type="button" disabled={!usage.selectedNodeId} onclick={() => openInspector('history')}>History</button>
        </div>
      </div>
      <ClusterTopology
        nodes={topologyNodes}
        relationships={displayRelationships}
        selectedId={usage.selectedNodeId}
        offline={view === 'offline'}
        networks={cluster.status?.managed_networks ?? []}
        fastSubnet={{ nodes: fastSubnetNodes, onReconnect: (id) => void cluster.reconnectWorkerFastSubnet(id) }}
        onselect={(id) => usage.selectedNodeId = id}
      />
    </section>
  {:else if idle}
    <p class="notice">No members — Start as master or Join a cluster.</p>
  {:else}
    <p class="notice">Waiting for measured nodes…</p>
  {/if}

  {#if cluster.status?.feature_flags?.['private-network-setup'] && !idle}
    <ManagedNetwork presenter={network} />
  {/if}

  <section class="panel" aria-label="Recent logs">
    <div class="panel-head">
      <h2>Recent activity</h2>
      <a class="text-button" href={routes.logs}>View activity</a>
    </div>
    {#if logsNote}
      <p class="panel-pad muted">{logsNote}</p>
    {:else if recentLogs.length === 0}
      <p class="panel-pad muted">No recent log lines.</p>
    {:else}
      <div class="activity-list">
        {#each recentLogs as entry}
          <div class="activity-row">
            <span class="activity-marker"></span>
            <div class="od-field">
              <strong>{entry.node_id}</strong>
              <p>{entry.line}</p>
            </div>
            <time>{Number.isFinite(entry.ts) ? new Date(entry.ts * 1000).toISOString().slice(11, 19) + 'Z' : ''}</time>
          </div>
        {/each}
      </div>
    {/if}
  </section>
</div>

<dialog bind:this={inspectorDialog} class="usage-dialog"
  aria-label={inspectorMode === 'history' ? 'Recorded usage' : 'Node details'}
  onclose={handleDialogClose}>
  <div class="dialog-head">
    <h2>{inspectorMode === 'history' ? 'Recorded usage' : 'Selected node'} · {usage.selectedNodeId || '—'}</h2>
    <button class="btn icon-btn ghost" type="button" aria-label="Close inspector" onclick={closeInspector}><Icon name="close" /></button>
  </div>
  <div class="dialog-body">
    {#if inspectorMode === 'history'}
      {#if usage.selectedNodeId}
        {#key `${usage.selectedNodeId}:${usage.selectedLifetimeId}:${usage.usageWindow}`}
          <UsageHistory nodeId={usage.selectedNodeId}
            lifetimes={usage.usageLifetimes?.lifetimes ?? []}
            selectedLifetimeId={usage.selectedLifetimeId} window={usage.usageWindow}
            series={usage.usageSeries} page={usage.usagePage}
            recorder={usage.usageLifetimes ?? usage.liveUsage?.recording ?? null}
            error={usage.historyError}
            onlifetime={(id) => void usage.selectUsageLifetime(id)}
            onwindow={(window) => void usage.selectUsageWindow(window)}
            onnext={() => void usage.nextUsagePage()} />
        {/key}
      {/if}
    {:else if inspectorMode === 'details'}
      <NodeUsageDetails node={selectedNode}
        assignment={assignedToInference(usage.selectedNodeId) || !!selectedNode?.assignment}
        activity={inferenceActivity ?? null} />
      {#if cluster.status?.last_plan}
        <h3>Planning estimate · not measured usage</h3>
        <pre class="code-block">{JSON.stringify(cluster.status.last_plan, null, 2)}</pre>
      {/if}
    {/if}
  </div>
</dialog>
