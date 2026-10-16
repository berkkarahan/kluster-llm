<script lang="ts">
  import { createApp } from '#lib/app.svelte';
  import { routes } from '#lib/shared/navigation';
  import { onDestroy, tick, untrack } from 'svelte';
  import ErrorMessage from '#lib/shared/ErrorMessage.svelte';
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
    serializePrimaExtras,
    type PrimaExtrasState,
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

  let deployStep = $state<1 | 2 | 3>(1);
  let showJinjaEditor = $state(false);
  let featureOpen = $state(false);

  function normalizePrimaDefaults(force = false) {
    const saved = presenter.savedModelConfig;
    let changed = false;
    if (force || presenter.flashAttention === 'auto') {
      if (!force && (saved?.cache_type_k || saved?.cache_type_v)) {
        const vType = String(saved?.cache_type_v ?? presenter.cacheTypeV);
        presenter.flashAttention = ['f32', 'f16', 'bf16'].includes(vType) ? 'off' : 'on';
      } else {
        presenter.flashAttention = 'off';
        if (force || !saved?.cache_type_k) {
          presenter.cacheTypeK = 'f16';
        }
        if (force || !saved?.cache_type_v) {
          presenter.cacheTypeV = 'f16';
        }
      }
      changed = true;
    }
    if (changed) {
      presenter.markSettingsClean();
      presenter.scheduleEstimate();
    }
  }

  function restorePrimaDefaults() {
    presenter.useDefaultSettings();
    normalizePrimaDefaults(true);
  }

  function revertPrimaSavedSettings() {
    presenter.revertSavedSettings();
    normalizePrimaDefaults(false);
  }

  async function selectPrimaModel(modelId: string) {
    await presenter.selectModel(modelId);
    normalizePrimaDefaults(false);
  }

  $effect(() => {
    const selected = presenter.selectedModel;
    if (
      selected &&
      !presenter.parameters.length &&
      !presenter.settingsLoading &&
      !presenter.settingsLoadError &&
      presenter.savedModelConfig === null
    ) {
      untrack(() => void selectPrimaModel(selected));
      return;
    }
    if (!presenter.settingsLoading && presenter.flashAttention === 'auto') {
      untrack(() => normalizePrimaDefaults(false));
    }
  });

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

  $effect(() => {
    const raw = presenter.extraArgsText;
    if (raw !== lastSyncedExtraArgs) {
      const parsed = parsePrimaExtras(raw);
      loraRows = parsed.loraAdapters.map((item) => ({ ...item }));
      templateKwargsDraft = parsed.templateKwargsText;
      templateKwargsError = '';
      lastSyncedExtraArgs = raw;
    }
  });

  function commitPrimaExtras(patch: Partial<Omit<PrimaExtrasState, 'parseError'>>) {
    const nextState: Omit<PrimaExtrasState, 'parseError'> = {
      swaMode: patch.swaMode ?? primaExtras.swaMode,
      headMode: patch.headMode ?? primaExtras.headMode,
      pooling: patch.pooling ?? primaExtras.pooling,
      loraAdapters: patch.loraAdapters ?? loraRows,
      templateKwargsText: patch.templateKwargsText ?? primaExtras.templateKwargsText,
      otherTokens: patch.otherTokens ?? primaExtras.otherTokens
    };
    const serialized = serializePrimaExtras(nextState);
    lastSyncedExtraArgs = serialized;
    presenter.extraArgsText = serialized;
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

  // Integrated Prima Preparation & Profiling state (Step 3)
  let prepRuntimeId = $state('');
  let prepPreview = $state<Record<string, any> | null>(null);
  let prepJob = $state<Record<string, any> | null>(null);
  let prepBusy = $state(false);
  let prepError = $state('');
  let prepGeneration = 0;
  let prepTimer: ReturnType<typeof setTimeout> | null = null;
  let reviewedSettings = $state('');

  const runtimesLibrary = $derived(runtimes.status?.runtimes ?? presenter.status?.runtimes ?? null);
  const primaVersions = $derived(
    (runtimesLibrary?.versions ?? []).filter((v) => v.version.engine === 'primacpp' && v.build)
  );
  const targetPrimaVersion = $derived.by(() => {
    const target = runtimesLibrary?.target?.version;
    if (target?.engine === 'primacpp') return target;
    return primaVersions[0]?.version ?? null;
  });

  $effect(() => {
    if (!prepRuntimeId && targetPrimaVersion?.runtime_id) {
      prepRuntimeId = targetPrimaVersion.runtime_id;
    }
  });

  const currentSettingsJson = $derived.by(() => {
    try {
      return JSON.stringify(presenter.networkPlanPayload());
    } catch {
      return '';
    }
  });
  const prepFresh = $derived(Boolean(prepPreview) && currentSettingsJson === reviewedSettings);
  const prepActive = $derived(
    Boolean(prepJob && !['prepared', 'failed', 'cancelled', 'interrupted'].includes(String(prepJob.phase)))
  );
  const livePrepJob = $derived(
    (runtimes.primaPreparation ??
      (runtimesLibrary as Record<string, any> | null)?.prima_preparation ??
      null) as Record<string, any> | null
  );

  $effect(() => {
    if (!livePrepJob) return;
    const currentId = prepJob?.job_id;
    if (!currentId || livePrepJob.job_id === currentId || !prepActive) {
      const prevPhase = prepJob?.phase;
      prepJob = livePrepJob;
      if (livePrepJob.phase === 'prepared' && prevPhase !== 'prepared') {
        untrack(() => void presenter.refreshEstimate());
      }
    }
  });

  const prepProgress = $derived((prepJob?.progress as PrimaPreparationProgress | undefined) ?? null);
  const prepPct = $derived(
    prepProgress
      ? Math.min(100, Math.max(0, Math.round(Number(prepProgress.fraction ?? 0) * 100)))
      : prepJob?.phase === 'prepared'
        ? 100
        : prepActive
          ? 5
          : 0
  );
  const prepTransferLabel = $derived(
    prepProgress && Number(prepProgress.bytes_total ?? 0) > 0
      ? composeProgressLabel(
          Number(prepProgress.bytes_done ?? 0),
          Number(prepProgress.bytes_total ?? 0),
          prepProgress.rate_bps ?? null,
          prepProgress.eta_seconds ?? null
        )
      : ''
  );

  const displayTopology = $derived(
    ((prepPreview?.prima_topology as PrimaTopology | undefined) ?? planTopology)
  );
  const displayRanks = $derived((displayTopology?.ranks ?? []) as PrimaRankAllocation[]);
  const displayScheduler = $derived(
    ((prepPreview?.scheduler ?? prepPreview?.profiling?.scheduler ?? planScheduler) as HighsScheduleReport | null)
  );

  async function inspectPlacement() {
    if (!prepRuntimeId || !presenter.selectedModel) return null;
    const seq = ++prepGeneration;
    prepBusy = true;
    prepError = '';
    try {
      const settings = presenter.networkPlanPayload();
      const data = await runtimes.primaPlan(prepRuntimeId, settings);
      if (seq === prepGeneration) {
        prepPreview = data;
        reviewedSettings = JSON.stringify(settings);
      }
      return data;
    } catch (e) {
      if (seq === prepGeneration) prepError = String(e);
      return null;
    } finally {
      if (seq === prepGeneration) prepBusy = false;
    }
  }

  async function pollPrepJob(id: string, seq: number) {
    try {
      const data = await runtimes.primaJob(id);
      if (seq !== prepGeneration) return;
      prepJob = data;
      if (!['prepared', 'failed', 'cancelled', 'interrupted'].includes(String(data.phase))) {
        prepTimer = setTimeout(() => void pollPrepJob(id, seq), 1000);
      } else if (data.phase === 'prepared') {
        void presenter.refreshEstimate();
      }
    } catch (e) {
      if (seq === prepGeneration) prepError = String(e);
    }
  }

  async function runPrepare(includeProfile: boolean) {
    if (!prepRuntimeId || !presenter.selectedModel) return;
    let activePreview = prepPreview;
    if (!activePreview || !prepFresh) {
      activePreview = await inspectPlacement();
      if (!activePreview) return;
    }
    const digest = String(activePreview.plan_digest ?? presenter.planResult?.plan_digest ?? '');
    if (!digest) {
      prepError = 'Inspect placement first to compute a valid plan digest.';
      return;
    }
    const seq = ++prepGeneration;
    prepBusy = true;
    prepError = '';
    try {
      const data = await runtimes.preparePrima(
        prepRuntimeId,
        presenter.networkPlanPayload(),
        digest,
        includeProfile
      );
      if (seq === prepGeneration) {
        prepJob = data;
        void pollPrepJob(String(data.job_id), seq);
      }
    } catch (e) {
      if (seq === prepGeneration) prepError = String(e);
    } finally {
      if (seq === prepGeneration) prepBusy = false;
    }
  }

  async function cancelPrepare() {
    if (!prepJob?.job_id) return;
    try {
      await runtimes.cancelPrima(String(prepJob.job_id));
    } catch (e) {
      prepError = String(e);
    }
  }

  onDestroy(() => {
    ++prepGeneration;
    if (prepTimer) clearTimeout(prepTimer);
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

  const fit = $derived(String(presenter.planResult?.fit_status ?? ''));
  const ready = $derived(
    !blocked &&
      !runtimeBlocked &&
      !!presenter.planResult &&
      !presenter.estimating &&
      !presenter.estimateError &&
      ['fits', 'tight'].includes(fit) &&
      inferenceSupported
  );
  const estimateReady = $derived(
    presenter.status?.role === 'master' && !!presenter.selectedModel && !presenter.settingsLoading
  );
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

  $effect(() => {
    if (estimateReady) untrack(() => presenter.scheduleEstimate());
  });

  async function nextStep(target: 1 | 2 | 3) {
    if (target === 1) {
      deployStep = 1;
      return;
    }
    if (!presenter.selectedModel || presenter.settingsLoading || presenter.settingsLoadError) return;
    if (!presenter.validateRunSettings(true) || templateKwargsError) {
      deployStep = 2;
      featureOpen = true;
      await tick();
      const first = Object.keys(presenter.fieldErrors)[0];
      const element = document.getElementById(`run-${first?.replaceAll('_', '-')}`);
      element?.closest('details')?.setAttribute('open', '');
      element?.focus();
      return;
    }
    if (target === 3 && (!presenter.planResult || presenter.estimating)) {
      await presenter.refreshEstimate();
    }
    deployStep = target;
  }

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
      <p>Plan Ring-TCP rank placement, solve HiGHS Halda MILP layer windows, and prepare or deploy across your cluster.</p>
    </div>
    <div class="prima-header-badges">
      <span class="chip chip-info">Prima engine</span>
      <span class="chip mono">prima-ring-tcp</span>
      {#if targetPrimaVersion}
        <span class="chip mono">{targetPrimaVersion.name || targetPrimaVersion.commit.slice(0, 10)}</span>
      {/if}
      <a class="text-button" href={routes.settingsLlama}>Prima runtime settings →</a>
    </div>
  </header>

  {#if modelSupport || qualificationErrors.length > 0 || presenter.planResult?.inference_supported !== undefined}
    <section class="panel prima-status-strip" aria-label="Prima architecture and qualification status">
      <div class="prima-status-row">
        <div class="prima-status-cell">
          <span class="small muted">Architecture support</span>
          {#if modelSupport?.supported === false}
            <span class="chip chip-danger">Unsupported architecture</span>
          {:else if modelSupport?.warning}
            <span class="chip chip-warn">Support unverified</span>
          {:else if modelSupport?.supported === true}
            <span class="chip chip-ok">Architecture supported</span>
          {:else}
            <span class="chip">Pending inspection</span>
          {/if}
        </div>
        <div class="prima-status-cell">
          <span class="small muted">Engine qualification</span>
          {#if qualificationErrors.length > 0 || presenter.planResult?.inference_supported === false}
            <span class="chip chip-warn">
              Evaluation &amp; profiling mode · {qualificationErrors.length || 1} gate(s) open
            </span>
          {:else}
            <span class="chip chip-ok">Qualified for live inference</span>
          {/if}
        </div>
        <div class="prima-status-cell">
          <span class="small muted">Active scheduler</span>
          {#if displayScheduler}
            <span class="chip mono">
              {displayScheduler.scheduler_mode} · k={displayScheduler.cycle_count_k} · W={displayScheduler.window_size_w}
            </span>
          {:else}
            <span class="chip">Awaiting plan</span>
          {/if}
        </div>
      </div>
      {#if modelSupport?.error}
        <p class="small danger-text" role="alert">{modelSupport.error}</p>
      {:else if modelSupport?.warning}
        <p class="small warning-text">{modelSupport.warning}</p>
      {/if}
      {#if qualificationErrors.length > 0}
        <p class="small muted">
          Placement inspection, file staging, and native device profiling are active in Step 3. Live inference stays gated until qualification passes ({qualificationErrors.join(', ')}).
        </p>
      {/if}
    </section>
  {/if}

  {#if presenter.status?.process_role === 'master' || presenter.status?.inference_up}
    <section class="panel running-model" aria-label="Running model">
      <div>
        <span class="tag">Running</span>
        <strong>{presenter.status?.model_name ?? 'Loading model'}</strong>
        <p class="small muted">Configuration edits apply to the next Prima deployment.</p>
      </div>
      <button
        class="btn btn-danger"
        disabled={!!presenter.busy || presenter.status?.role !== 'master'}
        onclick={() => presenter.stopInference()}>Stop model</button>
    </section>
  {/if}

  <nav class="steps" aria-label="Deploy steps">
    {#each ['Select', 'Configure', 'Review & deploy'] as title, index}
      {#if index}<span class="step-line"></span>{/if}
      <button
        type="button"
        class="step"
        class:active={deployStep === index + 1}
        aria-current={deployStep === index + 1 ? 'step' : undefined}
        disabled={index > 0 && (!presenter.selectedModel || presenter.settingsLoading)}
        onclick={() => nextStep((index + 1) as 1 | 2 | 3)}
      >
        <span class="step-number">{index + 1}</span>{title}
      </button>
    {/each}
  </nav>

  <div class="deploy-layout">
    <div class="stack">
      <DownloadPanel />
      {#if blocked}<p class="notice small" role="status">{blocked}</p>{/if}
      {#if presenter.settingsLoadError}
        <div class="actions">
          <button class="btn" onclick={() => selectPrimaModel(presenter.selectedModel)}>Retry settings</button>
          <button class="btn" onclick={restorePrimaDefaults}>Use defaults</button>
        </div>
      {/if}
      {#if presenter.status?.apply_error}
        <ErrorMessage source="apply" message={presenter.status.apply_error} />
      {/if}

      <section class="panel deployment-form">
        {#if deployStep === 1}
          <div class="deploy-section">
            <p class="eyebrow">01 / Model &amp; Architecture</p>
            <h2>Choose a GGUF model for the Ring-TCP cluster</h2>
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
            <p class="eyebrow">02 / Ring Topology &amp; HiGHS Scheduler</p>
            <h2>Choose how ranks participate in the Ring-TCP pipeline</h2>
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
                  value={presenter.nodePriority ? 'priority' : presenter.placementStrategy}
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
        {:else if deployStep === 2}
          <div class="deploy-section">
            <p class="eyebrow">02 / Prima Configuration</p>
            <h2>Configure context, SWA memory, and Prima runtime options</h2>
            <p class="small muted">
              Prima-native parameters and launch extras. Rank 0 reserves <code>parallel_slots + 1</code> KV sequences for pipelined ring micro-batches.
            </p>
            <div class="field-grid">
              <div class="parameter-control">
                <label class="field" for="run-context">Total context capacity
                  <input
                    id="run-context"
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
              <ParameterControl {presenter} name="enable_thinking" />
            </div>
          </div>

          <details class="advanced" open={presenter.advancedOpen}>
            <summary>Memory &amp; loading (SWA, KV precision &amp; mmap)</summary>
            <div class="advanced-body">
              <p class="small muted">
                Node safety caps: local VRAM {presenter.vramPercent}%, RAM {presenter.ramPercent}%.
                <a class="text-button" href={routes.settings}>Node settings →</a>
              </p>
              <div class="field-grid">
                {#each ['cache_type_k', 'cache_type_v', 'flash_attention', 'vram_percent', 'ram_percent', 'mmap_mlock', 'checkpoints', 'cache_ram'] as name}
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
              {#each ['batch_size', 'micro_batch', 'threads', 'threads_batch', 'device'] as name}
                <ParameterControl {presenter} {name} />
              {/each}
            </div>
          </details>

          <details class="advanced">
            <summary>Generation defaults</summary>
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
                  class="field mono"
                  value={presenter.stopSequences === null ? '' : JSON.stringify(presenter.stopSequences)}
                  placeholder={'["END", "User:"]'}
                  oninput={(e) => {
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
                  <button type="button" class="btn" onclick={addLoraAdapter}>+ Add LoRA adapter</button>
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
                  value={templateKwargsDraft}
                  placeholder={'{"enable_thinking": true}'}
                  oninput={(e) => handleTemplateKwargsInput(e.currentTarget.value)}
                />
              </label>
              {#if templateKwargsError}
                <ErrorMessage source="validation:template-kwargs" message={templateKwargsError} snapshot={false} />
              {/if}
            </div>
          </details>

          <details class="advanced">
            <summary>Declared Prima launch extras (JSON argv)</summary>
            <div class="advanced-body">
              <p class="small muted">
                Prima validates <code>extra_args</code> strictly against declared launch flags (<code>--swa-full</code>, <code>--swa-compact</code>, <code>--embedding</code>, <code>--rerank</code>, <code>--pooling</code>, <code>--lora</code>, <code>--lora-scaled</code>, <code>--chat-template-kwargs</code>).
              </p>
              <label class="field" for="run-extra-args">Extra arguments
                <textarea
                  id="run-extra-args"
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

          <div class="deploy-section">
            <div class="actions">
              <button
                class="btn"
                disabled={!presenter.savedModelConfig || !!presenter.busy}
                onclick={revertPrimaSavedSettings}>Revert to saved</button>
              <button
                class="btn"
                disabled={!!presenter.busy}
                onclick={restorePrimaDefaults}>Restore defaults</button>
              <button
                class="btn"
                class:btn-accent={presenter.settingsDirty()}
                disabled={!!presenter.busy || presenter.settingsLoading || !!presenter.settingsLoadError}
                onclick={() => presenter.saveModelSettings()}>Save model defaults</button>
            </div>
            <p class="small muted">
              {presenter.settingsDirty() ? 'Unsaved changes' : 'Saved configuration loaded'} · saving affects future deployments.
            </p>
          </div>
        {:else}
          <div class="deploy-section">
            <p class="eyebrow">03 / Prepare, Profile &amp; Deploy</p>
            <h2>Stage Ring-TCP assets, profile devices, and deploy</h2>
            <strong>{presenter.selectedModel}</strong>
            <p class="small muted">
              {presenter.status?.inference_up
                ? `Deploying replaces ${presenter.status.model_name}.`
                : 'Inspect placement, stage model & adapter files across ranks, or run native profiling to unlock HiGHS Halda MILP scheduling.'}
            </p>

            {#if presenter.status?.role === 'master'}
              <section class="prima-prep-box" aria-label="Prima preparation and profiling">
                <div class="od-row">
                  <h3>Prima asset preparation &amp; device profiling</h3>
                  {#if prepJob?.phase}
                    <span class="tag mono" aria-live="polite">Job: {prepJob.phase}</span>
                  {/if}
                </div>
                {#if primaVersions.length > 0}
                  <div class="prep-controls">
                    <label class="field" for="prima-deploy-runtime">Prepared Prima runtime
                      <select
                        id="prima-deploy-runtime"
                        class="field"
                        bind:value={prepRuntimeId}
                        onchange={() => {
                          ++prepGeneration;
                          prepPreview = null;
                        }}
                        disabled={prepBusy || prepActive}
                      >
                        {#each primaVersions as entry}
                          <option value={entry.version.runtime_id}>
                            {entry.version.name || entry.version.commit.slice(0, 12)}
                          </option>
                        {/each}
                      </select>
                    </label>
                    <div class="actions">
                      <button
                        type="button"
                        class="btn"
                        onclick={inspectPlacement}
                        disabled={!prepRuntimeId || !presenter.selectedModel || prepBusy || prepActive}
                      >
                        Inspect placement
                      </button>
                      <button
                        type="button"
                        class="btn"
                        onclick={() => runPrepare(false)}
                        disabled={!prepRuntimeId || !presenter.selectedModel || prepBusy || prepActive}
                      >
                        Prepare files
                      </button>
                      <button
                        type="button"
                        class="btn btn-accent"
                        onclick={() => runPrepare(true)}
                        disabled={!prepRuntimeId || !presenter.selectedModel || prepBusy || prepActive}
                      >
                        Prepare &amp; profile
                      </button>
                      {#if prepActive}
                        <button type="button" class="btn btn-danger" onclick={cancelPrepare}>
                          Cancel preparation
                        </button>
                      {/if}
                    </div>
                  </div>
                  {#if prepPreview && !prepFresh}
                    <p class="small warning-text">
                      Draft settings changed since last inspection. Clicking Prepare will re-inspect placement automatically.
                    </p>
                  {/if}
                  {#if prepPreview}
                    <p class="small muted mono">
                      Profiles: {prepPreview.profiling?.status ?? 'unmeasured'} · Candidates: {prepPreview.candidate_count ?? 0}
                      {#if prepPreview.plan_digest} · Digest: {String(prepPreview.plan_digest).slice(0, 12)}{/if}
                    </p>
                  {/if}
                  {#if prepJob}
                    <div class="prep-progress-panel" aria-live="polite">
                      <div class="flex items-center gap-2">
                        {#if prepActive}<span class="spinner" aria-hidden="true"></span>{/if}
                        <span
                          class:chip={true}
                          class:chip-ok={prepJob.phase === 'prepared'}
                          class:chip-warn={prepActive}
                          class:chip-danger={prepJob.phase === 'failed'}
                          class:chip-muted={prepJob.phase === 'cancelled' || prepJob.phase === 'interrupted'}
                        >
                          {prepProgress?.label || `Preparation: ${prepJob.phase}`}
                        </span>
                        {#if prepProgress?.steps_total}
                          <span class="small muted mono">
                            Step {prepProgress.step} of {prepProgress.steps_total}
                          </span>
                        {/if}
                        <span class="ml-auto mono text-[11px]" style="color: var(--muted)">
                          {prepPct}%
                        </span>
                      </div>
                      <div
                        class="progress"
                        role="progressbar"
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={prepPct}
                        aria-label="Prima preparation and profiling progress"
                      >
                        <span style:width={`${prepPct}%`}></span>
                      </div>
                      <div class="mono text-[11px]" style="color: var(--muted)">
                        {#if prepProgress?.detail}
                          <span>{prepProgress.detail}</span>
                        {/if}
                        {#if prepTransferLabel}
                          <span>{prepProgress?.detail ? ' · ' : ''}{prepTransferLabel}</span>
                        {/if}
                        {#if prepJob.phase === 'prepared'}
                          <span>{prepProgress?.detail || prepTransferLabel ? ' · ' : ''}Estimate refreshed with latest device profiles.</span>
                        {/if}
                      </div>

                      {#if prepJob.nodes && Object.keys(prepJob.nodes).length > 0}
                        <div class="prep-node-list">
                          {#each Object.entries(prepJob.nodes) as [nodeId, nodeState] (nodeId)}
                            {@const nodeProg = (nodeState as Record<string, any>)?.progress as PrimaPreparationProgress | undefined}
                            {@const nodePhase = String((nodeState as Record<string, any>)?.phase ?? 'pending')}
                            {@const nodePct = nodeProg
                              ? Math.min(100, Math.max(0, Math.round(Number(nodeProg.fraction ?? 0) * 100)))
                              : nodePhase === 'prepared'
                                ? 100
                                : 0}
                            <div class="prep-node-row">
                              <div class="od-row small mono">
                                <span>{nodeId} · {nodeProg?.label || nodePhase}</span>
                                <span class="muted">{nodeProg?.detail ? `${nodeProg.detail} · ` : ''}{nodePct}%</span>
                              </div>
                              <div class="progress prep-node-bar" aria-hidden="true">
                                <span style:width={`${nodePct}%`}></span>
                              </div>
                            </div>
                          {/each}
                        </div>
                      {/if}
                    </div>
                    {#if prepJob.error}
                      <ErrorMessage source="prima-prepare" message={prepJob.error} />
                    {/if}
                  {/if}
                  {#if prepError}
                    <ErrorMessage source="prima-prepare" message={prepError} />
                  {/if}
                {:else}
                  <p class="small muted">
                    No built Prima runtime is available yet. Pin and build a Prima version in
                    <a href={routes.settingsLlama}>Settings → Prima</a> to stage files and profile devices.
                  </p>
                {/if}
              </section>
            {/if}

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
            <label class="check-row">
              <input type="checkbox" bind:checked={presenter.remember} />
              Save as model default after successful deployment
            </label>
          </div>
        {/if}

        {#if Object.keys(presenter.fieldErrors).length}
          <div class="notice small" role="alert">{Object.values(presenter.fieldErrors).join(' ')}</div>
        {/if}
        {#if runtimeBlocked}
          <div class="notice small">
            {runtimeBlocked} <a href={routes.settingsLlama}>Open Prima settings →</a>
          </div>
        {/if}

        <div class="deploy-progress-slot">
          <LoadProgressPanel {presenter} />
        </div>

        <div class="form-foot">
          <button class="btn" disabled={deployStep === 1} onclick={() => nextStep(deployStep === 3 ? 2 : 1)}>
            Back
          </button>
          {#if deployStep < 3}
            <button
              class="btn btn-accent"
              disabled={!presenter.selectedModel || presenter.settingsLoading || !!presenter.settingsLoadError}
              onclick={() => nextStep(deployStep === 1 ? 2 : 3)}
            >
              {deployStep === 1 ? 'Configure' : 'Review deployment'}
            </button>
          {:else}
            <button class="btn btn-accent" disabled={!ready || !!presenter.busy} onclick={() => presenter.run()}>
              {presenter.status?.inference_up ? 'Replace running model' : 'Deploy model'}
            </button>
          {/if}
        </div>
      </section>
    </div>
    <DeploymentPlan {presenter} onRequestProfile={() => void nextStep(3)} />
  </div>
</div>

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
  .deployment-form { overflow: hidden; }
  h2 { font-size: 20px; font-weight: 600; }
  h3 { font-size: 15px; font-weight: 600; }
  .prima-header-badges { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .prima-status-strip { padding: 16px 20px; display: flex; flex-direction: column; gap: 10px; }
  .prima-status-row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
  .prima-status-cell { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; min-width: 0; }
  .danger-text { color: var(--danger); }
  .warning-text { color: var(--warn); }
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
  .prima-prep-box { padding: 18px; border: 1px solid var(--border); border-radius: 8px; display: flex; flex-direction: column; gap: 14px; }
  .prep-controls { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
  .prep-progress-panel { display: flex; flex-direction: column; gap: 8px; padding: 12px; border: 1px solid var(--border); border-radius: 6px; }
  .prep-node-list { display: flex; flex-direction: column; gap: 6px; padding-top: 6px; border-top: 1px solid var(--border); }
  .prep-node-row { display: flex; flex-direction: column; gap: 4px; }
  .prep-node-bar { height: 4px; }
  .deploy-progress-slot { padding: 0 24px; }
  .deploy-progress-slot:empty { display: none; }
  .review-table { overflow-x: auto; max-width: 100%; }
  table { width: 100%; font-size: 12px; text-align: left; border-collapse: collapse; }
  td, th { padding: 12px 8px; border-bottom: 1px solid var(--border); max-width: 240px; overflow-wrap: anywhere; }
  td small { display: block; color: var(--muted); margin-top: 4px; }
  tr.changed td:first-child { color: var(--accent); }
  textarea { min-height: 90px; resize: vertical; }
  pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; }
  .form-foot { padding: 20px 24px; }
  @media (max-width: 700px) {
    .field-grid, .prima-status-row { grid-template-columns: 1fr; }
    .lora-row { grid-template-columns: 1fr; }
    .deploy-section { padding: 18px; }
    .deploy-progress-slot { padding: 0 18px; }
    .advanced summary { padding: 18px; }
    .advanced-body { padding: 0 18px 18px; }
  }
</style>
