<script lang="ts">
  import ErrorMessage from '$lib/components/ErrorMessage.svelte';
  import { appStore } from '$lib/store.svelte';
  import LoadProgressPanel from '$lib/components/LoadProgressPanel.svelte';
  import InferenceActivity from '$lib/components/InferenceActivity.svelte';
  import ManagedNetwork from '$lib/components/ManagedNetwork.svelte';
  import ClusterTopology from '$lib/components/ClusterTopology.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import NodeUsageDetails from '$lib/components/NodeUsageDetails.svelte';
  import UsageHistory from '$lib/components/UsageHistory.svelte';
  import { formatGib } from '$lib/format-memory';
  import { onMount, tick } from 'svelte';

  const members = $derived(Object.values(appStore.status?.members ?? {}));
  const view = $derived(appStore.viewState());
  const idle = $derived(view === 'idle');
  const isMaster = $derived(appStore.status?.role === 'master');
  const isWorker = $derived(appStore.status?.role === 'worker');
  const startJoinDisabled = $derived(!idle || !!appStore.busy);
  const inferenceActivity = $derived(appStore.status?.inference_activity);
  const memberIds = $derived(new Set(Object.keys(appStore.status?.members ?? {})));
  const displayNodes = $derived.by(() => {
    const usage = appStore.liveUsage?.nodes ?? {};
    if (view !== 'live' && view !== 'offline') return {};
    const members = appStore.status?.members ?? {};
    const current = Object.fromEntries(Object.entries(members).map(([id, member]) => {
      const existing = usage[id];
      const rpc_status = member.role === 'master' ? appStore.status?.rpc_lifecycle?.status : member.rpc_status;
      if (existing) return [id, { ...existing, rpc_status }];
      return [id, {
        node_id: id,
        host: member.host || id,
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
      return Object.fromEntries(Object.entries({ ...usage, ...current }).map(([id, node]) =>
        [id, { ...node, connected: false, freshness: node.reading ? 'stale' as const : 'unavailable' as const }]
      ));
    }
    return current;
  });
  const displayRelationships = $derived(
    view === 'live'
      ? (appStore.liveUsage?.relationships ?? []).filter((edge) =>
          memberIds.has(edge.from_node_id) && memberIds.has(edge.to_node_id))
      : []
  );
  const selectedNode = $derived(displayNodes[appStore.selectedNodeId] ?? null);
  const nodeCount = $derived(Object.keys(displayNodes).length);
  const recording = $derived(appStore.liveUsage?.recording ?? null);
  const netsecWarnings = $derived(appStore.status?.netsec_warnings ?? []);
  const overlayStatus = $derived(appStore.status?.overlay_status ?? '');

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
    const nodeId = appStore.selectedNodeId;
    if (!nodeId) return;
    if (loadedHistoryNode === nodeId && appStore.selectedLifetimeId && !appStore.historyError) {
      await appStore.loadUsageHistory();
      return;
    }
    loadedHistoryNode = nodeId;
    await appStore.selectUsageNode(nodeId);
  }

  $effect(() => {
    const nodeId = appStore.selectedNodeId;
    if (inspectorMode !== 'history' || !nodeId || loadedHistoryNode === nodeId) return;
    loadedHistoryNode = nodeId;
    void appStore.selectUsageNode(nodeId);
  });

  function assignedToInference(nodeId: string): boolean {
    const plan = appStore.status?.last_plan;
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
    (appStore.usageSeries?.buckets ?? [])
      .map((bucket) => bucket.host_cpu_max)
      .filter((value): value is number => value != null && Number.isFinite(value))
  );
  const activity = $derived(appStore.status?.inference_activity);
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
      <button class="btn btn-accent" type="button" disabled={startJoinDisabled} onclick={() => appStore.startMaster()}>Start</button>
      <button class="btn btn-danger" type="button" disabled={!!appStore.busy || !isMaster} onclick={() => appStore.stopCluster()}>Stop</button>
      <a class="btn" href="/run">Deploy</a>
    </div>
  </header>

  <section class="panel panel-pad stack-sm" aria-label="Cluster membership">
    <div class="od-cluster" style="align-items: end">
      <label class="field od-fill" style="flex-basis: 240px">
        Join key
        <input class="field mono" bind:value={appStore.joinKeyInput} autocomplete="off" spellcheck="false" />
      </label>
      <button class="btn" type="button" disabled={startJoinDisabled || !appStore.joinKeyInput} onclick={() => appStore.joinCluster()}>Join</button>
      <button class="btn" type="button" disabled={!!appStore.busy || !isWorker} onclick={() => appStore.leaveCluster()}>Leave</button>
      <button class="btn" type="button" disabled={!!appStore.busy || !isMaster || members.filter((member) => member.role === 'worker').length === 0} onclick={() => void appStore.updateWorkers()}>Update workers</button>
    </div>
    <LoadProgressPanel />
    {#if isMaster && appStore.status?.update}
      <p class="mono small muted">update {appStore.status.update.phase ?? '—'}{#if (appStore.status.update.pct ?? 0) > 0} · {Math.round(appStore.status.update.pct ?? 0)}%{/if}</p>
    {/if}
    {#if appStore.status?.join_key}
      <p class="mono small">key <span class="green">{appStore.status.join_key}</span></p>
    {/if}
    <p class="mono small muted">node {appStore.status?.node_id ?? '—'} · rpc {appStore.status?.rpc_bind ?? '—'} · overlay {appStore.status?.overlay ?? 'none'}{appStore.status?.wg_address ? ` ${appStore.status.wg_address}` : ''}</p>
    {#if appStore.status?.gpu_ids?.length}
      <p class="mono small muted">active GPUs {appStore.status.gpu_ids.map((id) => {
        const gpu = appStore.gpus.find((item) => item.index === id);
        return gpu ? `${id}:${gpu.name}` : String(id);
      }).join(' · ')}</p>
    {/if}
    {#if appStore.usageError}<ErrorMessage source="usage-request" message={appStore.usageError} snapshot={false} />{/if}
    {#if appStore.status?.apply_error}<ErrorMessage source="apply" message={appStore.status.apply_error} />{/if}
    {#if appStore.status?.apply_warning}<p class="small warning">{appStore.status.apply_warning}</p>{/if}
    {#if appStore.status?.update?.error}<ErrorMessage source="update" message={appStore.status.update.error} />{/if}
    {#each appStore.status?.update?.workers ?? [] as worker}
      <ErrorMessage source={`update-worker:${worker.node_id ?? worker.host}`} message={worker.error} label={`Update ${worker.host ?? worker.node_id ?? '?'}`} />
    {/each}
    {#each netsecWarnings as warning}<p class="small warning" role="note">{warning}</p>{/each}
    {#if overlayStatus}<p class="usage-overlay-status small muted">{overlayStatus}</p>{/if}
  </section>

  <section class="stat-strip" aria-label="Cluster summary">
    <div class="od-stat">
      <span class="stat-label">Members</span>
      <span class="stat-number">{nodeCount || '—'}<span class="unit">{connectedCount} connected</span></span>
      <span class="stat-helper">{appStore.status?.role ?? 'idle'}</span>
    </div>
    <div class="od-stat">
      <span class="stat-label">Inference</span>
      <span class="stat-number words">{appStore.status?.inference_up ? 'Up' : 'Idle'}</span>
      <span class="stat-helper">{appStore.status?.model_name || 'No model loaded'}</span>
    </div>
    <div class="od-stat">
      <span class="stat-label">Measured memory</span>
      <span class="stat-number">{memoryUsed == null ? '—' : (memoryUsed / 1024 ** 3).toFixed(1)}{#if memoryUsed != null}<span class="unit">GiB</span>{/if}</span>
      <span class="stat-helper">Host readings only</span>
    </div>
    <div class="od-stat">
      <span class="stat-label">Last plan</span>
      <span class="stat-number words">{appStore.status?.last_plan ? 'Saved' : 'None'}</span>
      <span class="stat-helper" role="status">{recording ? `Recording: ${recording.recording} · last saved ${recording.last_successful_save ?? 'never'}` : 'Recording: unavailable'}</span>
    </div>
  </section>

  <div class="split">
    <section class="panel run-panel" aria-label="Active run">
      <div class="run-heading">
        <div class="run-model">
          <span class="model-emblem"><Icon name="cube" /></span>
          <div>
            <h2>{appStore.status?.model_name || 'No active run'}</h2>
            <p>{activity?.phase ?? (appStore.status?.inference_up ? 'ready' : 'idle')}</p>
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
        <span class="mono">{appStore.status?.inference_up ? `proxy :${appStore.status.control_port ?? 18765}/v1` : 'Inference is stopped'}</span>
        <a class="text-button" href="/integrations">Open chat <Icon name="arrow" small /></a>
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
          <button class="btn" type="button" disabled={!appStore.selectedNodeId} onclick={() => openInspector('details')}>Details</button>
          <button class="btn" type="button" disabled={!appStore.selectedNodeId} onclick={() => openInspector('history')}>History</button>
        </div>
      </div>
      <ClusterTopology
        nodes={topologyNodes}
        relationships={displayRelationships}
        selectedId={appStore.selectedNodeId}
        offline={view === 'offline'}
        networks={appStore.status?.managed_networks ?? []}
        onselect={(id) => appStore.selectedNodeId = id}
      />
    </section>
  {:else if idle}
    <p class="notice">No members — Start as master or Join a cluster.</p>
  {:else}
    <p class="notice">Waiting for measured nodes…</p>
  {/if}

  {#if appStore.status?.managed_networks !== undefined && !idle}
    <ManagedNetwork />
  {/if}

  <section class="panel" aria-label="Recent logs">
    <div class="panel-head">
      <h2>Recent activity</h2>
      <a class="text-button" href="/logs">View activity</a>
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
    <h2>{inspectorMode === 'history' ? 'Recorded usage' : 'Selected node'} · {appStore.selectedNodeId || '—'}</h2>
    <button class="btn icon-btn ghost" type="button" aria-label="Close inspector" onclick={closeInspector}><Icon name="close" /></button>
  </div>
  <div class="dialog-body">
    {#if inspectorMode === 'history'}
      {#if appStore.selectedNodeId}
        {#key `${appStore.selectedNodeId}:${appStore.selectedLifetimeId}:${appStore.usageWindow}`}
          <UsageHistory nodeId={appStore.selectedNodeId}
            lifetimes={appStore.usageLifetimes?.lifetimes ?? []}
            selectedLifetimeId={appStore.selectedLifetimeId} window={appStore.usageWindow}
            series={appStore.usageSeries} page={appStore.usagePage}
            recorder={appStore.usageLifetimes ?? appStore.liveUsage?.recording ?? null}
            error={appStore.historyError}
            onlifetime={(id) => void appStore.selectUsageLifetime(id)}
            onwindow={(window) => void appStore.selectUsageWindow(window)}
            onnext={() => void appStore.nextUsagePage()} />
        {/key}
      {/if}
    {:else if inspectorMode === 'details'}
      <NodeUsageDetails node={selectedNode}
        assignment={assignedToInference(appStore.selectedNodeId) || !!selectedNode?.assignment}
        activity={inferenceActivity ?? null} />
      {#if appStore.status?.last_plan}
        <h3>Planning estimate · not measured usage</h3>
        <pre class="code-block">{JSON.stringify(appStore.status.last_plan, null, 2)}</pre>
      {/if}
    {/if}
  </div>
</dialog>
