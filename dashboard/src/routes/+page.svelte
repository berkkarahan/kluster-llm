<script lang="ts">
  import { appStore } from '$lib/store.svelte';
  import LoadProgressPanel from '$lib/components/LoadProgressPanel.svelte';
  import InferenceActivity from '$lib/components/InferenceActivity.svelte';
  import NodeMap from '$lib/components/NodeMap.svelte';
  import NodeUsageDetails from '$lib/components/NodeUsageDetails.svelte';
  import UsageHistory from '$lib/components/UsageHistory.svelte';
  import { tick } from 'svelte';

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
    if (view === 'offline') {
      return Object.fromEntries(Object.entries(usage).map(([id, node]) =>
        [id, { ...node, connected: false, freshness: node.reading ? 'stale' as const : 'unavailable' as const }]
      ));
    }
    if (view !== 'live') return {};
    const members = appStore.status?.members ?? {};
    return Object.fromEntries(Object.entries(members).map(([id, member]) => {
      const existing = usage[id];
      if (existing) return [id, existing];
      return [id, {
        node_id: id,
        host: member.host || id,
        role: member.role === 'worker' ? 'worker' as const : 'master' as const,
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
  const updateError = $derived(
    (appStore.status?.update?.workers ?? []).filter((w) => w.error)
      .map((w) => `${w.host ?? w.node_id ?? '?'}: ${w.error}`)
  );
  const netsecWarnings = $derived(appStore.status?.netsec_warnings ?? []);
  const overlayStatus = $derived(appStore.status?.overlay_status ?? '');

  let chatOpen = $state(false);
  let chatTrigger = $state<HTMLButtonElement | undefined>(undefined);
  let chatClose = $state<HTMLButtonElement | undefined>(undefined);
  let inspectorDialog = $state<HTMLDialogElement | undefined>(undefined);
  let inspectorMode = $state<'details' | 'history' | null>(null);
  let loadedHistoryNode = '';

  async function openChat() {
    chatOpen = true;
    await tick();
    chatClose?.focus();
  }

  function closeChat() {
    chatOpen = false;
    chatTrigger?.focus();
  }

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

  function onWindowKeydown(event: KeyboardEvent) {
    if (event.key !== 'Escape') return;
    // Capture phase: the dialog is still open here, so it owns Escape and Chat stays untouched.
    if (inspectorMode || inspectorDialog?.open) return;
    if (chatOpen) closeChat();
  }

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
</script>

<svelte:window onkeydowncapture={onWindowKeydown} />

<div class="usage-workspace">
  <div class="usage-toolbar">
    <details class="cluster-controls">
      <summary class="btn">Cluster · {appStore.status?.role ?? 'idle'}</summary>
      <div class="cluster-controls-body panel">
        <div class="grid grid-cols-2 gap-2">
          <button class="btn btn-accent" disabled={startJoinDisabled} onclick={() => appStore.startMaster()}
            >Start</button
          >
          <button class="btn btn-danger" disabled={!!appStore.busy || !isMaster} onclick={() => appStore.stopCluster()}
            >Stop</button
          >
          <button class="btn" disabled={startJoinDisabled || !appStore.joinKeyInput} onclick={() => appStore.joinCluster()}
            >Join</button
          >
          <button class="btn" disabled={!!appStore.busy || !isWorker} onclick={() => appStore.leaveCluster()}
            >Leave</button
          >
          <button
            class="btn col-span-2"
            disabled={!!appStore.busy || !isMaster || members.filter((m) => m.role === 'worker').length === 0}
            onclick={() => void appStore.updateWorkers()}>Update workers</button
          >
        </div>
        <a class="btn" href="/run">Plan &amp; Run →</a>
        {#if isMaster && appStore.status?.update}
          <p class="mono text-[11px]" style="color: var(--muted)">
            update <span style="color: var(--fg)">{appStore.status.update.phase ?? '—'}</span>
            {#if (appStore.status.update.pct ?? 0) > 0}· {Math.round(appStore.status.update.pct ?? 0)}%{/if}
          </p>
          {#each appStore.status.update.workers ?? [] as w}
            <p class="mono text-[11px]" style="color: var(--muted)">
              {w.host ?? w.node_id ?? '?'}
              <span style="color: var(--fg)"> {w.phase}</span>
              {#if w.error}<span style="color: var(--danger, #c44)"> {w.error}</span>{/if}
            </p>
          {/each}
        {/if}
        <LoadProgressPanel />
        {#if appStore.status?.join_key}
          <p class="mono text-[11px] break-all" style="color: var(--muted)">
            key <span style="color: var(--accent)">{appStore.status.join_key}</span>
          </p>
        {/if}
        <p class="mono text-[11px]" style="color: var(--muted)">
          node <span style="color: var(--fg)">{appStore.status?.node_id ?? '—'}</span>
        </p>
        <p class="mono text-[11px]" style="color: var(--muted)">
          rpc <span style="color: var(--fg)">{appStore.status?.rpc_bind ?? '—'}</span>
        </p>
        <p class="mono text-[11px]" style="color: var(--muted)">
          overlay <span style="color: var(--fg)">{appStore.status?.overlay ?? 'none'}</span>
          {#if appStore.status?.wg_address}<span style="color: var(--accent)"> {appStore.status.wg_address}</span>{/if}
        </p>
        {#if appStore.status?.gpu_ids?.length}
          <p class="mono text-[11px]" style="color: var(--muted)">
            active GPUs
            <span style="color: var(--fg)">
              {appStore.status.gpu_ids
                .map((id) => {
                  const g = appStore.gpus.find((x) => x.index === id);
                  return g ? `${id}:${g.name}` : String(id);
                })
                .join(' · ')}
            </span>
          </p>
        {/if}
        {#if appStore.status?.overlay_status}
          <p class="mono text-[11px]" style="color: var(--muted)">{appStore.status.overlay_status}</p>
        {/if}
        {#each appStore.status?.netsec_warnings ?? [] as w}
          <p class="mono text-[11px]" style="color: var(--danger, #c44)">{w}</p>
        {/each}
      </div>
    </details>

    <div class="usage-toolbar-main">
      <span class="mono uppercase tracking-wider text-[11px]">Live topology</span>
      <span class="mono text-[11px]" style="color: var(--muted)">{nodeCount} node{nodeCount === 1 ? '' : 's'}</span>
      <span class="mono text-[11px]" role="status" style="color: var(--muted)">
        Recording: {recording?.recording ?? 'unavailable'} · last saved {recording?.last_successful_save ?? 'never'}
      </span>
      {#if appStore.usageError}
        <span class="mono text-[11px]" role="alert" style="color: var(--danger)">{appStore.usageError}</span>
      {/if}
      {#if appStore.status?.apply_error}
        <span class="mono text-[11px]" role="alert" style="color: var(--danger, #c44)">{appStore.status.apply_error}</span>
      {/if}
      {#if appStore.status?.apply_warning}
        <span class="mono text-[11px]" style="color: var(--warn, #b80)">{appStore.status.apply_warning}</span>
      {/if}
      {#if appStore.status?.update?.error}
        <span class="mono text-[11px]" role="alert" style="color: var(--danger, #c44)">{appStore.status.update.error}</span>
      {/if}
      {#each updateError as error}
        <span class="mono text-[11px]" role="alert" style="color: var(--danger, #c44)">update {error}</span>
      {/each}
      {#each netsecWarnings as warning}
        <span class="mono text-[11px]" role="alert" style="color: var(--danger, #c44)">{warning}</span>
      {/each}
      {#if overlayStatus}
        <span class="usage-overlay-status mono text-[11px]" style="color: var(--muted)">{overlayStatus}</span>
      {/if}
    </div>

    <div class="usage-toolbar-actions">
      <button class="btn" disabled={!appStore.selectedNodeId} onclick={() => openInspector('details')}>Details</button>
      <button class="btn" disabled={!appStore.selectedNodeId} onclick={() => openInspector('history')}>History</button>
      <button bind:this={chatTrigger} class="btn" aria-label="Open chat" aria-controls="cluster-chat"
        aria-expanded={chatOpen} onclick={openChat}>Chat</button>
    </div>
  </div>

  <div class="usage-topology" data-od-id="topology-workspace">
    {#if inferenceActivity && inferenceActivity.phase !== 'idle'}
      <div class="usage-activity mono text-[11px]">
        cluster inference <InferenceActivity activity={inferenceActivity} />
      </div>
    {/if}
    {#if view === 'offline'}
      <p class="mono text-[11px]" role="status" style="padding: 6px 10px; color: var(--muted)">
        Disconnected — keeping last cluster view; readings are not live.
      </p>
    {/if}
    <div class="usage-topology-body">
      {#if view === 'loading'}
        <p class="mono" style="padding: 8px 10px">Loading cluster…</p>
      {:else if nodeCount}
        <NodeMap nodes={displayNodes} relationships={displayRelationships}
          selectedId={appStore.selectedNodeId} onselect={(id) => appStore.selectedNodeId = id} />
      {:else if idle}
        <p class="mono" style="padding: 8px 10px">No members — Start as master or Join a cluster.</p>
      {:else}
        <p class="mono" style="padding: 8px 10px">Waiting for measured nodes…</p>
      {/if}
    </div>
  </div>

  <dialog bind:this={inspectorDialog} class="usage-dialog panel"
    aria-label={inspectorMode === 'history' ? 'Recorded usage' : 'Node details'}
    onclose={handleDialogClose}>
    <div class="usage-dialog-head">
      <h2 class="mono uppercase tracking-wider text-xs" style="color: var(--muted)">
        {inspectorMode === 'history' ? 'Recorded usage' : 'Selected node'} · {appStore.selectedNodeId || '—'}
      </h2>
      <button class="btn" aria-label="Close inspector" onclick={closeInspector}>×</button>
    </div>
    <div class="usage-dialog-body">
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
          <h3 class="mono text-xs mt-3">Planning estimate · not measured usage</h3>
          <pre class="mt-2 p-3 panel mono text-[11px] overflow-auto max-h-48">{JSON.stringify(appStore.status.last_plan, null, 2)}</pre>
        {/if}
      {/if}
    </div>
  </dialog>

  <aside id="cluster-chat" class="usage-chat-drawer flex flex-col panel" class:open={chatOpen}
    aria-label="Cluster chat" inert={!chatOpen} style="border-color: var(--border)">
    <div class="px-3 py-2 border-b mono text-[11px] uppercase tracking-wider flex items-center" style="border-color: var(--border); color: var(--muted)">
      Chat
      <span class="ml-auto" style="color: var(--fg)">{appStore.status?.model_name ?? 'no model'}</span>
      <button bind:this={chatClose} class="btn ml-2" aria-label="Close chat" onclick={closeChat}>×</button>
    </div>
    <div class="flex-1 overflow-auto p-3 flex flex-col gap-3">
      {#each appStore.chat as msg}
        <div class="mono text-[12px]">
          <div style="color: var(--muted)">{msg.role}</div>
          <div class="whitespace-pre-wrap" style="color: var(--fg)">{msg.content || '…'}</div>
        </div>
      {:else}
        <p class="mono text-[12px]" style="color: var(--muted)">
          Run a model, then chat via OpenAI-compatible proxy on the agent.
        </p>
      {/each}
    </div>
    <div class="px-3 pt-2 flex flex-wrap gap-2 items-end" style="border-color: var(--border)">
      <div class="flex flex-col gap-0.5">
        <label class="mono text-[10px]" style="color: var(--muted)" for="chat-temp">Temp</label>
        <input
          id="chat-temp"
          class="field mono"
          style="width: 4.5rem"
          type="number"
          min="0"
          max="2"
          step="0.05"
          placeholder={appStore.autoSamplingHint('temperature')}
          value={appStore.temperature ?? ''}
          disabled={!appStore.status?.inference_up || appStore.chatStreaming}
          onchange={(e) => {
            const raw = (e.currentTarget as HTMLInputElement).value.trim();
            appStore.temperature = raw === '' ? appStore.samplingDefault('temperature') : Number(raw);
          }}
        />
      </div>
      <div class="flex flex-col gap-0.5">
        <label class="mono text-[10px]" style="color: var(--muted)" for="chat-think">Thinking</label>
        <select
          id="chat-think"
          class="field mono"
          disabled={!appStore.status?.inference_up || appStore.chatStreaming}
          value={appStore.enableThinking === null ? 'auto' : appStore.enableThinking ? 'on' : 'off'}
          onchange={(e) => {
            const v = (e.currentTarget as HTMLSelectElement).value;
            appStore.enableThinking = v === 'auto' ? null : v === 'on';
          }}
        >
          <option value="auto">{appStore.autoThinkingHint()}</option>
          <option value="on">On</option>
          <option value="off">Off</option>
        </select>
      </div>
    </div>
    <div class="p-3 border-t flex gap-2" style="border-color: var(--border)">
      <input
        class="field mono flex-1"
        placeholder={appStore.status?.inference_up ? 'Message…' : 'Start inference first'}
        bind:value={appStore.chatInput}
        disabled={!appStore.status?.inference_up || appStore.chatStreaming}
        onkeydown={(e) => e.key === 'Enter' && appStore.sendChat()}
      />
      {#if appStore.chatStreaming}
        <button class="btn btn-danger" onclick={() => appStore.stopChat()}>Stop</button>
      {:else}
        <button class="btn btn-accent" disabled={!appStore.status?.inference_up} onclick={() => appStore.sendChat()}
          >Send</button
        >
      {/if}
    </div>
  </aside>
</div>
