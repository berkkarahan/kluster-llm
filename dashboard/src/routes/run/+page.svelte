<script lang="ts">
  import AutoEditField from '$lib/components/AutoEditField.svelte';
  import DownloadPanel from '$lib/components/DownloadPanel.svelte';
  import HardwareSettings from '$lib/components/HardwareSettings.svelte';
  import LoadProgressPanel from '$lib/components/LoadProgressPanel.svelte';
  import SliderField from '$lib/components/SliderField.svelte';
  import { appStore } from '$lib/store.svelte';
  import { formatGb } from '$lib/format-transfer';
  import { CONTEXT_STEP, KV_CACHE_TYPES, MIN_CONTEXT } from '$lib/types';

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
  const settingsDirty = $derived(appStore.settingsDirty());
  const maxThreads = $derived(
    typeof navigator !== 'undefined' && navigator.hardwareConcurrency
      ? Math.max(16, navigator.hardwareConcurrency * 2)
      : 64
  );

  const parallelMode = $derived(appStore.parallelSlots === 'auto' ? 'auto' : 'edit');
  const batchMode = $derived(appStore.batchSize === 'auto' ? 'auto' : 'edit');
  const microMode = $derived(appStore.microBatch === 'auto' ? 'auto' : 'edit');
  const checkpointMode = $derived(appStore.checkpoints === 'auto' ? 'auto' : 'edit');
  const cacheRamMode = $derived(appStore.cacheRam === 'auto' ? 'auto' : 'edit');
  const speculativeMode = $derived(appStore.speculative === 'auto' ? 'auto' : 'edit');
</script>

<div class="max-w-2xl mx-auto p-4">
  <h1 class="mono text-[12px] uppercase tracking-wider mb-1" style="color: var(--muted)">
    SYSTEM SETTINGS
  </h1>
  <p class="mono text-[11px] mb-3" style="color: var(--muted)">
    Model: {appStore.selectedModel || '—'}
  </p>

  <DownloadPanel />

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
        {formatGb(Number(appStore.planResult.estimated_bytes), 1)}
        / budget {formatGb(Number(appStore.planResult.budget_bytes), 1)}
        {#if fitStatus}
          · <span style="color: var(--accent)">{fitStatus}</span>
        {/if}
        {#if appStore.estimating}
          · <span style="color: var(--muted)">updating…</span>
        {/if}
      </div>
    {/if}

    <label class="mono text-[11px]" style="color: var(--muted)" for="run-model">Model</label>
    <select
      id="run-model"
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
    <SliderField
      id="run-context"
      label=""
      value={appStore.context}
      min={MIN_CONTEXT}
      max={maxContext}
      step={CONTEXT_STEP}
      disabled={contextDisabled}
      hint={`Max ${maxContext.toLocaleString()} (model)`}
      onChange={(v) => {
        appStore.context = v;
        bumpEstimate();
      }}
    />
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

    <div class="pt-1 border-t flex flex-col gap-2" style="border-color: var(--border)">
      <div class="mono text-[11px] uppercase tracking-wider" style="color: var(--muted)">
        Hardware
      </div>
      <HardwareSettings
        lockBackend={appStore.status?.role === 'master' || appStore.status?.role === 'worker'}
        onChanged={bumpEstimate}
      />
    </div>

    {#if appStore.device === 'gpu'}
      <div class="mono text-[11px]" style="color: var(--muted)">Placement weights (VRAM / latency)</div>
      <div class="flex flex-col gap-2">
        <SliderField
          id="run-vram-weight"
          label="VRAM weight"
          value={appStore.vramWeight}
          min={0}
          max={1}
          step={0.05}
          numberWidth="4.5rem"
          disabled={!!appStore.busy}
          onChange={(v) => {
            appStore.vramWeight = v;
            bumpEstimate();
          }}
        />
        <SliderField
          id="run-latency-weight"
          label="Latency weight"
          value={appStore.latencyWeight}
          min={0}
          max={1}
          step={0.05}
          numberWidth="4.5rem"
          disabled={!!appStore.busy}
          onChange={(v) => {
            appStore.latencyWeight = v;
            bumpEstimate();
          }}
        />
      </div>
      {#if appStore.fieldErrors.placementWeights}
        <p class="mono text-[11px]" style="color: var(--accent)">{appStore.fieldErrors.placementWeights}</p>
      {/if}
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
          <SliderField
            id="run-parallel"
            label=""
            value={typeof appStore.parallelSlots === 'number' ? appStore.parallelSlots : 2}
            min={1}
            max={32}
            step={1}
            numberWidth="4.5rem"
            disabled={!!appStore.busy}
            onChange={(v) => {
              appStore.parallelSlots = v;
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
          <SliderField
            id="run-batch"
            label=""
            value={typeof appStore.batchSize === 'number' ? appStore.batchSize : 512}
            min={1}
            max={4096}
            step={1}
            numberWidth="5rem"
            disabled={!!appStore.busy}
            onChange={(v) => {
              appStore.batchSize = v;
            }}
          />
        </AutoEditField>

        <AutoEditField
          label="Micro-batch"
          mode={microMode}
          disabled={!!appStore.busy}
          onModeChange={(m) => appStore.setAutoInt('microBatch', m)}
        >
          <SliderField
            id="run-ubatch"
            label=""
            value={typeof appStore.microBatch === 'number' ? appStore.microBatch : 2}
            min={1}
            max={512}
            step={1}
            numberWidth="4.5rem"
            disabled={!!appStore.busy}
            onChange={(v) => {
              appStore.microBatch = v;
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

        <div class="mono text-[11px]" style="color: var(--muted)">Sampling</div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <SliderField
            id="inf-temp"
            label="Temperature"
            value={appStore.temperature}
            min={0}
            max={2}
            step={0.05}
            disabled={!!appStore.busy}
            onChange={(v) => (appStore.temperature = v)}
          />
          <SliderField
            id="inf-top-p"
            label="Top P"
            value={appStore.topP}
            min={0}
            max={1}
            step={0.01}
            disabled={!!appStore.busy}
            onChange={(v) => (appStore.topP = v)}
          />
          <SliderField
            id="inf-top-k"
            label="Top K"
            value={appStore.topK}
            min={0}
            max={200}
            step={1}
            numberWidth="4.5rem"
            disabled={!!appStore.busy}
            onChange={(v) => (appStore.topK = v)}
          />
          <SliderField
            id="inf-min-p"
            label="Min P"
            value={appStore.minP}
            min={0}
            max={1}
            step={0.01}
            disabled={!!appStore.busy}
            onChange={(v) => (appStore.minP = v)}
          />
          <SliderField
            id="inf-pres"
            label="Presence penalty"
            value={appStore.presencePenalty}
            min={-2}
            max={2}
            step={0.05}
            disabled={!!appStore.busy}
            onChange={(v) => (appStore.presencePenalty = v)}
          />
          <SliderField
            id="inf-rep"
            label="Repeat penalty"
            value={appStore.repeatPenalty}
            min={0}
            max={2}
            step={0.05}
            disabled={!!appStore.busy}
            onChange={(v) => (appStore.repeatPenalty = v)}
          />
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
          <SliderField
            id="run-ckpt"
            label=""
            value={typeof appStore.checkpoints === 'number' ? appStore.checkpoints : 0}
            min={0}
            max={64}
            step={1}
            numberWidth="4.5rem"
            disabled={!!appStore.busy}
            onChange={(v) => {
              appStore.checkpoints = v;
            }}
          />
        </AutoEditField>

        <AutoEditField
          label="Cache RAM (MiB)"
          mode={cacheRamMode}
          disabled={!!appStore.busy}
          onModeChange={(m) => appStore.setAutoInt('cacheRam', m)}
        >
          <SliderField
            id="run-cache-ram"
            label=""
            value={typeof appStore.cacheRam === 'number' ? appStore.cacheRam : 0}
            min={0}
            max={65536}
            step={256}
            numberWidth="5.5rem"
            disabled={!!appStore.busy}
            onChange={(v) => {
              appStore.cacheRam = v;
            }}
          />
        </AutoEditField>

        <SliderField
          id="run-threads"
          label="Threads"
          value={appStore.threads}
          min={1}
          max={maxThreads}
          step={1}
          numberWidth="4.5rem"
          disabled={!!appStore.busy}
          onChange={(v) => {
            appStore.threads = v;
          }}
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

    <LoadProgressPanel />

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
        class:btn-accent={settingsDirty}
        disabled={!appStore.selectedModel || !!appStore.busy}
        onclick={() => appStore.saveModelSettings()}>Save settings</button
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
                  {formatGb(Number((appStore.planResult.node_alloc_bytes as number[])?.[i] ?? 0), 1)}
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
