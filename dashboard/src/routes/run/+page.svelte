<script lang="ts">
  import { appStore } from '$lib/store.svelte';
  import { CONTEXT_STEP, KV_CACHE_TYPES, MIN_CONTEXT } from '$lib/types';

  function fmtBytes(n?: number | null) {
    if (n == null) return '—';
    return `${(n / 1e9).toFixed(1)} GB`;
  }

  function onGpuToggle(index: number) {
    appStore.toggleGpu(index);
    void appStore.persistGpuSelection();
  }

  const blocked = $derived(appStore.planRunBlockedReason());
  const fitStatus = $derived(
    appStore.planResult?.fit_status ? String(appStore.planResult.fit_status) : null
  );
  const maxContext = $derived(appStore.selectedModelMaxContext());
  const contextDisabled = $derived(appStore.contextAuto || !!appStore.busy);
</script>

<div class="max-w-2xl mx-auto p-4">
  <h1 class="mono text-[12px] uppercase tracking-wider mb-1" style="color: var(--muted)">RUN SETTINGS</h1>
  <p class="mono text-[11px] mb-3" style="color: var(--muted)">
    Model: {appStore.selectedModel || '—'}
  </p>

  {#if appStore.status?.apply_error}
    <p class="mono text-[11px] mb-3" style="color: var(--danger, #c44)">{appStore.status.apply_error}</p>
  {/if}
  {#if appStore.status?.apply_warning}
    <p class="mono text-[11px] mb-3" style="color: var(--warn, #b80)">{appStore.status.apply_warning}</p>
  {/if}

  {#if blocked}
    <p class="mono text-[11px] mb-3" style="color: var(--accent)">{blocked}</p>
  {/if}

  <div class="panel p-3 flex flex-col gap-3" style="border-color: var(--border)">
    {#if appStore.planResult?.estimated_bytes != null}
      <div class="mono text-[11px] p-2 rounded" style="background: var(--bg); border: 1px solid var(--border)">
        Estimated memory (last Plan):
        {fmtBytes(Number(appStore.planResult.estimated_bytes))}
        / budget {fmtBytes(Number(appStore.planResult.budget_bytes))}
        {#if fitStatus}
          · <span style="color: var(--accent)">{fitStatus}</span>
        {/if}
      </div>
    {/if}

    <label class="mono text-[11px]" style="color: var(--muted)">Model</label>
    <select
      class="field mono"
      value={appStore.selectedModel}
      onchange={(e) => {
        const v = (e.currentTarget as HTMLSelectElement).value;
        void appStore.selectModel(v);
      }}
    >
      {#each appStore.models as m}
        <option value={m.id}>{m.id}</option>
      {/each}
    </select>

    <div class="flex items-center gap-2">
      <label class="mono text-[11px] flex-1" style="color: var(--muted)" title="Context window size for KV cache" for="run-context">
        Context
      </label>
      <label class="mono text-[11px] flex items-center gap-1" style="color: var(--muted)">
        <input type="checkbox" bind:checked={appStore.contextAuto} disabled={!!blocked || !!appStore.busy} />
        Auto
      </label>
    </div>
    <div class="flex items-center gap-2">
      <input
        id="run-context"
        class="flex-1"
        type="range"
        min={MIN_CONTEXT}
        max={maxContext}
        step={CONTEXT_STEP}
        value={appStore.context}
        disabled={contextDisabled}
        oninput={(e) => {
          appStore.context = Number((e.currentTarget as HTMLInputElement).value);
        }}
      />
      <input
        class="field mono"
        style="width: 5.5rem"
        type="number"
        min={MIN_CONTEXT}
        max={maxContext}
        step={CONTEXT_STEP}
        value={appStore.context}
        disabled={contextDisabled}
        onchange={(e) => {
          const raw = Number((e.currentTarget as HTMLInputElement).value);
          const stepped = Math.round(raw / CONTEXT_STEP) * CONTEXT_STEP;
          appStore.context = Math.min(maxContext, Math.max(MIN_CONTEXT, stepped || MIN_CONTEXT));
        }}
      />
    </div>
    <p class="mono text-[10px]" style="color: var(--muted)">
      Max {maxContext.toLocaleString()} (model)
    </p>
    {#if appStore.fieldErrors.context}
      <p class="mono text-[11px]" style="color: var(--accent)">{appStore.fieldErrors.context}</p>
    {/if}

    <label class="mono text-[11px]" style="color: var(--muted)" title="KV cache dtype (not weight quantization)">
      KV cache dtype
    </label>
    <div class="flex gap-2">
      <select class="field mono flex-1" bind:value={appStore.cacheTypeK} disabled={!!appStore.busy}>
        {#each KV_CACHE_TYPES as t}
          <option value={t}>{t}</option>
        {/each}
      </select>
      <select class="field mono flex-1" bind:value={appStore.cacheTypeV} disabled={!!appStore.busy}>
        {#each KV_CACHE_TYPES as t}
          <option value={t}>{t}</option>
        {/each}
      </select>
    </div>

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

    {#if appStore.device === 'gpu'}
      {#if appStore.gpus.length > 0}
        <label class="mono text-[11px]" style="color: var(--muted)">GPUs ({appStore.gpuBackend})</label>
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
              <span>[{g.index}] {g.name} <span style="color: var(--muted)">{fmtBytes(g.vram_bytes)}</span></span>
            </label>
          {/each}
        </div>
      {/if}
      <label class="mono text-[11px]" style="color: var(--muted)">GPU memory (%)</label>
      <input
        class="field mono"
        type="number"
        min="10"
        max="100"
        bind:value={appStore.vramPercent}
        disabled={!!appStore.busy}
        onchange={() => void appStore.setVramPercent(appStore.vramPercent)}
      />
    {:else}
      <label class="mono text-[11px]" style="color: var(--muted)">CPU memory (%)</label>
      <input
        class="field mono"
        type="number"
        min="10"
        max="100"
        bind:value={appStore.ramPercent}
        disabled={!!appStore.busy}
        onchange={() => void appStore.setRamPercent(appStore.ramPercent)}
      />
    {/if}

    <details bind:open={appStore.advancedOpen}>
      <summary class="mono text-[11px] cursor-pointer" style="color: var(--muted)">Advanced</summary>
      <div class="flex flex-col gap-2 mt-2 pl-1">
        <input class="field mono" placeholder="Draft model path" bind:value={appStore.draftModel} />
        <input class="field mono" placeholder="Parallel slots (auto or number)" bind:value={appStore.parallelSlots} />
        <input class="field mono" placeholder="Batch size (auto or number)" bind:value={appStore.batchSize} />
        <input class="field mono" placeholder="Micro-batch (auto or number)" bind:value={appStore.microBatch} />
        <label class="mono text-[11px] flex items-center gap-2">
          <input type="checkbox" bind:checked={appStore.vision} /> Vision
        </label>
        <input class="field mono" placeholder="mmproj path" bind:value={appStore.mmproj} />
        <input class="field mono" placeholder="Chat template name" bind:value={appStore.chatTemplate} />
        <textarea class="field mono" rows="3" placeholder="Chat template jinja" bind:value={appStore.chatTemplateText}
        ></textarea>
        <select class="field mono" bind:value={appStore.mmapMlock}>
          <option value="auto">mmap/mlock auto</option>
          <option value="mmap">mmap</option>
          <option value="mlock">mlock</option>
          <option value="mmap+mlock">mmap+mlock</option>
          <option value="none">none</option>
        </select>
        <input class="field mono" placeholder="Checkpoints (auto or number)" bind:value={appStore.checkpoints} />
        <input class="field mono" placeholder="Cache RAM MiB (auto or number)" bind:value={appStore.cacheRam} />
        <input class="field mono" type="number" min="1" bind:value={appStore.threads} />
        <input class="field mono" placeholder="Extra args (space-separated)" bind:value={appStore.extraArgsText} />
      </div>
    </details>

    <label class="mono text-[11px] flex items-center gap-2" style="color: var(--muted)">
      <input type="checkbox" bind:checked={appStore.remember} /> Remember settings for this model
    </label>

    <p class="mono text-[11px]" style="color: var(--muted)">
      Automatic placement — layer split and RPC distribution are computed by kllm (no manual tensor split).
    </p>

    <div class="flex gap-2 flex-wrap">
      <button class="btn" type="button" onclick={() => appStore.resetRunSettings()}>Reset</button>
      <button
        class="btn"
        disabled={!!blocked || !appStore.selectedModel || !!appStore.busy}
        onclick={() => appStore.plan()}>Plan</button
      >
      <button
        class="btn btn-accent"
        disabled={!!blocked || !appStore.selectedModel || !!appStore.busy || fitStatus === 'overflow'}
        onclick={() => appStore.run()}>Run</button
      >
    </div>

    {#if appStore.status?.inference_up}
      <p class="mono text-[12px]" style="color: var(--accent)">
        Inference up — proxy :{appStore.status.control_port ?? 18765}/v1 · llama :{appStore.status.infer_port ?? 8080}
      </p>
    {/if}

    {#if appStore.planResult}
      {#if appStore.planResult.hint}
        <p class="mono text-[11px]" style="color: var(--muted)">{String(appStore.planResult.hint)}</p>
      {/if}
      {#if appStore.planResult.device_requested}
        <p class="mono text-[12px]" style="color: var(--muted)">
          Placement: {String(appStore.planResult.device_requested)} → {String(appStore.planResult.device_effective)}
          · split {JSON.stringify(appStore.planResult.tensor_split)}
        </p>
      {/if}
    {/if}
  </div>
</div>
