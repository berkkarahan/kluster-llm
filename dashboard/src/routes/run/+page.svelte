<script lang="ts">
  import { appStore } from '$lib/store.svelte';
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
