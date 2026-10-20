<script lang="ts">
  import { createApp } from '#lib/app.svelte';
  import { routes } from '#lib/shared/navigation';
  import { onMount, onDestroy, tick, untrack } from 'svelte';
  import PrimaDeploymentShell from './PrimaDeploymentShell.svelte';
  import PrimaDeploymentSummary from './PrimaDeploymentSummary.svelte';
  import ErrorMessage from '#lib/shared/ErrorMessage.svelte';
  import { primaPlacementError } from '#lib/modules/runtimes/presenter';
  import DownloadPanel from '#lib/modules/models/view/DownloadPanel.svelte';
  import LoadProgressPanel from '#lib/modules/run/view/LoadProgressPanel.svelte';
  import JinjaEditor from '#lib/modules/run/view/JinjaEditor.svelte';
  import ParameterControl from '#lib/modules/run/view/ParameterControl.svelte';
  import DeploymentPlan from '#lib/modules/run/view/DeploymentPlan.svelte';
  import { formatGib } from '#lib/shared/format-memory';
  import { composeProgressLabel } from '#lib/shared/format-transfer';
  import { MIN_CONTEXT } from '#lib/modules/run/entities';
  import type {
    DeviceAllocation,
    HighsNodeAllocation,
    HighsScheduleReport,
    PlacementStrategyMode,
    PrimaModelSupport,
    PrimaNetworkEdge,
    PrimaPreparationProgress,
    PrimaRankAllocation,
    PrimaTopology
  } from '#lib/modules/run/entities';
  import {
    PARAMETER_LABELS,
    parseArgumentText,
    parsePrimaExtras,
    patchPrimaExtras,
    type PrimaExtrasPatch,
    type PrimaHeadMode,
    type PrimaLoraAdapter,
    type PrimaPoolingMode,
    type PrimaSwaMode
  } from '#lib/modules/run/configuration';
  import { runtimeRunReason } from '#lib/modules/run/runtime-readiness';
  import type { RunInteractor } from '../interactor.svelte';
  import type { RuntimesInteractor } from '#lib/modules/runtimes/interactor.svelte';

  let {
    presenter = createApp().run,
    runtimes = createApp().runtimes
  }: {
    presenter?: RunInteractor;
    runtimes?: RuntimesInteractor;
  } = $props();

  let section = $state('overview');
  let tab = $state('settings');
  let search = $state('');
  let technical = $state(false);
  let showJinjaEditor = $state(false);
  let featureOpen = $state(false);
  let reviewPanel = $state<HTMLElement>();
  let defaultsDialog: HTMLDialogElement;
  let confirmed = $state(false);
  let defaultsSnapshot = $state<{field:string;before:unknown;after:unknown}[]>([]);
  const deployment = $derived(runtimes.deployment);
  const restorePrimaDefaults = () => presenter.useDefaultSettings();
  const revertPrimaSavedSettings = () => presenter.revertSavedSettings();
  const selectPrimaModel = (id: string) => presenter.selectModel(id);
  onMount(() => runtimes.preparation.acquire());
  onDestroy(() => deployment.stop());
  async function openReview() {
    if (!await deployment.openReview()) return;
    confirmed = false;
    tab = 'deployment';
    await tick(); reviewPanel?.focus();
  }
  function showDefaults() {
    if (presenter.settingsLoading) return;
    defaultsSnapshot = presenter.defaultSettingsChanges();
    defaultsDialog.showModal();
  }
  // Approval belongs to one frozen review, never to the next inspected snapshot.
  $effect(() => { void deployment.review; untrack(() => { confirmed = false; }); });
  function backToEditing() {
    deployment.review = null;
    confirmed = false;
    tab = 'settings';
    document.getElementById('prima-tab-settings')?.focus();
  }

  const blocked = $derived(presenter.planRunBlockedReason());
  const runtimeBlocked = $derived(
    runtimeRunReason(presenter.status?.runtimes, presenter.status?.node_id ?? '', presenter.planResult)
  );
  const nodeIds = $derived([
    ...new Set([
      ...(presenter.nodePriority ?? []),
      ...Object.keys(presenter.status?.members ?? {}),
      ...presenter.requiredNodes
    ])
  ]);
  const effective = $derived(presenter.effectiveConfig());
  const allocations = $derived((presenter.planResult?.device_allocations as DeviceAllocation[] | undefined) ?? []);

  // Prima-specific plan structures
  const planTopology = $derived((presenter.planResult?.prima_topology as PrimaTopology | undefined) ?? null);
  const planScheduler = $derived((presenter.planResult?.scheduler as HighsScheduleReport | undefined) ?? null);
  const modelSupport = $derived((presenter.planResult?.model_support as PrimaModelSupport | undefined) ?? null);
  const qualificationErrors = $derived(
    (presenter.planResult?.qualification_errors as string[] | undefined) ?? []
  );
  const networkEdges = $derived(
    (presenter.planResult?.network_edges as PrimaNetworkEdge[] | undefined) ?? []
  );
  const inferenceSupported = $derived(
    presenter.planResult?.inference_supported !== false &&
      qualificationErrors.length === 0 &&
      modelSupport?.supported !== false
  );

  // Prima extra_args structured controls
  const primaExtras = $derived(parsePrimaExtras(presenter.extraArgsText));
  let loraRows = $state<PrimaLoraAdapter[]>([]);
  let lastSyncedExtraArgs = $state<string | null>(null);
  let templateKwargsDraft = $state('');
  let templateKwargsError = $state('');
  let stopDraft = $state('');
  let lastSyncedRevision = -1;

  $effect(() => {
    const raw = presenter.extraArgsText;
    const revision = presenter.editorRevision;
    if (raw !== lastSyncedExtraArgs || revision !== lastSyncedRevision) {
      const parsed = parsePrimaExtras(raw);
      loraRows = parsed.loraAdapters.map((item) => ({ ...item }));
      templateKwargsDraft = parsed.templateKwargsText;
      templateKwargsError = '';
      extrasEditError = '';
      if (revision !== lastSyncedRevision) stopDraft = presenter.stopSequences === null ? '' : JSON.stringify(presenter.stopSequences);
      lastSyncedRevision = revision;
      lastSyncedExtraArgs = raw;
    }
  });

  let extrasEditError = $state('');
  function commitPrimaExtras(patch: PrimaExtrasPatch) {
    presenter.noteRawDraftEdit();
    try {
      const serialized = patchPrimaExtras(presenter.extraArgsText, patch);
      extrasEditError = '';
      lastSyncedExtraArgs = serialized;
      presenter.extraArgsText = serialized;
    } catch (error) { extrasEditError = String(error); }
    presenter.scheduleEstimate();
  }

  function handleSwaChange(mode: PrimaSwaMode) {
    commitPrimaExtras({ swaMode: mode });
  }

  function handleHeadModeChange(mode: PrimaHeadMode) {
    const nextPooling: PrimaPoolingMode =
      mode === 'reranking' ? 'rank' : mode === 'generation' ? '' : primaExtras.pooling;
    commitPrimaExtras({ headMode: mode, pooling: nextPooling });
  }

  function handlePoolingChange(pooling: PrimaPoolingMode) {
    commitPrimaExtras({ pooling });
  }

  function addLoraAdapter() {
    loraRows = [...loraRows, { path: '', scale: '1' }];
    commitPrimaExtras({loraAdapters:loraRows});
  }

  function updateLoraAdapter(index: number, patch: Partial<PrimaLoraAdapter>) {
    loraRows = loraRows.map((row, idx) => (idx === index ? { ...row, ...patch } : row));
    commitPrimaExtras({ loraAdapters: loraRows });
  }

  function removeLoraAdapter(index: number) {
    loraRows = loraRows.filter((_, idx) => idx !== index);
    commitPrimaExtras({ loraAdapters: loraRows });
  }

  function handleTemplateKwargsInput(raw: string) {
    templateKwargsDraft = raw;
    const trimmed = raw.trim();
    if (!trimmed) {
      templateKwargsError = '';
      commitPrimaExtras({ templateKwargsText: '' });
      return;
    }
    try {
      const parsed = JSON.parse(trimmed);
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        throw new Error('Not a JSON object');
      }
      templateKwargsError = '';
      commitPrimaExtras({ templateKwargsText: trimmed });
    } catch {
      templateKwargsError =
        'Enter a JSON object of template keyword arguments (for example {"enable_thinking": true}).';
    }
  }

  const hasUnsupportedLlamaFlags = $derived(
    presenter.vision || presenter.moeOffload !== 'auto'
  );

  function clearUnsupportedLlamaFlags() {
    presenter.vision = false;
    presenter.mmproj = '';
    presenter.moeOffload = 'auto';
    presenter.scheduleEstimate();
  }

  const runtimesLibrary = $derived(runtimes.status?.runtimes ?? null);
  const primaVersions = $derived((runtimesLibrary?.versions ?? []).filter(v => v.version.engine === 'primacpp' && v.build));
  const targetPrimaVersion = $derived(runtimesLibrary?.target?.version.engine === 'primacpp'
    ? runtimesLibrary.target.version : primaVersions[0]?.version ?? null);
  $effect(() => {
    if (presenter.settingsScope !== 'primacpp' || !deployment.runtimeId && targetPrimaVersion?.runtime_id)
      untrack(() => void deployment.setRuntime(targetPrimaVersion?.runtime_id ?? ''));
  });
  $effect(() => {
    const key = deployment.semanticKey();
    const loading = presenter.settingsLoading;
    untrack(() => { if (key && !loading) deployment.schedule(); });
  });
  $effect(() => { const job = runtimes.primaPreparation; untrack(() => runtimes.preparation.observe(job)); });
  const displayTopology = $derived(deployment.fresh ? planTopology : null);
  const displayRanks = $derived((displayTopology?.ranks ?? []) as PrimaRankAllocation[]);
  const displayScheduler = $derived(deployment.fresh ? planScheduler : null);
  const fieldSections: Record<string,string> = {
    model:'overview',context:'overview',context_auto:'overview',parallel_slots:'overview',max_output_tokens:'overview',
    network:'computers',placement_strategy:'computers',required_nodes:'computers',node_shares:'computers',node_priority:'computers',device:'computers',gpu_ids:'computers',vram_percent:'computers',ram_percent:'computers',
    cache_type_k:'memory',cache_type_v:'memory',flash_attention:'memory',swa:'memory',mmap_mlock:'memory',checkpoints:'memory',cache_ram:'memory',batch_size:'memory',micro_batch:'memory',threads:'memory',threads_batch:'memory',
    saved_settings:'expert',extra_args:'expert',template_kwargs:'features',lora:'features',chat_template:'features',draft_model:'features',speculative:'features'
  };
  const fieldIds: Record<string,string> = { saved_settings:'prima-unrepresented', cache_type_k:'run-cache-k',cache_type_v:'run-cache-v',flash_attention:'run-flash',template_kwargs:'run-prima-template-kwargs',placement_strategy:'run-placement-mode',required_nodes:'prima-computers',node_shares:'prima-manual-shares',node_priority:'run-placement-mode',swa:'run-prima-swa',chat_template:'run-template',draft_model:'run-draft',lora:'prima-lora-add' };
  function fieldSection(field:string) { return fieldSections[field] ?? 'features'; }
  const issues = $derived(Object.entries({...presenter.fieldErrors,...presenter.localDraftErrors}).map(([field,message]) => ({field,message,section:fieldSection(field)})));
  const searchItems = $derived([
    ...Object.entries(PARAMETER_LABELS).filter(([field]) => field !== 'moe_offload' && presenter.parameterDescriptor(field)?.available !== false).map(([field,label]) => ({field,label:label+' '+(presenter.parameterDescriptor(field)?.cli ?? '--'+field.replaceAll('_','-'))})),
    {field:'template_kwargs',label:'Template keywords --chat-template-kwargs'}, {field:'swa',label:'Sliding-window cache --swa-full --swa-compact'},
    {field:'lora',label:'LoRA adapters --lora --lora-scaled'}, {field:'model',label:'Model --model'}, {field:'network',label:'Network --network-id'},
    {field:'extra_args',label:'Extra arguments JSON argv --extra-arg'}, {field:'context',label:'Total context --ctx-size'},
    {field:'head_mode',label:'Head mode --embedding --rerank'}, {field:'pooling',label:'Pooling --pooling'}
  ]);
  const matches = $derived(presenter.settingsLoading || presenter.busy || presenter.status?.role !== 'master' ? [] : searchItems.filter(item=>item.label.toLowerCase().includes(search.toLowerCase().trim())));
  async function focusField(field:string) {
    tab = 'settings';
    section=fieldSection(field); technical=true; featureOpen=true;
    await tick();
    let element=document.getElementById(fieldIds[field] ?? (field==='head_mode' ? 'run-prima-head-mode' : field==='pooling' ? 'run-prima-pooling' : 'run-'+field.replaceAll('_','-')));
    element ??= document.getElementById('run-'+field.replaceAll('_','-')+'-mode');
    if (field==='context_auto' || field==='context' && presenter.contextAuto) element=document.getElementById('run-context-auto');
    if (!element && ['draft_max','draft_model'].includes(field)) element=document.getElementById('run-speculative');
    if (!element && field==='pooling') element=document.getElementById('run-prima-head-mode');
    for (let parent=element?.parentElement;parent;parent=parent.parentElement) if(parent instanceof HTMLDetailsElement) parent.open=true;
    element?.focus();
  }
  $effect(() => {
    const errors: Record<string,string> = {};
    if (templateKwargsError) errors.template_kwargs=templateKwargsError;
    if (extrasEditError || primaExtras.parseError) errors.extra_args=extrasEditError || primaExtras.parseError!;
    untrack(() => { presenter.localDraftErrors=errors; presenter.validateRunSettings(); deployment.changed(); });
  });

  function gpuNamesFor(node: string): string[] {
    const member = presenter.status?.members[node];
    const fromMember = (member?.devices ?? [])
      .map((device) => device.name)
      .filter((name): name is string => Boolean(name));
    if (fromMember.length) return fromMember;
    const planned = (presenter.planResult?.node_gpu_names as Record<string, unknown> | undefined)?.[node];
    return Array.isArray(planned)
      ? planned.filter((name): name is string => typeof name === 'string' && Boolean(name))
      : [];
  }

  function rankForNode(node: string): PrimaRankAllocation | undefined {
    return displayRanks.find((r) => r.node_id === node);
  }

  function haldaForNode(node: string): HighsNodeAllocation | undefined {
    return displayScheduler?.node_allocations?.find((a) => a.node_id === node);
  }

  function formatLayerRanges(rank: PrimaRankAllocation | undefined): string {
    if (!rank || !rank.layers?.length) return 'Coordinator';
    return rank.layers.map((r) => `${r.start}–${r.end - 1}`).join(', ');
  }

  const changes = $derived(
    Object.entries(effective?.requested ?? {}).filter(
      ([key, value]) => JSON.stringify(value) !== JSON.stringify(presenter.savedModelConfig?.[key])
    )
  );
  const cliExport = $derived.by(() => {
    try {
      return presenter.configurationCommand();
    } catch {
      return 'Fix argument syntax to export this configuration.';
    }
  });
  const jsonExport = $derived.by(() => {
    try {
      const body = presenter.networkPlanPayload();
      delete body.remember;
      delete body.advanced_open;
      return JSON.stringify(body, null, 2);
    } catch {
      return '';
    }
  });

  function display(value: unknown): string {
    if (value === null || value === undefined) return 'Inherit default';
    if (Array.isArray(value)) return value.length ? JSON.stringify(value) : 'None';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  function layerCount(node: string): string {
    const rank = rankForNode(node);
    if (rank?.layers?.length) {
      return String(rank.layers.reduce((sum, r) => sum + Math.max(0, r.end - r.start), 0));
    }
    const halda = haldaForNode(node);
    if (halda && Number.isFinite(halda.total_layers)) {
      return String(halda.total_layers);
    }
    const assigned = allocations.filter((item) => item.node_id === node);
    if (assigned.some((item) => !Number.isFinite(item.layers))) return '—';
    const cpu = node === presenter.status?.node_id ? Number(presenter.planResult?.cpu_layers ?? NaN) : 0;
    if (!assigned.length && !Number.isFinite(cpu)) return '—';
    return String(
      assigned.reduce(
        (sum, item) => sum + item.layers - (item.output_layer ? 1 : 0),
        Number.isFinite(cpu) ? cpu : 0
      )
    );
  }
</script>

<div class="stack deploy-page prima-deploy-page">
  <header class="page-heading">
    <div>
      <p class="eyebrow">Deploy · Prima.cpp</p>
      <h1 id="page-title">Deploy a model</h1>
      <p>Configure a draft and review the deployment before running.</p>
    </div>
    <div class="prima-header-badges">
      <span class="chip chip-info">Prima engine</span>
      <span class="chip mono">prima-ring-tcp</span>
      {#if targetPrimaVersion}
        <span class="chip mono">{targetPrimaVersion.name || targetPrimaVersion.commit?.slice(0, 10) || targetPrimaVersion.runtime_id}</span>
      {/if}
      <a class="text-button" href={routes.settingsLlama}>Prima runtime settings →</a>
    </div>
  </header>

  {#if deployment.lifecycleReason}
    <section class="panel running-model" aria-label="Running model">
      <div>
        <span class="tag">{presenter.status?.inference_up ? 'Running' : 'Starting or stopping'}</span>
        <strong>{presenter.status?.model_name ?? 'Loading model'}</strong>
        <p class="small muted">{deployment.lifecycleReason}</p>
      </div>
      <button
        class="btn btn-danger"
        disabled={!!presenter.busy || presenter.status?.role !== 'master'}
        onclick={() => presenter.stopInference()}>Stop model</button>
    </section>
  {/if}

  <DownloadPanel />
  {#if presenter.capabilitiesError}<p class="notice" role="status">{presenter.capabilitiesError}</p>{/if}
  {#if presenter.settingsLoadError}<div class="notice" role="alert">{presenter.settingsLoadError}<button class="btn" onclick={() => selectPrimaModel(presenter.selectedModel)}>Retry settings</button></div>{/if}
  {#if presenter.status?.apply_error}<ErrorMessage source="apply" message={presenter.status.apply_error} />{/if}
  <div class="deploy-progress-slot"><LoadProgressPanel {presenter} /></div>
  <PrimaDeploymentShell bind:tab bind:section bind:search bind:technical dirty={presenter.settingsDirty()} {issues} {matches} focus={focusField}>
    {#snippet children()}
      <fieldset disabled={presenter.status?.role !== 'master' || presenter.settingsLoading}
        oninput={() => presenter.noteRawDraftEdit()} onchange={() => presenter.noteRawDraftEdit()}>
      <div hidden={section !== 'overview'} data-section="overview">
          <div class="deploy-section">
            <p class="eyebrow">Model &amp; architecture</p>
            <h2>Choose a model</h2>
            <label class="field" for="run-model">Model on the master
              <select
                id="run-model"
                class="field"
                value={presenter.selectedModel}
                disabled={!!presenter.busy}
                onchange={(e) => selectPrimaModel(e.currentTarget.value)}
              >
                {#each presenter.models as model}<option value={model.id}>{model.id}</option>{/each}
              </select>
            </label>
            <div class="actions">
              <a class="text-button" href={routes.models}>Browse models →</a>
              {#if displayTopology?.total_layers}
                <span class="small muted mono">{displayTopology.total_layers} transformer layers · {displayRanks.length || nodeIds.length} participant rank(s)</span>
              {/if}
            </div>
            {#if hasUnsupportedLlamaFlags}
              <div class="notice small warning-box" role="status">
                <span>Saved settings for this model enable llama.cpp-only features ({presenter.vision ? 'vision projector' : ''}{presenter.vision && presenter.moeOffload !== 'auto' ? ', ' : ''}{presenter.moeOffload !== 'auto' ? `RPC MoE offload (${presenter.moeOffload})` : ''}) that Prima rejects.</span>
                <button type="button" class="btn" onclick={clearUnsupportedLlamaFlags}>Clear unsupported llama.cpp flags</button>
              </div>
            {/if}
          </div>

        <div class="deploy-section">
          <label class="field" for="prima-deploy-runtime">Preview runtime (does not change the cluster target)
            <select id="prima-deploy-runtime" class="field" value={deployment.runtimeId} onchange={e => deployment.setRuntime(e.currentTarget.value)}>
              <option value="">Choose an installed Prima runtime</option>
              {#each primaVersions as entry}<option value={entry.version.runtime_id}>{entry.version.name || entry.version.commit?.slice(0,12) || entry.version.runtime_id}</option>{/each}
            </select>
          </label>
          <p class="small muted mono">{deployment.runtimeId}</p>
          {#if presenter.legacyModelConfig}<details><summary>Unscoped legacy settings (not imported)</summary><p>These settings may belong to another engine. They have not been applied or migrated. Compare them with this draft before manually choosing any values.</p><pre>{JSON.stringify(presenter.legacyModelConfig,null,2)}</pre></details>{/if}
        </div>
          <div class="deploy-section">
            <p class="eyebrow">Next workload</p>
            <h2>Context and concurrency</h2>
            <p class="small muted">
              Prima-native parameters and launch extras. Rank 0 reserves <code>parallel_slots + 1</code> KV sequences for pipelined ring micro-batches.
            </p>
            <div class="field-grid">
              <div class="parameter-control">
                <label class="field" for="run-context">Total context capacity
                  <input
                    id="run-context"
                    aria-invalid={!!presenter.fieldErrors.context}
                    aria-describedby="prima-context-help"
                    class="field mono"
                    type="number"
                    bind:value={presenter.context}
                    min={MIN_CONTEXT}
                    max={presenter.selectedModelMaxContext()}
                    step="1"
                    disabled={presenter.contextAuto}
                    oninput={() => presenter.scheduleEstimate()}
                  />
                </label>
                <label class="check-row">
                  <input
                    type="checkbox"
                    id="run-context-auto"
                    bind:checked={presenter.contextAuto}
                    onchange={() => presenter.scheduleEstimate()}
                  />
                  Plan context automatically
                </label>
                <p class="small muted">
                  Total KV capacity shared by concurrent requests. Model maximum: {presenter.selectedModelMaxContext().toLocaleString()} tokens.
                </p>
                {#if presenter.fieldErrors.context}
                  <ErrorMessage source="validation:context" message={presenter.fieldErrors.context} snapshot={false} />
                {/if}
              </div>
              <ParameterControl {presenter} name="parallel_slots" />
              <ParameterControl {presenter} name="max_output_tokens" />

            </div>
          </div>

      </div>
      <div hidden={section !== 'computers'} data-section="computers" id="prima-computers" tabindex="-1">
          <div class="deploy-section">
            <p class="eyebrow">Placement constraints</p>
            <h2>Choose participating computers</h2>
            <div class="field-grid">
              <label class="field" for="run-network">Inference network
                <select
                  id="run-network"
                  class="field"
                  bind:value={presenter.selectedNetworkId}
                  onchange={() => presenter.scheduleEstimate()}
                >
                  <option value="">LAN / WireGuard</option>
                  {#each presenter.status?.managed_networks ?? [] as network}
                    <option value={network.network_id}>{network.subnet} · {network.observed_state}</option>
                  {/each}
                </select>
              </label>
              <label class="field" for="run-placement-mode">Placement
                <select
                  id="run-placement-mode"
                  class="field"
                  value={presenter.placementStrategy}
                  onchange={(e) => presenter.setPlacementStrategy(e.currentTarget.value as PlacementStrategyMode)}
                >
                  <option value="auto">Automatic (HiGHS / Profiled)</option>
                  <option value="capacity_first">Capacity-first</option>
                  <option value="priority">Priority</option>
                </select>
              </label>
            </div>
            <p class="small muted">
              Automatic placement solves the HiGHS Halda MILP across cycle counts <em>k</em> and memory pressure sets <code>M1</code>–<code>M4</code> when device profiles exist, falling back to capacity-first. Checked nodes must host at least one layer window.
            </p>
            {#if !presenter.nodePriority}
              <label class="check-row">
                <input
                  type="checkbox"
                  bind:checked={presenter.justEnoughMaster}
                  onchange={() => presenter.scheduleEstimate()}
                />
                Just Enough Master Allocation
              </label>
              <p class="small muted">
                Assign worker GPU capacity first and keep only the remaining layer window on Rank 0 (master coordinator).
              </p>
            {/if}
            {#if presenter.runGpuIds !== null}
              <p class="small">
                Saved local GPU selection: {presenter.runGpuIds.join(', ') || 'CPU only'}.
                <button class="text-button" onclick={() => presenter.setParameter('gpu_ids', null)}>
                  Use node selection
                </button>
              </p>
            {/if}

            {#each nodeIds as node, index (node)}
              {@const member = presenter.status?.members[node]}
              {@const gpuNames = gpuNamesFor(node)}
              {@const rank = rankForNode(node)}
              {@const halda = haldaForNode(node)}
              <div class="node-option">
                <div class="node-option-head">
                  <label for={'required-' + node}>
                    <input
                      id={'required-' + node}
                      type="checkbox"
                      checked={presenter.requiredNodes.includes(node)}
                      disabled={!member && !presenter.requiredNodes.includes(node)}
                      onchange={(e) => presenter.setNodeRequired(node, e.currentTarget.checked)}
                    />
                    Must use this node · {member?.role === 'master' || node === presenter.status?.node_id ? 'Master' : 'Worker'} · {node}{gpuNames.length ? ` · ${gpuNames.join(', ')}` : ''}{!member ? ' · unavailable' : ''}
                  </label>
                  {#if rank}
                    <span class="tag mono">
                      Rank {rank.rank} → {rank.next_node_id} · -lw {formatLayerRanges(rank)} · -ngl {rank.ngl}
                    </span>
                  {:else if halda}
                    <span class="tag mono">Rank {halda.rank}</span>
                  {/if}
                </div>

                {#if presenter.nodePriority}
                  <div class="actions">
                    <span class="small">Priority {index + 1}</span>
                    <button
                      class="btn"
                      aria-label={'Move ' + node + ' up'}
                      disabled={index === 0}
                      onclick={() => presenter.moveNodePriority(node, -1)}>↑</button>
                    <button
                      class="btn"
                      aria-label={'Move ' + node + ' down'}
                      disabled={index === nodeIds.length - 1}
                      onclick={() => presenter.moveNodePriority(node, 1)}>↓</button>
                    {#if !member}
                      <button
                        class="btn"
                        aria-label={'Remove unavailable ' + node}
                        onclick={() => presenter.removeNodePriority(node)}>Remove</button>
                    {/if}
                  </div>
                {/if}

                {#if presenter.planResult}
                  <p class="small muted">
                    GPU: {formatGib(Number((presenter.planResult.node_vram_alloc_bytes as Record<string, number> | undefined)?.[node]))}
                    / {formatGib(Number((presenter.planResult.node_gpu_budget_bytes as Record<string, number> | undefined)?.[node]))} usable
                    · CPU RAM: {formatGib(Number((presenter.planResult.node_ram_alloc_bytes as Record<string, number> | undefined)?.[node]))}
                    / {formatGib(Number((presenter.planResult.node_ram_budget_bytes as Record<string, number> | undefined)?.[node]))} usable
                    · {layerCount(node)} model layers
                    {member?.unified_memory ? ' · shared RAM/GPU pool' : ''}
                  </p>
                {/if}

                {#if halda}
                  <p class="small mono muted">
                    Halda: w={halda.w_m}, n={halda.n_m} (CPU/cycle={halda.cpu_layers_per_cycle}) · set <span class="tag">{halda.memory_pressure_set}</span>{halda.forced_m4 ? ' (forced M4)' : ''} · graphs={halda.prima_graphs} · step {halda.total_step_ms.toFixed(2)} ms (compute {halda.compute_ms.toFixed(2)} / reload {halda.disk_reload_ms.toFixed(2)} / net {halda.network_ms.toFixed(2)})
                  </p>
                {/if}
              </div>
            {/each}

            {#if displayScheduler?.pruned_nodes?.length}
              <div class="notice small" role="status">
                <strong>Pruned weak workers:</strong>
                {displayScheduler.pruned_nodes
                  .map((p) => `${p.node_id} (${p.reason}${p.detail ? `: ${p.detail}` : ''})`)
                  .join(' · ')}
              </div>
            {/if}
          </div>
        <div class="deploy-section field-grid">{#each ['device','vram_percent','ram_percent'] as name}<ParameterControl {presenter} {name} />{/each}</div>
        {#if presenter.nodeShares}
          <div class="deploy-section"><h3>Manual-share compatibility settings</h3><p>Saved shares are retained exactly. They have not been converted to priorities. Choose an explicit placement strategy to replace them.</p><pre id="prima-manual-shares" tabindex="-1">{JSON.stringify(presenter.nodeShares,null,2)}</pre></div>
        {/if}
        <p class="section-note">Unchecked computers remain eligible. Required computers must receive a layer. Hardware safety limits remain authoritative.</p>
      </div>
      <div hidden={section !== 'memory'} data-section="memory">
          <details class="advanced" open>
            <summary>Memory &amp; loading (SWA, KV precision &amp; mmap)</summary>
            <div class="advanced-body">
              <p class="small muted">
                Node safety caps: local VRAM {presenter.vramPercent}%, RAM {presenter.ramPercent}%.
                <a class="text-button" href={routes.settings}>Node settings →</a>
              </p>
              <div class="field-grid">
                {#each ['cache_type_k', 'cache_type_v', 'flash_attention', 'mmap_mlock', 'checkpoints', 'cache_ram'] as name}
                  <ParameterControl {presenter} {name} />
                {/each}
                <div class="parameter-control">
                  <label class="field" for="run-prima-swa">Sliding-Window Attention (SWA) KV layout
                    <select
                      id="run-prima-swa"
                      class="field"
                      value={primaExtras.swaMode}
                      onchange={(e) => handleSwaChange(e.currentTarget.value as PrimaSwaMode)}
                    >
                      <option value="compact">Compact SWA (--swa-compact, default)</option>
                      <option value="full">Full SWA (--swa-full)</option>
                    </select>
                  </label>
                  <p class="small muted">
                    Compact SWA trims sliding-window KV buffers on supported hybrid/sliding-window models; Full SWA allocates the full context window across all layers.
                  </p>
                </div>
              </div>
              <p class="small muted">
                Prima note: Vulkan supports GPU-resident KV caches across all 9 precisions (<code>f32</code>, <code>f16</code>, <code>bf16</code>, <code>q8_0</code>, <code>q4_0</code>, <code>q4_1</code>, <code>q5_0</code>, <code>q5_1</code>, <code>iq4_nl</code>) with Flash Attention; quantized V caches require Flash Attention <code>on</code>. Setting <strong>Model loading</strong> to <code>none</code> (<code>--no-mmap</code>) forces Halda memory pressure set <code>M4</code> with zero disk-reload latency.
              </p>
            </div>
          </details>

          <details class="advanced">
            <summary>Capacity &amp; performance</summary>
            <div class="advanced-body field-grid">
              {#each ['batch_size', 'micro_batch', 'threads', 'threads_batch'] as name}
                <ParameterControl {presenter} {name} />
              {/each}
            </div>
          </details>

      </div>
      <div hidden={section !== 'features'} data-section="features">
          <details class="advanced" open>
            <summary>Generation defaults</summary>
            <div class="advanced-body"><ParameterControl {presenter} name="enable_thinking" /></div>
            <div class="advanced-body">
              <p class="small muted">
                Defaults for requests to the next deployed model. Clients and chat sessions can override them.
              </p>
              <div class="field-grid">
                {#each ['temperature', 'top_p', 'top_k', 'min_p', 'presence_penalty', 'repeat_penalty', 'frequency_penalty', 'seed'] as name}
                  <ParameterControl {presenter} {name} />
                {/each}
              </div>
              <label class="field" for="run-stop">Stop sequences (JSON array)
                <textarea
                  id="run-stop"
                  aria-invalid={!!presenter.stopParseError}
                  class="field mono"
                  value={stopDraft}
                  placeholder={'["END", "User:"]'}
                  oninput={(e) => {
                    stopDraft = e.currentTarget.value;
                    try {
                      const value = e.currentTarget.value.trim() ? JSON.parse(e.currentTarget.value) : null;
                      if (value !== null && (!Array.isArray(value) || value.some((item) => typeof item !== 'string' || !item))) {
                        throw new Error();
                      }
                      presenter.stopSequences = value;
                      presenter.stopParseError = '';
                      presenter.scheduleEstimate();
                    } catch {
                      presenter.stopParseError = 'Enter a JSON array of nonempty literal strings.';
                      presenter.validateRunSettings(true);
                    }
                  }}
                ></textarea>
              </label>
              <p class="small muted">Empty inherits defaults; [] clears stop sequences.</p>
            </div>
          </details>

          <details class="advanced" bind:open={featureOpen}>
            <summary>Prima server mode, LoRA adapters &amp; templates</summary>
            <div class="advanced-body">
              <div class="field-grid">
                {#each ['reasoning_effort', 'reasoning_budget', 'reasoning_format'] as name}
                  <ParameterControl {presenter} {name} />
                {/each}
                <div class="parameter-control">
                  <label class="field" for="run-prima-head-mode">Head server mode
                    <select
                      id="run-prima-head-mode"
                      class="field"
                      value={primaExtras.headMode}
                      onchange={(e) => handleHeadModeChange(e.currentTarget.value as PrimaHeadMode)}
                    >
                      <option value="generation">Text &amp; chat generation (default)</option>
                      <option value="embedding">Embeddings (--embedding)</option>
                      <option value="reranking">Reranking (--rerank)</option>
                    </select>
                  </label>
                  <p class="small muted">
                    Embeddings and reranking run on Rank 0 head output and are mutually exclusive.
                  </p>
                </div>
              </div>

              {#if primaExtras.headMode === 'embedding' || primaExtras.pooling}
                <label class="field" for="run-prima-pooling">Embedding pooling mode (--pooling)
                  <select
                    id="run-prima-pooling"
                    class="field"
                    value={primaExtras.pooling}
                    onchange={(e) => handlePoolingChange(e.currentTarget.value as PrimaPoolingMode)}
                  >
                    <option value="">Model / engine default</option>
                    <option value="none">none</option>
                    <option value="mean">mean</option>
                    <option value="cls">cls</option>
                    <option value="last">last</option>
                    <option value="rank">rank</option>
                  </select>
                </label>
              {/if}

              <label class="field" for="run-speculative">Speculative decoding (Rank 0 CPU draft-simple)
                <select
                  id="run-speculative"
                  class="field"
                  bind:value={presenter.speculative}
                  onchange={() => presenter.scheduleEstimate()}
                >
                  <option value="auto">Model / engine default</option>
                  <option value="off">Off</option>
                  <option value="draft">Draft model (CPU draft-simple)</option>
                </select>
              </label>
              {#if presenter.speculative === 'draft' || presenter.draftModel}
                <label class="field" for="run-draft">Draft model path (-md)
                  <input
                    id="run-draft"
                    class="field mono"
                    bind:value={presenter.draftModel}
                    oninput={() => presenter.scheduleEstimate()}
                    placeholder="/path/to/draft-model.gguf"
                  />
                </label>
                <ParameterControl {presenter} name="draft_max" />
                <p class="small muted">
                  Prima executes draft-simple speculative decoding on CPU (`-ngld 0`) and stages the draft GGUF across prepared ranks.
                </p>
              {/if}

              <div class="lora-section">
                <div class="od-row">
                  <span class="field-title">Managed LoRA adapters (<code>--lora</code> / <code>--lora-scaled</code>)</span>
                  <button type="button" class="btn" id="prima-lora-add" onclick={addLoraAdapter}>+ Add LoRA adapter</button>
                </div>
                <p class="small muted">
                  LoRA adapter GGUFs are digest-verified and distributed across all Ring-TCP ranks during Prima preparation.
                </p>
                {#if loraRows.length > 0}
                  <div class="stack-sm">
                    {#each loraRows as row, idx (idx)}
                      <div class="lora-row">
                        <input
                          class="field mono lora-path"
                          type="text"
                          aria-label={`LoRA adapter ${idx + 1} path`}
                          placeholder="/path/to/adapter.gguf"
                          value={row.path}
                          oninput={(e) => updateLoraAdapter(idx, { path: e.currentTarget.value })}
                        />
                        <input
                          class="field mono lora-scale"
                          type="number"
                          step="0.05"
                          aria-label={`LoRA adapter ${idx + 1} scale`}
                          placeholder="1.0"
                          value={row.scale}
                          oninput={(e) => updateLoraAdapter(idx, { scale: e.currentTarget.value })}
                        />
                        <button
                          type="button"
                          class="btn"
                          aria-label={`Remove LoRA adapter ${idx + 1}`}
                          onclick={() => removeLoraAdapter(idx)}>Remove</button>
                      </div>
                    {/each}
                  </div>
                {/if}
              </div>

              <label class="field" for="run-template">Chat template
                <select
                  id="run-template"
                  class="field"
                  bind:value={presenter.chatTemplate}
                  onchange={() => {
                    if (presenter.chatTemplate !== 'custom') presenter.chatTemplateText = '';
                    presenter.scheduleEstimate();
                  }}
                >
                  <option value="default">{presenter.templateEmbedded ? 'Embedded model template' : 'Engine fallback'}</option>
                  {#each presenter.templatePresets as template}
                    <option value={template.id}>{template.label}</option>
                  {/each}
                  <option value="custom">Custom Jinja</option>
                </select>
              </label>
              {#if presenter.chatTemplate === 'custom'}
                <button class="btn" onclick={() => (showJinjaEditor = true)}>Edit Jinja template</button>
              {/if}

              <label class="field" for="run-prima-template-kwargs">
                Template keyword defaults (<code>--chat-template-kwargs</code> JSON object)
                <input
                  id="run-prima-template-kwargs"
                  class="field mono"
                  type="text"
                  aria-invalid={!!templateKwargsError}
                  aria-describedby={templateKwargsError ? "prima-kwargs-error" : undefined}
                  value={templateKwargsDraft}
                  placeholder={'{"enable_thinking": true}'}
                  oninput={(e) => handleTemplateKwargsInput(e.currentTarget.value)}
                />
              </label>
              {#if templateKwargsError}
                <p id="prima-kwargs-error" role="alert">{templateKwargsError}</p>
              {/if}
            </div>
          </details>

      </div>
      <div hidden={section !== 'expert'} data-section="expert">
        {#if Object.keys(presenter.unrepresentedSettings).length}<div class="deploy-section"><h3>Unrepresented saved intent</h3><pre id="prima-unrepresented" tabindex="-1">{JSON.stringify(presenter.unrepresentedSettings,null,2)}</pre><p>These values have not been discarded or forwarded. Explicitly restore defaults to remove them.</p></div>{/if}
          <details class="advanced" open>
            <summary>Declared Prima launch extras (JSON argv)</summary>
            <div class="advanced-body">
              <p class="small muted">
                Prima validates <code>extra_args</code> strictly against declared launch flags (<code>--swa-full</code>, <code>--swa-compact</code>, <code>--embedding</code>, <code>--rerank</code>, <code>--pooling</code>, <code>--lora</code>, <code>--lora-scaled</code>, <code>--chat-template-kwargs</code>).
              </p>
              <label class="field" for="run-extra-args">Extra arguments
                <textarea
                  id="run-extra-args"
                  aria-invalid={!!presenter.fieldErrors.extra_args}
                  class="field mono"
                  value={presenter.extraArgsText}
                  placeholder={'["--swa-full"]'}
                  oninput={(e) => {
                    presenter.extraArgsText = e.currentTarget.value;
                    lastSyncedExtraArgs = e.currentTarget.value;
                    try {
                      const parsed = parsePrimaExtras(e.currentTarget.value);
                      loraRows = parsed.loraAdapters.map((item) => ({ ...item }));
                      templateKwargsDraft = parsed.templateKwargsText;
                      templateKwargsError = ''; extrasEditError = '';
                      parseArgumentText(e.currentTarget.value);
                      presenter.scheduleEstimate();
                    } catch {
                      presenter.validateRunSettings(true);
                    }
                  }}
                ></textarea>
              </label>
              {#if presenter.fieldErrors.extra_args}
                <ErrorMessage source="validation:extra_args" message={presenter.fieldErrors.extra_args} snapshot={false} />
              {/if}
            </div>
          </details>

            <details class="advanced">
              <summary>Export configuration</summary>
              <div class="advanced-body">
                <div class="actions">
                  <button class="btn" onclick={() => presenter.copyConfiguration('cli')}>Copy CLI</button>
                  <button class="btn" onclick={() => presenter.copyConfiguration('json')}>Copy JSON</button>
                </div>
                <label class="field">CLI command<textarea class="field mono" readonly value={cliExport}></textarea></label>
                <label class="field">API JSON<textarea class="field mono" readonly value={jsonExport}></textarea></label>
              </div>
            </details>
        <details class="advanced"><summary>Installed flag catalog</summary><div class="advanced-body"><pre>{JSON.stringify(presenter.expertFlags,null,2)}</pre></div></details>
        <div hidden={!technical}>
            {#if displayRanks.length > 0}
              <h3>Ring-TCP rank topology &amp; Halda schedule</h3>
              <p class="small muted">
                {#if displayTopology}
                  Master coordinator: <code>{displayTopology.master_node_id}</code> · Data port <code>{displayTopology.data_port}</code> · Signal port <code>{displayTopology.signal_port}</code> · {displayTopology.total_layers} layers
                {/if}
              </p>
              <div class="review-table" role="region" aria-label="Prima rank placement">
                <table>
                  <thead>
                    <tr>
                      <th>Rank</th>
                      <th>Node</th>
                      <th>Layers (-lw)</th>
                      <th>Devices</th>
                      <th>-ngl</th>
                      <th>Tensor split</th>
                      <th>Halda (w, n / set / graphs)</th>
                      <th>Next hop</th>
                    </tr>
                  </thead>
                  <tbody>
                    {#each displayRanks as rank (rank.rank)}
                      {@const halda = displayScheduler?.node_allocations?.find((a) => a.node_id === rank.node_id)}
                      <tr>
                        <td class="mono">#{rank.rank} ({rank.role})</td>
                        <td class="mono">{rank.node_id}</td>
                        <td class="mono">{formatLayerRanges(rank)}</td>
                        <td class="mono">{rank.devices?.join(', ') || 'CPU'}</td>
                        <td class="mono">{rank.ngl}</td>
                        <td class="mono">{rank.tensor_split?.join(', ') || '—'}</td>
                        <td class="mono">
                          {#if halda}
                            w={halda.w_m}, n={halda.n_m} · {halda.memory_pressure_set}{halda.forced_m4 ? ' (forced M4)' : ''} · graphs={halda.prima_graphs} · {halda.total_step_ms.toFixed(1)} ms
                          {:else}
                            —
                          {/if}
                        </td>
                        <td class="mono">{rank.next_node_id}</td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
            {:else if allocations.length}
              <h3>Device allocation</h3>
              <div class="review-table">
                <table>
                  <thead>
                    <tr>
                      <th>Node / device</th>
                      <th>Layers</th>
                      <th>Estimated / usable budget</th>
                    </tr>
                  </thead>
                  <tbody>
                    {#each allocations as allocation}
                      <tr>
                        <td>{allocation.node_id} / {allocation.device ?? allocation.backend}</td>
                        <td>{allocation.layer_start}–{allocation.layer_end - 1}</td>
                        <td>{formatGib(allocation.estimated_bytes)} / {formatGib(allocation.budget_bytes)}</td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
            {/if}

            {#if displayScheduler?.evaluated_cycles?.length}
              <details class="advanced">
                <summary>Evaluated Halda cycle candidates ({displayScheduler.evaluated_cycles.length})</summary>
                <div class="advanced-body review-table">
                  <table>
                    <thead>
                      <tr>
                        <th>Cycle count (k)</th>
                        <th>Window size (W)</th>
                        <th>Feasible</th>
                        <th>Objective step time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {#each displayScheduler.evaluated_cycles as cand (cand.cycle_count_k)}
                        <tr class:changed={cand.cycle_count_k === displayScheduler.cycle_count_k}>
                          <td class="mono">k={cand.cycle_count_k}</td>
                          <td class="mono">W={cand.window_size_w}</td>
                          <td>{cand.feasible ? 'Yes' : 'No'}</td>
                          <td class="mono">
                            {cand.objective_step_ms != null ? `${cand.objective_step_ms.toFixed(2)} ms` : '—'}
                          </td>
                        </tr>
                      {/each}
                    </tbody>
                  </table>
                </div>
              </details>
            {/if}

            {#if networkEdges.length > 0 || qualificationErrors.length > 0}
              <details class="advanced">
                <summary>Ring network edges &amp; qualification gates</summary>
                <div class="advanced-body">
                  <pre class="mono small">{JSON.stringify({ qualification_errors: qualificationErrors, network_edges: networkEdges }, null, 2)}</pre>
                </div>
              </details>
            {/if}

            {#if effective}
              <h3>Changes from saved configuration</h3>
              <p class="small muted">
                {changes.length} changed setting(s). Resolved values include automatic adjustments.
              </p>
              <div class="review-table">
                <table>
                  <thead>
                    <tr>
                      <th>Setting</th>
                      <th>Requested</th>
                      <th>Effective</th>
                      <th>Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {#each Object.entries(effective.requested).filter(([key]) => key !== 'extra_args') as [key, value]}
                      <tr class:changed={changes.some(([name]) => name === key)}>
                        <td>{PARAMETER_LABELS[key] ?? key.replaceAll('_', ' ')}</td>
                        <td>{display(value)}</td>
                        <td>
                          {display(effective.values[key])}
                          {#if effective.adjustments[key]}<small>{effective.adjustments[key]}</small>{/if}
                        </td>
                        <td>{effective.sources[key] ?? 'default'}</td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
            {:else}
              <p class="notice small">Refresh the estimate to review effective values.</p>
            {/if}

        </div>
      </div>
      </fieldset>
    {/snippet}
    {#snippet nextDeployment()}
      <PrimaDeploymentSummary {deployment} {presenter} onReview={openReview} />
      {#if issues.length}<div class="notice" role="alert"><strong>Check deployment settings</strong><ul>{#each issues as issue}<li><button class="text-button" onclick={() => focusField(issue.field)}>{issue.message}</button></li>{/each}</ul></div>{/if}
      {#if deployment.review}
        <section class="panel inline-review" bind:this={reviewPanel} tabindex="-1" aria-labelledby="prima-review-title">
          <h2 id="prima-review-title">Review this exact deployment</h2>
          <p>Model: {String(deployment.review.settings.model)}</p>
          <p class="mono">Runtime: {deployment.review.runtimeId}</p>
          <p>Only this reviewed snapshot will be submitted. Stop any existing run explicitly before deploying.</p>
          <h3>Warnings and qualification</h3>
          {#each [...(deployment.review.plan.estimate_warnings ?? []), ...(deployment.review.plan.qualification_errors ?? [])] as warning}<p>{typeof warning === 'string' ? warning : JSON.stringify(warning)}</p>{/each}
          {#if deployment.review.plan.hint}<p>{deployment.review.plan.hint}</p>{/if}
          <h3>Requested and effective settings</h3>
          <div class="review-table"><table><thead><tr><th>Setting</th><th>Requested</th><th>Effective</th><th>Source / adjustment</th></tr></thead><tbody>
            {#each Object.entries(deployment.review.settings).filter(([key]) => !['settings_source','remember','advanced_open'].includes(key)) as [key,value]}
              <tr><th>{PARAMETER_LABELS[key] ?? key.replaceAll('_',' ')}</th><td>{display(value)}</td><td>{deployment.review.plan.effective_config?.values?.[key] === undefined ? '—' : display(deployment.review.plan.effective_config.values[key])}</td><td>{deployment.review.plan.effective_config?.sources?.[key] ?? '—'} {deployment.review.plan.effective_config?.adjustments?.[key] ?? ''}</td></tr>
            {/each}
          </tbody></table></div>
          <h3>Participants and placement</h3>
          <div class="review-table"><table><thead><tr><th>Rank</th><th>Computer</th><th>Layers</th><th>Devices</th><th>GPU layers</th></tr></thead><tbody>
            {#each deployment.review.plan.prima_topology?.ranks ?? [] as rank}
              <tr><td>{rank.rank}</td><td>{rank.node_id}</td><td>{formatLayerRanges(rank)}</td><td>{display(rank.devices)}</td><td>{rank.ngl ?? '—'}</td></tr>
            {/each}
          </tbody></table></div>
          <details><summary>Technical details: exact snapshot and plan digest</summary><pre>{JSON.stringify(deployment.review,null,2)}</pre></details>
          <label class="check-row"><input type="checkbox" bind:checked={confirmed} disabled={deployment.launchState === 'sending'} />I reviewed this exact configuration and placement.</label>
          <div class="actions"><button class="btn" onclick={backToEditing}>Back to editing</button><button class="btn btn-accent" disabled={!confirmed || !!deployment.reviewReason} onclick={() => deployment.launch()}>{deployment.launchState === 'sending' ? 'Submitting deployment…' : 'Run reviewed deployment'}</button></div>
        </section>
      {/if}
      {#if deployment.launchState === 'accepted'}<p class="notice" role="status">Deployment accepted. Follow the loading progress and running status above.</p>{/if}
    {/snippet}
    {#snippet footer()}
      <div class="actions">
        <button class="btn" disabled={presenter.status?.role !== 'master' || !deployment.runtimeId || !!presenter.busy || presenter.settingsLoading || !!presenter.settingsLoadError || issues.length > 0} onclick={() => presenter.saveModelSettings()}>Save draft</button>
        <button class="btn" disabled={presenter.settingsLoading || !presenter.savedModelConfig || presenter.status?.role !== 'master'} onclick={revertPrimaSavedSettings}>Revert</button>
        <button class="btn" disabled={presenter.settingsLoading || presenter.status?.role !== 'master'} onclick={showDefaults}>Defaults…</button>
      </div>
      <p class="small muted">Prima-only model settings · revision {presenter.settingsRevision}. Saving never changes active inference.</p>
    {/snippet}
  </PrimaDeploymentShell>
</div>

<dialog bind:this={defaultsDialog} aria-labelledby="prima-defaults-title">
  <h2 id="prima-defaults-title">Restore defaults to this draft?</h2>
  <p>Resets all deployment controls, including templates, sampling, adapters and extra arguments. This does not overwrite saved settings or affect a running model.</p>
  <pre>{defaultsSnapshot.map(row=>`${row.field}: ${JSON.stringify(row.before)} → ${JSON.stringify(row.after)}`).join('\n') || 'No value changes. Confirming still creates an unsaved reset.'}</pre>
  <div class="actions"><button class="btn" onclick={() => defaultsDialog.close()}>Keep draft</button><button class="btn" onclick={() => { restorePrimaDefaults(); defaultsDialog.close(); }}>Restore to draft</button></div>
</dialog>

{#if showJinjaEditor}
  <JinjaEditor
    initialText={presenter.chatTemplateText || presenter.modelTemplateText || ''}
    onApply={(text) => {
      presenter.chatTemplate = 'custom';
      presenter.chatTemplateText = text;
      presenter.scheduleEstimate();
    }}
    onClose={() => (showJinjaEditor = false)}
  />
{/if}

<style>
  .prima-deploy-page { min-width:0;overflow-wrap:anywhere; }
  .prima-deploy-page :global([hidden]) { display:none !important; }
  .prima-deploy-page :global(.btn), .prima-deploy-page :global(select), .prima-deploy-page :global(input:not([type=checkbox])) {min-height:44px;max-width:100%;}
  .prima-deploy-page :global(.plan-summary) {position:static;top:auto;min-width:0;border-radius:8px;}
  .prima-deploy-page :global(.parameter-heading) {flex-wrap:wrap;}
  .prima-deploy-page :global(.scope-label) {white-space:normal;}
  fieldset {border:0;margin:0;padding:0;min-width:0;}
  .section-note {padding:20px;font-size:14px;}
  dialog {width:min(820px,calc(100vw - 32px));max-height:85vh;overflow:auto;padding:24px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text);}
  dialog::backdrop {background:#000a;}
  dialog > * {margin-bottom:16px;}
  dialog p {overflow-wrap:anywhere;}
  dialog button {min-height:44px;}

  h2 { font-size: 20px; font-weight: 600; }
  h3 { font-size: 15px; font-weight: 600; }
  .prima-header-badges { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .warning-box { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; border-color: var(--warn); }
  .deploy-section { padding: 24px; display: flex; flex-direction: column; gap: 18px; }
  .advanced { border-top: 1px solid var(--border); border-bottom: 0; border-radius: 0; margin: 0; }
  .advanced summary { padding: 20px 24px; cursor: pointer; font-size: 14px; }
  .advanced-body { padding: 0 24px 24px; display: flex; flex-direction: column; gap: 20px; }
  .advanced-body.field-grid { display: grid; }
  .field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; }
  .field { display: flex; flex-direction: column; gap: 8px; font-size: 13px; min-width: 0; }
  .field-title { font-size: 13px; font-weight: 500; }
  .node-option { padding: 16px; border: 1px solid var(--border); border-radius: 8px; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
  .node-option-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
  .node-option label { display: flex; gap: 10px; align-items: center; font-size: 13px; min-width: 0; }
  .actions, .running-model, .od-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .od-row { justify-content: space-between; }
  .running-model { padding: 18px 24px; justify-content: space-between; }
  .running-model strong { margin-left: 12px; }
  .notice { padding: 14px; border: 1px solid var(--border); border-radius: 8px; }
  .lora-section { display: flex; flex-direction: column; gap: 10px; padding: 14px; border: 1px solid var(--border); border-radius: 8px; }
  .lora-row { display: grid; grid-template-columns: minmax(0, 1fr) 110px auto; gap: 10px; align-items: center; }
  .deploy-progress-slot:empty { display:none; }
  .inline-review { padding:24px;display:grid;gap:16px;min-width:0; }
  .inline-review summary { cursor:pointer;min-height:44px; }
  .inline-review table { min-width:600px; }

  .review-table { overflow-x: auto; max-width: 100%; }
  table { width: 100%; font-size: 12px; text-align: left; border-collapse: collapse; }
  td, th { padding: 12px 8px; border-bottom: 1px solid var(--border); max-width: 240px; overflow-wrap: anywhere; }
  td small { display: block; color: var(--muted); margin-top: 4px; }
  tr.changed td:first-child { color: var(--accent); }
  textarea { min-height: 90px; resize: vertical; }
  pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; }
  @media (max-width: 700px) {
    .field-grid { grid-template-columns: 1fr; }
    .lora-row { grid-template-columns: 1fr; }
    .deploy-section { padding: 18px; }
    .inline-review { padding:18px; }
    .advanced summary { padding: 18px; }
    .advanced-body { padding: 0 18px 18px; }
  }
</style>
