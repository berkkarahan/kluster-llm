<script lang="ts">
  import type { PrimaDeployment } from '../prima-deployment.svelte';
  import type { RunInteractor } from '../interactor.svelte';

  let {
    deployment,
    presenter,
    plan: propPlan = undefined,
  }: {
    deployment: PrimaDeployment;
    presenter: RunInteractor;
    plan?: Record<string, any> | null;
  } = $props();

  const plan = $derived(propPlan !== undefined ? propPlan : deployment.latestPlan);
  const activeRun = $derived(deployment.activeRun);

  // Pillar 1: Placement
  const placementStatus = $derived(
    deployment.fresh
      ? deployment.placementError || plan?.fit_status || 'Calculated'
      : plan?.fit_status
      ? `${plan.fit_status} (draft modified)`
      : 'Calculated automatically when needed'
  );

  // Pillar 2: Assets
  const assetsStatus = $derived.by(() => {
    if (activeRun) {
      const syncStage = activeRun.stages?.find(s => s.stage_name === 'asset_sync');
      if (syncStage?.status === 'completed') return 'Prepared on all nodes';
      if (syncStage?.status === 'running') {
        const pct = Math.round((syncStage.progress_fraction || 0) * 100);
        return `Syncing assets (${pct}%)`;
      }
      if (syncStage?.status === 'failed') return 'Asset preparation failed';
      if (activeRun.phase === 'prepared') return 'Prepared';
    }
    const job = deployment.runtimes?.preparation?.job;
    if (deployment.jobMatches && job?.phase) return job.phase;
    if (job?.phase === 'prepared') return 'Prepared';
    return 'Not prepared';
  });

  // Pillar 3: Profile Coverage
  const profileStatus = $derived.by(() => {
    if (activeRun) {
      const profStage = activeRun.stages?.find(s => s.stage_name === 'node_profile');
      if (profStage?.status === 'running') return 'Profiling hardware…';
      if (profStage?.status === 'failed') return 'Node profiling failed';
      if (profStage?.status === 'completed') {
        const count = Object.keys(deployment.coverage?.node_profiles || activeRun.nodes || {}).length;
        return count > 0 ? `Profiled (${count} ${count === 1 ? 'node' : 'nodes'})` : 'Profiled';
      }
    }
    if (deployment.coverage?.coverage_status === 'profiled') {
      const count = Object.keys(deployment.coverage.node_profiles || {}).length;
      if (count > 0) return `Profiled (${count} ${count === 1 ? 'node' : 'nodes'})`;
      return 'Profiled';
    }
    if (plan?.profiling?.status) {
      return plan.profiling.status;
    }
    return 'Unmeasured';
  });

  // Pillar 4: Network Links
  const linkStatus = $derived.by(() => {
    // 1. Concrete activeRun stage status takes precedence
    const linkStage = activeRun?.stages?.find(s => s.stage_name === 'link_profile');
    if (linkStage?.status === 'running') return 'Profiling links…';
    if (linkStage?.status === 'failed') return 'Link profiling failed';
    if (linkStage?.status === 'skipped') return 'Single-node (skipped)';
    if (linkStage?.status === 'completed') return 'Measured (Ring-TCP verified)';

    // 2. Multi-node measured coverage takes precedence over plan pending states
    if (deployment.coverage?.link_matrix && deployment.coverage.link_matrix.length > 0) {
      return 'Measured (Ring-TCP verified)';
    }

    // 3. Topology / member count resolution
    const ranks = plan?.prima_topology?.ranks;
    const ranksCount = Array.isArray(ranks) ? ranks.length : 0;
    const memberCount = Object.keys(presenter?.status?.members ?? activeRun?.nodes ?? {}).length;

    // Single-node is valid ONLY if explicit 1 rank, or no plan yet and memberCount === 1
    if (ranksCount === 1) return 'Single-node (skipped)';
    if (ranksCount === 0 && memberCount === 1) return 'Single-node (skipped)';

    // 4. In-flight or unmeasured multi-node states:
    if (deployment.preparing) return 'Awaiting link profiling';
    if (!plan && memberCount > 1) return 'Awaiting plan & profiling';
    if (ranksCount > 1 || memberCount > 1) {
      if (!plan) return 'Awaiting plan & profiling';
      return 'Unmeasured';
    }

    return 'Unmeasured';
  });

  // Architecture Support
  const archStatus = $derived.by(() => {
    if (!deployment.fresh || !plan) return 'Pending calculation';
    if (plan.model_support?.supported === false) return 'Unsupported architecture';
    if (plan.model_support?.warning) return 'Support unverified';
    if (plan.model_support?.supported === true) return 'Architecture supported';
    return 'Pending calculation';
  });

  // Pillar 5: Inference Qualification
  const qualificationStatus = $derived.by(() => {
    if (!plan) return 'Unknown';
    if (plan.inference_supported === true && !plan.qualification_errors?.length) {
      return 'Qualified (HiGHS optimal)';
    }
    if (plan.qualification_errors?.length) {
      return plan.qualification_errors.join('; ');
    }
    return 'Not confirmed';
  });
</script>

<div class="readiness-card">
  <div class="card-header">
    <div class="title-group">
      <h3>Deployment readiness</h3>
      <span class="plan-tag" class:fresh={deployment.fresh} class:stale={!deployment.fresh && !!deployment.preview}>
        {deployment.inspecting ? 'Calculating…' : deployment.fresh ? 'Current plan' : deployment.preview ? 'Stale plan' : 'Plan pending'}
      </span>
    </div>
    {#if deployment.draftModified}
      <div class="draft-notice" role="status">
        <span class="chip-warn">Draft modified</span>
        <span class="notice-desc">Settings changed since last plan. SQLite profiling evidence preserved.</span>
      </div>
    {/if}
  </div>

  <dl class="pillars-grid">
    <div class="pillar-item">
      <dt>1. Placement</dt>
      <dd class:error-text={deployment.placementError || plan?.fit_status === 'overflow'}>
        {placementStatus}
      </dd>
    </div>

    <div class="pillar-item">
      <dt>2. Assets</dt>
      <dd class:ready={assetsStatus.includes('Prepared')} class:error-text={assetsStatus.includes('failed')}>
        {assetsStatus}
      </dd>
    </div>

    <div class="pillar-item">
      <dt>3. Profile coverage</dt>
      <dd class:ready={profileStatus.includes('Profiled')} class:error-text={profileStatus.includes('failed')}>
        {profileStatus}
      </dd>
    </div>

    <div class="pillar-item">
      <dt>4. Network links</dt>
      <dd
        class:ready={linkStatus.includes('Measured') || linkStatus.includes('Single-node')}
        class:error-text={linkStatus.includes('failed') || linkStatus.includes('timeout')}
        class:warn-text={linkStatus.includes('Profiling')}
      >
        {linkStatus}
      </dd>
    </div>

    <div class="pillar-item">
      <dt>5. Qualification</dt>
      <dd class:ready={qualificationStatus.includes('Qualified')} class:error-text={plan?.qualification_errors?.length}>
        {qualificationStatus}
      </dd>
    </div>

    <div class="pillar-item">
      <dt>Architecture support</dt>
      <dd class:ready={archStatus === 'Architecture supported'} class:error-text={archStatus === 'Unsupported architecture'}>
        {archStatus}
      </dd>
    </div>
  </dl>

  {#if plan?.model_support?.error}
    <p class="support-alert" role="alert">{plan.model_support.error}</p>
  {/if}
  {#if plan?.model_support?.warning}
    <p class="support-status" role="status">{plan.model_support.warning}</p>
  {/if}
  {#if plan && (plan.inference_supported !== true || plan.qualification_errors?.length)}
    <p class="qualification-notice">
      Evaluation &amp; profiling mode. Architecture support does not confirm inference qualification; deployment remains blocked until all qualification gates pass.
    </p>
  {/if}
</div>

<style>
  .readiness-card {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 14px 16px;
    display: grid;
    gap: 12px;
  }
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }
  .title-group {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  h3 {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
  }
  .plan-tag {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 10px;
    background: var(--raised);
    color: var(--muted);
  }
  .plan-tag.fresh {
    background: var(--green-wash);
    color: var(--accent);
    border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--line));
  }
  .plan-tag.stale {
    background: color-mix(in srgb, var(--warning) 15%, transparent);
    color: var(--warning);
    border: 1px solid color-mix(in srgb, var(--warning) 35%, var(--line));
  }
  .draft-notice {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
  }
  .chip-warn {
    font-size: 10px;
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 4px;
    background: var(--warning);
    color: var(--accent-ink);
  }
  .notice-desc {
    color: var(--muted);
  }
  .pillars-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 180px), 1fr));
    gap: 12px;
    margin: 0;
  }
  .pillar-item {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }
  dt {
    font-size: 12px;
    color: var(--muted);
    font-weight: 500;
  }
  dd {
    margin: 0;
    font-size: 13px;
    font-weight: 500;
    color: var(--text);
    overflow-wrap: anywhere;
  }
  dd.ready {
    color: var(--accent);
  }
  dd.error-text {
    color: var(--danger);
  }
  dd.warn-text {
    color: var(--warning);
  }
  .support-alert {
    margin: 0;
    font-size: 12px;
    color: var(--danger);
  }
  .support-status {
    margin: 0;
    font-size: 12px;
    color: var(--warning);
  }
  .qualification-notice {
    margin: 0;
    font-size: 12px;
    color: var(--muted);
    padding: 6px 10px;
    background: var(--raised);
    border-radius: 4px;
  }
</style>
