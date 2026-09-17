<script lang="ts">
  import { appStore } from '$lib/store.svelte';

  function fmtBytes(n?: number) {
    if (n == null) return '—';
    return `${(n / 1e9).toFixed(2)} GB`;
  }
</script>

<div class="max-w-4xl mx-auto p-4">
  <h1 class="mono text-[12px] uppercase tracking-wider mb-3" style="color: var(--muted)">Models</h1>

  <div class="panel p-3 mb-4 flex flex-col gap-2" style="border-color: var(--border)">
    <label class="mono text-[11px]" style="color: var(--muted)">Download (org/repo:file.gguf or URL)</label>
    <div class="flex gap-2">
      <input class="field mono flex-1" bind:value={appStore.modelSource} placeholder="TheBloke/…:model.Q4_K_M.gguf" />
      <button class="btn btn-accent" disabled={!appStore.modelSource || !!appStore.busy} onclick={() => appStore.downloadModel()}
        >Download</button
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
