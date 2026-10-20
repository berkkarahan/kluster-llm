<script lang="ts">
  import type { PrimaDeployment } from '../prima-deployment.svelte';
  import type { RunInteractor } from '../interactor.svelte';
  import PrimaPreparationStatus from '#lib/modules/runtimes/view/PrimaPreparationStatus.svelte';
  import DeploymentPlan from './DeploymentPlan.svelte';
  let { deployment, presenter, onReview }: { deployment:PrimaDeployment; presenter:RunInteractor; onReview:()=>void } = $props();
  const plan = $derived(deployment.fresh ? deployment.preview?.plan : null);
  const effective = $derived(plan?.effective_config);
</script>
<section class="panel summary">
  <div class="heading"><h2>Next deployment</h2><span class="tag">{deployment.inspecting ? 'Calculating deployment…' : deployment.fresh ? 'Current plan' : deployment.preview ? 'Stale plan' : 'Plan pending'}</span></div>
  <p>Draft revision {deployment.revision}</p>
  <strong>{presenter.selectedModel || 'No model selected'}</strong>
  <p class="mono">Runtime: {deployment.runtimeId || 'Not selected'}</p>
  <p>Network: {presenter.selectedNetworkId || 'LAN / WireGuard'}</p>
  <dl>
    <div><dt>Total context / slots (requested)</dt><dd>{presenter.contextAuto ? 'Automatic' : presenter.context} / {presenter.parallelSlots}</dd></div>
    <div><dt>Requested K / V cache</dt><dd>{presenter.cacheTypeK} / {presenter.cacheTypeV}</dd></div>
    <div><dt>Placement</dt><dd>{deployment.fresh ? deployment.placementError || plan?.fit_status || 'Calculated' : 'Calculated automatically when needed'}</dd></div>
    <div><dt>Assets</dt><dd>{deployment.jobMatches ? deployment.runtimes.preparation.job?.phase : deployment.runtimes.preparation.job ? 'Previous or unmatched plan' : 'Not prepared'}</dd></div>
    <div><dt>Profile coverage</dt><dd>{plan?.profiling?.status ?? 'Unmeasured'}</dd></div>
    <div><dt>Architecture support</dt>
    <dd>{!plan ? 'Pending calculation' : plan.model_support?.supported === false ? 'Unsupported architecture' : plan.model_support?.warning ? 'Support unverified' : plan.model_support?.supported === true ? 'Architecture supported' : 'Pending calculation'}</dd></div>
    <div><dt>Inference qualification</dt><dd>{!plan ? 'Unknown' : plan.inference_supported === true && !plan.qualification_errors?.length ? 'Qualified' : plan.qualification_errors?.join('; ') || 'Not confirmed'}</dd></div>
  </dl>
  {#if plan?.model_support?.error}<p role="alert">{plan.model_support.error}</p>{/if}
  {#if plan?.model_support?.warning}<p role="status">{plan.model_support.warning}</p>{/if}
  {#if plan && (plan.inference_supported !== true || plan.qualification_errors?.length)}
    <p>Evaluation &amp; profiling mode. Architecture support does not confirm inference qualification; deployment remains blocked until the qualification gates pass.</p>
  {/if}
  {#if effective}
    <details><summary>Requested → effective / source</summary>
      {#each Object.entries(effective.requested ?? {}) as [key,value]}
        <p><strong>{key}</strong>: {JSON.stringify(value)} → {JSON.stringify(effective.values?.[key]) ?? 'Unknown'} · {effective.sources?.[key] ?? 'Unknown source'} {effective.adjustments?.[key] ?? ''}</p>
      {/each}
    </details>
  {/if}
  <PrimaPreparationStatus observer={deployment.runtimes.preparation} />
  {#if !deployment.fresh && deployment.preview}<p>Settings changed. Prepare and Review will calculate the updated plan automatically.</p>{/if}
  {#if deployment.placementError}<p role="alert">{deployment.placementError}</p>{/if}
  <div class="actions">
    {#if deployment.planningError}<button class="btn" onclick={() => deployment.inspect(true)} disabled={!!deployment.inspectReason || deployment.inspecting || !!deployment.pendingAction}>Retry planning</button>{/if}
    <button class="btn" onclick={() => deployment.prepare(false)} disabled={!!deployment.prepareActionReason}>{deployment.pendingAction === 'prepare' ? 'Preparing deployment…' : 'Prepare files'}</button>
    <button class="btn" onclick={() => deployment.prepare(true)} disabled={!!deployment.prepareActionReason}>Prepare &amp; profile</button>
    {#if deployment.uncertainPreparation}<button class="btn" onclick={() => deployment.prepare(false,true)}>Retry same preparation request</button>{/if}
    <button class="btn btn-accent" onclick={onReview} disabled={!!deployment.reviewActionReason || !!deployment.review}>{deployment.pendingAction === 'review' ? 'Calculating deployment…' : 'Review deployment'}</button>
    {#if deployment.launchState === 'unknown'}<button class="btn" onclick={() => deployment.reconcile()}>Reconcile run status</button>{/if}
  </div>
  {#if deployment.prepareActionReason}<p>{deployment.prepareActionReason}</p>{/if}
  {#if deployment.reviewActionReason}<p>{deployment.reviewActionReason}</p>{/if}
  {#if deployment.error}<p role="alert">{deployment.error}</p>{/if}
  {#if deployment.notice}<p role="status">{deployment.notice}</p>{/if}
</section>
<DeploymentPlan {presenter} />
<section class="panel summary" aria-label="Active configuration">
  <h2>Running configuration is separate</h2>
  <p>{presenter.status?.inference_up ? presenter.status.model_name : 'No active inference reported'}</p>
  <p>Draft changes affect only the next deployment.</p>
  {#if presenter.activeRunConfig}<details><summary>Backend active snapshot</summary><pre>{JSON.stringify(presenter.activeRunConfig,null,2)}</pre></details>{/if}
</section>
<style>
 .summary {padding:18px;display:grid;gap:12px;border-radius:8px;min-width:0;overflow-wrap:anywhere;}
 dl {display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:16px;}
 dl > div {min-width:0;}
 h2 {font-size:17px;font-weight:600;} p,dt,dd {font-size:13px;} dt {color:var(--muted);} dd {margin:0 0 10px;}
 .heading {display:flex;gap:8px;justify-content:space-between;flex-wrap:wrap;} .actions {display:flex;flex-wrap:wrap;gap:12px;} button,summary {min-height:44px;} summary {cursor:pointer;} pre {white-space:pre-wrap;overflow-wrap:anywhere;}
</style>
