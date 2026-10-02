<script lang="ts">
  import ErrorMessage from '$lib/components/ErrorMessage.svelte';
  import { goto } from '$app/navigation';
  import { appStore } from '$lib/store.svelte';
  import DownloadPanel from '$lib/components/DownloadPanel.svelte';
  import LoadProgressPanel from '$lib/components/LoadProgressPanel.svelte';
  import { formatGb } from '$lib/format-transfer';
  import type { ModelSearchFit, ModelSearchHit } from '$lib/types';

  let modalOpen = $state(false);
  let searchDialog = $state<HTMLDivElement | null>(null);

  function fitChip(fit: ModelSearchFit) {
    if (fit === 'fits') return 'chip-ok';
    if (fit === 'overflow') return 'chip-danger';
    return 'chip-muted';
  }

  function sizeLabel(row: ModelSearchHit) {
    if (!row.size_bytes) return 'unknown size';
    return formatGb(row.size_bytes, 1);
  }

  function fitText(fit: ModelSearchFit) {
    if (fit === 'fits') return 'fits (weight-size heuristic)';
    if (fit === 'overflow') return 'overflow (weight-size heuristic)';
    return 'unknown fit';
  }

  function displayPath(row: ModelSearchHit) {
    return row.path || row.filename;
  }

  function closeSearch() {
    modalOpen = false;
  }

  $effect(() => {
    if (!modalOpen) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const node = searchDialog;
    queueMicrotask(() => {
      const first = node?.querySelector<HTMLElement>('button, [href], input, select, textarea');
      (first ?? node)?.focus();
    });
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        modalOpen = false;
        return;
      }
      if (event.key !== 'Tab' || !searchDialog) return;
      const focusable = [...searchDialog.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea'
      )].filter((el) => !el.hasAttribute('disabled'));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !searchDialog.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      previous?.focus();
    };
  });

  async function runSearch() {
    if (!appStore.modelSource.trim() || appStore.busy || appStore.searching) return;
    modalOpen = true;
    try {
      await appStore.searchModels();
    } catch {
      /* error is shown in the modal */
    }
  }

  async function downloadRow(source: string) {
    modalOpen = false;
    await appStore.downloadModel(source);
  }

  /** Select model and jump to Plan & Run — Unsloth "load this model" path. */
  async function useModel(id: string) {
    await appStore.selectModel(id);
    await goto('/run');
  }
</script>

<div class="page-frame max-w-4xl mx-auto p-4">
  <h1 class="page-title">Models</h1>
  <p class="page-description">
    Search Hugging Face GGUFs, download on the master (workers need no local copy), then Load from Plan &amp; Run.
  </p>

  <div class="flex flex-col gap-3 mb-4">
    {#if appStore.downloadJob && appStore.downloadJob.state !== 'idle'}
      <div class="panel p-3" style="border-color: var(--border)">
        <DownloadPanel />
      </div>
    {:else}
      <DownloadPanel />
    {/if}
    <LoadProgressPanel />
  </div>

  <div class="panel p-3 mb-4 flex flex-col gap-2" style="border-color: var(--border)">
    <label class="mono text-[11px]" style="color: var(--muted)" for="model-source">Search</label>
    <form
      class="flex gap-2"
      onsubmit={(e) => {
        e.preventDefault();
        void runSearch();
      }}
    >
      <input
        id="model-source"
        class="field mono flex-1"
        bind:value={appStore.modelSource}
        placeholder="Qwen3.8-27B-Uncensored-GGUF:Q4_K_M, org/repo:Q4_K_M, or URL"
      />
      <button class="btn btn-accent" disabled={!appStore.modelSource.trim() || !!appStore.busy} type="submit"
        >Search</button
      >
    </form>

    {#if appStore.status?.rpc_cache}
      <p class="mono text-[11px]" style="color: var(--muted)">
        RPC tensor cache: {appStore.status.rpc_cache.exists ? 'available' : 'created on worker startup'} · {appStore.status.rpc_cache.path}
      </p>
    {/if}
    <label class="mono text-[11px] mt-2 flex items-center gap-2" style="color: var(--muted)" for="hf-token">
      Hugging Face token
      {#if appStore.hfTokenConfigured}
        <span class="chip chip-ok">saved</span>
        {#if appStore.hfTokenSource === 'env'}
          <span class="chip chip-muted">env</span>
        {/if}
      {/if}
    </label>
    <div class="flex gap-2">
      <input
        id="hf-token"
        class="field mono flex-1"
        type="password"
        autocomplete="off"
        spellcheck="false"
        bind:value={appStore.hfTokenInput}
        placeholder="hf_…"
      />
      <button
        class="btn btn-accent"
        disabled={!appStore.hfTokenInput.trim() || !!appStore.busy}
        onclick={() => appStore.saveHfToken()}>Save</button
      >
      <button
        class="btn"
        disabled={!!appStore.busy || (!appStore.hfTokenConfigured && !appStore.hfTokenInput)}
        onclick={() => appStore.clearHfToken()}>Clear</button
      >
    </div>
  </div>

  <div class="panel model-table-scroll" style="border-color: var(--border)">
    <table class="w-full text-left">
      <thead>
        <tr class="mono text-[11px]" style="color: var(--muted)">
          <th class="px-3 py-2 border-b font-medium" style="border-color: var(--border)">name</th>
          <th class="px-3 py-2 border-b font-medium" style="border-color: var(--border)">size</th>
          <th class="px-3 py-2 border-b font-medium" style="border-color: var(--border)">status</th>
          <th class="px-3 py-2 border-b font-medium" style="border-color: var(--border)"></th>
        </tr>
      </thead>
      <tbody>
        {#each appStore.models as m}
          <tr class="mono" class:bg-selected={appStore.selectedModel === m.id}>
            <td class="px-3 py-2 border-b" style="border-color: var(--border)">{m.id}</td>
            <td class="px-3 py-2 border-b" style="border-color: var(--border)">{formatGb(m.size_bytes ?? null)}</td>
            <td class="px-3 py-2 border-b" style="border-color: var(--border)">
              {#if m.loaded}
                <span class="chip chip-ok">Ready</span>
              {:else if appStore.loadProgress?.model === m.id && (appStore.loadProgress.phase === 'starting' || appStore.loadProgress.phase === 'loading')}
                <span class="chip chip-warn">Loading…</span>
              {:else}
                <span class="chip chip-muted">local</span>
              {/if}
            </td>
            <td class="px-3 py-2 border-b text-right" style="border-color: var(--border)">
              <div class="inline-flex gap-2">
                <button
                  class="btn btn-accent"
                  disabled={!!appStore.busy}
                  onclick={() => void useModel(m.id)}>Load</button
                >
                <button
                  class="btn btn-danger"
                  disabled={!!appStore.busy}
                  onclick={() => void appStore.deleteModel(m.id)}>Delete</button
                >
              </div>
            </td>
          </tr>
        {:else}
          <tr>
            <td colspan="4" class="px-3 py-6 mono" style="color: var(--muted)">No local GGUFs in ~/.kllm/models</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</div>

{#if modalOpen}
  <div
    class="overlay"
    role="presentation"
    onclick={(e) => {
      if (e.target === e.currentTarget) modalOpen = false;
    }}
  >
    <div
      bind:this={searchDialog}
      class="panel modal p-4 max-w-2xl w-full mx-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="model-search-title"
      tabindex="-1"
    >
      <div class="flex items-center justify-between mb-3">
        <h2 id="model-search-title" class="mono text-[12px] uppercase tracking-wider" style="color: var(--muted)">Search results</h2>
        <button class="btn" type="button" onclick={closeSearch}>Close</button>
      </div>
      {#if appStore.searchQuery}
        <dl class="mono text-[11px] mb-3 grid gap-x-2" style="grid-template-columns: auto minmax(0, 1fr)">
          <dt style="color: var(--muted)">Latest search</dt>
          <dd class="min-w-0" style="overflow-wrap:anywhere">{appStore.searchQuery}</dd>
        </dl>
      {/if}
      <div role="status" aria-live="polite">
        {#if appStore.searching}
          <p class="mono text-[12px]" style="color: var(--muted)">Searching…</p>
        {:else if appStore.searchError}
          <ErrorMessage source="search" message={appStore.searchError} snapshot={false} />
        {:else if appStore.searchResults.length === 0}
          <p class="mono text-[12px]" style="color: var(--muted)">No supported downloadable options were found.</p>
        {/if}
      </div>
      {#if !appStore.searching && !appStore.searchError && appStore.searchWarnings.length}
        <ul class="mono text-[11px] mb-3" style="color: var(--warn)" aria-label="Partial search warnings">
          {#each appStore.searchWarnings as warning}
            <li style="overflow-wrap:anywhere">{warning}</li>
          {/each}
        </ul>
      {/if}
      {#if !appStore.searching && !appStore.searchError && appStore.searchResults.length}
        <ul class="flex flex-col gap-2 max-h-[60vh] overflow-auto">
          {#each appStore.searchResults as row (row.source)}
            <li class="flex items-start justify-between gap-2 panel p-2" style="border-color: var(--border)">
              <div class="min-w-0">
                <div class="mono text-[12px]" style="overflow-wrap:anywhere">{displayPath(row)}</div>
                <div class="mono text-[11px]" style="color: var(--muted); overflow-wrap:anywhere">
                  {row.repo_id ?? row.source}
                  {#if row.revision}
                    · revision {row.revision}
                  {/if}
                  {#if row.quant}
                    · {row.quant}
                  {/if}
                  · {sizeLabel(row)}
                </div>
              </div>
              <div class="flex items-center gap-2 shrink-0">
                <span class={`chip ${fitChip(row.fit)}`}>{fitText(row.fit)}</span>
                <button
                  class="btn btn-accent"
                  type="button"
                  aria-label={`Download ${displayPath(row)}`}
                  onclick={() => void downloadRow(row.source)}>Download</button
                >
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  </div>
{/if}
