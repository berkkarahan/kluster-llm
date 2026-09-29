<script lang="ts">
  import type { DeviceAllocation } from '$lib/types';
  import AutoEditField from '$lib/components/AutoEditField.svelte';
  import DownloadPanel from '$lib/components/DownloadPanel.svelte';
  import HardwareSettings from '$lib/components/HardwareSettings.svelte';
  import LoadProgressPanel from '$lib/components/LoadProgressPanel.svelte';
  import JinjaEditor from '$lib/components/JinjaEditor.svelte';
  import SliderField from '$lib/components/SliderField.svelte';
  import { appStore } from '$lib/store.svelte';
  import { formatGib } from '$lib/format-memory';
  import { CONTEXT_STEP, KV_CACHE_TYPES, MIN_CONTEXT } from '$lib/types';

  function bumpEstimate() {
    appStore.scheduleEstimate();
  }

  const blocked = $derived(appStore.planRunBlockedReason());
  const loadPhase = $derived(appStore.loadProgress?.phase);
  const inferenceStoppable = $derived(
    appStore.status?.role === 'master' && (
      appStore.status?.process_role === 'master'
      || !!appStore.status?.inference_up
      || !!appStore.status?.model_name
      || loadPhase === 'starting'
      || loadPhase === 'loading'
    )
  );
  const fitStatus = $derived(
    appStore.planResult?.fit_status ? String(appStore.planResult.fit_status) : null
  );
  const maxContext = $derived(appStore.selectedModelMaxContext());
  const contextDisabled = $derived(appStore.contextAuto || !!appStore.busy);
  const estimateReady = $derived(
    appStore.status?.role === 'master' &&
    Object.keys(appStore.status?.members ?? {}).length > 0 &&
    appStore.models.length > 0 && !!appStore.selectedModel
  );
  $effect(() => {
    if (estimateReady) appStore.scheduleEstimate();
  });
  const settingsDirty = $derived(appStore.settingsDirty());
  let showJinjaEditor = $state(false);
  const nodeIds = $derived(Object.keys(appStore.status?.members ?? {}));
  const workloadBytes = $derived(appStore.distributableBytes);

  const parallelMode = $derived(appStore.parallelSlots === 'auto' ? 'auto' : 'edit');
  const batchMode = $derived(appStore.batchSize === 'auto' ? 'auto' : 'edit');
  const microMode = $derived(appStore.microBatch === 'auto' ? 'auto' : 'edit');
  const checkpointMode = $derived(appStore.checkpoints === 'auto' ? 'auto' : 'edit');
  const cacheRamMode = $derived(appStore.cacheRam === 'auto' ? 'auto' : 'edit');
  const speculativeMode = $derived(appStore.speculative === 'auto' ? 'auto' : 'edit');
</script>

<div class="max-w-2xl mx-auto p-4">
  <h1 class="mono text-[12px] uppercase tracking-wider mb-1" style="color: var(--muted)">
    PLAN &amp; RUN
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

    <section class="panel p-3 flex flex-col gap-3" aria-label="Planning">
      <div class="flex items-center justify-between gap-2">
        <h2 class="mono text-[11px] uppercase tracking-wider">Planning</h2>
        <label class="mono text-[11px]" for="run-placement-mode">Placement</label>
        <select id="run-placement-mode" class="field mono" style="width: auto" disabled={!!appStore.busy || appStore.device === 'cpu'}
          value={appStore.nodeShares ? 'manual' : 'auto'}
          onchange={(e) => {
            if (e.currentTarget.value === 'auto') { appStore.nodeShares = null; bumpEstimate(); }
            else appStore.setNodeShare(nodeIds[0], 1 / Math.max(1, nodeIds.length));
          }}>
          <option value="auto">Automatic</option><option value="manual">Manual</option>
        </select>
      </div>
      {#if appStore.estimateError}<p class="mono text-[11px]" role="alert" style="color: var(--danger)">{appStore.estimateError}</p>{/if}
      {#if !blocked && appStore.planResult?.estimated_bytes != null}
        <div class="mono text-[11px] p-2" style="background: var(--bg); border: 1px solid var(--border)">
          Estimated total memory: {formatGib(appStore.planResult.estimated_bytes as number)}
          / usable total memory budget {formatGib(appStore.planResult.budget_bytes as number)}
          {#if appStore.device === 'gpu' && appStore.planResult.vram_alloc_bytes != null}
            <div>Estimated GPU memory: {formatGib(appStore.planResult.vram_alloc_bytes as number)}</div>
          {/if}
          {#if appStore.planResult.cpu_alloc_bytes != null}<div>Estimated master CPU RAM: {formatGib(appStore.planResult.cpu_alloc_bytes as number)}</div>{/if}
          {#if fitStatus}<span style="color: var(--accent)">{fitStatus}</span>{/if}
          {#if appStore.estimating}<span style="color: var(--muted)"> · updating…</span>{/if}
        </div>
      {/if}
      {#if appStore.nodeShares}
        <p class="mono text-[11px]" style="color: var(--muted)">Target GPU workload: {workloadBytes ? formatGib(workloadBytes) : 'estimating…'} · 100% across nodes; full layers may round targets.</p>
        {#each nodeIds as node}
          {@const share = appStore.nodeShares[node] ?? 0}
          {@const allocations = (appStore.planResult?.device_allocations as DeviceAllocation[] | undefined) ?? []}
          <div class="flex flex-col gap-1 mono text-[11px]">
            <label for={'share-' + node}>{node === appStore.status?.node_id ? 'Master' : 'Worker'} · {node} · target {workloadBytes ? formatGib(workloadBytes * share) : '—'} ({(share * 100).toFixed(1)}%)</label>
            <input id={'share-' + node} type="range" min="0" max={workloadBytes ? workloadBytes / 2**30 : 1}
              step="any" value={workloadBytes ? workloadBytes * share / 2**30 : share}
              aria-valuetext={`${workloadBytes ? formatGib(workloadBytes * share) : 'estimating'} of ${workloadBytes ? formatGib(workloadBytes) : 'model workload'} (${(share * 100).toFixed(1)}%)`}
              disabled={!!blocked || !!appStore.busy}
              oninput={(e) => appStore.setNodeShare(node, Number(e.currentTarget.value) / (workloadBytes ? workloadBytes / 2**30 : 1))} />
            <span style="color: var(--muted)">
              Actual workload: {formatGib(Number((appStore.planResult?.node_workload_bytes as Record<string, number> | undefined)?.[node] ?? 0))}
              · GPU: {formatGib(allocations.filter((a) => a.node_id === node).reduce((n, a) => n + a.estimated_bytes, 0))}
              / {formatGib(Number((appStore.planResult?.node_gpu_budget_bytes as Record<string, number> | undefined)?.[node] ?? 0))} usable
              · {allocations.filter((a) => a.node_id === node).reduce((n, a) => n + a.layers, 0)} layers
            </span>
          </div>
        {/each}
      {/if}
      {#if fitStatus === 'overflow' && appStore.planResult?.hint}
        <p class="mono text-[11px]" role="alert" style="color: var(--danger)">{String(appStore.planResult.hint)}</p>
      {/if}
    </section>

    <details bind:open={appStore.advancedOpen} class="panel p-3">
      <summary class="mono text-[11px] cursor-pointer uppercase tracking-wider">Advanced settings</summary>
      <div class="flex flex-col gap-3 mt-2 pl-1">
        <div class="mono text-[11px] uppercase tracking-wider" style="color: var(--muted)">Performance</div>
        <AutoEditField
          label="Parallel requests"
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
              if (typeof appStore.microBatch === 'number' && appStore.microBatch > v) appStore.microBatch = v;
              bumpEstimate();
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
            max={typeof appStore.batchSize === 'number' ? appStore.batchSize : 4096}
            step={1}
            numberWidth="4.5rem"
            disabled={!!appStore.busy}
            onChange={(v) => { appStore.microBatch = v; bumpEstimate(); }}
          />
        </AutoEditField>

        <label class="mono text-[11px]" style="color: var(--muted)" for="run-flash">Flash Attention</label>
        <select id="run-flash" class="field mono" bind:value={appStore.flashAttention} disabled={!!appStore.busy} onchange={bumpEstimate}>
          <option value="auto">Auto</option><option value="on">On</option><option value="off">Off</option>
        </select>

        <div class="border-t pt-2 flex flex-col gap-3" style="border-color: var(--border)">
          <div class="mono text-[11px] uppercase tracking-wider" style="color: var(--muted)">Hardware &amp; memory</div>
          <HardwareSettings lockBackend={appStore.status?.role === 'master' || appStore.status?.role === 'worker'} onChanged={bumpEstimate} />
          <label class="mono text-[11px]" style="color: var(--muted)" for="run-cache-k">KV cache dtype (K / V)</label>
          <div class="flex gap-2">
            <select id="run-cache-k" class="field mono flex-1" bind:value={appStore.cacheTypeK} disabled={!!appStore.busy} onchange={bumpEstimate}>
              {#each KV_CACHE_TYPES as t}<option value={t}>{t}</option>{/each}
            </select>
            <select aria-label="KV cache dtype V" class="field mono flex-1" bind:value={appStore.cacheTypeV} disabled={!!appStore.busy} onchange={bumpEstimate}>
              {#each KV_CACHE_TYPES as t}<option value={t}>{t}</option>{/each}
            </select>
          </div>
        </div>

        <div class="mono text-[11px] uppercase tracking-wider border-t pt-2" style="color: var(--muted); border-color: var(--border)">Model behavior</div>
        <AutoEditField
          label="Speculative / draft model"
          mode={speculativeMode}
          disabled={!!appStore.busy}
          onModeChange={(m) => appStore.setSpeculativeMode(m)}
        >
          <input class="field mono w-full" placeholder="Draft model path" bind:value={appStore.draftModel}
            disabled={!!appStore.busy} onchange={bumpEstimate} />
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
          <label class="mono text-[11px]" style="color: var(--muted)" for="run-mmproj">mmproj path</label>
          <input
            id="run-mmproj"
            class="field mono"
            placeholder="Path to mmproj GGUF"
            bind:value={appStore.mmproj}
            disabled={!!appStore.busy}
            onchange={bumpEstimate}
          />
        {/if}

        <div class="mono text-[11px] uppercase tracking-wider border-t pt-2" style="color: var(--muted); border-color: var(--border)">Sampling</div>
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

        <label class="mono text-[11px]" style="color: var(--muted)" for="run-template">Chat template</label>
        <select id="run-template" class="field mono" value={appStore.chatTemplateText && appStore.chatTemplate === 'default' ? 'custom' : appStore.chatTemplate}
          disabled={!!appStore.busy} onchange={(e) => {
            const value = e.currentTarget.value;
            if (value === 'custom') showJinjaEditor = true;
            else { appStore.chatTemplate = value; appStore.chatTemplateText = ''; }
          }}>
          <option value="default">Auto — {appStore.templateEmbedded ? 'embedded GGUF template' : 'engine fallback'}</option>
          <option value="chatml">ChatML</option>
          {#each appStore.templatePresets as preset}<option value={preset.id}>{preset.label}</option>{/each}
          <option value="custom">Custom Jinja</option>
        </select>
        {#if appStore.chatTemplate === 'custom' || appStore.chatTemplateText}
          <div class="flex gap-2">
            <button class="btn" type="button" onclick={() => showJinjaEditor = true}>Edit Jinja</button>
            <button class="btn" type="button" onclick={() => { appStore.chatTemplate = 'default'; appStore.chatTemplateText = ''; }}>Reset to model</button>
          </div>
        {:else if appStore.modelTemplateText}
          <button class="btn self-start" type="button" onclick={() => showJinjaEditor = true}>Copy model template to editor</button>
        {/if}

        <div class="mono text-[11px] uppercase tracking-wider border-t pt-2" style="color: var(--muted); border-color: var(--border)">Runtime</div>

        <label class="mono text-[11px]" style="color: var(--muted)" for="run-mmap">mmap / mlock</label>
        <select id="run-mmap" class="field mono" bind:value={appStore.mmapMlock} disabled={!!appStore.busy}>
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
              appStore.checkpoints = v; bumpEstimate();
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
              appStore.cacheRam = v; bumpEstimate();
            }}
          />
        </AutoEditField>

        <SliderField
          id="run-threads"
          label="Threads"
          value={appStore.threads}
          min={1}
          max={appStore.threadsMax}
          step={1}
          numberWidth="4.5rem"
          disabled={!!appStore.busy}
          onChange={(v) => {
            appStore.threads = v; bumpEstimate();
          }}
        />
        {#if appStore.fieldErrors.threads}
          <p class="mono text-[11px]" style="color: var(--accent)">{appStore.fieldErrors.threads}</p>
        {/if}

        <label class="mono text-[11px]" style="color: var(--muted)" for="run-extra">Extra args</label>
        <input
          id="run-extra"
          class="field mono"
          placeholder="Space-separated llama-server args"
          bind:value={appStore.extraArgsText}
          disabled={!!appStore.busy}
          onchange={bumpEstimate}
        />
        <label class="mono text-[11px] flex items-center gap-2" style="color: var(--muted)">
          <input type="checkbox" bind:checked={appStore.remember} /> Remember settings for this model
        </label>
      </div>
    </details>

    <p class="mono text-[11px]" style="color: var(--muted)">
      Layer split distributes contiguous model layers across selected devices.
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
        disabled={!!blocked || !appStore.selectedModel || !!appStore.busy || !!appStore.estimateError || fitStatus === 'overflow'}
        onclick={() => appStore.run()}>Run</button
      >
      <button
        class="btn btn-danger"
        type="button"
        disabled={!!appStore.busy || !inferenceStoppable}
        onclick={() => appStore.stopInference()}>Stop</button
      >
    </div>

    {#if appStore.status?.inference_up}
      <p class="mono text-[12px]" style="color: var(--accent)">
        Inference up — proxy :{appStore.status.control_port ?? 18765}/v1 · llama :{appStore.status.infer_port ?? 8080}
      </p>
    {/if}

    {#if !blocked && appStore.planResult}
      <section class="panel p-3 flex flex-col gap-2 overflow-x-auto" aria-label="Planning results">
      <h2 class="mono text-[11px] uppercase tracking-wider">Planning results</h2>
      {#if appStore.planResult.hint}
        <p class="mono text-[11px]" style="color: var(--muted)">{String(appStore.planResult.hint)}</p>
      {/if}
      {#if appStore.planResult.device_requested}
        <p class="mono text-[12px]" style="color: var(--muted)">
          Placement: {String(appStore.planResult.device_requested)} → {String(appStore.planResult.device_effective)} · {String(appStore.planResult.split_mode ?? 'layer')} split
        </p>
      {/if}
      {#if appStore.planResult.estimate_confidence}
        <p class="mono text-[11px]" style="color: var(--muted)">Estimate: {String(appStore.planResult.estimate_confidence)}</p>
        <p class="mono text-[11px]">Master CPU: {String(appStore.planResult.cpu_layers ?? 0)} layers, estimated RAM {formatGib(appStore.planResult.cpu_alloc_bytes as number)}</p>
        {#if appStore.planResult.resolved_launch_settings}
          {@const resolved = appStore.planResult.resolved_launch_settings as Record<string, unknown>}
          <p class="mono text-[11px]">Resolved: {String(resolved.parallel_slots)} slot(s), batch {String(resolved.batch_size)}, micro-batch {String(resolved.micro_batch)}, Flash Attention {String(resolved.flash_attention)}</p>
        {/if}
      {/if}
      {#if Array.isArray(appStore.planResult.device_allocations) && (appStore.planResult.split_mode ?? 'layer') === 'layer'}
        <table class="w-full text-left mono text-[11px] mt-2">
          <thead><tr><th>device</th><th>layers (zero-based)</th><th>estimated / usable budget</th></tr></thead>
          <tbody>
            {#each appStore.planResult.device_allocations as raw}
              {@const allocation = raw as DeviceAllocation}
              <tr><td>{allocation.node_id}: {allocation.device} ({allocation.backend})</td>
                <td>{allocation.layer_start}–{allocation.layer_end - 1}{allocation.output_layer ? ' · includes output' : ''}</td>
                <td>{formatGib(allocation.estimated_bytes)} / {formatGib(allocation.budget_bytes)}</td></tr>
            {/each}
          </tbody>
        </table>
      {/if}
      {#if Array.isArray(appStore.planResult.node_ids)}
        <table class="w-full text-left border-collapse mono text-[11px] mt-2">
          <thead>
            <tr style="color: var(--muted)">
              <th class="font-medium py-1 border-b" style="border-color: var(--border)">node</th>
              <th class="font-medium py-1 border-b" style="border-color: var(--border)">share</th>
              <th class="font-medium py-1 border-b" style="border-color: var(--border)">alloc</th>
              {#if !appStore.nodeShares}<th class="font-medium py-1 border-b" style="border-color: var(--border)">score</th>{/if}
              <th class="font-medium py-1 border-b" style="border-color: var(--border)">control RTT</th>
            </tr>
          </thead>
          <tbody>
            {#each appStore.planResult.node_ids as nid, i}
              <tr>
                <td class="py-1 border-b" style="border-color: var(--border)">{String(nid)}</td>
                <td class="py-1 border-b" style="border-color: var(--border)">
                  {(100 * Number((appStore.planResult.node_alloc_bytes as number[])?.[i] ?? 0) / Math.max(1, Number(appStore.planResult.estimated_bytes ?? 1))).toFixed(1)}%
                </td>
                <td class="py-1 border-b" style="border-color: var(--border)">
                  {formatGib((appStore.planResult.node_alloc_bytes as number[])?.[i])}
                </td>
                {#if !appStore.nodeShares}<td class="py-1 border-b" style="border-color: var(--border)">
                  {Number((appStore.planResult.node_scores as number[])?.[i] ?? 0).toFixed(3)}
                </td>{/if}
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
      </section>
    {/if}
  </div>
</div>
{#if showJinjaEditor}
  <JinjaEditor
    initialText={appStore.chatTemplateText || appStore.modelTemplateText || ''}
    onApply={(text) => { appStore.chatTemplate = 'custom'; appStore.chatTemplateText = text; bumpEstimate(); }}
    onClose={() => { showJinjaEditor = false; }}
  />
{/if}
