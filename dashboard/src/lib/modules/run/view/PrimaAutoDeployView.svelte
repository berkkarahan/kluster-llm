<script lang="ts">
  import { createApp } from '#lib/app.svelte';
  import { routes } from '#lib/shared/navigation';
  import { formatGib } from '#lib/shared/format-memory';
  import LoadProgressPanel from '#lib/modules/run/view/LoadProgressPanel.svelte';
  import DownloadPanel from '#lib/modules/models/view/DownloadPanel.svelte';
  import type { RunInteractor } from '../interactor.svelte';
  import type { RuntimesInteractor } from '#lib/modules/runtimes/interactor.svelte';
  import type { ClusterInteractor } from '#lib/modules/cluster/interactor.svelte';
  import type { Member } from '#lib/modules/cluster/entities';

  let {
    presenter = createApp().run,
    runtimes = createApp().runtimes,
    cluster = presenter.cluster ?? createApp().cluster,
    deployError = $bindable(''),
    errorCode = $bindable(''),
    onDeploy
  }: {
    presenter?: RunInteractor;
    runtimes?: RuntimesInteractor;
    cluster?: ClusterInteractor;
    deployError?: string;
    errorCode?: string;
    onDeploy?: (model: string) => Promise<unknown>;
  } = $props();

  let isDeploying = $state(false);
  let localError = $state('');
  let localErrorCode = $state('');
  let launchAccepted = $state(false);

  const status = $derived(cluster?.status ?? presenter?.status);
  const activeErrorCode = $derived(errorCode || localErrorCode);
  const activeError = $derived(deployError || localError);

  const onlineMembers = $derived.by(() => {
    const members = status?.members ?? {};
    return Object.values(members).filter((m): m is Member => Boolean(m && typeof m === 'object' && m.online !== false));
  });

  const onlineNodeCount = $derived(
    onlineMembers.length > 0
      ? onlineMembers.length
      : Object.keys(status?.members ?? {}).length === 0 && status?.node_id
        ? 1
        : 0
  );

  const detectedGpus = $derived.by(() => {
    let count = 0;
    const names: string[] = [];
    for (const member of onlineMembers) {
      if (Array.isArray(member.devices)) {
        for (const dev of member.devices) {
          if (dev && typeof dev === 'object') {
            count++;
            if (dev.name && !names.includes(dev.name)) names.push(dev.name);
          }
        }
      } else if (Array.isArray(member.rpc_devices)) {
        count += member.rpc_devices.filter((d) => Boolean(d && typeof d === 'object' && d.memory_kind === 'gpu')).length;
      }
    }
    return { count, names };
  });

  const aggregateVramBytes = $derived.by(() => {
    let bytes = 0;
    for (const member of onlineMembers) {
      if (typeof member.vram_bytes === 'number' && member.vram_bytes > 0) {
        bytes += member.vram_bytes;
      }
    }
    return bytes;
  });

  const aggregateRamBytes = $derived.by(() => {
    let bytes = 0;
    for (const member of onlineMembers) {
      const ram = member.available_ram_bytes ?? member.total_ram_bytes;
      if (typeof ram === 'number' && ram > 0) {
        bytes += ram;
      }
    }
    return bytes;
  });

  const inferenceActive = $derived(
    Boolean(
      status?.inference_up ||
      runtimes?.deployment?.lifecycleReason ||
      presenter.busy === 'run' ||
      presenter.busy === '/v2/inference/runs'
    )
  );

  const hasModels = $derived((presenter.models ?? []).length > 0);

  const deployDisabled = $derived(
    status?.role !== 'master' ||
    !presenter.selectedModel ||
    !hasModels ||
    inferenceActive ||
    isDeploying ||
    activeErrorCode === 'PRIMA_MODEL_UNSUPPORTED' ||
    activeErrorCode === 'PRIMA_MODEL_CORRUPT' ||
    activeErrorCode === 'PRIMA_INSUFFICIENT_CAPACITY'
  );

  function handleModelSelect(id: string) {
    presenter.selectModel(id);
    localError = '';
    localErrorCode = '';
    deployError = '';
    errorCode = '';
    launchAccepted = false;
  }

  async function handleDeploy() {
    if (deployDisabled) return;
    localError = '';
    localErrorCode = '';
    deployError = '';
    errorCode = '';
    launchAccepted = false;
    isDeploying = true;
    try {
      if (onDeploy) {
        await onDeploy(presenter.selectedModel);
      } else if (typeof presenter.autoDeploy === 'function') {
        await presenter.autoDeploy(presenter.selectedModel);
      } else {
        if (presenter.selectedNetworkId) {
          await presenter.http.loadToken();
        }
        const payload: Record<string, unknown> = {
          model: presenter.selectedModel,
          prima_auto_deploy: true,
        };
        if (presenter.selectedNetworkId) {
          payload.network_id = presenter.selectedNetworkId;
        }
        await presenter.http.command('/v2/inference/runs', payload);
      }
      launchAccepted = true;
    } catch (err: any) {
      const msg = err?.message || String(err);
      localError = msg;
      const code = err?.code || err?.detail?.code || '';
      if (code) {
        localErrorCode = code;
      } else if (msg.includes('PRIMA_MODEL_UNSUPPORTED')) {
        localErrorCode = 'PRIMA_MODEL_UNSUPPORTED';
      } else if (msg.includes('PRIMA_MODEL_CORRUPT')) {
        localErrorCode = 'PRIMA_MODEL_CORRUPT';
      } else if (msg.includes('PRIMA_INSUFFICIENT_CAPACITY')) {
        localErrorCode = 'PRIMA_INSUFFICIENT_CAPACITY';
      }
    } finally {
      isDeploying = false;
    }
  }
</script>

<div class="prima-auto-deploy-view stack">
  <DownloadPanel />

  <section class="panel auto-deploy-card" aria-label="One-click deploy">
    <div class="card-header">
      <div>
        <h2>One-Click Auto Deploy</h2>
        <p class="small muted">
          Prima auto-planning delegates layer partitioning, GPU offloading, and pipeline topology directly to the native scheduler.
        </p>
      </div>
      <div class="cluster-readiness-badge" aria-label="Cluster readiness">
        <span class="badge-status {onlineNodeCount > 0 ? 'status-ready' : 'status-offline'}">
          {onlineNodeCount > 0 ? 'Cluster Ready' : 'Cluster Offline'}
        </span>
        <div class="readiness-details">
          <span class="badge-item">{onlineNodeCount} {onlineNodeCount === 1 ? 'node' : 'nodes'} online</span>
          <span class="badge-item">{detectedGpus.count} {detectedGpus.count === 1 ? 'GPU' : 'GPUs'} detected</span>
          <span class="badge-item mono">{formatGib(aggregateVramBytes)} VRAM</span>
          <span class="badge-item mono">{formatGib(aggregateRamBytes)} RAM</span>
        </div>
      </div>
    </div>

    <div class="deploy-controls">
      <label class="field" for="auto-deploy-model-select">Choose model
        <select
          id="auto-deploy-model-select"
          class="field mono"
          value={presenter.selectedModel}
          disabled={isDeploying || !hasModels}
          onchange={(e) => handleModelSelect(e.currentTarget.value)}
        >
          {#if !hasModels}
            <option value="">No downloaded models available</option>
          {/if}
          {#each presenter.models as model}
            <option value={model.id}>{model.id}</option>
          {/each}
        </select>
      </label>
      <div class="actions model-actions">
        <a class="text-button" href={routes.models}>Browse and download models →</a>
      </div>
    </div>

    <div class="status-and-errors" aria-label="Deployment status and diagnostics">
      {#if activeErrorCode === 'PRIMA_MODEL_UNSUPPORTED' || activeError?.includes('PRIMA_MODEL_UNSUPPORTED')}
        <div class="error-chip chip-danger chip-unsupported" role="alert" data-chip="PRIMA_MODEL_UNSUPPORTED">
          <span class="chip-title">PRIMA_MODEL_UNSUPPORTED</span>
          <span class="chip-desc">This model architecture is not supported by Prima native runtime.</span>
        </div>
      {/if}

      {#if activeErrorCode === 'PRIMA_INSUFFICIENT_CAPACITY' || activeError?.includes('PRIMA_INSUFFICIENT_CAPACITY')}
        <div class="error-chip chip-danger chip-capacity" role="alert" data-chip="PRIMA_INSUFFICIENT_CAPACITY">
          <span class="chip-title">PRIMA_INSUFFICIENT_CAPACITY</span>
          <span class="chip-desc">Insufficient cluster memory across available nodes to allocate model layers.</span>
        </div>
      {/if}

      {#if activeErrorCode === 'PRIMA_MODEL_CORRUPT' || activeError?.includes('PRIMA_MODEL_CORRUPT')}
        <div class="error-chip chip-danger chip-corrupt" role="alert" data-chip="PRIMA_MODEL_CORRUPT">
          <span class="chip-title">PRIMA_MODEL_CORRUPT</span>
          <span class="chip-desc">Model file is corrupted, zero-byte, or not a valid GGUF file.</span>
        </div>
      {/if}

      {#if inferenceActive}
        <div class="error-chip chip-warning chip-active-run" role="status" data-chip="ACTIVE_RUN_CONFLICT">
          <span class="chip-title">Active Deployment</span>
          <span class="chip-desc">Stop active model before deploying.</span>
        </div>
      {/if}

      {#if activeError && !activeErrorCode}
        <div class="error-chip chip-danger chip-general" role="alert">
          <span class="chip-desc">{activeError}</span>
        </div>
      {/if}

      {#if launchAccepted}
        <div class="notice success-notice" role="status">
          <strong>Deployment accepted.</strong> Prima is auto-planning topology and staging native ranks.
        </div>
      {/if}
    </div>

    <div class="deploy-action-row">
      <button
        id="btn-deploy-model"
        class="btn btn-primary btn-deploy"
        type="button"
        disabled={deployDisabled}
        onclick={handleDeploy}
      >
        {isDeploying ? 'Deploying model…' : 'Deploy Model'}
      </button>
      {#if deployDisabled && inferenceActive}
        <span class="small muted help-note">An inference run is already active. Stop it above before deploying.</span>
      {:else if deployDisabled && !presenter.selectedModel}
        <span class="small muted help-note">Select a model to deploy.</span>
      {:else if deployDisabled && status?.role !== 'master'}
        <span class="small muted help-note">Only the cluster master can deploy models.</span>
      {/if}
    </div>
  </section>

  <div class="deploy-progress-slot">
    <LoadProgressPanel {presenter} />
  </div>
</div>

<style>
  .prima-auto-deploy-view { min-width: 0; }
  .auto-deploy-card { padding: 24px; display: flex; flex-direction: column; gap: 20px; border: 1px solid var(--border); border-radius: 8px; }
  .card-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; }
  .card-header h2 { font-size: 20px; font-weight: 600; margin: 0 0 6px 0; }
  .cluster-readiness-badge { display: flex; flex-direction: column; gap: 6px; padding: 12px 16px; border-radius: 8px; background: var(--surface-2, #18181b); border: 1px solid var(--border); }
  .badge-status { font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
  .badge-status.status-ready { color: var(--success, #22c55e); }
  .badge-status.status-offline { color: var(--danger, #ef4444); }
  .readiness-details { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 12px; color: var(--muted); }
  .badge-item { display: inline-flex; align-items: center; }
  .badge-item:not(:last-child)::after { content: "·"; margin-left: 10px; color: var(--muted); }
  .deploy-controls { display: flex; flex-direction: column; gap: 8px; }
  .field { display: flex; flex-direction: column; gap: 8px; font-size: 13px; }
  .model-actions { margin-top: 4px; }
  .status-and-errors { display: flex; flex-direction: column; gap: 10px; }
  .error-chip { display: flex; flex-direction: column; gap: 4px; padding: 12px 16px; border-radius: 6px; font-size: 13px; }
  .chip-danger { background: rgba(239, 68, 68, 0.1); border: 1px solid var(--danger, #ef4444); color: var(--danger, #ef4444); }
  .chip-warning { background: rgba(234, 179, 8, 0.1); border: 1px solid var(--warn, #eab308); color: var(--warn, #eab308); }
  .chip-title { font-weight: 600; font-family: monospace; }
  .chip-desc { font-size: 12px; }
  .deploy-action-row { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  .btn-deploy { min-width: 160px; min-height: 44px; font-size: 15px; font-weight: 600; }
  .success-notice { padding: 12px 16px; border-radius: 6px; background: rgba(34, 197, 94, 0.1); border: 1px solid var(--success, #22c55e); color: var(--success, #22c55e); }
  .help-note { font-size: 12px; }
  .deploy-progress-slot:empty { display: none; }
</style>
