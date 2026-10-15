<script lang="ts">
  /**
   * Prima engine settings. Shown on demand when the node launches with the
   * primacpp engine (an engine-mode indicator, not a source edit). It reuses
   * the llama.cpp runtime module's data (`presenter.status.runtimes`, the same
   * build/target/job contracts) plus the shared UX machinery under `shared/`;
   * every other settings section keeps its existing component.
   *
   * Prima stays experimental: the six qualification gates render honestly from
   * `qualification_errors`, and any evidence this panel does not receive stays
   * an em-dash — never a zero or an invented pass.
   */
  import { onMount } from 'svelte';
  import PrimaPreparation from './PrimaPreparation.svelte';
  import SourcePinForm from './shared/SourcePinForm.svelte';
  import NodeBuildTable from './shared/NodeBuildTable.svelte';
  import {
    RuntimeActions, isPreparingJob, shortSha, formatDisk, versionLabel
  } from './shared/runtimeActions.svelte';
  import './shared/runtime-shared.css';
  import { createApp } from '#lib/app.svelte';
  import { routes } from '#lib/shared/navigation';
  import { errors } from '#lib/shared/errors';
  import Icon from '#lib/shared/Icon.svelte';
  import ErrorMessage from '#lib/shared/ErrorMessage.svelte';
  import type { RuntimeVersion, RuntimeJob, RuntimeNode, PrimaDeviceReport } from '#lib/modules/runtimes/entities';
  import type { RuntimesInteractor } from '../interactor.svelte';
  let { presenter = createApp().runtimes }: { presenter?: RuntimesInteractor } = $props();

  // Derived so a presenter prop swap rebuilds the wrapper with the new interactor.
  const actions = $derived(new RuntimeActions(presenter));

  /** Source-controlled gates (src/kllm/modules/primacpp/interactors/recipe.py). Order matters. */
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
  /** The digest the engine publishes now; absent payloads stay unknown, never assumed current. */
  const sourceDigest = $derived(pinnedSource?.recipe_digest ?? null);
  /** A saved pin from an earlier build recipe cannot be built, prepared or run as-is. */
  const staleRecipe = $derived(!!pinDigest && !!sourceDigest && pinDigest !== sourceDigest);
  /** Missing policy data means closed; only an explicit error list can open a gate. */
  const gateErrors = $derived(pinned?.qualification_errors);
  const gatesClosed = $derived(gateErrors ? GATES.filter((gate) => gateErrors.includes(gate.id)).length : GATES.length);
  const qualified = $derived(!!pinned && !staleRecipe && gatesClosed === 0 && pinned.inference_supported !== false);
  const inferenceBlocked = $derived(qualified ? 'Not running' : staleRecipe ? 'Blocked by stale build' : 'Blocked by qualification');

  const jobs = $derived((library?.jobs ?? []).filter((job) => job.runtime_id === version?.runtime_id));
  const lastJob = $derived(jobs.length ? jobs[jobs.length - 1] : null);
  const requiredNodes = $derived((presenter.planResult?.runtime_required_nodes as string[] | undefined) ?? [target?.master_node_id ?? (worker ? '' : presenter.status?.node_id ?? '')]);
  const readyNodes = $derived(nodes.filter((n) => n.phase === 'prepared' && n.prepared?.runtime_id === version?.runtime_id));
  const preparing = $derived(nodes.filter((n) => n.job && ['queued', 'downloading', 'building', 'verifying'].includes(n.job.phase)).length);
  const offlineNodes = $derived(nodes.filter((n) => n.phase === 'offline').length);
  const localPhase = $derived(nodes.find((n) => n.node_id === presenter.status?.node_id)?.phase?.replaceAll('_', ' ') ?? 'unknown');

  const gateChip = (id: string) => {
    if (!gateErrors) return 'blocked'; // No policy payload yet: closed, not passed.
    return gateErrors.includes(id) ? 'unpassed' : 'passed';
  };
  const protectedVersion = (id: string) =>
    id === target?.version.runtime_id || id === library?.selected || isPreparingJob(lastJob);

  let dialog: HTMLDialogElement;
  let mode = $state<'add' | 'details' | 'logs'>('details');
  let details = $state<RuntimeVersion | null>(null);
  let returnFocus: HTMLElement | null = null;
  /** Pin-the-source defaults, always prefilled from the engine's pinned recipe. */
  let pinDefaults = $state({ repository: '', ref: '', refType: 'tag' as 'tag' | 'branch' | 'commit', name: '' });

  let devices = $state<PrimaDeviceReport | null>(null);
  let devicesError = $state('');

  onMount(() => {
    const abort = new AbortController();
    const deviceAbort = new AbortController();
    void actions.action(async () => { await presenter.refresh(abort.signal); });
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

  function open(next: 'add' | 'details' | 'logs', v: RuntimeVersion | null = null) {
    if (v) details = v;
    returnFocus = document.activeElement as HTMLElement;
    mode = next;
    dialog.showModal();
  }
  function close() { dialog.close(); returnFocus?.focus(); }

  async function build() {
    if (!version) return;
    await actions.action(() => presenter.build(version.runtime_id));
  }

  /**
   * Install the pinned initial release and make it the cluster target: resolve
   * the release tag (the resolver enforces the immutable SHA and the current
   * build recipe), save it when the library does not hold this exact identity
   * yet, then set the target. A saved pin from an earlier recipe is never
   * reused — it can never be built again. Builds reconcile per node through
   * the same master assignment as llama.cpp.
   */
  async function installInitial() {
    const src = pinnedSource;
    if (!master || !src?.release) return;
    await actions.action(async () => {
      const resolved = await presenter.resolve<RuntimeVersion>({
        engine: 'primacpp', repository: src.repository, ref: src.release, ref_type: 'tag', name: `Prima.cpp ${src.release}`
      });
      const existing = (library?.versions ?? []).find((entry) => entry.version.runtime_id === resolved.runtime_id);
      if (!existing) await presenter.add(resolved);
      try {
        await presenter.setTarget({
          runtime_id: resolved.runtime_id, expected_revision: target?.revision ?? 0, request_id: crypto.randomUUID()
        });
      } catch (failure) {
        try { await presenter.refresh(); } catch { /* Keep the original request error visible. */ }
        throw failure;
      }
    });
  }

  async function retry(node: RuntimeNode) {
    if (master) await actions.action(() => presenter.retryNode(node.node_id));
    else if (node.node_id === presenter.status?.node_id) await build();
  }

  /**
   * Inline Prima pinning: the recipe pin is the only revision the resolver
   * accepts, so the form opens prefilled from it; masters also set the saved
   * revision as the cluster target (the same signed mechanism as llama.cpp),
   * while workers stay read-only for targeting.
   */
  function startPin() {
    const src = pinnedSource;
    // Prefer the pinned release when the recipe declares one; the commit ref is
    // the fallback and both resolve to the same immutable SHA.
    if (src?.release) { pinDefaults = { repository: src.repository, ref: src.release, refType: 'tag', name: '' }; }
    else { pinDefaults = { repository: src?.repository ?? '', ref: src?.ref || '', refType: src?.ref_type ?? 'tag', name: '' }; }
    open('add');
  }

  async function savePinned(value: RuntimeVersion) {
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
  }
</script>

<div class="runtime-settings">
  <div class="section-intro"><div><h2>Prima</h2><p>Optional experimental engine. Build and profile it now; inference stays locked until every qualification gate passes.</p></div></div>

  {#if library?.control && !library.control.available}
    <div class="notice" role="status">Signed cluster control is unavailable on this node, so cluster target changes and managed runs are paused. Saving, building and locally selecting versions still works.{#if library.control.message} {library.control.message}{/if}</div>
  {/if}
  <ErrorMessage source="runtime-action" message={actions.message} />
  {#if library?.reconcile_error}<ErrorMessage source="runtime-reconciliation" message={library.reconcile_error} />{/if}

  {#if !pinned}
    <!-- Runtime identity: the engine built for this launch has no saved pin yet. -->
    <section class="panel" aria-labelledby="empty-heading">
      <div class="runtime-panel-head"><div><h3 id="empty-heading">Runtime identity</h3><p>Pin a Prima version: resolve the supported fork to one immutable commit and keep it in the managed library.</p></div><span class="runtime-badge">{entries.length} pinned source{entries.length === 1 ? '' : 's'}</span></div>
      <div class="empty">
        <Icon name="cube" />
        <h4>No pinned Prima source</h4>
        <p>Pin an exact Prima revision with “+ Pin a version”. Kluster resolves it to one immutable commit covered by the build recipe digest, and it appears here for builds and profiling.</p>
        <button class="btn btn-accent" disabled={worker || actions.busy} onclick={startPin}>+ Pin a version</button>
        <p class="small muted">The same engine picker also remains in Settings → llama.cpp.</p>
      </div>
    </section>
  {:else}
    <section class="panel" aria-labelledby="identity-heading">
      <div class="runtime-panel-head">
        <div><h3 id="identity-heading">Runtime identity</h3><p>Pinned by Kluster's build recipe. This snapshot is immutable for the life of the process.</p></div>
        <div class="runtime-head-meta">
          <span class="runtime-badge" title="Library entries with the primacpp engine">{entries.length} pinned source{entries.length === 1 ? '' : 's'}</span>
          <span class="runtime-badge green" title={pinDigest ?? undefined}>Pinned source</span>
          <button class="btn" disabled={worker || actions.busy} onclick={startPin}>+ Pin a version</button>
        </div>
      </div>
      <div class="detail-grid">
        <div class="detail-col">
          <div class="detail-rows">
            <div class="detail-row"><span>Repository</span><code class="break">{version?.repository}</code></div>
            {#if pinnedSource?.release}
              <div class="detail-row"><span>Release</span><span class="mono" title={pinnedSource.release_commit}>Prima.cpp {pinnedSource.release}</span></div>
            {/if}
            <div class="detail-row"><span>Commit</span><code class="break">{version?.commit}</code></div>
            <div class="detail-row"><span>Recipe digest</span><code class="break" title={pinDigest ?? undefined}>{pinDigest ?? '—'}</code></div>
            <div class="detail-row"><span>Source identity</span><code class="break" title={version?.runtime_id}>{shortSha(version?.runtime_id)}…</code></div>
          </div>
          <p class="small muted">The recipe digest covers the pinned source, dependency archive hashes and build implementation. Nodes may build different backends; they never build a different digest.</p>
        </div>
        <div class="detail-col">
          <details class="cap-details">
            <summary>Capabilities &amp; staged build</summary>
            <div>
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
              <div class="detail-rows">
                <div class="detail-row"><span>Staged build · this node</span><span class="mono">{buildInfo ? `${buildInfo.backend ?? 'auto'} · ${shortSha(buildInfo.commit)}` : '—'}</span></div>
                <div class="detail-row"><span>Disk</span><span class="mono">{formatDisk(buildInfo?.size_bytes)}</span></div>
                <div class="detail-row"><span>Mode</span><span class="mono">{buildInfo?.prebuilt ? 'Prebuilt' : 'Source build'}</span></div>
              </div>
            </div>
          </details>
        </div>
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
      <div class="policy-strip"><strong>Recipe identity is immutable.</strong><span>Every node builds the same pinned digest; recipe advancement requires a fresh prepared build.</span></div>
    </section>

    <!-- Qualification gates: compact rows; the full requirement text stays on hover. -->
    <section class="panel" aria-labelledby="gates-heading">
      <div class="runtime-panel-head"><div><h3 id="gates-heading">Qualification gates</h3><p>Closed gates are locked requirements, not errors.</p></div><span class="runtime-badge" class:green={gatesClosed === 0}>{GATES.length - gatesClosed} of {GATES.length} passed</span></div>
      <div class="gate-list">
        {#each GATES as gate (gate.id)}
          {@const chip = gateChip(gate.id)}
          <div class="gate-row" title={gate.description}>
            <span class="gate-lock" aria-hidden="true"><Icon name={chip === 'passed' ? 'circleCheck' : 'shield'} /></span>
            <h4>{gate.label}</h4>
            <span class="chip {chip === 'passed' ? 'chip-ok' : 'chip-muted'}">{chip}</span>
          </div>
        {/each}
      </div>
      <div class="table-note">Gates re-evaluate against the pinned source and staged build; closed gates block inference selection — not builds, profiling or placement previews.</div>
    </section>

    <!-- Build & run: one panel for the pinned target, its cluster reach and jobs. -->
    <section class="panel" aria-labelledby="build-run-heading">
      <div class="runtime-panel-head">
        <div><h3 id="build-run-heading">Build &amp; run</h3><p>{worker ? "The master assigns and verifies this node's builds." : 'A single pinned source, built per node for its backend.'}</p></div>
        <div class="runtime-head-meta">
          {#if master && pinnedSource?.release}<button class="btn btn-accent" disabled={actions.busy || (target?.version.engine === 'primacpp' && !staleRecipe)} onclick={installInitial}>Install {pinnedSource.release} and set target</button>{/if}
          <span class="runtime-badge" class:green={master}>{worker ? 'Managed by master' : master ? 'Enforced by master' : 'Local selection'}</span>
        </div>
      </div>
      {#if staleRecipe}
        <div class="policy-strip"><strong>The saved pin is stale.</strong><span>The build recipe changed after this source was resolved, so this pin can no longer be built, prepared or run. {master ? `Install ${pinnedSource?.release ?? 'the pinned release'} and set target to resolve the current recipe and rebuild.` : 'The master resolves the current recipe and rebuilds it.'} Past builds stay in the library for inspection.</span></div>
      {/if}
      <div class="target-summary">
        <div class="running-box">
          <div class="running-top">
            <span class="muted small">{running ? 'Running version' : 'Next run'}</span>
            <span class="runtime-badge" class:green={!!running}>{running ? 'Run active' : inferenceBlocked}</span>
          </div>
          <div class="running-value">{running ? versionLabel(running) : target ? versionLabel(target.version) : version ? versionLabel(version) : 'No prepared build'}<small class="mono">{shortSha(running?.runtime_id ?? target?.version.runtime_id ?? version?.runtime_id)}</small></div>
          <p class="small muted">{running
            ? 'The active run keeps its pinned binaries until it fully stops.'
            : inferenceBlocked === 'Not running'
              ? 'The next run uses the saved target once its participating nodes are ready.'
              : staleRecipe
                ? 'The saved source predates the current build recipe; resolve the recipe and rebuild before a run.'
                : 'Inference selection stays blocked while qualification is incomplete; builds and profiling continue.'}</p>
          <a class="text-button" href={routes.run}>Review deployment →</a>
        </div>
        <div>
          {#if target && target.version.runtime_id === version?.runtime_id}
            <div class="target-meta">
              <span class="muted small">{target.version.ref_type}: {target.version.ref}</span>
              <span class="mono small">{shortSha(target.version.commit)}</span>
              <span class="runtime-badge green">Cluster target</span>
              <span class="runtime-badge" class:warning={staleRecipe}>{staleRecipe ? 'Build stale · recipe changed' : buildInfo ? 'Build prepared' : 'Build required'}</span>
            </div>
            <p class="small muted">Distributed runs additionally require the signed manifest from the preparation below; they never launch from a local selection alone.</p>
          {:else}
            <div class="target-meta">
              <span class="runtime-badge">No Prima target set</span>
              <span class="small muted">{master ? 'Select a prepared Prima version as the cluster target when you intend to run it.' : 'The master assigns the cluster target.'}</span>
            </div>
          {/if}
        </div>
      </div>
      {#if pinned?.qualification_errors?.length}
        {@const labelFor = (value: string) => GATES.find((gate) => gate.id === value)?.label ?? value.replaceAll('_', ' ')}
        <div class="policy-strip"><strong>Selection stays closed.</strong><span>Inference remains blocked while qualification is incomplete: {pinned.qualification_errors.map(labelFor).join(', ')}.</span></div>
      {/if}
      {#if !nodes.length}
        <div class="empty"><h4>No cluster nodes reporting</h4><p>Preparation state appears once this node and its connected workers report.</p></div>
      {:else}
        <div class="readiness-summary"><strong>{worker ? `This node: ${localPhase}` : target ? `${readyNodes.length} of ${nodes.length} nodes prepared` : 'Build and sync before preparation'}</strong><span class="mono small muted">{preparing} preparing · {offlineNodes} offline</span></div>
        <NodeBuildTable {nodes} {presenter} {actions} openLogs={() => open('logs')} onretry={retry} variant="prima" {requiredNodes} tableLabel="Prima build jobs, scroll for all columns" />
      {/if}
      <div class="table-note">Phases: queued → downloading → building → verifying → staged. Worker nodes see builds read-only; cancel and retry stay with the master. Failed jobs keep their staged files for inspection.</div>
    </section>

    <!-- Placement & profiles: the existing evaluated preparation panel. -->
    <PrimaPreparation {presenter} />

    <!-- Compute: the pinned Prima build's own device probe. -->
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
      <div class="table-note">Run-device assignment follows Hardware's GPU selection and the Deploy participants; the planner derives the tensor split from the planned allocation. No performance figures exist for this engine yet — measurements appear only through the deferred profile and performance gates.</div>
    </section>
  {/if}
</div>

<dialog bind:this={dialog} class="runtime-dialog" aria-labelledby="runtime-dialog-title" oncancel={close}>
  <div class="runtime-dialog-head"><h2 id="runtime-dialog-title">{mode === 'add' ? 'Pin a Prima version' : mode === 'logs' ? 'Build log' : details ? versionLabel(details) : 'Version details'}</h2><button class="btn" aria-label="Close dialog" onclick={close}>×</button></div>
  <div class="runtime-dialog-body">
    {#if mode === 'add'}
      {#if pinnedSource}<p class="small muted">Prefilled from the engine's pinned recipe{pinnedSource.release ? ` (release ${pinnedSource.release})` : ''}; only that recipe revision passes resolution.</p>{/if}
      <SourcePinForm
        {presenter}
        {actions}
        engine="primacpp"
        initial={pinDefaults}
        datalistId="prima-pin-refs"
        placeholder="https://github.com/user/prima.cpp.git"
        saveLabel={master ? 'Save version & set target' : 'Save version'}
        footNote={master
          ? 'Tags and branches resolve to one immutable commit covered by the recipe digest. Saving also sets this revision as the cluster target.'
          : 'Tags and branches resolve to one immutable commit covered by the recipe digest; the master assigns the cluster target.'}
        onsave={savePinned}
        onclose={close}
      />
    {:else if mode === 'logs'}
      <ErrorMessage source="runtime-action" message={actions.message} />
      <button class="btn" disabled={actions.busy} onclick={() => actions.refreshLogs()}>Refresh log</button>
      <textarea class="log-console" readonly aria-label="Build output" rows="12" value={actions.logText || 'No build output yet.'}></textarea>
    {:else}
      <ErrorMessage source="runtime-action" message={actions.message} />
      <dl>
        <dt>Repository</dt><dd class="break">{details?.repository}</dd>
        <dt>Reference</dt><dd>{details?.ref_type}: {details?.ref}</dd>
        <dt>Resolved commit</dt><dd class="mono break">{details?.commit}</dd>
        <dt>Recipe digest</dt><dd class="mono break">{pinDigest ?? '—'}</dd>
        <dt>Source identity</dt><dd class="mono break">{details?.runtime_id}</dd>
        {#if pinned?.qualification_errors?.length}<dt>Qualification</dt><dd class="warning small">Gates pending: {pinned.qualification_errors.map((value) => value.replaceAll('_', ' ')).join(', ')}</dd>{/if}
      </dl>
    {/if}
  </div>
  {#if mode !== 'add'}
    <div class="runtime-dialog-foot"><button class="btn" onclick={close}>Close</button>{#if mode === 'details' && pinned}<button class="btn" disabled={actions.busy || isPreparingJob(lastJob) || protectedVersion(pinned.version.runtime_id)} onclick={build}>{buildInfo ? 'Verify build' : 'Build'}</button>{/if}</div>
  {/if}
</dialog>
