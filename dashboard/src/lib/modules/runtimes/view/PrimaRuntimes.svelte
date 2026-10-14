<script lang="ts">
  /**
   * Prima engine settings. Shown on demand when the node launches with the
   * primacpp engine (an engine-mode indicator, not a source edit). It reuses the
   * llama.cpp runtime module's data (`presenter.status.runtimes`, the same
   * build/target/job contracts) and renders Prima-specific panels; every other
   * settings section keeps its existing component.
   *
   * Prima stays experimental: the six qualification gates render honestly from
   * `qualification_errors`, and any evidence this panel does not receive stays
   * an em-dash — never a zero or an invented pass.
   */
  import { onMount } from 'svelte';
  import PrimaPreparation from './PrimaPreparation.svelte';
  import { createApp } from '#lib/app.svelte';
  import { routes } from '#lib/shared/navigation';
  import { errors } from '#lib/shared/errors';
  import Icon from '#lib/shared/Icon.svelte';
  import ErrorMessage from '#lib/shared/ErrorMessage.svelte';
  import type { RuntimeVersion, RuntimeJob, RuntimeNode, RuntimeLibrary, PrimaDeviceReport, RuntimeSources } from '#lib/modules/runtimes/entities';
  import type { RuntimesInteractor } from '../interactor.svelte';
  let { presenter = createApp().runtimes }: { presenter?: RuntimesInteractor } = $props();

  /** Source-controlled gates (src/kllm/modules/primacpp/recipe.json). Order matters. */
  const GATES = [
    { id: 'vulkan_gpu_inference', label: 'Vulkan GPU inference', description: 'A verified Vulkan inference pass must complete on every physical device selected for a run.' },
    { id: 'api_parity', label: 'API parity', description: "Prima's HTTP surface must match the llama.cpp endpoints the dashboard and clients depend on." },
    { id: 'platform_backend_parity', label: 'Platform & backend parity', description: 'Behavior must match across every supported OS and backend combination in the cluster.' },
    { id: 'model_and_placement_parity', label: 'Model & placement parity', description: 'Qualified models must produce matching outputs under every supported rank placement.' },
    { id: 'signed_rank_lifecycle', label: 'Signed rank lifecycle', description: 'Rank join, leave and migration must complete under signed cluster control.' },
    { id: 'hardware_performance_gate', label: 'Hardware performance gate', description: "Reference hardware must meet the cluster's measured performance floor. No measurements are recorded yet." }
  ] as const;

  /** Recipe-documented capability set; the pinned binary negotiates the final set at launch. */
  const CAPABILITIES = [
    'context', 'parallel_slots', 'flash_attention', 'checkpoints', 'draft_model',
    'prima_cache', 'prompt_cache', 'kv_unified', 'tensor_split'
  ] as const;

  const library = $derived(presenter.status?.runtimes);
  const master = $derived(presenter.status?.role === 'master');
  const worker = $derived(presenter.status?.role === 'worker');
  const target = $derived(library?.target);
  const running = $derived(library?.running);
  const nodes = $derived(library?.nodes ?? []);
  const pinnedSource = $derived(library?.engine_sources?.find((s) => s.engine === 'primacpp') ?? null);

  const entries = $derived((library?.versions ?? []).filter((entry) => entry.version.engine === 'primacpp'));
  /** Prefer the cluster target (what a Prima launch pins), then the running/selected build. */
  const pinned = $derived.by(() => {
    if (!entries.length) return null;
    const byId = (id: string | null | undefined) => (id ? entries.find((entry) => entry.version.runtime_id === id) ?? null : null);
    const targetEntry = target && target.version.engine === 'primacpp' ? byId(target.version.runtime_id) : null;
    const runningEntry = running?.engine === 'primacpp' ? byId(running.runtime_id) : null;
    return targetEntry ?? runningEntry ?? byId(library?.selected) ?? entries[0];
  });
  const version = $derived(pinned?.version ?? null);
  const buildInfo = $derived(pinned?.build ?? null);
  /** The immutable identity: the recipe digest is Prima's build-pin component. */
  const pinDigest = $derived(version?.recipe_digest ?? null);
  /** Missing policy data means closed; only an explicit error list can open a gate. */
  const gateErrors = $derived(pinned?.qualification_errors);
  const gatesClosed = $derived(gateErrors ? GATES.filter((gate) => gateErrors.includes(gate.id)).length : GATES.length);
  const qualified = $derived(!!pinned && gatesClosed === 0 && pinned.inference_supported !== false);
  const inferenceBlocked = $derived(qualified ? 'Not running' : 'Blocked by qualification');

  const jobs = $derived((library?.jobs ?? []).filter((job) => job.runtime_id === version?.runtime_id));
  const lastJob = $derived(jobs.length ? jobs[jobs.length - 1] : null);
  const requiredNodes = $derived((presenter.planResult?.runtime_required_nodes as string[] | undefined) ?? [target?.master_node_id ?? (worker ? '' : presenter.status?.node_id ?? '')]);
  const readyNodes = $derived(nodes.filter((n) => n.phase === 'prepared' && n.prepared?.runtime_id === version?.runtime_id));
  const preparing = $derived(nodes.filter((n) => n.job && ['queued', 'downloading', 'building', 'verifying'].includes(n.job.phase)).length);
  const offlineNodes = $derived(nodes.filter((n) => n.phase === 'offline').length);
  const localPhase = $derived(nodes.find((n) => n.node_id === presenter.status?.node_id)?.phase?.replaceAll('_', ' ') ?? 'unknown');

  const short = (value: string | null | undefined) => value?.slice(0, 8) ?? '—';
  const disk = (bytes: number | undefined) => (bytes ? `${(bytes / 1024 ** 2).toFixed(0)} MiB` : '—');
  const label = (v: RuntimeVersion) => v.name || v.ref;
  const gateChip = (id: string) => {
    if (!gateErrors) return 'blocked'; // No policy payload yet: closed, not passed.
    return gateErrors.includes(id) ? 'unpassed' : 'passed';
  };
  const isPreparing = (job: RuntimeJob | null | undefined) => !!job && ['queued', 'downloading', 'building', 'verifying'].includes(job.phase);
  const protectedVersion = (id: string) =>
    id === target?.version.runtime_id || id === library?.selected || isPreparing(lastJob);

  let busy = $state(false);
  let error = $state('');
  let errorGeneration = $state(errors.generation);
  let details = $state<RuntimeVersion | null>(null);
  let logText = $state('');
  let logJob = $state<RuntimeJob | null>(null);
  let logNode = $state<RuntimeNode | null>(null);
  let dialog: HTMLDialogElement;
  let mode = $state<'add' | 'details' | 'logs'>('details');
  let returnFocus: HTMLElement | null = null;
  let devices = $state<PrimaDeviceReport | null>(null);
  let devicesError = $state('');

  onMount(() => {
    const abort = new AbortController();
    const deviceAbort = new AbortController();
    void action(async () => { await presenter.refresh(abort.signal); });
    void (async () => {
      const generation = errors.generation;
      try {
        const report = await presenter.primaDevices(deviceAbort.signal);
        if (generation === errors.generation) devices = report;
      } catch (failure) {
        if (generation === errors.generation && !(failure instanceof DOMException && failure.name === 'AbortError')) {
          devicesError = String(failure);
          errors.observe('prima-devices', devicesError, '', false, generation);
        }
      }
    })();
    return () => { abort.abort(); deviceAbort.abort(); };
  });

  async function action(work: () => Promise<unknown>) {
    const generation = errors.generation;
    busy = true;
    error = '';
    errorGeneration = generation;
    errors.observe('runtime-action', '', '', false, generation);
    try { await work(); }
    catch (failure) {
      if (generation === errors.generation && !(failure instanceof DOMException && failure.name === 'AbortError')) {
        error = String(failure); errors.publish('runtime-action', error, generation);
      }
    } finally { busy = false; }
  }

  function open(next: 'add' | 'details' | 'logs', v: RuntimeVersion | null = null) {
    if (v) details = v;
    returnFocus = document.activeElement as HTMLElement;
    mode = next;
    dialog.showModal();
  }
  function close() { pinController?.abort(); pinGeneration++; dialog.close(); returnFocus?.focus(); }

  async function build() {
    if (!version) return;
    await action(() => presenter.build(version.runtime_id));
  }

  /**
   * Install the pinned initial release and make it the cluster target: resolve
   * the release tag (the resolver enforces the immutable SHA), save it when the
   * library does not hold it yet, then set the target. Builds reconcile per node
   * through the same master assignment as llama.cpp.
   */
  async function installInitial() {
    const src = pinnedSource;
    if (!master || !src?.release) return;
    await action(async () => {
      const version = (library?.versions ?? []).find((entry) =>
        entry.version.engine === 'primacpp' && (!src.release_commit || entry.version.commit === src.release_commit))?.version;
      let runtimeId = version?.runtime_id ?? '';
      if (!version) {
        const resolved = await presenter.resolve<RuntimeVersion>({
          engine: 'primacpp', repository: src.repository, ref: src.release, ref_type: 'tag', name: `Prima.cpp ${src.release}`
        });
        await presenter.add(resolved);
        runtimeId = resolved.runtime_id;
      }
      try {
        await presenter.setTarget({
          runtime_id: runtimeId, expected_revision: target?.revision ?? 0, request_id: crypto.randomUUID()
        });
      } catch (failure) {
        try { await presenter.refresh(); } catch { /* Keep the original request error visible. */ }
        throw failure;
      }
    });
  }

  async function showLogs(job: RuntimeJob | null | undefined, node: RuntimeNode | null = null) {
    if (!job?.job_id) return;
    logJob = job; logNode = node;
    logText = '';
    open('logs');
    await refreshLogs();
  }

  async function refreshLogs() {
    if (!logJob?.job_id) return;
    const jobId = logJob.job_id;
    const remoteNodeId = logNode && logNode.node_id !== presenter.status?.node_id ? logNode.node_id : undefined;
    await action(async () => {
      const result = remoteNodeId !== undefined
        ? await presenter.remoteLogs(remoteNodeId, jobId)
        : await presenter.localLogs(jobId);
      logText = result.text;
    });
  }

  async function retry(node: RuntimeNode) {
    if (master) await action(() => presenter.retryNode(node.node_id));
    else if (node.node_id === presenter.status?.node_id) await build();
  }

  async function cancel(job: RuntimeJob, node: RuntimeNode | null = null) {
    if (!job.job_id) return;
    const remote = node && node.node_id !== presenter.status?.node_id;
    await action(() => remote
      ? presenter.cancelRemote(node!.node_id, job.job_id!)
      : presenter.cancelLocal(job.job_id!));
  }

  /**
   * Inline Prima pinning for the primacpp engine mode: browse, resolve and save
   * an exact source revision without leaving this screen. The recipe pin is the
   * only revision the resolver accepts, so the form opens prefilled from it;
   * masters also set the saved revision as the cluster target (the same signed
   * mechanism as the llama.cpp library), while workers stay read-only for
   * targeting and the master assigns it.
   */
  let pinName = $state('');
  let pinRepository = $state('');
  let pinRef = $state('');
  let pinRefType = $state<'tag' | 'branch' | 'commit'>('tag');
  let pinResolved = $state<RuntimeVersion | null>(null);
  let pinSources = $state<RuntimeSources | null>(null);
  let pinSourcesLoading = $state(false);
  let pinController: AbortController | null = null;
  let pinGeneration = 0;

  $effect(() => {
    pinRepository; pinRef; pinRefType; pinName;
    pinResolved = null;
    pinGeneration++;
  });

  function startPin() {
    const src = pinnedSource;
    pinRepository = src?.repository ?? '';
    // Prefer the pinned release when the recipe declares one; the commit ref is
    // the fallback and both resolve to the same immutable SHA.
    if (src?.release) { pinRef = src.release; pinRefType = 'tag'; }
    else { pinRef = src?.ref || ''; pinRefType = src?.ref_type ?? 'tag'; }
    pinName = '';
    pinResolved = null;
    pinSources = null;
    open('add');
  }

  async function discoverPinSources() {
    pinController?.abort(); pinController = new AbortController();
    const selectedRepo = pinRepository;
    const generation = pinGeneration;
    pinSourcesLoading = true;
    await action(async () => {
      const value = await presenter.sources(selectedRepo, pinController?.signal);
      if (generation === pinGeneration && selectedRepo === pinRepository) pinSources = value;
    });
    pinSourcesLoading = false;
  }

  async function resolvePin() {
    pinController?.abort(); pinController = new AbortController();
    const generation = pinGeneration;
    await action(async () => {
      const value = await presenter.resolve<RuntimeVersion>(
        { engine: 'primacpp', repository: pinRepository, ref: pinRef, ref_type: pinRefType, name: pinName },
        pinController?.signal
      );
      if (generation === pinGeneration) pinResolved = value;
    });
  }

  async function savePin() {
    if (!pinResolved) return;
    const value = pinResolved;
    await action(async () => {
      const existing = (library?.versions ?? []).find((entry) => entry.version.runtime_id === value.runtime_id);
      if (!existing) await presenter.add(value);
      if (master && value.runtime_id !== target?.version.runtime_id) {
        try {
          await presenter.setTarget({
            runtime_id: value.runtime_id, expected_revision: target?.revision ?? 0, request_id: crypto.randomUUID()
          });
        } catch (failure) {
          try { await presenter.refresh(); } catch { /* Keep the original request error visible. */ }
          throw failure;
        }
      }
      close();
    });
  }
</script>

<div class="runtime-settings">
  <div class="section-intro">
    <div><h2>Prima</h2><p>Optional experimental engine. Build and profile it now; inference stays locked until every qualification gate passes.</p></div>
  </div>

  {#if library?.control && !library.control.available}
    <div class="notice" role="status">Signed cluster control is unavailable on this node, so cluster target changes and managed runs are paused. Saving, building and locally selecting versions still works.{#if library.control.message} {library.control.message}{/if}</div>
  {/if}
  <ErrorMessage source="runtime-action" message={errorGeneration === errors.generation ? error : ''} />
  {#if library?.reconcile_error}<ErrorMessage source="runtime-reconciliation" message={library.reconcile_error} />{/if}

  <!-- Pin a Prima version: inline library entry for this node's primacpp launch. -->
  <section class="panel" aria-labelledby="pin-heading">
    <div class="runtime-panel-head"><div><h3 id="pin-heading">Pin a Prima version</h3><p>Resolve the supported fork to one immutable commit and keep it in the managed library for builds and profiling.</p></div><span class="runtime-head-meta"><button class="btn btn-accent" disabled={worker || busy} onclick={startPin}>+ Pin a version</button><span class="runtime-badge">{entries.length} pinned source{entries.length === 1 ? '' : 's'}</span></span></div>
    <div class="table-note">Prima accepts only the revision the pinned build recipe declares{pinnedSource ? ` — prefilled from ${pinnedSource.release ? `release ${pinnedSource.release}` : 'the recipe commit'}` : ''}. Masters set the saved revision as the cluster target; workers stay read-only while the master assigns targeting.</div>
  </section>

  {#if !pinned}
    <section class="panel" aria-labelledby="empty-heading">
      <div class="runtime-panel-head"><div><h3 id="empty-heading">Runtime identity</h3><p>The engine built for this launch is not in the managed library.</p></div><span class="runtime-badge">Unsaved</span></div>
      <div class="empty">
        <Icon name="cube" />
        <h4>No pinned Prima source</h4>
        <p>Pin an exact Prima revision with “Pin a version” above. Kluster resolves it to one immutable commit covered by the build recipe digest, and it appears here for builds and profiling.</p>
        <button class="btn btn-accent" disabled={worker || busy} onclick={startPin}>Pin a version</button>
        <p class="small muted">The same engine picker also remains in Settings → llama.cpp.</p>
      </div>
    </section>
  {:else}
    <!-- Runtime identity: the immutable pin. -->
    <section class="panel" aria-labelledby="identity-heading">
      <div class="runtime-panel-head"><div><h3 id="identity-heading">Runtime identity</h3><p>Pinned by Kluster's build recipe. This snapshot is immutable for the life of the process.</p></div><span class="runtime-badge green" title={pinDigest ?? undefined}>Pinned source</span></div>
      <div class="detail-grid">
        <div class="detail-col">
          <div class="detail-rows">
            <div class="detail-row"><span>Repository</span><code class="break">{version?.repository}</code></div>
            {#if pinnedSource?.release}
              <div class="detail-row"><span>Release</span><span class="mono" title={pinnedSource.release_commit}>Prima.cpp {pinnedSource.release}</span></div>
            {/if}
            <div class="detail-row"><span>Commit</span><code class="break">{version?.commit}</code></div>
            <div class="detail-row"><span>Recipe digest</span><code class="break" title={pinDigest ?? undefined}>{pinDigest ?? '—'}</code></div>
            <div class="detail-row"><span>Source identity</span><code class="break" title={version?.runtime_id}>{short(version?.runtime_id)}…</code></div>
          </div>
          <p class="small muted">The recipe digest covers the pinned source, dependency archive hashes and build implementation. Nodes may build different backends; they never build a different digest.</p>
        </div>
        <div class="detail-col">
          <p class="small muted">{buildInfo?.capabilities?.features ? 'Declared capabilities · probed from this build' : 'Documented capabilities · recipe'}</p>
          <div class="cap-wrap">
            {#each (buildInfo?.capabilities?.features ?? [...CAPABILITIES]) as cap (cap)}
              <span class="runtime-badge" title={buildInfo?.capabilities?.features ? 'Reported by the staged server during its verifying build phase' : 'Documented for this recipe; the pinned binary negotiates the final set at launch'}>{cap}</span>
            {/each}
          </div>
          {#if buildInfo?.capabilities?.backends?.length}
            <div class="detail-rows">
              <div class="detail-row"><span>Probed backends</span><span class="mono">{buildInfo.capabilities.backends.join(', ')}</span></div>
              <div class="detail-row"><span>Probed devices</span><span class="mono">{buildInfo.capabilities.devices?.length ? `${buildInfo.capabilities.devices.length} device${buildInfo.capabilities.devices.length === 1 ? '' : 's'}` : '—'}</span></div>
            </div>
          {/if}
          <p class="small muted">Staged build · this node</p>
          <div class="detail-rows">
            <div class="detail-row"><span>Staged build</span><span class="mono">{buildInfo ? `${buildInfo.backend ?? 'auto'} · ${short(buildInfo.commit)}` : '—'}</span></div>
            <div class="detail-row"><span>Disk</span><span class="mono">{disk(buildInfo?.size_bytes)}</span></div>
            <div class="detail-row"><span>Mode</span><span class="mono">{buildInfo?.prebuilt ? 'Prebuilt' : 'Source build'}</span></div>
          </div>
          {#if !qualified}
            <div class="notice warn" role="status">
              <Icon name="alert" small />
              <div>
                <strong>Prima is not qualified for inference{gatesClosed === GATES.length ? ' — all gates unpassed' : gatesClosed > 0 ? ` — ${gatesClosed} of ${GATES.length} gates unpassed` : ' — selection is disabled for this build'}.</strong>
                <p>This build can be compiled, staged and profiled for evaluation only. The engine cannot be selected for model runs until every gate passes. Prima is an optional, experimental engine.</p>
              </div>
            </div>
          {/if}
        </div>
      </div>
      <div class="policy-strip"><strong>Recipe identity is immutable.</strong><span>Every node builds the same pinned digest; recipe advancement requires a fresh prepared build.</span></div>
    </section>

    <!-- Qualification gates -->
    <section class="panel" aria-labelledby="gates-heading">
      <div class="runtime-panel-head"><div><h3 id="gates-heading">Qualification gates</h3><p>Six independent gates qualify Prima for inference. Closed gates are locked requirements, not errors.</p></div><span class="runtime-badge" class:green={gatesClosed === 0}>{GATES.length - gatesClosed} of {GATES.length} passed</span></div>
      <div class="gate-list">
        {#each GATES as gate (gate.id)}
          {@const chip = gateChip(gate.id)}
          <div class="gate-row">
            <span class="gate-lock" aria-hidden="true"><Icon name={chip === 'passed' ? 'circleCheck' : 'shield'} /></span>
            <div><h4>{gate.label}</h4><p>{gate.description}</p></div>
            <span class="chip {chip === 'passed' ? 'chip-ok' : 'chip-muted'}">{chip}</span>
          </div>
        {/each}
      </div>
      <div class="table-note">Gates are re-evaluated against the pinned source and staged build; closed gates block inference selection — not builds, profiling or placement previews.</div>
    </section>

    <!-- Cluster target (same signed mechanism as llama.cpp; Prima runs need gates plus the prepared manifest). -->
    <section class="panel" aria-labelledby="target-heading">
      <div class="runtime-panel-head"><div><h3 id="target-heading">Cluster target</h3><p>{worker ? `Preparation is assigned by ${target?.master_node_id ?? 'your master'}.` : 'A single pinned source, built per node.'}</p></div><span class="runtime-badge" class:green={master}>{worker ? 'Managed by master' : master ? 'Enforced by master' : 'Local selection'}</span></div>
      <div class="target-summary">
        <div class="running-box">
          <div class="running-top">
            <span class="muted small">{running ? 'Running version' : 'Next run'}</span>
            <span class="runtime-badge" class:green={!!running}>{running ? 'Run active' : inferenceBlocked}</span>
          </div>
          <div class="running-value">{running ? label(running) : target ? label(target.version) : version ? label(version) : 'No prepared build'}<small class="mono">{short(running?.runtime_id ?? target?.version.runtime_id ?? version?.runtime_id)}</small></div>
          <p class="small muted">{running
            ? 'The active run keeps its pinned binaries until it fully stops.'
            : inferenceBlocked === 'Not running'
              ? 'The next run uses the saved target once its participating nodes are ready.'
              : 'Inference selection stays blocked while qualification is incomplete; builds and profiling continue.'}</p>
          <a class="text-button" href={routes.run}>Review deployment →</a>
        </div>
        {#if target && target.version.runtime_id === version?.runtime_id}
          <div class="target-meta">
            <span class="muted small">{target.version.ref_type}: {target.version.ref}</span>
            <span class="mono small">{short(target.version.commit)}</span>
            <span class="runtime-badge green">Cluster target</span>
            <span class="runtime-badge">{buildInfo ? 'Build prepared' : 'Build required'}</span>
          </div>
          <p class="small muted">Distributed runs additionally require the signed manifest from the preparation below; they never launch from a local selection alone.</p>
        {:else}
          <div class="target-meta">
            <span class="runtime-badge">No Prima target set</span>
            <span class="small muted">{master ? 'Select a prepared Prima version as the cluster target when you intend to run it.' : 'The master assigns the cluster target.'}</span>
          </div>
        {/if}
      </div>
      {#if pinned?.qualification_errors?.length}
        <div class="policy-strip"><strong>Selection stays closed.</strong><span>Inference remains blocked while qualification is incomplete: {GATES.filter((gate) => (pinned.qualification_errors ?? []).includes(gate.id)).map((gate) => gate.label).join(', ')}.</span></div>
      {/if}
    </section>

    <!-- Managed builds -->
    <section class="panel" aria-labelledby="builds-heading">
      <div class="runtime-panel-head"><div><h3 id="builds-heading">Managed build</h3><p>{worker ? "The master assigns and verifies this node's builds." : "Build the pinned source for this node's backend, then prepare the cluster."}</p></div><span class="runtime-head-meta">{#if master && pinnedSource?.release}<button class="btn btn-accent" disabled={busy || target?.version.engine === 'primacpp'} onclick={installInitial}>Install {pinnedSource.release} and set target</button>{/if}</span></div>
      {#if !library?.nodes?.length}
        <div class="empty"><h4>No cluster nodes reporting</h4><p>Preparation state appears once this node and its connected workers report.</p></div>
      {:else}
        <div class="readiness-summary"><strong>{worker ? `This node: ${localPhase}` : target ? `${readyNodes.length} of ${nodes.length} nodes prepared` : 'Build and sync before preparation'}</strong><span class="mono small muted">{preparing} preparing · {offlineNodes} offline</span></div>
        <!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users must be able to scroll the wide table.) -->
        <div class="table-scroll" tabindex="0" role="region" aria-label="Prima build jobs, scroll for all columns"><table><thead><tr><th>Node</th><th>Phase</th><th>Staged artifact</th><th>Disk</th><th>Actions</th></tr></thead><tbody>
          {#each library?.nodes ?? [] as node (node.node_id)}
            {@const nodeJob = node.job}
            <tr>
              <td><strong>{node.name ?? node.node_id}</strong><small>{node.address ? `${node.address} · ` : ''}{node.role.toUpperCase()}{node.node_id === presenter.status?.node_id ? ' · THIS NODE' : ''}{requiredNodes.includes(node.node_id) ? ' · REQUIRED' : ''}</small></td>
              <td><span class="state-label" class:green={node.phase === 'prepared'} class:warning={['failed', 'offline', 'interrupted', 'cancelled'].includes(node.phase)}>{node.phase.replaceAll('_', ' ')}</span>{#if nodeJob && isPreparing(nodeJob)}<progress aria-label={`${node.name ?? node.node_id} ${nodeJob.phase}`}></progress>{/if}{#if node.error}<div class="node-error"><ErrorMessage source={`runtime-node:${node.node_id}`} message={node.error} /></div>{/if}</td>
              <td class="mono small">{short(node.prepared?.commit ?? null)}</td>
              <td class="mono small muted">{disk(node.prepared?.size_bytes)}</td>
              <td><div class="table-actions">
                {#if ['failed', 'interrupted', 'cancelled', 'waiting_for_target'].includes(node.phase)}<button class="btn" disabled={busy || !(master || node.node_id === presenter.status?.node_id)} onclick={() => retry(node)}>{node.phase === 'waiting_for_target' ? 'Sync' : 'Retry'}</button>{/if}
                <button class="btn" disabled={busy || !node.job?.job_id || node.phase === 'offline' || (worker && node.node_id !== presenter.status?.node_id)} onclick={() => showLogs(node.job, node)}>Logs</button>
                {#if nodeJob && isPreparing(nodeJob)}<button class="btn" disabled={busy || (worker && node.node_id !== presenter.status?.node_id)} onclick={() => cancel(nodeJob, node)}>Cancel</button>{/if}
              </div></td>
            </tr>
          {/each}
        </tbody></table></div>
      {/if}
      <div class="policy-strip"><strong>Phases: queued → downloading → building → verifying → staged.</strong><span>Worker nodes see this panel read-only; cancel and retry stay with the master. Failed jobs keep their staged files for inspection.</span></div>
    </section>

    <!-- Placement & profiles: the existing evaluated preparation panel. -->
    <PrimaPreparation {presenter} />

    <!-- Compute: the pinned Prima build's own device probe + selection ownership. -->
    <section class="panel" aria-labelledby="compute-heading">
      <div class="runtime-panel-head"><div><h3 id="compute-heading">Compute &amp; devices</h3><p>Backend selection stays in Hardware; run-device assignment follows the Deploy plan. This probe reports the pinned Prima build's own physical devices.</p></div><span class="runtime-badge" class:green={!!devices?.available}>{devices?.available ? `${devices.devices?.length ?? 0} probed device${(devices.devices?.length ?? 0) === 1 ? '' : 's'}` : 'Unavailable'}</span></div>
      {#if devicesError}
        <ErrorMessage source="prima-devices" message={devicesError} />
      {:else if !devices}
        <div class="policy-strip"><strong>Probe pending.</strong><span>Waiting for the local device report.</span></div>
      {:else if !devices.available}
        <div class="policy-strip"><strong>Unavailable.</strong><span>{devices.reason ?? 'No device report.'}</span></div>
      {:else}
        <!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users must be able to scroll the wide table.) -->
        <div class="table-scroll" tabindex="0" role="region" aria-label="Prima device report, scroll for all columns"><table><thead><tr><th>Index</th><th>Device</th><th>Physical UUID</th><th>Driver</th></tr></thead><tbody>
          {#each devices.devices ?? [] as device, index (device.device_identity ?? index)}
            <tr>
              <td class="mono small">{device.physical_index ?? index}</td>
              <td><strong>{device.name ?? device.description ?? '—'}</strong>{#if device.description && device.description !== device.name}<small class="break">{device.description}</small>{/if}</td>
              <td class="mono small">{device.device_identity ? `${device.device_identity.slice(0, 12)}…` : '—'}</td>
              <td class="mono small">{device.driver_identity ?? '—'}</td>
            </tr>
          {/each}
          {#if !(devices.devices ?? []).length}
            <tr><td colspan="4" class="muted small">The Prima build reports no GPU device; CPU builds and placement evaluation stay available.</td></tr>
          {/if}
        </tbody></table></div>
      {/if}
      <div class="policy-strip"><strong>Selection ownership is unchanged.</strong><span>Run-device assignment follows Hardware's GPU selection and the Deploy participants; the planner assigns layers and derives the tensor split from the planned allocation. These rows are the Prima build's own identity probe, not a selector.</span></div>
      <div class="policy-strip"><strong>Honest default: nothing is measured yet.</strong><span>No throughput, memory or device performance figures exist for this engine. Measurements appear only through the deferred profile and performance gates.</span></div>
    </section>
  {/if}
</div>

<dialog bind:this={dialog} class="runtime-dialog" aria-labelledby="runtime-dialog-title" oncancel={close}>
  <div class="runtime-dialog-head"><h2 id="runtime-dialog-title">{mode === 'add' ? 'Pin a Prima version' : mode === 'logs' ? 'Build log' : details ? label(details) : 'Version details'}</h2><button class="btn" aria-label="Close dialog" onclick={close}>×</button></div>
  <div class="runtime-dialog-body">
    <ErrorMessage source="runtime-action" message={errorGeneration === errors.generation ? error : ''} />
    {#if mode === 'add'}
      {#if pinnedSource}<p class="small muted">Prefilled from the engine's pinned recipe{pinnedSource.release ? ` (release ${pinnedSource.release})` : ''}; only that recipe revision passes resolution.</p>{/if}
      <label class="field">Display name <input bind:value={pinName} placeholder="Optional name" /></label>
      <label class="field">Public HTTPS Git repository <input bind:value={pinRepository} type="url" placeholder="https://github.com/user/prima.cpp.git" /></label>
      <button class="btn" disabled={busy || !pinRepository} onclick={discoverPinSources}>{pinSourcesLoading ? 'Loading releases…' : 'Browse releases and refs'}</button>
      {#if pinSources?.warning}<p class="small muted">{pinSources.warning}</p>{/if}
      {#if pinSources?.releases.length}<label class="field">Release <select aria-label="Release tag" onchange={(event) => { pinRefType = 'tag'; pinRef = event.currentTarget.value; }}><option value="">Choose a release</option>{#each pinSources.releases as release}<option value={release.tag}>{release.name}{release.prerelease ? ' · prerelease' : ''}</option>{/each}</select></label>{/if}
      <div class="ref-grid"><label class="field">Reference type <select bind:value={pinRefType}><option value="tag">Tag</option><option value="branch">Branch</option><option value="commit">Commit</option></select></label><label class="field">{pinRefType === 'commit' ? 'Full commit SHA' : pinRefType === 'branch' ? 'Branch name' : 'Tag'}<input bind:value={pinRef} list="prima-pin-refs" /><datalist id="prima-pin-refs">{#each (pinRefType === 'branch' ? pinSources?.branches : pinSources?.tags) ?? [] as value}<option value={value}></option>{/each}</datalist></label></div>
      <p class="small muted">Tags and branches resolve to one immutable commit covered by the recipe digest{master ? '. Saving also sets this revision as the cluster target.' : '; the master assigns the cluster target.'}</p>
      {#if pinResolved}<div class="resolved-box"><strong>Resolved source</strong><code>{pinResolved.commit}</code><span class="small break">{pinResolved.repository}</span></div>{/if}
    {:else if mode === 'logs'}
      <button class="btn" disabled={busy} onclick={refreshLogs}>Refresh log</button>
      <textarea class="log-console" readonly aria-label="Build output" rows="12" value={logText || 'No build output yet.'}></textarea>
    {:else if details}
      <dl>
        <dt>Repository</dt><dd class="break">{details.repository}</dd>
        <dt>Reference</dt><dd>{details.ref_type}: {details.ref}</dd>
        <dt>Resolved commit</dt><dd class="mono break">{details.commit}</dd>
        <dt>Recipe digest</dt><dd class="mono break">{pinDigest ?? '—'}</dd>
        <dt>Source identity</dt><dd class="mono break">{details.runtime_id}</dd>
        {#if pinned?.qualification_errors?.length}<dt>Qualification</dt><dd class="warning small">Gates pending: {pinned.qualification_errors.map((value) => value.replaceAll('_', ' ')).join(', ')}</dd>{/if}
      </dl>
    {/if}
  </div>
  <div class="runtime-dialog-foot">
    {#if mode === 'add'}<button class="btn" disabled={busy || !pinRef || !pinRepository} onclick={resolvePin}>Resolve revision</button><button class="btn btn-accent" disabled={busy || !pinResolved} onclick={savePin}>Save version{master ? ' & set target' : ''}</button>{:else if mode === 'details' && pinned}<button class="btn" disabled={busy || isPreparing(lastJob) || protectedVersion(pinned.version.runtime_id)} onclick={build}>{buildInfo ? 'Verify build' : 'Build'}</button>{/if}
    <button class="btn" onclick={close}>Close</button>
  </div>
</dialog>

<style>
  /* Prima settings reuse the llama.cpp runtime panel language (same tokens,
     panel heads, badges, tables and breakpoints) without touching app.css. */
  .runtime-settings{display:grid;gap:24px;min-width:0}
  .notice{display:flex;align-items:flex-start;gap:12px;padding:14px 16px;border:1px solid var(--line,var(--border));background:#1b1e24;color:var(--muted);font-size:14px;overflow-wrap:anywhere}
  .notice.warn{background:#252219;color:var(--warning)}
  .notice strong{color:inherit}
  .section-intro{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}
  .section-intro h2{font-size:28px}
  .section-intro p{color:var(--muted);font-size:14px;margin-top:8px}
  .runtime-panel-head{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px;padding:24px;border-bottom:1px solid var(--line,var(--border))}
  .runtime-panel-head h3{font-size:18px}
  .runtime-panel-head p{font-size:14px;color:var(--muted);margin-top:4px}
  .runtime-badge{display:inline-flex;align-items:center;gap:6px;padding:2px 8px;border:1px solid var(--line,var(--border));font:11px/1.5 var(--mono);text-transform:uppercase;letter-spacing:.4px;color:var(--muted);white-space:nowrap}
  .runtime-head-meta{display:inline-flex;align-items:center;gap:10px;flex-wrap:wrap}
  .runtime-badge.green{color:var(--accent);background:var(--green-wash,#132b22);border-color:#2b5a44}
  .detail-grid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr)}
  .detail-col{padding:24px;display:grid;gap:16px;min-width:0;align-content:start}
  .detail-col+.detail-col{border-left:1px solid var(--line,var(--border))}
  .detail-rows{display:grid}
  .detail-row{display:flex;justify-content:space-between;align-items:start;gap:24px;padding:12px 0;border-bottom:1px solid var(--line,var(--border));font-size:14px}
  .detail-row:last-child{border-bottom:0}
  .detail-row>span:first-child{color:var(--muted);flex:none}
  .detail-row>span:last-child,.detail-row>code:last-child{text-align:right;min-width:0;font-size:13px}
  .cap-wrap{display:flex;flex-wrap:wrap;gap:8px}
  .gate-list{display:grid}
  .gate-row{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:16px;align-items:center;padding:14px 24px;border-top:1px solid var(--line,var(--border))}
  .gate-row:first-child{border-top:0}
  .gate-lock{display:grid;place-items:center;width:32px;height:32px;border:1px solid var(--line,var(--border));border-radius:4px;color:var(--muted)}
  .gate-row h4{margin:0;font-size:14px;font-weight:500}
  .gate-row p{font-size:13px;color:var(--muted);margin-top:4px}
  .target-summary{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr)}
  .target-summary>div{padding:24px;display:grid;gap:16px;min-width:0;align-content:start}
  .target-summary>div+div{border-left:1px solid var(--line,var(--border))}
  .target-meta{display:flex;flex-wrap:wrap;gap:8px 16px;font-size:12px;color:var(--muted);align-items:center}
  .running-top{display:flex;align-items:center;justify-content:space-between;gap:12px}
  .running-value{font-size:24px;font-weight:500;overflow-wrap:anywhere}
  .running-value small{font-size:12px;margin-left:8px;color:var(--muted)}
  .readiness-summary{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;padding:16px 24px}
  .readiness-summary strong{font-weight:500}
  .table-scroll{min-width:0;max-width:100%;overflow-x:auto;overscroll-behavior-x:contain}
  table{width:100%;border-collapse:collapse;font-size:14px;text-align:left}
  th{padding:12px 16px;background:#101317;font:11px var(--mono);text-transform:uppercase;letter-spacing:.5px;color:var(--muted);white-space:nowrap}
  td{padding:16px;border-top:1px solid var(--line,var(--border));vertical-align:middle}
  td strong{font-size:15px;font-weight:500}
  td small{display:block;margin-top:6px;font-size:11px;color:var(--muted)}
  .table-actions{display:flex;align-items:center;justify-content:flex-end;flex-wrap:wrap;gap:8px}
  .table-actions .btn{font-size:12px;padding:8px 10px}
  .node-error{margin-top:8px}
  .policy-strip{display:grid;gap:4px;padding:16px 24px;border-top:1px solid var(--line,var(--border));font-size:14px}
  .policy-strip span{color:var(--muted)}
  .table-note{padding:16px 24px;font-size:12px;color:var(--muted);border-top:1px solid var(--line,var(--border))}
  .empty{display:grid;justify-items:center;text-align:center;gap:12px;padding:40px 24px}
  .empty :global(svg){width:32px;height:32px;color:var(--muted)}
  .empty h4{margin:0;font-size:15px;font-weight:500}
  .empty p{color:var(--muted);font-size:14px;max-width:46ch}
  .state-label{text-transform:capitalize;font-size:12px}
  .state-label.green{color:var(--accent)}
  .state-label.warning{color:var(--warn,var(--warning))}
  .warning{color:var(--warn,var(--warning))}
  .runtime-dialog{width:min(680px,calc(100vw - 32px));max-height:calc(100dvh - 48px);overflow:auto}
  .runtime-dialog-head{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:20px 24px;border-bottom:1px solid var(--line,var(--border))}
  .runtime-dialog-head h2{font-size:20px}
  .runtime-dialog-body{padding:24px;display:grid;gap:16px}
  .runtime-dialog-foot{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap;padding:20px 24px;border-top:1px solid var(--line,var(--border))}
  dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:8px 24px;margin:0}
  dt{color:var(--muted);font-size:13px}
  dd{margin:0;font-size:14px;min-width:0}
  .field{display:grid;gap:8px;font-size:14px}
  input,select{min-width:0;width:100%;padding:10px 12px;min-height:48px;border:1px solid var(--control,#647181);border-radius:4px;background:var(--bg);color:var(--fg,var(--text));font:inherit}
  .ref-grid{display:grid;grid-template-columns:auto minmax(0,1fr);gap:16px}
  .resolved-box{display:grid;gap:8px;padding:16px;border:1px solid var(--line,var(--border))}
  .resolved-box code{overflow-wrap:anywhere}
  .break{overflow-wrap:anywhere}
  .log-console{background:var(--bg);border:1px solid var(--line,var(--border));padding:16px;font:12px/1.8 var(--mono);max-height:360px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;margin:0;width:100%}
  @media(hover:hover){
    tbody tr:hover td{background:var(--raised,#181c22)}
  }
  @media(max-width:1023px){
    .detail-grid,.target-summary{grid-template-columns:1fr}
    .detail-col+.detail-col,.target-summary>div+div{border-left:0;border-top:1px solid var(--line,var(--border))}
  }
  @media(max-width:768px){
    .runtime-panel-head,.detail-col,.target-summary>div{padding:20px}
    .gate-row{padding:16px 20px;gap:12px}
    .gate-row{grid-template-columns:auto minmax(0,1fr)}
    .gate-row .chip{grid-column:2;justify-self:start}
    .table-actions{justify-content:flex-start}
  }
  @media(max-width:520px){
    .runtime-dialog-head,.runtime-dialog-body,.runtime-dialog-foot{padding:20px}
    dl{grid-template-columns:1fr;gap:8px}
    .ref-grid{grid-template-columns:1fr}
  }
</style>
