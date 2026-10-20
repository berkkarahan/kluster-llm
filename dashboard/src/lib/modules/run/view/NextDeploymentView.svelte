<script lang="ts">
  import type { PrimaDeployment } from '../prima-deployment.svelte';
  import type { RunInteractor } from '../interactor.svelte';
  import DeploymentReadinessCard from './DeploymentReadinessCard.svelte';
  import PrepareProfileStepper from './PrepareProfileStepper.svelte';
  import DeploymentPlan from './DeploymentPlan.svelte';
  import PrimaPreparationStatus from '#lib/modules/runtimes/view/PrimaPreparationStatus.svelte';

  let {
    deployment,
    presenter,
    onReview,
  }: {
    deployment: PrimaDeployment;
    presenter: RunInteractor;
    onReview: () => void;
  } = $props();

  const plan = $derived(deployment.latestPlan);
  const effective = $derived(plan?.effective_config);
</script>

<div class="next-deployment-view">
  <section class="panel summary-header">
    <div class="heading">
      <h2>Next deployment</h2>
      <span class="draft-tag">Draft revision {deployment.revision}</span>
    </div>

    <div class="meta-grid">
      <div class="meta-item">
        <span class="meta-label">Selected Model:</span>
        <strong class="meta-value">{presenter.selectedModel || 'No model selected'}</strong>
      </div>
      <div class="meta-item">
        <span class="meta-label">Runtime:</span>
        <span class="meta-value font-mono">{deployment.runtimeId || 'Not selected'}</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">Network Transport:</span>
        <span class="meta-value">{presenter.selectedNetworkId || 'LAN / WireGuard'}</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">Requested Context / Slots:</span>
        <span class="meta-value">{presenter.contextAuto ? 'Automatic' : presenter.context} / {presenter.parallelSlots}</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">Requested Cache (K / V):</span>
        <span class="meta-value">{presenter.cacheTypeK} / {presenter.cacheTypeV}</span>
      </div>
    </div>

    {#if effective}
      <details class="effective-details">
        <summary>Requested → Effective configuration adjustments</summary>
        <div class="adjustments-list">
          {#each Object.entries(effective.requested ?? {}) as [key, value]}
            <p>
              <strong>{key}</strong>: {JSON.stringify(value)} → {JSON.stringify(effective.values?.[key]) ?? 'Unknown'}
              · {effective.sources?.[key] ?? 'Unknown source'} {effective.adjustments?.[key] ?? ''}
            </p>
          {/each}
        </div>
      </details>
    {/if}
  </section>
  {#if !deployment.fresh && deployment.preview}
    <p class="draft-stale-notice">Settings changed. Prepare and Review will calculate the updated plan automatically.</p>
  {/if}

  <!-- Authoritative 5-pillar deployment readiness card -->
  <DeploymentReadinessCard {deployment} {presenter} />

  {#if deployment.runtimes?.preparation?.job}
    <PrimaPreparationStatus observer={deployment.runtimes.preparation} />
  {/if}

  <!-- Svelte 5 Step-by-Step 5-stage Prepare & Profile visualizer -->
  <PrepareProfileStepper {deployment} {presenter} {onReview} />

  {#if deployment.planningError}
    <div class="notice alert-warning" role="alert">
      <p>Planning inspection error: {deployment.planningError}</p>
      <button
        class="btn btn-sm"
        disabled={!!deployment.inspectReason || deployment.inspecting || !!deployment.pendingAction}
        onclick={() => deployment.inspect(true)}
      >
        Retry planning
      </button>
    </div>
  {/if}

  {#if deployment.error && deployment.error !== deployment.planningError}
    <p class="error-notice" role="alert">{deployment.error}</p>
  {/if}

  {#if deployment.notice}
    <p class="status-notice" role="status">{deployment.notice}</p>
  {/if}

  {#if deployment.uncertainPreparation}
    <div class="notice alert-warning">
      <p>Previous preparation outcome is uncertain.</p>
      <button class="btn" onclick={() => deployment.prepare(false, true)}>
        Retry same preparation request
      </button>
    </div>
  {/if}

  {#if deployment.launchState === 'unknown'}
    <div class="notice alert-warning">
      <p>Run outcome is unknown. Reconcile cluster status before retrying.</p>
      <button class="btn" onclick={() => deployment.reconcile()}>
        Reconcile run status
      </button>
    </div>
  {/if}

  <!-- Placement Memory Plan -->
  <DeploymentPlan {presenter} />

  <!-- Running configuration boundary -->
  <section class="panel summary-footer" aria-label="Active configuration">
    <h2>Running configuration is separate</h2>
    <p>{presenter.status?.inference_up ? presenter.status.model_name : 'No active inference reported'}</p>
    <p class="hint-text">Draft changes affect only the next deployment.</p>
    {#if presenter.activeRunConfig}
      <details>
        <summary>Backend active snapshot</summary>
        <pre>{JSON.stringify(presenter.activeRunConfig, null, 2)}</pre>
      </details>
    {/if}
  </section>
</div>

<style>
  .next-deployment-view {
    display: grid;
    gap: 16px;
    min-width: 0;
  }
  .summary-header, .summary-footer {
    padding: 18px;
    display: grid;
    gap: 12px;
    border-radius: 8px;
    min-width: 0;
    overflow-wrap: anywhere;
    background: var(--surface);
    border: 1px solid var(--line);
  }
  .heading {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }
  h2 {
    margin: 0;
    font-size: 17px;
    font-weight: 600;
  }
  .draft-tag {
    font-size: 12px;
    color: var(--muted);
  }
  .meta-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 200px), 1fr));
    gap: 12px;
  }
  .meta-item {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .meta-label {
    font-size: 11px;
    color: var(--muted);
  }
  .meta-value {
    font-size: 13px;
  }
  .effective-details summary {
    font-size: 12px;
    color: var(--muted);
    cursor: pointer;
    min-height: 36px;
  }
  .adjustments-list {
    padding: 8px 0;
    font-size: 12px;
  }
  .adjustments-list p {
    margin: 4px 0;
  }
  .alert-warning {
    padding: 10px 14px;
    background: var(--yellow-wash, rgba(242, 199, 106, 0.1));
    border: 1px solid var(--warning);
    border-radius: 6px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }
  .alert-warning p {
    margin: 0;
    font-size: 13px;
    color: var(--text);
  }
  .error-notice {
    padding: 8px 12px;
    background: var(--red-wash);
    color: var(--danger);
    border: 1px solid var(--danger);
    border-radius: 6px;
    margin: 0;
    font-size: 13px;
  }
  .status-notice {
    padding: 8px 12px;
    background: var(--surface);
    color: var(--muted);
    border: 1px solid var(--line);
    border-radius: 6px;
    margin: 0;
    font-size: 13px;
  }
  .hint-text {
    font-size: 13px;
    color: var(--muted);
    margin: 0;
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-size: 11px;
    background: var(--bg);
    padding: 8px;
    border-radius: 4px;
  }
</style>
