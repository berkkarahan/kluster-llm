<script lang="ts">
  import PrimaPreparation from './PrimaPreparation.svelte';
  import { createApp } from '#lib/app.svelte';
  import { routes } from '#lib/shared/navigation';
  import { onMount } from 'svelte';
  import { errors } from '#lib/shared/errors';
  import ErrorMessage from '#lib/shared/ErrorMessage.svelte';
  import SourcePinForm from './shared/SourcePinForm.svelte';
  import NodeBuildTable from './shared/NodeBuildTable.svelte';
  import {
    RuntimeActions, isPreparingJob, shortSha, formatDisk, versionLabel
  } from './shared/runtimeActions.svelte';
  import './shared/runtime-shared.css';
  import type { RuntimeVersion, RuntimeNode } from '#lib/modules/runtimes/entities';
  import type { RuntimesInteractor } from '../interactor.svelte';
  let { presenter = createApp().runtimes }: { presenter?: RuntimesInteractor } = $props();

  const upstream = 'https://github.com/ggml-org/llama.cpp.git';
  // Derived so a presenter prop swap rebuilds the wrapper with the new interactor.
  const actions = $derived(new RuntimeActions(presenter));
  const library = $derived(presenter.status?.runtimes);
  const bootstrap = $derived(library?.bootstrap ?? { repository: upstream, ref: 'v0.5.0' });
  const bootstrapVersion = $derived(library?.versions.find(entry => entry.version.repository === bootstrap.repository && entry.version.ref_type === 'tag' && entry.version.ref === bootstrap.ref)?.version);
  const master = $derived(presenter.status?.role === 'master');
  const worker = $derived(presenter.status?.role === 'worker');
  const target = $derived(library?.target);
  const running = $derived(library?.running);
  const nodes = $derived(library?.nodes ?? []);
  const requiredNodes = $derived((presenter.planResult?.runtime_required_nodes as string[] | undefined) ?? [target?.master_node_id ?? (worker ? '' : presenter.status?.node_id ?? '')]);
  const readyNodes = $derived(nodes.filter(n => n.phase === 'prepared' && n.prepared?.runtime_id === target?.version.runtime_id));
  const preparing = $derived(nodes.filter(n => ['queued', 'downloading', 'building', 'verifying'].includes(n.phase)).length);
  const waiting = $derived(nodes.filter(n => n.phase === 'waiting_for_target').length);
  const localPhase = $derived(nodes.find(n => n.node_id === presenter.status?.node_id)?.phase?.replaceAll('_', ' ') ?? 'unknown');
  const offlineNodes = $derived(nodes.filter(n => n.phase === 'offline').length);
  let draft = $state('');
  let dialog: HTMLDialogElement;
  let mode = $state<'add' | 'logs' | 'details' | 'remove'>('add');
  let sourceDefaults = $state({ repository: upstream, ref: 'v0.5.0', refType: 'tag' as 'tag' | 'branch' | 'commit', name: '' });
  let engine = $state<'llamacpp' | 'primacpp'>('llamacpp');
  let details = $state<RuntimeVersion | null>(null);
  let returnFocus: HTMLElement | null = null;

  $effect(() => { if (!draft && target?.version.runtime_id) draft = target.version.runtime_id; });
  $effect(() => {
    for (const job of library?.jobs ?? []) errors.observe(`runtime-job:${job.job_id}`, job.error ?? '', job.job_id ?? '');
    for (const node of nodes) errors.observe(`runtime-node:${node.node_id}`, node.error ?? '', node.job?.job_id ?? '');
  });

  onMount(() => {
    const abort = new AbortController();
    void actions.action(async () => { await presenter.refresh(abort.signal); });
    return () => { abort.abort(); };
  });

  function open(next: typeof mode) {
    returnFocus = document.activeElement as HTMLElement;
    mode = next;
    dialog.showModal();
  }
  function close() { dialog.close(); returnFocus?.focus(); }
  function add() {
    engine = 'llamacpp';
    sourceDefaults = { repository: library?.bootstrap?.repository ?? upstream, ref: library?.bootstrap?.ref || 'v0.5.0', refType: 'tag', name: '' };
    open('add');
  }
  function chooseEngine(value: 'llamacpp' | 'primacpp') {
    engine = value;
    if (value === 'primacpp') {
      const source = library?.engine_sources?.find(s => s.engine === value);
      if (source) {
        // Prefer the pinned release when the recipe declares one; the commit
        // ref stays the fallback and both resolve to the same immutable SHA.
        if (source.release) { sourceDefaults = { repository: source.repository, ref: source.release, refType: 'tag', name: '' }; }
        else { sourceDefaults = { repository: source.repository, ref: source.ref, refType: source.ref_type, name: '' }; }
      }
    } else {
      sourceDefaults = { repository: bootstrap.repository, ref: bootstrap.ref, refType: 'tag', name: '' };
    }
  }
  async function setTarget(runtimeId: string) {
    await actions.action(async () => {
      try {
        await presenter.setTarget({ runtime_id: runtimeId, expected_revision: target?.revision ?? 0, request_id: crypto.randomUUID() });
      } catch (failure) {
        // A dropped response can hide a successful target change; refresh before exposing retry.
        try { await presenter.refresh(); } catch { /* Keep the original request error visible. */ }
        throw failure;
      }
    });
  }
  async function installDefault() {
    if (!master) return;
    await actions.action(async () => {
      let version = bootstrapVersion;
      if (!version) {
        version = await presenter.resolve<RuntimeVersion>({
          repository: bootstrap.repository, ref: bootstrap.ref, ref_type: 'tag', name: bootstrap.ref
        });
        await presenter.add(version);
      }
      draft = version.runtime_id;
      try {
        await presenter.setTarget({
          runtime_id: version.runtime_id, expected_revision: target?.revision ?? 0, request_id: crypto.randomUUID()
        });
      } catch (failure) {
        try { await presenter.refresh(); } catch { /* Keep the original request error visible. */ }
        throw failure;
      }
    });
  }
  async function build(version: RuntimeVersion) {
    await actions.action(() => presenter.build(version.runtime_id));
  }
  async function selectLocal(version: RuntimeVersion) {
    await actions.action(() => presenter.selectLocal(version.runtime_id));
  }
  function jobFor(id: string) { return [...(library?.jobs ?? [])].reverse().find(job => job.runtime_id === id); }
  function protectedVersion(id: string) {
    return id === target?.version.runtime_id || id === running?.runtime_id || id === library?.selected || isPreparingJob(jobFor(id));
  }
  function inspect(version: RuntimeVersion) { details = version; open('details'); }
  function remove(version: RuntimeVersion) { details = version; open('remove'); }
  function scopeLabel(scope: string) { const os = scope.split('-')[0]; return `${os === 'darwin' ? 'macOS' : os === 'linux' ? 'Linux' : 'Windows'}/Vulkan`; }
  async function sync() { await setTarget(draft); }
  async function confirmRemove() {
    if (!details) return;
    await actions.action(async () => { await presenter.remove(details!.runtime_id); close(); });
  }
  async function retry(node: RuntimeNode) {
    if (master) await actions.action(() => presenter.retryNode(node.node_id));
    else if (target && node.node_id === presenter.status?.node_id) await build(target.version);
  }
  async function saveVersion(value: RuntimeVersion) {
    await presenter.add(value);
    draft = value.runtime_id;
  }
</script>

<div class="runtime-settings">
  <div class="section-intro"><div><h2>llama.cpp</h2><p>Pin the source. Prepare every node. Keep the current run safe.</p></div></div>
  {#if worker}<div class="notice">Managed by master. The cluster target is assigned by {target?.master_node_id ?? 'your master'}.</div>{/if}
  {#if library?.target_state === 'label_stale'}<div class="notice" role="status">The master’s node label changed. The saved target is being re-sent automatically.</div>{/if}
  {#if library?.target_state === 'binding_stale'}<div class="notice" role="status">The cluster signing binding changed. {library.target_error ?? 'Re-apply the saved target to continue syncing.'}</div>{/if}
  {#if library?.control && !library.control.available}
    <div class="notice" role="status">Signed cluster control is unavailable on this node, so cluster target changes and managed runs are paused. Saving, building and locally selecting versions still works.{#if library.control.message} {library.control.message}{/if}</div>
  {/if}
  <ErrorMessage source="runtime-action" message={actions.message} />
  {#if library?.reconcile_error}<ErrorMessage source="runtime-reconciliation" message={library.reconcile_error} />{/if}

  <section class="panel" aria-labelledby="target-heading">
    <div class="runtime-panel-head"><div><h3 id="target-heading">Cluster target</h3><p>A single source revision, built for each node.</p></div><span class="runtime-badge" class:green={master}>{worker ? 'Managed by master' : master ? 'Enforced by master' : 'Local selection'}</span></div>
    <div class="target-grid">
      <div class="target-picker">
        <div class="target-control"><label class="field"><span>Target version {#if draft && draft !== target?.version.runtime_id}<small>· Unsaved selection</small>{/if}</span>
          <select bind:value={draft} disabled={worker || actions.busy} aria-label="Target llama.cpp version"><option value="">Choose a saved version</option>{#each library?.versions ?? [] as entry}<option value={entry.version.runtime_id}>{versionLabel(entry.version)} · {entry.version.repository === upstream ? 'Upstream' : 'Fork'}</option>{/each}</select></label>
          {#if master}<button class="btn btn-accent" disabled={!draft || actions.busy} onclick={sync}>Set target &amp; sync</button>{/if}
        </div>
        {#if target}<div class="target-meta"><span>{target.version.ref_type}: {target.version.ref}</span><code>{shortSha(target.version.commit)}</code><span class="runtime-badge">Immutable SHA</span></div><p class="muted small break">{target.version.repository}</p>{:else}<p class="muted small">Add a version to choose an exact source revision.</p>{/if}
        <p class="muted small">{worker ? 'Local builds use this node’s configured backend.' : 'Joining and returning workers prepare the saved target automatically.'}</p>
      </div>
      <div class="running-box"><div class="running-top"><span class="muted small">{running ? 'Running version' : 'Next run'}</span><span class="runtime-badge" class:green={!!running}>{running ? 'Run active' : 'No active run'}</span></div>
        <div class="running-value">{running ? versionLabel(running) : target ? versionLabel(target.version) : 'Awaiting selection'}<small class="mono">{shortSha(running?.commit ?? target?.version.commit)}</small></div>
        <p class="small muted">{running && running.runtime_id !== target?.version.runtime_id ? 'The active run keeps its original binaries. Prepared builds switch after it fully stops.' : 'The next run uses the saved target once its participating nodes are ready.'}</p>
        <a class="text-button" href={routes.run}>Review deployment →</a>
      </div>
    </div>
    <div class="policy-strip"><strong>One target. Local builds. Persistent enforcement.</strong><span>Backend builds may differ; source revisions must match.</span></div>
    {#if running && target && running.runtime_id !== target.version.runtime_id}<div class="pending-note"><strong>Pending target: {versionLabel(target.version)}</strong><span class="mono small">{shortSha(target.version.commit)}</span><p>Preparation runs beside {versionLabel(running)}.</p></div>{/if}
  </section>

  <section class="panel" aria-labelledby="library-heading">
    <div class="runtime-panel-head"><div><h3 id="library-heading">Saved versions <span class="muted small">/ {library?.versions.length ?? 0}</span></h3><p>Keep known-good builds. Try a new revision without replacing them.</p></div><div class="version-actions">{#if master}<button class="btn btn-accent" disabled={actions.busy || (bootstrapVersion !== undefined && bootstrapVersion.runtime_id === target?.version.runtime_id)} onclick={installDefault}>Install {bootstrap.ref} and set target</button>{/if}<button class="btn" disabled={worker || actions.busy} onclick={add}>+ Add version</button></div></div>
    {#if !library?.versions.length}<div class="empty"><h3>No managed versions yet</h3><p>{library?.legacy ? 'A legacy runtime is installed. Prepare a managed source snapshot to verify its identity across the cluster.' : 'Save an upstream release or a public Git fork to get started.'}</p><button class="btn" disabled={worker || actions.busy} onclick={add}>Add upstream version</button></div>{/if}
    {#each library?.versions ?? [] as entry (entry.version.runtime_id)}
      {@const version = entry.version}
      {@const job = jobFor(version.runtime_id)}
      <article class="version-row">
        <div class="version-info"><div class="version-name"><button class="text-button" onclick={() => inspect(version)}>{versionLabel(version)}</button>{#if version.runtime_id === target?.version.runtime_id}<span class="runtime-badge green">Target</span>{:else if version.runtime_id === running?.runtime_id}<span class="runtime-badge">Running</span>{:else if version.repository !== upstream}<span class="runtime-badge">Fork</span>{/if}{#if entry.build?.prebuilt}<span class="runtime-badge" title="Installed from a prebuilt release pack. The managed 10GbE private network needs a source build of this version.">Prebuilt</span>{/if}</div><div class="version-source">{version.engine === 'primacpp' ? 'Prima.cpp · ' : ''}{version.repository}</div><div class="version-ref"><span>{version.ref_type} · {version.ref}</span><code>{shortSha(version.commit)}</code></div></div>
        <div class="version-backend"><span>{entry.build?.backend ?? presenter.status?.accel_backend ?? 'Auto backend'}</span><span class="small" class:green={!!entry.build}>{isPreparingJob(job) ? job?.phase : job?.phase === 'failed' ? 'Failed' : entry.build?.prebuilt ? 'Prebuilt' : entry.build ? 'Built' : 'Not built'}</span></div>
        <div class="version-disk mono small muted">{formatDisk(entry.build?.size_bytes)}</div>
        <div class="version-actions"><button class="btn" disabled={actions.busy || isPreparingJob(job) || worker && version.runtime_id !== target?.version.runtime_id} onclick={() => build(version)}>{entry.build ? 'Verify build' : 'Build'}</button><button class="btn" disabled={!job?.job_id || actions.busy} onclick={() => actions.showLogs(job, null, () => open('logs'))}>Logs</button>
          {#if master}<button class="btn" disabled={actions.busy || draft === version.runtime_id || entry.inference_supported === false} onclick={() => draft = version.runtime_id}>Select</button>{:else if !worker}<button class="btn" disabled={!entry.build || actions.busy || !!running || entry.inference_supported === false} onclick={() => selectLocal(version)}>Use locally</button>{/if}
          <button class="btn runtime-remove" title={protectedVersion(version.runtime_id) ? 'Running, selected, targeted or building versions are protected' : 'Remove this local version'} aria-label={`Remove ${versionLabel(version)}`} disabled={actions.busy || worker || protectedVersion(version.runtime_id)} onclick={() => remove(version)}>×</button>
        </div>
        {#if entry.scope?.endsWith('-vulkan-local')}
          <p class="small muted">{entry.inference_supported ? 'Qualified for local' : 'Local'} {scopeLabel(entry.scope)}{entry.inference_supported ? '' : ' qualification pending'}. Multi-node runs require separate physical qualification.</p>
          {#if entry.inference_supported}<p class="small muted">Qualified models: {entry.qualified_models?.map(model => model.name ?? model.sha256.slice(0, 12)).join(', ')}. Features: {entry.qualified_features?.join(', ')}.</p>{/if}
        {/if}
        {#if entry.qualification_errors?.length}<p class="version-error small warning">Qualification pending: {entry.qualification_errors.map(value => value.replaceAll('_', ' ')).join(', ')}</p>{/if}
        {#if job?.error}<div class="version-error"><ErrorMessage source={`runtime-job:${job.job_id}`} message={job.error} /></div>{/if}
      </article>
    {/each}
    <div class="library-foot">Builds and disk usage shown for this node · {presenter.status?.node_id ?? '—'}</div>
  </section>

  <PrimaPreparation {presenter} />

  <section class="panel" aria-labelledby="readiness-heading">
    <div class="runtime-panel-head"><div><h3 id="readiness-heading">Node readiness</h3><p>{worker ? 'Cluster nodes; this node builds locally and the master owns preparation.' : 'Source revision is shared. Each node builds for its local backend.'}</p></div><span class="runtime-badge">{presenter.planResult?.runtime_required_nodes ? `${requiredNodes.length} required by current plan` : 'Choose run participants in Deploy'}</span></div>
    <div class="readiness-summary"><strong>{worker ? `This node: ${localPhase} · ${nodes.length} cluster ${nodes.length === 1 ? 'node' : 'nodes'}` : target ? `${readyNodes.length} of ${nodes.length} nodes prepared` : 'Select a target to prepare the cluster'}</strong><span class="mono small muted">{preparing} preparing · {offlineNodes} offline{#if waiting} · {waiting} waiting{/if}</span></div>
    <NodeBuildTable {nodes} {presenter} {actions} openLogs={() => open('logs')} onretry={retry} variant="llama" {requiredNodes} targetCommit={target?.version.commit ?? null} tableLabel="Node readiness table, scroll for all columns" />
    <div class="table-note">Only the master and workers selected for a run must be ready. Unused offline children do not block it. <a class="text-button" href={routes.run}>Choose compute in Deploy →</a></div>
  </section>
</div>

<dialog bind:this={dialog} class="runtime-dialog" aria-labelledby="runtime-dialog-title" oncancel={close}>
  <div class="runtime-dialog-head"><h2 id="runtime-dialog-title">{mode === 'add' ? 'Add llama.cpp version' : mode === 'logs' ? 'Build log' : mode === 'remove' ? 'Remove version' : details ? versionLabel(details) : 'Version details'}</h2><button class="btn" aria-label="Close dialog" onclick={close}>×</button></div>
  <div class="runtime-dialog-body">
    {#if mode === 'add'}
      <label class="field">Inference engine <select aria-label="Inference engine" value={engine} onchange={(event) => chooseEngine(event.currentTarget.value as 'llamacpp' | 'primacpp')}><option value="llamacpp">llama.cpp</option>{#if library?.engine_sources?.length}<option value="primacpp">Prima.cpp · under qualification</option>{/if}</select></label>
      {#if engine === 'primacpp'}<p class="small muted">Prima can be built for evaluation. Inference selection requires API, platform, placement, lifecycle and performance qualification.</p>{/if}
      <SourcePinForm
        {presenter}
        {actions}
        engine={engine}
        initial={sourceDefaults}
        datalistId="runtime-refs"
        placeholder={upstream}
        onsave={saveVersion}
        onclose={close}
      />
    {:else if mode === 'logs'}
      <ErrorMessage source="runtime-action" message={actions.message} />
      <button class="btn" disabled={actions.busy} onclick={() => actions.refreshLogs()}>Refresh log</button>
      <textarea class="log-console" readonly aria-label="Build output" rows="12" value={actions.logText || 'No build output yet.'}></textarea>
    {:else if mode === 'remove'}
      <ErrorMessage source="runtime-action" message={actions.message} />
      <p>Remove {details ? versionLabel(details) : 'this version'} and its local build files?</p><p class="small muted">Running, selected, targeted and building versions are protected.</p>
    {:else}
      <ErrorMessage source="runtime-action" message={actions.message} />
      <dl><dt>Repository</dt><dd>{details?.repository}</dd><dt>Reference</dt><dd>{details?.ref_type}: {details?.ref}</dd><dt>Resolved commit</dt><dd class="mono">{details?.commit}</dd><dt>Source identity</dt><dd class="mono">{details?.runtime_id}</dd></dl>
    {/if}
  </div>
  <div class="runtime-dialog-foot"><button class="btn" onclick={close}>Close</button>{#if mode === 'remove'}<button class="btn" disabled={actions.busy || !details || protectedVersion(details.runtime_id)} onclick={confirmRemove}>Remove local version</button>{/if}</div>
</dialog>
