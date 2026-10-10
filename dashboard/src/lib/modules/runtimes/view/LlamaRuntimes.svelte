<script lang="ts">
  import { createApp } from '#lib/app.svelte';
  import { routes } from '#lib/shared/navigation';
  import { onMount } from 'svelte';
  import { errors } from '#lib/shared/errors';
  import ErrorMessage from '#lib/shared/ErrorMessage.svelte';
  import type { RuntimeVersion, RuntimeJob, RuntimeNode, RuntimeSources } from '#lib/modules/runtimes/entities';
  import type { RuntimesInteractor } from '../interactor.svelte';
  let { presenter = createApp().runtimes }: { presenter?: RuntimesInteractor } = $props();

  const upstream = 'https://github.com/ggml-org/llama.cpp.git';
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
  let busy = $state(false);
  let error = $state('');
  let errorGeneration = $state(errors.generation);
  let dialog: HTMLDialogElement;
  let mode = $state<'add' | 'logs' | 'details' | 'remove'>('add');
  let repository = $state(upstream);
  let ref = $state('v0.5.0');
  let refType = $state<'tag' | 'branch' | 'commit'>('tag');
  let name = $state('');
  let resolved = $state<RuntimeVersion | null>(null);
  let sources = $state<RuntimeSources | null>(null);
  let sourcesLoading = $state(false);
  let details = $state<RuntimeVersion | null>(null);
  let logText = $state('');
  let logNode = $state<RuntimeNode | null>(null);
  let logJob = $state<RuntimeJob | null>(null);
  let returnFocus: HTMLElement | null = null;
  let resolveGeneration = 0;
  let controller: AbortController | null = null;

  $effect(() => { if (!draft && target?.version.runtime_id) draft = target.version.runtime_id; });
  $effect(() => {
    repository; ref; refType; name;
    resolved = null;
    resolveGeneration++;
  });
  $effect(() => {
    for (const job of library?.jobs ?? []) errors.observe(`runtime-job:${job.job_id}`, job.error ?? '', job.job_id ?? '');
    for (const node of nodes) errors.observe(`runtime-node:${node.node_id}`, node.error ?? '', node.job?.job_id ?? '');
  });

  onMount(() => {
    const abort = new AbortController();
    void action(async () => { await presenter.refresh(abort.signal); });
    return () => { abort.abort(); controller?.abort(); };
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

  function open(next: typeof mode) {
    returnFocus = document.activeElement as HTMLElement;
    mode = next;
    dialog.showModal();
  }
  function close() { controller?.abort(); resolveGeneration++; dialog.close(); returnFocus?.focus(); }
  function add() { repository = library?.bootstrap?.repository ?? upstream; ref = library?.bootstrap?.ref || 'v0.5.0'; refType = 'tag'; name = ''; resolved = null; sources = null; open('add'); }
  async function discoverSources() {
    controller?.abort(); controller = new AbortController();
    const selectedRepo = repository;
    const generation = resolveGeneration;
    sourcesLoading = true;
    await action(async () => {
      const value = await presenter.sources(repository, controller?.signal);
      if (generation === resolveGeneration && repository === selectedRepo) sources = value;
    });
    sourcesLoading = false;
  }
  async function resolveSource() {
    controller?.abort(); controller = new AbortController();
    const generation = resolveGeneration;
    await action(async () => {
      const value = await presenter.resolve<RuntimeVersion>({ repository, ref, ref_type: refType, name }, controller?.signal);
      if (generation === resolveGeneration) resolved = value;
    });
  }
  async function save() {
    if (!resolved) return;
    const value = resolved;
    await action(async () => { await presenter.add(value); draft = value.runtime_id; close(); });
  }
  async function sync() {
    await setTarget(draft);
  }
  async function setTarget(runtimeId: string) {
    await action(async () => {
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
    await action(async () => {
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
    await action(() => presenter.build(version.runtime_id));
  }
  async function selectLocal(version: RuntimeVersion) {
    await action(() => presenter.selectLocal(version.runtime_id));
  }
  function jobFor(id: string) { return [...(library?.jobs ?? [])].reverse().find(job => job.runtime_id === id); }
  function isPreparing(job: RuntimeJob | undefined | null) { return !!job && ['queued', 'downloading', 'building', 'verifying'].includes(job.phase); }
  function protectedVersion(id: string) {
    return id === target?.version.runtime_id || id === running?.runtime_id || id === library?.selected || isPreparing(jobFor(id));
  }
  function short(value: string | null | undefined) { return value?.slice(0, 8) ?? '—'; }
  function disk(bytes: number | undefined) { return bytes ? `${(bytes / 1024 ** 2).toFixed(0)} MiB` : '—'; }
  function label(version: RuntimeVersion) { return version.name || version.ref; }
  function inspect(version: RuntimeVersion) { details = version; open('details'); }
  function remove(version: RuntimeVersion) { details = version; open('remove'); }
  async function confirmRemove() {
    if (!details) return;
    await action(async () => { await presenter.remove(details!.runtime_id); close(); });
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
    else if (target && node.node_id === presenter.status?.node_id) await build(target.version);
  }
  async function cancel(job: RuntimeJob, node: RuntimeNode | null = null) {
    if (!job.job_id) return;
    const remote = node && node.node_id !== presenter.status?.node_id;
    await action(() => remote
      ? presenter.cancelRemote(node!.node_id, job.job_id!)
      : presenter.cancelLocal(job.job_id!));
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
  <ErrorMessage source="runtime-action" message={errorGeneration === errors.generation ? error : ''} />
  {#if library?.reconcile_error}<ErrorMessage source="runtime-reconciliation" message={library.reconcile_error} />{/if}

  <section class="panel" aria-labelledby="target-heading">
    <div class="runtime-panel-head"><div><h3 id="target-heading">Cluster target</h3><p>A single source revision, built for each node.</p></div><span class="runtime-badge" class:green={master}>{worker ? 'Managed by master' : master ? 'Enforced by master' : 'Local selection'}</span></div>
    <div class="target-grid">
      <div class="target-picker">
        <div class="target-control"><label class="field"><span>Target version {#if draft && draft !== target?.version.runtime_id}<small>· Unsaved selection</small>{/if}</span>
          <select bind:value={draft} disabled={worker || busy} aria-label="Target llama.cpp version"><option value="">Choose a saved version</option>{#each library?.versions ?? [] as entry}<option value={entry.version.runtime_id}>{label(entry.version)} · {entry.version.repository === upstream ? 'Upstream' : 'Fork'}</option>{/each}</select></label>
          {#if master}<button class="btn btn-accent" disabled={!draft || busy} onclick={sync}>Set target &amp; sync</button>{/if}
        </div>
        {#if target}<div class="target-meta"><span>{target.version.ref_type}: {target.version.ref}</span><code>{short(target.version.commit)}</code><span class="runtime-badge">Immutable SHA</span></div><p class="muted small break">{target.version.repository}</p>{:else}<p class="muted small">Add a version to choose an exact source revision.</p>{/if}
        <p class="muted small">{worker ? 'Local builds use this node’s configured backend.' : 'Joining and returning workers prepare the saved target automatically.'}</p>
      </div>
      <div class="running-box"><div class="running-top"><span class="muted small">{running ? 'Running version' : 'Next run'}</span><span class="runtime-badge" class:green={!!running}>{running ? 'Run active' : 'No active run'}</span></div>
        <div class="running-value">{running ? label(running) : target ? label(target.version) : 'Awaiting selection'}<small class="mono">{short(running?.commit ?? target?.version.commit)}</small></div>
        <p class="small muted">{running && running.runtime_id !== target?.version.runtime_id ? 'The active run keeps its original binaries. Prepared builds switch after it fully stops.' : 'The next run uses the saved target once its participating nodes are ready.'}</p>
        <a class="text-button" href={routes.run}>Review deployment →</a>
      </div>
    </div>
    <div class="policy-strip"><strong>One target. Local builds. Persistent enforcement.</strong><span>Backend builds may differ; source revisions must match.</span></div>
    {#if running && target && running.runtime_id !== target.version.runtime_id}<div class="pending-note"><strong>Pending target: {label(target.version)}</strong><span class="mono small">{short(target.version.commit)}</span><p>Preparation runs beside {label(running)}.</p></div>{/if}
  </section>

  <section class="panel" aria-labelledby="library-heading">
    <div class="runtime-panel-head"><div><h3 id="library-heading">Saved versions <span class="muted small">/ {library?.versions.length ?? 0}</span></h3><p>Keep known-good builds. Try a new revision without replacing them.</p></div><div class="version-actions">{#if master}<button class="btn btn-accent" disabled={busy || (bootstrapVersion !== undefined && bootstrapVersion.runtime_id === target?.version.runtime_id)} onclick={installDefault}>Install {bootstrap.ref} and set target</button>{/if}<button class="btn" disabled={worker || busy} onclick={add}>+ Add version</button></div></div>
    {#if !library?.versions.length}<div class="empty"><h3>No managed versions yet</h3><p>{library?.legacy ? 'A legacy runtime is installed. Prepare a managed source snapshot to verify its identity across the cluster.' : 'Save an upstream release or a public Git fork to get started.'}</p><button class="btn" disabled={worker || busy} onclick={add}>Add upstream version</button></div>{/if}
    {#each library?.versions ?? [] as entry (entry.version.runtime_id)}
      {@const version = entry.version}
      {@const job = jobFor(version.runtime_id)}
      <article class="version-row">
        <div class="version-info"><div class="version-name"><button class="text-button" onclick={() => inspect(version)}>{label(version)}</button>{#if version.runtime_id === target?.version.runtime_id}<span class="runtime-badge green">Target</span>{:else if version.runtime_id === running?.runtime_id}<span class="runtime-badge">Running</span>{:else if version.repository !== upstream}<span class="runtime-badge">Fork</span>{/if}{#if entry.build?.prebuilt}<span class="runtime-badge" title="Installed from a prebuilt release pack. The managed 10GbE private network needs a source build of this version.">Prebuilt</span>{/if}</div><div class="version-source">{version.repository}</div><div class="version-ref"><span>{version.ref_type} · {version.ref}</span><code>{short(version.commit)}</code></div></div>
        <div class="version-backend"><span>{entry.build?.backend ?? presenter.status?.accel_backend ?? 'Auto backend'}</span><span class="small" class:green={!!entry.build}>{isPreparing(job) ? job?.phase : job?.phase === 'failed' ? 'Failed' : entry.build?.prebuilt ? 'Prebuilt' : entry.build ? 'Built' : 'Not built'}</span></div>
        <div class="version-disk mono small muted">{disk(entry.build?.size_bytes)}</div>
        <div class="version-actions"><button class="btn" disabled={busy || isPreparing(job) || worker && version.runtime_id !== target?.version.runtime_id} onclick={() => build(version)}>{entry.build ? 'Verify build' : 'Build'}</button><button class="btn" disabled={!job?.job_id || busy} onclick={() => showLogs(job)}>Logs</button>
          {#if master}<button class="btn" disabled={busy || draft === version.runtime_id} onclick={() => draft = version.runtime_id}>Select</button>{:else if !worker}<button class="btn" disabled={!entry.build || busy || !!running} onclick={() => selectLocal(version)}>Use locally</button>{/if}
          <button class="btn runtime-remove" title={protectedVersion(version.runtime_id) ? 'Running, selected, targeted or building versions are protected' : 'Remove this local version'} aria-label={`Remove ${label(version)}`} disabled={busy || worker || protectedVersion(version.runtime_id)} onclick={() => remove(version)}>×</button>
        </div>
        {#if job?.error}<div class="version-error"><ErrorMessage source={`runtime-job:${job.job_id}`} message={job.error} /></div>{/if}
      </article>
    {/each}
    <div class="library-foot">Builds and disk usage shown for this node · {presenter.status?.node_id ?? '—'}</div>
  </section>

  <section class="panel" aria-labelledby="readiness-heading">
    <div class="runtime-panel-head"><div><h3 id="readiness-heading">Node readiness</h3><p>{worker ? 'Cluster nodes; this node builds locally and the master owns preparation.' : 'Source revision is shared. Each node builds for its local backend.'}</p></div><span class="runtime-badge">{presenter.planResult?.runtime_required_nodes ? `${requiredNodes.length} required by current plan` : 'Choose run participants in Deploy'}</span></div>
    <div class="readiness-summary"><strong>{worker ? `This node: ${localPhase} · ${nodes.length} cluster ${nodes.length === 1 ? 'node' : 'nodes'}` : target ? `${readyNodes.length} of ${nodes.length} nodes prepared` : 'Select a target to prepare the cluster'}</strong><span class="mono small muted">{preparing} preparing · {offlineNodes} offline{#if waiting} · {waiting} waiting{/if}</span></div>
    <!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users must be able to scroll the wide table.) -->
    <div class="runtime-table-scroll" tabindex="0" role="region" aria-label="Node readiness table, scroll for all columns"><table><thead><tr><th>Node</th><th>Backend</th><th>Running SHA</th><th>Target SHA</th><th>Preparation</th><th>Actions</th></tr></thead><tbody>
      {#each nodes as node (node.node_id)}<tr><td><strong>{node.name ?? node.node_id}</strong><small>{node.address ? `${node.address} · ` : ''}{node.role.toUpperCase()}{node.node_id === presenter.status?.node_id ? ' · THIS NODE' : ''}{requiredNodes.includes(node.node_id) ? ' · REQUIRED' : ''}</small></td><td class="mono">{node.prepared?.backend ?? node.job?.backend ?? '—'}</td><td class="mono">{short(node.running?.commit)}</td><td class="mono">{short(target?.version.commit)}</td><td><span class="state-label" class:green={node.phase === 'prepared'} class:warning={['failed','offline','interrupted','cancelled'].includes(node.phase)}>{node.phase.replaceAll('_', ' ')}</span>{#if isPreparing(node.job)}<progress aria-label={`${node.name ?? node.node_id} ${node.phase}`}></progress>{/if}{#if node.error}<div class="node-error"><ErrorMessage source={`runtime-node:${node.node_id}`} message={node.error} /></div>{/if}</td>
        <td><div class="table-actions">{#if ['failed','interrupted','cancelled','waiting_for_target'].includes(node.phase)}<button class="btn" disabled={busy || !(master || node.node_id === presenter.status?.node_id)} onclick={() => retry(node)}>{node.phase === 'waiting_for_target' ? 'Sync' : 'Retry'}</button>{/if}<button class="btn" disabled={busy || !node.job?.job_id || node.phase === 'offline' || worker && node.node_id !== presenter.status?.node_id} onclick={() => showLogs(node.job, node)}>Logs</button>{#if node.job && isPreparing(node.job)}<button class="btn" disabled={busy || worker && node.node_id !== presenter.status?.node_id} onclick={() => cancel(node.job!, node)}>Cancel</button>{/if}</div></td></tr>{/each}
    </tbody></table></div>
    <div class="table-note">Only the master and workers selected for a run must be ready. Unused offline children do not block it. <a class="text-button" href={routes.run}>Choose compute in Deploy →</a></div>
  </section>
</div>

<dialog bind:this={dialog} class="runtime-dialog" aria-labelledby="runtime-dialog-title" oncancel={close}>
  <div class="runtime-dialog-head"><h2 id="runtime-dialog-title">{mode === 'add' ? 'Add llama.cpp version' : mode === 'logs' ? 'Build log' : mode === 'remove' ? 'Remove version' : details ? label(details) : 'Version details'}</h2><button class="btn" aria-label="Close dialog" onclick={close}>×</button></div>
  <div class="runtime-dialog-body">
    <ErrorMessage source="runtime-action" message={errorGeneration === errors.generation ? error : ''} />
    {#if mode === 'add'}
      <label class="field">Display name <input bind:value={name} placeholder="Optional name" /></label>
      <label class="field">Public HTTPS Git repository <input bind:value={repository} type="url" placeholder={upstream} /></label>
      <button class="btn" disabled={busy} onclick={discoverSources}>{sourcesLoading ? 'Loading releases…' : 'Browse releases and refs'}</button>
      {#if sources?.warning}<p class="small muted">{sources.warning}</p>{/if}
      {#if sources?.releases.length}<label class="field">Release <select aria-label="Release tag" onchange={(event) => { refType = 'tag'; ref = event.currentTarget.value; }}><option value="">Choose a release</option>{#each sources.releases as release}<option value={release.tag}>{release.name}{release.prerelease ? ' · prerelease' : ''}</option>{/each}</select></label>{/if}
      <div class="ref-grid"><label class="field">Reference type <select bind:value={refType}><option value="tag">Tag</option><option value="branch">Branch</option><option value="commit">Commit</option></select></label><label class="field">{refType === 'commit' ? 'Full commit SHA' : refType === 'branch' ? 'Branch name' : 'Tag'}<input bind:value={ref} list="runtime-refs" /><datalist id="runtime-refs">{#each (refType === 'branch' ? sources?.branches : sources?.tags) ?? [] as value}<option value={value}></option>{/each}</datalist></label></div>
      <p class="small muted">Tags and branches resolve to one immutable commit. Nodes build that exact snapshot using their own backend.</p>
      {#if resolved}<div class="resolved-box"><strong>Resolved source</strong><code>{resolved.commit}</code><span class="small break">{resolved.repository}</span></div>{/if}
    {:else if mode === 'logs'}<button class="btn" disabled={busy} onclick={refreshLogs}>Refresh log</button><textarea class="log-console" readonly aria-label="Build output" rows="12" value={logText || 'No build output yet.'}></textarea>
    {:else if mode === 'remove'}<p>Remove {details ? label(details) : 'this version'} and its local build files?</p><p class="small muted">Running, selected, targeted and building versions are protected.</p>
    {:else if details}<dl><dt>Repository</dt><dd>{details.repository}</dd><dt>Reference</dt><dd>{details.ref_type}: {details.ref}</dd><dt>Resolved commit</dt><dd class="mono">{details.commit}</dd><dt>Source identity</dt><dd class="mono">{details.runtime_id}</dd></dl>{/if}
  </div>
  <div class="runtime-dialog-foot"><button class="btn" onclick={close}>Close</button>{#if mode === 'add'}<button class="btn" disabled={busy || !ref || !repository} onclick={resolveSource}>Resolve revision</button><button class="btn btn-accent" disabled={busy || !resolved} onclick={save}>Save version</button>{:else if mode === 'remove'}<button class="btn" disabled={busy || !details || protectedVersion(details.runtime_id)} onclick={confirmRemove}>Remove local version</button>{/if}</div>
</dialog>

<style>
  .runtime-settings{display:grid;gap:24px;min-width:0}.notice{padding:14px 16px;border:1px solid var(--line,var(--border));background:#1b1e24;color:var(--muted);font-size:14px;overflow-wrap:anywhere}.section-intro h2{font-size:28px}.section-intro p{color:var(--muted);font-size:14px;margin-top:8px}.runtime-panel-head{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px;padding:24px;border-bottom:1px solid var(--line, var(--border))}.runtime-panel-head h3{font-size:18px}.runtime-panel-head p{font-size:14px;color:var(--muted);margin-top:4px}.runtime-badge{display:inline-flex;padding:2px 8px;border:1px solid var(--line,var(--border));font:11px/1.5 var(--mono);text-transform:uppercase;letter-spacing:.4px;color:var(--muted);white-space:nowrap}.green{color:var(--accent)}.runtime-badge.green{background:var(--green-wash,#132b22);border-color:#2b5a44}.target-grid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr)}.target-picker,.running-box{padding:24px;display:grid;gap:16px;min-width:0}.target-control{display:flex;align-items:flex-end;gap:12px}.target-control .field{flex:1;min-width:0}.field{display:grid;gap:8px;font-size:14px}.field small{color:var(--warn,#f2c76a)}input,select{min-width:0;width:100%;padding:10px 12px;min-height:48px;border:1px solid var(--control,#647181);border-radius:4px;background:var(--bg);color:var(--fg,var(--text));font:inherit}.target-control .btn{min-height:48px}.target-meta{display:flex;flex-wrap:wrap;gap:8px 16px;font-size:12px;color:var(--muted)}.break{overflow-wrap:anywhere}.running-box{border-left:1px solid var(--line,var(--border))}.running-top{display:flex;align-items:center;justify-content:space-between;gap:12px}.running-value{font-size:24px;font-weight:500;overflow-wrap:anywhere}.running-value small{font-size:12px;margin-left:8px;color:var(--muted)}.policy-strip{display:grid;gap:4px;padding:16px 24px;border-top:1px solid var(--line,var(--border));font-size:14px}.policy-strip span{color:var(--muted)}.pending-note{padding:16px 24px;background:#29251b;border-top:1px solid var(--line,var(--border));font-size:14px}.pending-note span{margin-left:12px}.pending-note p{color:var(--muted);font-size:12px;margin-top:4px}.version-row{display:grid;grid-template-columns:minmax(0,2fr) minmax(100px,1fr) 80px minmax(250px,1.5fr);gap:24px;padding:24px;border-bottom:1px solid var(--line,var(--border));align-items:center}.version-info,.version-backend{display:grid;gap:8px;min-width:0}.version-name{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.version-name .text-button{font-size:16px;color:var(--fg,var(--text));text-align:left;overflow-wrap:anywhere}.version-source{font-size:12px;color:var(--muted);overflow-wrap:anywhere}.version-ref{display:flex;flex-wrap:wrap;gap:8px;font:12px var(--mono);color:var(--muted)}.version-actions,.table-actions{display:flex;align-items:center;justify-content:flex-end;flex-wrap:wrap;gap:8px}.version-actions .btn,.table-actions .btn{font-size:12px;padding:8px 10px;min-height:44px}.runtime-remove{width:36px}.version-error{grid-column:1/-1}.library-foot,.table-note{padding:16px 24px;font-size:12px;color:var(--muted);border-top:1px solid var(--line,var(--border))}.readiness-summary{display:flex;justify-content:space-between;flex-wrap:wrap;gap:16px;padding:16px 24px;border-bottom:1px solid var(--line,var(--border));font-size:14px}.runtime-table-scroll{min-width:0;max-width:100%;overflow-x:auto;overscroll-behavior-x:contain}table{width:100%;min-width:930px;border-collapse:collapse;font-size:14px}th{padding:12px 16px;text-align:left;background:var(--bg);font:11px var(--mono);text-transform:uppercase;letter-spacing:.5px;color:var(--muted)}td{padding:20px 16px;border-top:1px solid var(--line,var(--border));vertical-align:middle}td strong{font-size:16px}td small{display:block;margin-top:6px;font-size:11px;color:var(--muted)}.node-error{max-width:220px;overflow-wrap:anywhere}.state-label{text-transform:capitalize;font-size:12px}.warning{color:var(--warn,#f2c76a)}progress{display:block;width:140px;height:4px;margin-top:8px;accent-color:var(--accent)}.empty{padding:32px;display:grid;justify-items:start;gap:12px}.empty p{font-size:14px;color:var(--muted)}.runtime-dialog{padding:0;width:min(680px,calc(100% - 32px));max-height:calc(100dvh - 48px);overflow-y:auto;background:var(--surface);color:var(--fg,var(--text));border:1px solid var(--control,#647181);border-radius:8px}.runtime-dialog::backdrop{background:#000b}.runtime-dialog-head,.runtime-dialog-foot{padding:20px 24px;display:flex;align-items:center;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line,var(--border))}.runtime-dialog-head h2{font-size:22px}.runtime-dialog-body{padding:24px;display:grid;gap:24px}.runtime-dialog-foot{justify-content:flex-end;flex-wrap:wrap;border-bottom:0;border-top:1px solid var(--line,var(--border))}.ref-grid{display:grid;grid-template-columns:150px minmax(0,1fr);gap:16px}.resolved-box{padding:16px;border:1px solid #2b5a44;background:#132b22;display:grid;gap:8px;font-size:14px}.resolved-box code{font-size:12px;overflow-wrap:anywhere}.log-console{background:var(--bg);border:1px solid var(--line,var(--border));padding:16px;font:12px/1.8 var(--mono);max-height:360px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}dl{display:grid;grid-template-columns:130px minmax(0,1fr);gap:16px;font-size:14px}dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}
  @media(max-width:1200px){.version-row{grid-template-columns:minmax(0,1fr) 140px}.version-actions{grid-column:1/-1;justify-content:flex-start}.version-disk{grid-column:2;grid-row:1;align-self:end}.version-backend{grid-column:2;grid-row:1;align-self:start}.target-control{flex-direction:column;align-items:stretch}}
  @media(max-width:768px){.target-grid{grid-template-columns:1fr}.running-box{border-left:0;border-top:1px solid var(--line,var(--border))}.target-control{flex-direction:row;align-items:flex-end}.version-row{gap:16px}.runtime-panel-head,.target-picker,.running-box,.version-row{padding:20px}}
  @media(max-width:520px){.target-control{flex-direction:column;align-items:stretch}.version-row{grid-template-columns:1fr}.version-backend,.version-disk{grid-column:auto;grid-row:auto}.version-backend{display:flex;justify-content:space-between}.version-actions{grid-column:auto}.runtime-panel-head{align-items:flex-start}.ref-grid{grid-template-columns:1fr}.running-top{flex-wrap:wrap}.runtime-dialog-head,.runtime-dialog-body,.runtime-dialog-foot{padding:20px}dl{grid-template-columns:1fr;gap:8px}}
</style>
