<script lang="ts">
  import { appStore } from '$lib/store.svelte';
  import type { DownloadProgress } from '$lib/types';

  function fmtBytes(n?: number) {
    if (n == null) return '—';
    return `${(n / 1e9).toFixed(2)} GB`;
  }

  function fmtGb(n: number) {
    return (n / 1e9).toFixed(1);
  }

  function downloadLabel(d: DownloadProgress) {
    const pct = Math.round(d.pct);
    if (d.total == null) return `${pct}% · ${fmtGb(d.bytes)} GB`;
    return `${pct}% · ${fmtGb(d.bytes)} / ${fmtGb(d.total)} GB`;
  }

  const dl = $derived(appStore.status?.download ?? null);
</script>

<div class="max-w-4xl mx-auto p-4">
  <h1 class="mono text-[12px] uppercase tracking-wider mb-3" style="color: var(--muted)">Models</h1>

  <div class="panel p-3 mb-4 flex flex-col gap-2" style="border-color: var(--border)">
    <label class="mono text-[11px]" style="color: var(--muted)" for="model-source">Download</label>
    <div class="flex gap-2">
      <input
        id="model-source"
        class="field mono flex-1"
        bind:value={appStore.modelSource}
        placeholder="org/repo:file.gguf, org/repo:Q4_K_M, or URL"
      />
      <button
        class="btn btn-accent"
        disabled={!appStore.modelSource || !!appStore.busy}
        onclick={() => appStore.downloadModel()}>Download</button
      >
    </div>
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
                <button
                  class="btn"
                  disabled={!!appStore.busy}
                  onclick={() => {
                    appStore.selectedModel = m.id;
                    void appStore.distributeModel();
                  }}>Distribute</button
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
