<script lang="ts">
  import { appStore } from '$lib/store.svelte';

  function fmtBytes(n?: number | null) {
    if (n == null) return '—';
    return `${(n / 1e9).toFixed(1)} GB`;
  }

  function onGpuToggle(index: number) {
    appStore.toggleGpu(index);
    void appStore.persistGpuSelection();
  }
</script>

<div class="max-w-2xl mx-auto p-4">
  <h1 class="mono text-[12px] uppercase tracking-wider mb-3" style="color: var(--muted)">Plan & Run</h1>

  <div class="panel p-3 flex flex-col gap-3" style="border-color: var(--border)">
    <label class="mono text-[11px]" style="color: var(--muted)">Model</label>
    <select class="field mono" bind:value={appStore.selectedModel}>
      {#each appStore.models as m}
        <option value={m.id}>{m.id}</option>
      {/each}
    </select>

    <label class="mono text-[11px]" style="color: var(--muted)">Context</label>
    <input class="field mono" type="number" bind:value={appStore.context} min="512" step="512" />

    <label class="mono text-[11px]" style="color: var(--muted)">Device</label>
    <select
      class="field mono"
      value={appStore.device}
      disabled={!!appStore.busy}
      onchange={(e) => {
        const v = (e.currentTarget as HTMLSelectElement).value;
        if (v === 'cpu' || v === 'gpu') void appStore.setDevice(v);
      }}
    >
      <option value="gpu">GPU (default)</option>
      <option value="cpu">CPU</option>
    </select>
    <p class="mono text-[11px]" style="color: var(--muted)">
      Persists to ~/.kllm/config.toml. Prefer GPU; falls back to CPU when VRAM is unavailable.
    </p>

    {#if appStore.device === 'gpu'}
      {#if appStore.gpus.length > 0}
        <label class="mono text-[11px]" style="color: var(--muted)">
          GPUs ({appStore.gpuBackend})
        </label>
        <div class="flex flex-col gap-1">
          {#each appStore.gpus as g}
            <label class="mono text-[11px] flex items-start gap-2 cursor-pointer" style="color: var(--fg)">
              <input
                type="checkbox"
                class="mt-0.5"
                checked={appStore.selectedGpuIds.includes(g.index)}
                disabled={appStore.gpus.length === 1 || !!appStore.busy}
                onchange={() => onGpuToggle(g.index)}
              />
              <span>
                [{g.index}] {g.name}
                <span style="color: var(--muted)"> {fmtBytes(g.vram_bytes)}</span>
              </span>
            </label>
          {/each}
        </div>
      {/if}
      <label class="mono text-[11px]" style="color: var(--muted)">
        VRAM budget (%)
      </label>
      <div class="flex items-center gap-2">
        <input
          class="flex-1"
          type="range"
          min="10"
          max="100"
          step="1"
          value={appStore.vramPercent}
          disabled={!!appStore.busy}
          oninput={(e) => {
            appStore.vramPercent = Number((e.currentTarget as HTMLInputElement).value);
          }}
          onchange={(e) => {
            const v = Number((e.currentTarget as HTMLInputElement).value);
            void appStore.setVramPercent(v);
          }}
        />
        <input
          class="field mono"
          style="width: 4.5rem"
          type="number"
          min="10"
          max="100"
          step="1"
          value={appStore.vramPercent}
          disabled={!!appStore.busy}
          onchange={(e) => {
            const v = Number((e.currentTarget as HTMLInputElement).value);
            void appStore.setVramPercent(v);
          }}
        />
      </div>
    {:else}
      <label class="mono text-[11px]" style="color: var(--muted)">
        RAM budget (%)
      </label>
      <div class="flex items-center gap-2">
        <input
          class="flex-1"
          type="range"
          min="10"
          max="100"
          step="1"
          value={appStore.ramPercent}
          disabled={!!appStore.busy}
          oninput={(e) => {
            appStore.ramPercent = Number((e.currentTarget as HTMLInputElement).value);
          }}
          onchange={(e) => {
            const v = Number((e.currentTarget as HTMLInputElement).value);
            void appStore.setRamPercent(v);
          }}
        />
        <input
          class="field mono"
          style="width: 4.5rem"
          type="number"
          min="10"
          max="100"
          step="1"
          value={appStore.ramPercent}
          disabled={!!appStore.busy}
          onchange={(e) => {
            const v = Number((e.currentTarget as HTMLInputElement).value);
            void appStore.setRamPercent(v);
          }}
        />
      </div>
    {/if}

    <div class="flex gap-2">
      <button class="btn" disabled={!appStore.selectedModel || !!appStore.busy} onclick={() => appStore.plan()}
        >Plan</button
      >
      <button class="btn btn-accent" disabled={!appStore.selectedModel || !!appStore.busy} onclick={() => appStore.run()}
        >Run</button
      >
    </div>

    {#if appStore.status?.inference_up}
      <p class="mono text-[12px]" style="color: var(--accent)">
        Inference up — OpenAI base http://127.0.0.1:{appStore.status.control_port ?? 18765}/v1 (proxy) · llama
        :{appStore.status.infer_port ?? 8080}
      </p>
    {/if}

    {#if appStore.planResult}
      {#if appStore.planResult.device_requested}
        <p class="mono text-[12px]" style="color: var(--muted)">
          Device {String(appStore.planResult.device_requested)} → {String(appStore.planResult.device_effective)}
          {#if Array.isArray(appStore.planResult.node_devices) && (appStore.planResult.node_devices as { fallback?: boolean }[]).some((n) => n.fallback)}
            (fallback on some nodes)
          {/if}
        </p>
      {/if}
      <pre class="panel p-3 mono text-[11px] overflow-auto" style="border-color: var(--border)"
        >{JSON.stringify(appStore.planResult, null, 2)}</pre
      >
    {/if}
  </div>
</div>
