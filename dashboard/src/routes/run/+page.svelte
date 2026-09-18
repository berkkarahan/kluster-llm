<script lang="ts">
  import AutoEditField from '$lib/components/AutoEditField.svelte';
  import { appStore } from '$lib/store.svelte';
  import { CONTEXT_STEP, KV_CACHE_TYPES, MIN_CONTEXT } from '$lib/types';

  function fmtBytes(n?: number | null) {
    if (n == null) return '—';
    return `${(n / 1e9).toFixed(1)} GB`;
  }

  function onGpuToggle(index: number) {
    appStore.toggleGpu(index);
    void appStore.persistGpuSelection();
    appStore.scheduleEstimate();
  }

  function bumpEstimate() {
    appStore.scheduleEstimate();
  }

  const blocked = $derived(appStore.planRunBlockedReason());
  const fitStatus = $derived(
    appStore.planResult?.fit_status ? String(appStore.planResult.fit_status) : null
  );
  const maxContext = $derived(appStore.selectedModelMaxContext());
  const contextDisabled = $derived(appStore.contextAuto || !!appStore.busy);
  const estimateLabel = $derived(appStore.device === 'cpu' ? 'Estimated memory' : 'Estimated VRAM');

  const parallelMode = $derived(appStore.parallelSlots === 'auto' ? 'auto' : 'edit');
  const batchMode = $derived(appStore.batchSize === 'auto' ? 'auto' : 'edit');
  const microMode = $derived(appStore.microBatch === 'auto' ? 'auto' : 'edit');
  const checkpointMode = $derived(appStore.checkpoints === 'auto' ? 'auto' : 'edit');
  const cacheRamMode = $derived(appStore.cacheRam === 'auto' ? 'auto' : 'edit');
  const speculativeMode = $derived(appStore.speculative === 'auto' ? 'auto' : 'edit');
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
    {#if !blocked && appStore.planResult?.estimated_bytes != null}
      <div class="mono text-[11px] p-2 rounded" style="background: var(--bg); border: 1px solid var(--border)">
        {estimateLabel}:
        {fmtBytes(Number(appStore.planResult.estimated_bytes))}
        / budget {fmtBytes(Number(appStore.planResult.budget_bytes))}
        {#if fitStatus}
          · <span style="color: var(--accent)">{fitStatus}</span>
        {/if}
        {#if appStore.estimating}
          · <span style="color: var(--muted)">updating…</span>
        {/if}
      </div>
    {/if}

    <label class="mono text-[11px]" style="color: var(--muted)">Model</label>
    <select
      class="field mono"
      value={appStore.selectedModel}
      onchange={(e) => {
        const v = (e.currentTarget as HTMLSelectElement).value;
        void appStore.selectModel(v).then(() => bumpEstimate());
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
        <input
          type="checkbox"
          bind:checked={appStore.contextAuto}
          disabled={!!blocked || !!appStore.busy}
          onchange={bumpEstimate}
        />
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
          bumpEstimate();
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
          bumpEstimate();
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
      <select
        class="field mono flex-1"
        bind:value={appStore.cacheTypeK}
        disabled={!!appStore.busy}
        onchange={bumpEstimate}
      >
        {#each KV_CACHE_TYPES as t}
          <option value={t}>{t}</option>
        {/each}
      </select>
      <select
        class="field mono flex-1"
        bind:value={appStore.cacheTypeV}
        disabled={!!appStore.busy}
        onchange={bumpEstimate}
      >
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
        if (v === 'cpu' || v === 'gpu') {
          void appStore.setDevice(v).then(() => bumpEstimate());
        }
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
        onchange={() => void appStore.setVramPercent(appStore.vramPercent).then(() => bumpEstimate())}
      />
      <label class="mono text-[11px]" style="color: var(--muted)">Placement weights (VRAM / latency)</label>
      <div class="flex gap-2">
        <input
          class="field mono flex-1"
          type="number"
          min="0"
          max="1"
          step="0.05"
          bind:value={appStore.vramWeight}
          disabled={!!appStore.busy}
          onchange={bumpEstimate}
        />
        <input
          class="field mono flex-1"
          type="number"
          min="0"
          max="1"
          step="0.05"
          bind:value={appStore.latencyWeight}
          disabled={!!appStore.busy}
          onchange={bumpEstimate}
        />
      </div>
      {#if appStore.fieldErrors.placementWeights}
        <p class="mono text-[11px]" style="color: var(--accent)">{appStore.fieldErrors.placementWeights}</p>
      {/if}
    {:else}
      <label class="mono text-[11px]" style="color: var(--muted)">CPU memory (%)</label>
      <input
        class="field mono"
        type="number"
        min="10"
        max="100"
        bind:value={appStore.ramPercent}
        disabled={!!appStore.busy}
        onchange={() => void appStore.setRamPercent(appStore.ramPercent).then(() => bumpEstimate())}
      />
    {/if}

    <details bind:open={appStore.advancedOpen}>
      <summary class="mono text-[11px] cursor-pointer" style="color: var(--muted)">Advanced</summary>
      <div class="flex flex-col gap-3 mt-2 pl-1">
        <AutoEditField
          label="Speculative / draft model"
          mode={speculativeMode}
          disabled={!!appStore.busy}
          onModeChange={(m) => appStore.setSpeculativeMode(m)}
        >
          <input
            class="field mono w-full"
            placeholder="Draft model path"
            bind:value={appStore.draftModel}
            disabled={!!appStore.busy}
            onchange={bumpEstimate}
          />
        </AutoEditField>

        <AutoEditField
          label="Parallel slots"
          mode={parallelMode}
          disabled={!!appStore.busy}
          onModeChange={(m) => appStore.setAutoInt('parallelSlots', m)}
        >
          <input
            class="field mono w-full"
            type="number"
            min="1"
            value={typeof appStore.parallelSlots === 'number' ? appStore.parallelSlots : 2}
            disabled={!!appStore.busy}
            onchange={(e) => {
              appStore.parallelSlots = Math.max(1, Number((e.currentTarget as HTMLInputElement).value) || 1);
              bumpEstimate();
            }}
          />
        </AutoEditField>

        <AutoEditField
          label="Batch size"
          mode={batchMode}
          disabled={!!appStore.busy}
          onModeChange={(m) => appStore.setAutoInt('batchSize', m)}
        >
          <input
            class="field mono w-full"
            type="number"
            min="1"
            value={typeof appStore.batchSize === 'number' ? appStore.batchSize : 512}
            disabled={!!appStore.busy}
            onchange={(e) => {
              appStore.batchSize = Math.max(1, Number((e.currentTarget as HTMLInputElement).value) || 1);
            }}
          />
        </AutoEditField>

        <AutoEditField
          label="Micro-batch"
          mode={microMode}
          disabled={!!appStore.busy}
          onModeChange={(m) => appStore.setAutoInt('microBatch', m)}
        >
          <input
            class="field mono w-full"
            type="number"
            min="1"
            value={typeof appStore.microBatch === 'number' ? appStore.microBatch : 2}
            disabled={!!appStore.busy}
            onchange={(e) => {
              appStore.microBatch = Math.max(1, Number((e.currentTarget as HTMLInputElement).value) || 1);
            }}
          />
        </AutoEditField>

        <label class="mono text-[11px] flex items-center gap-2" style="color: var(--fg)">
          <input
            type="checkbox"
            bind:checked={appStore.vision}
            disabled={!!appStore.busy}
            onchange={bumpEstimate}
          />
          Vision
        </label>
        {#if appStore.vision}
          <label class="mono text-[11px]" style="color: var(--muted)">mmproj path</label>
          <input
            class="field mono"
            placeholder="Path to mmproj GGUF"
            bind:value={appStore.mmproj}
            disabled={!!appStore.busy}
            onchange={bumpEstimate}
          />
        {/if}


        <label class="mono text-[11px]" style="color: var(--muted)">Sampling (empty = auto)</label>
        <div class="grid grid-cols-2 gap-2">
          <div>
            <label class="mono text-[10px]" style="color: var(--muted)" for="inf-temp">Temperature</label>
            <input
              id="inf-temp"
              class="field mono w-full"
              type="number"
              min="0"
              max="2"
              step="0.05"
              placeholder={appStore.autoSamplingHint('temperature')}
              value={appStore.temperature ?? ''}
              disabled={!!appStore.busy}
              onchange={(e) => {
                const raw = (e.currentTarget as HTMLInputElement).value.trim();
                appStore.temperature = raw === '' ? null : Number(raw);
              }}
            />
          </div>
          <div>
            <label class="mono text-[10px]" style="color: var(--muted)" for="inf-top-p">Top P</label>
            <input
              id="inf-top-p"
              class="field mono w-full"
              type="number"
              min="0"
              max="1"
              step="0.01"
              placeholder={appStore.autoSamplingHint('top_p')}
              value={appStore.topP ?? ''}
              disabled={!!appStore.busy}
              onchange={(e) => {
                const raw = (e.currentTarget as HTMLInputElement).value.trim();
                appStore.topP = raw === '' ? null : Number(raw);
              }}
            />
          </div>
          <div>
            <label class="mono text-[10px]" style="color: var(--muted)" for="inf-top-k">Top K</label>
            <input
              id="inf-top-k"
              class="field mono w-full"
              type="number"
              min="0"
              step="1"
              placeholder={appStore.autoSamplingHint('top_k')}
              value={appStore.topK ?? ''}
              disabled={!!appStore.busy}
              onchange={(e) => {
                const raw = (e.currentTarget as HTMLInputElement).value.trim();
                appStore.topK = raw === '' ? null : Math.max(0, Math.floor(Number(raw)));
              }}
            />
          </div>
          <div>
            <label class="mono text-[10px]" style="color: var(--muted)" for="inf-min-p">Min P</label>
            <input
              id="inf-min-p"
              class="field mono w-full"
              type="number"
              min="0"
              max="1"
              step="0.01"
              placeholder={appStore.autoSamplingHint('min_p')}
              value={appStore.minP ?? ''}
              disabled={!!appStore.busy}
              onchange={(e) => {
                const raw = (e.currentTarget as HTMLInputElement).value.trim();
                appStore.minP = raw === '' ? null : Number(raw);
              }}
            />
          </div>
          <div>
            <label class="mono text-[10px]" style="color: var(--muted)" for="inf-pres">Presence penalty</label>
            <input
              id="inf-pres"
              class="field mono w-full"
              type="number"
              min="-2"
              max="2"
              step="0.05"
              placeholder={appStore.autoSamplingHint('presence_penalty')}
              value={appStore.presencePenalty ?? ''}
              disabled={!!appStore.busy}
              onchange={(e) => {
                const raw = (e.currentTarget as HTMLInputElement).value.trim();
                appStore.presencePenalty = raw === '' ? null : Number(raw);
              }}
            />
          </div>
          <div>
            <label class="mono text-[10px]" style="color: var(--muted)" for="inf-rep">Repeat penalty</label>
            <input
              id="inf-rep"
              class="field mono w-full"
              type="number"
              min="0"
              max="2"
              step="0.05"
              placeholder={appStore.autoSamplingHint('repeat_penalty')}
              value={appStore.repeatPenalty ?? ''}
              disabled={!!appStore.busy}
              onchange={(e) => {
                const raw = (e.currentTarget as HTMLInputElement).value.trim();
                appStore.repeatPenalty = raw === '' ? null : Number(raw);
              }}
            />
          </div>
        </div>

        <label class="mono text-[11px]" style="color: var(--muted)" for="inf-think">Enable thinking</label>
        <select
          id="inf-think"
          class="field mono"
          disabled={!!appStore.busy}
          value={appStore.enableThinking === null ? 'auto' : appStore.enableThinking ? 'on' : 'off'}
          onchange={(e) => {
            const v = (e.currentTarget as HTMLSelectElement).value;
            appStore.enableThinking = v === 'auto' ? null : v === 'on';
          }}
        >
          <option value="auto">{appStore.autoThinkingHint()}</option>
          <option value="on">On</option>
          <option value="off">Off</option>
        </select>

        <label class="mono text-[11px]" style="color: var(--muted)">Chat template</label>
        <input
          class="field mono"
          placeholder="Template name (default)"
          bind:value={appStore.chatTemplate}
          disabled={!!appStore.busy}
        />
        <label class="mono text-[11px]" style="color: var(--muted)">Chat template (jinja)</label>
        <textarea
          class="field mono"
          rows="3"
          placeholder="Optional custom jinja"
          bind:value={appStore.chatTemplateText}
          disabled={!!appStore.busy}
        ></textarea>

        <label class="mono text-[11px]" style="color: var(--muted)">mmap / mlock</label>
        <select class="field mono" bind:value={appStore.mmapMlock} disabled={!!appStore.busy}>
          <option value="auto">auto</option>
          <option value="mmap">mmap</option>
          <option value="mlock">mlock</option>
          <option value="mmap+mlock">mmap+mlock</option>
          <option value="none">none</option>
        </select>

        <AutoEditField
          label="Checkpoints"
          mode={checkpointMode}
          disabled={!!appStore.busy}
          onModeChange={(m) => appStore.setAutoInt('checkpoints', m)}
        >
          <input
            class="field mono w-full"
            type="number"
            min="0"
            value={typeof appStore.checkpoints === 'number' ? appStore.checkpoints : 0}
            disabled={!!appStore.busy}
            onchange={(e) => {
              appStore.checkpoints = Math.max(0, Number((e.currentTarget as HTMLInputElement).value) || 0);
            }}
          />
        </AutoEditField>

        <AutoEditField
          label="Cache RAM (MiB)"
          mode={cacheRamMode}
          disabled={!!appStore.busy}
          onModeChange={(m) => appStore.setAutoInt('cacheRam', m)}
        >
          <input
            class="field mono w-full"
            type="number"
            min="0"
            value={typeof appStore.cacheRam === 'number' ? appStore.cacheRam : 0}
            disabled={!!appStore.busy}
            onchange={(e) => {
              appStore.cacheRam = Math.max(0, Number((e.currentTarget as HTMLInputElement).value) || 0);
            }}
          />
        </AutoEditField>

        <label class="mono text-[11px]" style="color: var(--muted)">Threads</label>
        <input
          class="field mono"
          type="number"
          min="1"
          bind:value={appStore.threads}
          disabled={!!appStore.busy}
        />
        {#if appStore.fieldErrors.threads}
          <p class="mono text-[11px]" style="color: var(--accent)">{appStore.fieldErrors.threads}</p>
        {/if}

        <label class="mono text-[11px]" style="color: var(--muted)">Extra args</label>
        <input
          class="field mono"
          placeholder="Space-separated llama-server args"
          bind:value={appStore.extraArgsText}
          disabled={!!appStore.busy}
        />
      </div>
    </details>

    <label class="mono text-[11px] flex items-center gap-2" style="color: var(--muted)">
      <input type="checkbox" bind:checked={appStore.remember} /> Remember settings for this model
    </label>

    <p class="mono text-[11px]" style="color: var(--muted)">
      Automatic placement — layer split and RPC distribution are computed by kllm (no manual tensor split).
    </p>

    <div class="flex gap-2 flex-wrap">
      <button
        class="btn"
        type="button"
        onclick={() => {
          appStore.resetRunSettings();
          bumpEstimate();
        }}>Reset</button
      >
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

    {#if !blocked && appStore.planResult}
      {#if appStore.planResult.hint}
        <p class="mono text-[11px]" style="color: var(--muted)">{String(appStore.planResult.hint)}</p>
      {/if}
      {#if appStore.planResult.device_requested}
        <p class="mono text-[12px]" style="color: var(--muted)">
          Placement: {String(appStore.planResult.device_requested)} → {String(appStore.planResult.device_effective)}
        </p>
      {/if}
      {#if Array.isArray(appStore.planResult.node_ids)}
        <table class="w-full text-left border-collapse mono text-[11px] mt-2">
          <thead>
            <tr style="color: var(--muted)">
              <th class="font-medium py-1 border-b" style="border-color: var(--border)">node</th>
              <th class="font-medium py-1 border-b" style="border-color: var(--border)">split</th>
              <th class="font-medium py-1 border-b" style="border-color: var(--border)">alloc</th>
              <th class="font-medium py-1 border-b" style="border-color: var(--border)">score</th>
              <th class="font-medium py-1 border-b" style="border-color: var(--border)">latency</th>
            </tr>
          </thead>
          <tbody>
            {#each appStore.planResult.node_ids as nid, i}
              <tr>
                <td class="py-1 border-b" style="border-color: var(--border)">{String(nid)}</td>
                <td class="py-1 border-b" style="border-color: var(--border)">
                  {Number((appStore.planResult.tensor_split as number[])?.[i] ?? 0).toFixed(3)}
                </td>
                <td class="py-1 border-b" style="border-color: var(--border)">
                  {fmtBytes(Number((appStore.planResult.node_alloc_bytes as number[])?.[i] ?? 0))}
                </td>
                <td class="py-1 border-b" style="border-color: var(--border)">
                  {Number((appStore.planResult.node_scores as number[])?.[i] ?? 0).toFixed(3)}
                </td>
                <td class="py-1 border-b" style="border-color: var(--border)">
                  {#if (appStore.planResult.node_latency_ms as number[])?.[i] != null}
                    {Number((appStore.planResult.node_latency_ms as number[])[i]).toFixed(1)} ms
                  {:else}
                    —
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    {/if}
  </div>
</div>
