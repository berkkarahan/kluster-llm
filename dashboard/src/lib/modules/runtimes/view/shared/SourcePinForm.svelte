<script lang="ts">
  /**
   * Shared add-version form for the runtime settings dialogs: browse a public
   * HTTPS Git repository, resolve the reference to one immutable commit, save.
   * The parent owns persistence and dialog closing through `onsave`/`onclose`;
   * failures surface on the parent's RuntimeActions (one source per view).
   */
  import { onDestroy } from 'svelte';
  import ErrorMessage from '#lib/shared/ErrorMessage.svelte';
  import type { RuntimeSources, RuntimeVersion } from '#lib/modules/runtimes/entities';
  import type { RuntimesInteractor } from '../../interactor.svelte';
  import type { RuntimeActions } from './runtimeActions.svelte';

  let {
    presenter,
    actions,
    engine,
    initial,
    datalistId,
    saveLabel = 'Save version',
    saveTargetLabel,
    footNote = 'Tags and branches resolve to one immutable commit. Nodes build that exact snapshot using their own backend.',
    placeholder = 'https://github.com/ggml-org/llama.cpp.git',
    onsave,
    onsaveTarget,
    onclose
  }: {
    presenter: RuntimesInteractor;
    actions: RuntimeActions;
    engine: 'llamacpp' | 'primacpp';
    initial: { repository: string; ref: string; refType: 'tag' | 'branch' | 'commit'; name: string };
    datalistId: string;
    saveLabel?: string;
    saveTargetLabel?: string;
    footNote?: string;
    onsave: (value: RuntimeVersion) => Promise<unknown>;
    onsaveTarget?: (value: RuntimeVersion) => Promise<unknown>;
    onclose: () => void;
    placeholder?: string;
  } = $props();

  let name = $state('');
  let repository = $state('');
  let ref = $state('');
  let refType = $state<'tag' | 'branch' | 'commit'>('tag');
  let resolved = $state<RuntimeVersion | null>(null);
  let sources = $state<RuntimeSources | null>(null);
  let sourcesLoading = $state(false);
  let controller: AbortController | null = null;
  /** Supersedes superseded sources/resolve writes; destroyed state never lands. */
  let round = 0;

  /** Re-prefill whenever the parent hands over a new snapshot (engine switch, reopen). */
  $effect(() => {
    round++;
    name = initial.name ?? '';
    repository = initial.repository;
    ref = initial.ref;
    refType = initial.refType;
    resolved = null;
    sources = null;
  });

  $effect(() => {
    name; repository; ref; refType; engine;
    resolved = null;
  });

  onDestroy(() => { round++; controller?.abort(); });

  async function discoverSources() {
    controller?.abort(); controller = new AbortController();
    round++;
    const selected = repository;
    const seq = round;
    sourcesLoading = true;
    await actions.action(async () => {
      const value = await presenter.sources(selected, controller?.signal);
      if (seq === round) sources = value;
    });
    sourcesLoading = false;
  }

  async function resolveSource() {
    controller?.abort(); controller = new AbortController();
    round++;
    const seq = round;
    await actions.action(async () => {
      const value = await presenter.resolve<RuntimeVersion>(
        { engine, repository, ref, ref_type: refType, name },
        controller?.signal
      );
      if (seq === round) resolved = value;
    });
  }

  async function save() {
    const value = resolved;
    if (!value) return;
    await actions.action(async () => {
      await onsave(value);
      onclose();
    });
  }

  async function saveTarget() {
    const value = resolved;
    if (!value || !onsaveTarget) return;
    await actions.action(async () => {
      await onsaveTarget(value);
      onclose();
    });
  }
</script>

<ErrorMessage source={actions.section} message={actions.message} />
<label class="field">Display name <input bind:value={name} placeholder="Optional name" /></label>
<label class="field">Public HTTPS Git repository <input bind:value={repository} type="url" placeholder={placeholder} /></label>
<button class="btn" disabled={actions.busy} onclick={discoverSources}>{sourcesLoading ? 'Loading releases…' : 'Browse releases and refs'}</button>
{#if sources?.warning}<p class="small muted">{sources.warning}</p>{/if}
{#if sources?.releases.length}<label class="field">Release <select aria-label="Release tag" onchange={(event) => { refType = 'tag'; ref = event.currentTarget.value; }}><option value="">Choose a release</option>{#each sources.releases as release}<option value={release.tag}>{release.name}{release.prerelease ? ' · prerelease' : ''}</option>{/each}</select></label>{/if}
<div class="ref-grid"><label class="field">Reference type <select bind:value={refType}><option value="tag">Tag</option><option value="branch">Branch</option><option value="commit">Commit</option></select></label><label class="field">{refType === 'commit' ? 'Full commit SHA' : refType === 'branch' ? 'Branch name' : 'Tag'}<input bind:value={ref} list={datalistId} /><datalist id={datalistId}>{#each (refType === 'branch' ? sources?.branches : sources?.tags) ?? [] as value}<option value={value}></option>{/each}</datalist></label></div>
<p class="small muted">{footNote}</p>
{#if resolved}<div class="resolved-box"><strong>Resolved source</strong><code>{resolved.commit}</code><span class="small break">{resolved.repository}</span></div>{/if}
<div class="runtime-dialog-foot">
  <button class="btn" onclick={onclose}>Close</button>
  <button class="btn" disabled={actions.busy || !ref || !repository} onclick={resolveSource}>Resolve revision</button>
  <button class="btn" class:btn-accent={!onsaveTarget} disabled={actions.busy || !resolved || !repository} onclick={save}>{saveLabel}</button>
  {#if onsaveTarget}
    <button class="btn btn-accent" disabled={actions.busy || !resolved || !repository} onclick={saveTarget}>{saveTargetLabel ?? 'Save version & set target'}</button>
  {/if}
</div>
