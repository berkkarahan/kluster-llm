<script lang="ts">
  import { appStore } from '$lib/store.svelte';
  import type {
    DistributeProgress,
    DownloadProgress,
    ModelSearchFit,
    ModelSearchHit,
    ReceiveProgress,
  } from '$lib/types';

  let modalOpen = $state(false);

  function fmtBytes(n?: number | null) {
    if (n == null) return '—';
    return `${(n / 1e9).toFixed(2)} GB`;
  }

  function fmtGb(n: number) {
    return (n / 1e9).toFixed(1);
  }

  function downloadLabel(d: DownloadProgress | ReceiveProgress) {
    const pct = Math.round(d.pct);
    if (d.total == null) return `${pct}% · ${fmtGb(d.bytes)} GB`;
    return `${pct}% · ${fmtGb(d.bytes)} / ${fmtGb(d.total)} GB`;
  }

  function transferLabel(d: DistributeProgress) {
    const pct = Math.round(d.pct);
    const phase = d.phase ?? 'pulling';
    if (phase === 'hashing' && d.total != null && d.bytes != null) {
      return `hashing ${pct}% · ${fmtGb(d.bytes)} / ${fmtGb(d.total)} GB`;
    }
    return `distribute ${pct}%`;
  }

  function workerLabel(w: DistributeProgress['workers'][number]) {
    const pct = Math.round(w.pct);
    const host = w.host || w.node_id || 'worker';
    if (w.phase === 'skipped') return `${host} · skipped`;
    if (w.phase === 'error') return `${host} · error${w.error ? `: ${w.error}` : ''}`;
    if (w.total == null) return `${host} · ${w.phase} ${pct}%`;
    return `${host} · ${w.phase} ${pct}% · ${fmtGb(w.bytes)} / ${fmtGb(w.total)} GB`;
  }

  function fitChip(fit: ModelSearchFit) {
    if (fit === 'fits') return 'chip-ok';
    if (fit === 'overflow') return 'chip-warn';
    return 'chip-muted';
  }

  function fitLabel(row: ModelSearchHit) {
    const size = row.size_bytes ? fmtGb(row.size_bytes) : '—';
    const vram = appStore.searchTotalVram != null ? fmtGb(appStore.searchTotalVram) : '—';
    return `${size} / ${vram} GB`;
  }

  const dl = $derived(appStore.status?.download ?? null);
  const dist = $derived(appStore.status?.distribute ?? null);
  const recv = $derived(appStore.status?.receive ?? null);
  const isMaster = $derived(appStore.status?.role === 'master');

  $effect(() => {
    if (!modalOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') modalOpen = false;
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  async function runSearch() {
    if (!appStore.modelSource.trim() || appStore.busy) return;
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
</script>

<div class="max-w-4xl mx-auto p-4">
  <h1 class="mono text-[12px] uppercase tracking-wider mb-3" style="color: var(--muted)">Models</h1>

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
    {#if dl}
      <div class="flex flex-col gap-1">
        <div
          class="progress"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(dl.pct)}
          aria-label="Model download"
        >
          <span style:width={`${Math.min(100, Math.max(0, dl.pct))}%`}></span>
        </div>
        <div class="mono text-[11px]" style="color: var(--muted)">
          {downloadLabel(dl)}{#if dl.filename}<span> · {dl.filename}</span>{/if}
        </div>
      </div>
    {:else if appStore.busy === '/v1/models/download'}
      <div class="mono text-[11px]" style="color: var(--muted)">starting…</div>
    {/if}

    {#if dist}
      <div class="flex flex-col gap-2 mt-1">
        <div class="mono text-[11px]" style="color: var(--muted)">
          {transferLabel(dist)}{#if dist.filename}<span> · {dist.filename}</span>{/if}
        </div>
        <div
          class="progress"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(dist.pct)}
          aria-label={dist.phase === 'hashing' ? 'Model hashing' : 'Model distribute'}
        >
          <span style:width={`${Math.min(100, Math.max(0, dist.pct))}%`}></span>
        </div>
        {#each dist.workers as w (w.host + (w.node_id ?? ''))}
          <div class="flex flex-col gap-1">
            <div
              class="progress"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(w.pct)}
              aria-label={`Distribute to ${w.host}`}
            >
              <span style:width={`${Math.min(100, Math.max(0, w.pct))}%`}></span>
            </div>
            <div class="mono text-[11px]" style="color: var(--muted)">{workerLabel(w)}</div>
          </div>
        {/each}
      </div>
    {:else if appStore.busy === '/v1/models/distribute'}
      <div class="mono text-[11px]" style="color: var(--muted)">distributing…</div>
    {/if}

    {#if recv}
      <div class="flex flex-col gap-1 mt-1">
        <div
          class="progress"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(recv.pct)}
          aria-label="Model receive"
        >
          <span style:width={`${Math.min(100, Math.max(0, recv.pct))}%`}></span>
        </div>
        <div class="mono text-[11px]" style="color: var(--muted)">
          receive {downloadLabel(recv)}{#if recv.filename}<span> · {recv.filename}</span>{/if}
          {#if recv.phase}<span> · {recv.phase}</span>{/if}
        </div>
      </div>
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

  <div class="panel overflow-hidden" style="border-color: var(--border)">
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
          <tr class="mono">
            <td class="px-3 py-2 border-b" style="border-color: var(--border)">{m.id}</td>
            <td class="px-3 py-2 border-b" style="border-color: var(--border)">{fmtBytes(m.size_bytes)}</td>
            <td class="px-3 py-2 border-b" style="border-color: var(--border)">
              {#if m.loaded}
                <span class="chip chip-ok">loaded</span>
              {:else}
                <span class="chip chip-muted">local</span>
              {/if}
            </td>
            <td class="px-3 py-2 border-b text-right" style="border-color: var(--border)">
              <div class="inline-flex gap-2">
                {#if isMaster}
                  <button
                    class="btn"
                    disabled={!!appStore.busy}
                    onclick={() => void appStore.distributeModel(m.id)}>Distribute</button
                  >
                {/if}
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
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="model-search-title">
      <div
        class="flex items-center justify-between gap-2 px-3 py-2 border-b"
        style="border-color: var(--border)"
      >
        <h2 id="model-search-title" class="mono text-[12px] uppercase tracking-wider" style="color: var(--muted)">
          Hugging Face GGUFs
        </h2>
        <button class="btn" type="button" onclick={() => (modalOpen = false)}>Close</button>
      </div>
      <div class="overflow-auto flex-1 min-h-0">
        <table class="w-full text-left">
          <thead>
            <tr class="mono text-[11px]" style="color: var(--muted)">
              <th class="px-3 py-2 border-b font-medium" style="border-color: var(--border)">repo</th>
              <th class="px-3 py-2 border-b font-medium" style="border-color: var(--border)">file / quant</th>
              <th class="px-3 py-2 border-b font-medium" style="border-color: var(--border)">size</th>
              <th class="px-3 py-2 border-b font-medium" style="border-color: var(--border)">fit</th>
              <th class="px-3 py-2 border-b font-medium" style="border-color: var(--border)"></th>
            </tr>
          </thead>
          <tbody>
            {#if appStore.searching}
              <tr>
                <td colspan="5" class="px-3 py-6 mono" style="color: var(--muted)">searching…</td>
              </tr>
            {:else if appStore.searchError}
              <tr>
                <td colspan="5" class="px-3 py-6 mono" style="color: var(--danger)">{appStore.searchError}</td>
              </tr>
            {:else}
              {#each appStore.searchResults as row}
                <tr class="mono">
                  <td class="px-3 py-2 border-b align-top" style="border-color: var(--border)">
                    <div>{row.repo_id}</div>
                    {#if row.gated}
                      <span class="chip chip-warn">gated</span>
                    {/if}
                  </td>
                  <td class="px-3 py-2 border-b align-top" style="border-color: var(--border)">
                    <div>{row.filename}</div>
                    {#if row.quant}
                      <div class="text-[11px]" style="color: var(--muted)">{row.quant}</div>
                    {/if}
                  </td>
                  <td class="px-3 py-2 border-b align-top" style="border-color: var(--border)"
                    >{fmtBytes(row.size_bytes)}</td
                  >
                  <td class="px-3 py-2 border-b align-top" style="border-color: var(--border)">
                    <div class="flex flex-col gap-1 items-start">
                      <span class="chip {fitChip(row.fit)}">{row.fit}</span>
                      <span class="text-[11px]" style="color: var(--muted)">{fitLabel(row)}</span>
                    </div>
                  </td>
                  <td class="px-3 py-2 border-b text-right align-top" style="border-color: var(--border)">
                    <button
                      class="btn btn-accent"
                      disabled={!!appStore.busy}
                      onclick={() => void downloadRow(row.source)}>Download</button
                    >
                  </td>
                </tr>
              {:else}
                <tr>
                  <td colspan="5" class="px-3 py-6 mono" style="color: var(--muted)">No matching GGUFs</td>
                </tr>
              {/each}
            {/if}
          </tbody>
        </table>
      </div>
    </div>
  </div>
{/if}
