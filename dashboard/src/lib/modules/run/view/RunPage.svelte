<script lang="ts">
  import { createApp } from '$lib/app.svelte';
  import { routes } from '$lib/shared/navigation';
  import { tick, untrack } from 'svelte';
  import ErrorMessage from '$lib/shared/ErrorMessage.svelte';
  import DownloadPanel from '$lib/modules/models/view/DownloadPanel.svelte';
  import LoadProgressPanel from '$lib/modules/run/view/LoadProgressPanel.svelte';
  import JinjaEditor from '$lib/modules/run/view/JinjaEditor.svelte';
  import ParameterControl from '$lib/modules/run/view/ParameterControl.svelte';
  import ExpertArguments from '$lib/modules/run/view/ExpertArguments.svelte';
  import DeploymentPlan from '$lib/modules/run/view/DeploymentPlan.svelte';
  import { formatGib } from '$lib/shared/format-memory';
  import { MIN_CONTEXT } from '$lib/modules/run/entities';
  import type { DeviceAllocation } from '$lib/modules/run/entities';
  import { PARAMETER_LABELS } from '$lib/modules/run/configuration';
  import { runtimeRunReason } from '$lib/modules/run/runtime-readiness';
  import type { RunInteractor } from '../interactor.svelte';
  let { presenter = createApp().run }: { presenter?: RunInteractor } = $props();

  let deployStep = $state<1 | 2 | 3>(1);
  let showJinjaEditor = $state(false);
  let featureOpen = $state(false);
  const blocked = $derived(presenter.planRunBlockedReason());
  const runtimeBlocked = $derived(runtimeRunReason(presenter.status?.runtimes, presenter.status?.node_id ?? '', presenter.planResult));
  const nodeIds = $derived([...new Set([...(presenter.nodePriority ?? []), ...Object.keys(presenter.status?.members ?? {}), ...presenter.requiredNodes])]);
  const effective = $derived(presenter.effectiveConfig());
  const allocations = $derived((presenter.planResult?.device_allocations as DeviceAllocation[] | undefined) ?? []);

  function gpuNamesFor(node: string): string[] {
    const member = presenter.status?.members[node];
    const fromMember = (member?.devices ?? [])
      .map((device) => device.name)
      .filter((name): name is string => Boolean(name));
    if (fromMember.length) return fromMember;
    const planned = (presenter.planResult?.node_gpu_names as Record<string, unknown> | undefined)?.[node];
    return Array.isArray(planned) ? planned.filter((name): name is string => typeof name === 'string' && Boolean(name)) : [];
  }
  const fit = $derived(String(presenter.planResult?.fit_status ?? ''));
  const ready = $derived(!blocked && !runtimeBlocked && !!presenter.planResult && !presenter.estimating && !presenter.estimateError && ['fits', 'tight'].includes(fit));
  const estimateReady = $derived(presenter.status?.role === 'master' && !!presenter.selectedModel && !presenter.settingsLoading);
  const changes = $derived(Object.entries(effective?.requested ?? {}).filter(([key, value]) =>
    JSON.stringify(value) !== JSON.stringify(presenter.savedModelConfig?.[key])));
  const cliExport = $derived.by(() => { try { return presenter.configurationCommand(); } catch { return 'Fix argument syntax to export this configuration.'; } });
  const jsonExport = $derived.by(() => { try { const body = presenter.networkPlanPayload(); delete body.remember; delete body.advanced_open; return JSON.stringify(body, null, 2); } catch { return ''; } });
  $effect(() => { if (estimateReady) untrack(() => presenter.scheduleEstimate()); });

  async function nextStep(target: 1 | 2 | 3) {
    if (target === 1) { deployStep = 1; return; }
    if (!presenter.selectedModel || presenter.settingsLoading || presenter.settingsLoadError) return;
    if (!presenter.validateRunSettings(true)) {
      deployStep = 2;
      featureOpen = true;
      await tick();
      const first = Object.keys(presenter.fieldErrors)[0];
      const element = document.getElementById(`run-${first?.replaceAll('_', '-')}`);
      element?.closest('details')?.setAttribute('open', '');
      element?.focus();
      return;
    }
    if (target === 3 && (!presenter.planResult || presenter.estimating)) await presenter.refreshEstimate();
    deployStep = target;
  }

  function display(value: unknown): string {
    if (value === null || value === undefined) return 'Inherit default';
    if (Array.isArray(value)) return value.length ? JSON.stringify(value) : 'None';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  function layerCount(node: string): string {
    const assigned = allocations.filter(item => item.node_id === node);
    if (assigned.some(item => !Number.isFinite(item.layers))) return '—';
    const cpu = node === presenter.status?.node_id ? Number(presenter.planResult?.cpu_layers ?? NaN) : 0;
    if (!assigned.length && !Number.isFinite(cpu)) return '—';
    return String(assigned.reduce((sum, item) => sum + item.layers - (item.output_layer ? 1 : 0), Number.isFinite(cpu) ? cpu : 0));
  }
</script>

<div class="stack deploy-page">
  <header class="page-heading">
    <div><p class="eyebrow">Deploy</p><h1 id="page-title">Deploy a model</h1><p>Choose a model, configure it, and review the real memory plan.</p></div>
  </header>
  {#if presenter.status?.process_role === 'master' || presenter.status?.inference_up}
    <section class="panel running-model" aria-label="Running model">
      <div><span class="tag">Running</span><strong>{presenter.status?.model_name ?? 'Loading model'}</strong><p class="small muted">Configuration edits apply to the next deployment.</p></div>
      <button class="btn btn-danger" disabled={!!presenter.busy || presenter.status?.role !== 'master'} onclick={() => presenter.stopInference()}>Stop model</button>
    </section>
  {/if}
  <nav class="steps" aria-label="Deploy steps">
    {#each ['Select', 'Configure', 'Review & deploy'] as title, index}
      {#if index}<span class="step-line"></span>{/if}
      <button type="button" class="step" class:active={deployStep === index + 1} aria-current={deployStep === index + 1 ? 'step' : undefined}
        disabled={index > 0 && (!presenter.selectedModel || presenter.settingsLoading)} onclick={() => nextStep((index + 1) as 1 | 2 | 3)}>
        <span class="step-number">{index + 1}</span>{title}
      </button>
    {/each}
  </nav>
  <div class="deploy-layout">
    <div class="stack">
      <DownloadPanel />
      {#if blocked}<p class="notice small" role="status">{blocked}</p>{/if}
      {#if presenter.settingsLoadError}
        <div class="actions"><button class="btn" onclick={() => presenter.selectModel(presenter.selectedModel)}>Retry settings</button><button class="btn" onclick={() => presenter.useDefaultSettings()}>Use defaults</button></div>
      {/if}
      {#if presenter.status?.apply_error}<ErrorMessage source="apply" message={presenter.status.apply_error} />{/if}
      <section class="panel deployment-form">
        {#if deployStep === 1}
          <div class="deploy-section">
            <p class="eyebrow">01 / Model</p><h2>What would you like to run?</h2>
            <label class="field" for="run-model">Model on the master
              <select id="run-model" class="field" value={presenter.selectedModel} disabled={!!presenter.busy}
                onchange={(e) => presenter.selectModel(e.currentTarget.value)}>
                {#each presenter.models as model}<option value={model.id}>{model.id}</option>{/each}
              </select>
            </label>
            <a class="text-button" href={routes.models}>Browse models →</a>
          </div>
          <div class="deploy-section">
            <p class="eyebrow">02 / Compute</p><h2>Choose how the cluster participates</h2>
            <label class="field" for="run-network">Inference network
              <select id="run-network" class="field" bind:value={presenter.selectedNetworkId} onchange={() => presenter.scheduleEstimate()}>
                <option value="">LAN / WireGuard</option>
                {#each presenter.status?.managed_networks ?? [] as network}<option value={network.network_id}>{network.subnet} · {network.observed_state}</option>{/each}
              </select>
            </label>
            <label class="field" for="run-placement-mode">Placement
              <select id="run-placement-mode" class="field" value={presenter.nodePriority ? 'priority' : 'auto'}
                onchange={(e) => presenter.setPriorityPlanning(e.currentTarget.value === 'priority')}>
                <option value="auto">Automatic</option><option value="priority">Priority</option>
              </select>
            </label>
            <p class="small muted">Automatic placement prefers local GPUs, then remote and pooled devices. Checked nodes must host at least one model layer; unchecked nodes remain eligible.</p>
            {#if !presenter.nodePriority}
              <label class="check-row"><input type="checkbox" bind:checked={presenter.justEnoughMaster} onchange={() => presenter.scheduleEstimate()} />Just Enough Master Allocation</label>
              <p class="small muted">Fill worker GPUs first and give the master only the trailing layers they cannot hold, instead of filling the master's whole budget.</p>
            {/if}
            {#if presenter.runGpuIds !== null}
              <p class="small">Saved local GPU selection: {presenter.runGpuIds.join(', ') || 'CPU only'}.
                <button class="text-button" onclick={() => presenter.setParameter('gpu_ids', null)}>Use node selection</button></p>
            {/if}
            {#each nodeIds as node, index (node)}
              {@const member = presenter.status?.members[node]}
              {@const gpuNames = gpuNamesFor(node)}
              <div class="node-option">
                <label for={'required-' + node}><input id={'required-' + node} type="checkbox" checked={presenter.requiredNodes.includes(node)}
                  disabled={!member && !presenter.requiredNodes.includes(node)} onchange={(e) => presenter.setNodeRequired(node, e.currentTarget.checked)} />
                  Must use this node · {member?.role === 'master' || node === presenter.status?.node_id ? 'Master' : 'Worker'} · {node}{gpuNames.length ? ` · ${gpuNames.join(', ')}` : ''}{!member ? ' · unavailable' : ''}</label>
                {#if presenter.nodePriority}
                  <div class="actions"><span class="small">Priority {index + 1}</span>
                    <button class="btn" aria-label={'Move ' + node + ' up'} disabled={index === 0} onclick={() => presenter.moveNodePriority(node, -1)}>↑</button>
                    <button class="btn" aria-label={'Move ' + node + ' down'} disabled={index === nodeIds.length - 1} onclick={() => presenter.moveNodePriority(node, 1)}>↓</button>
                    {#if !member}<button class="btn" aria-label={'Remove unavailable ' + node} onclick={() => presenter.removeNodePriority(node)}>Remove</button>{/if}
                  </div>
                {/if}
                {#if presenter.planResult}
                  <p class="small muted">GPU: {formatGib(Number((presenter.planResult.node_vram_alloc_bytes as Record<string, number> | undefined)?.[node]))}
                    / {formatGib(Number((presenter.planResult.node_gpu_budget_bytes as Record<string, number> | undefined)?.[node]))} usable
                    · CPU RAM: {formatGib(Number((presenter.planResult.node_ram_alloc_bytes as Record<string, number> | undefined)?.[node]))}
                    / {formatGib(Number((presenter.planResult.node_ram_budget_bytes as Record<string, number> | undefined)?.[node]))} usable
                    · {layerCount(node)} model layers
                    {member?.unified_memory ? ' · shared RAM/GPU pool' : ''}</p>
                {/if}
              </div>
            {/each}
          </div>
        {:else if deployStep === 2}
          <div class="deploy-section">
            <p class="eyebrow">02 / Configuration</p><h2>Make the model yours</h2>
            <p class="small muted">Common settings first. All changes here are drafts for the next deployment.</p>
            <div class="field-grid">
              <div class="parameter-control">
                <label class="field" for="run-context">Total context capacity
                  <input id="run-context" class="field mono" type="number" bind:value={presenter.context} min={MIN_CONTEXT}
                    max={presenter.selectedModelMaxContext()} step="1" disabled={presenter.contextAuto} oninput={() => presenter.scheduleEstimate()} />
                </label>
                <label class="check-row"><input type="checkbox" bind:checked={presenter.contextAuto} onchange={() => presenter.scheduleEstimate()} />Plan context automatically</label>
                <p class="small muted">Total KV capacity shared by concurrent requests. Model maximum: {presenter.selectedModelMaxContext().toLocaleString()} tokens.</p>
                {#if presenter.fieldErrors.context}<ErrorMessage source="validation:context" message={presenter.fieldErrors.context} snapshot={false} />{/if}
              </div>
              <ParameterControl {presenter} name="parallel_slots" />
              <ParameterControl {presenter} name="max_output_tokens" />
              <ParameterControl {presenter} name="enable_thinking" />
            </div>
          </div>
          <details class="advanced" open={presenter.advancedOpen}>
            <summary>Capacity & performance</summary>
            <div class="advanced-body field-grid">
              {#each ['batch_size', 'micro_batch', 'threads', 'threads_batch', 'device'] as name}<ParameterControl {presenter} {name} />{/each}
            </div>
          </details>
          <details class="advanced">
            <summary>Memory & loading</summary>
            <div class="advanced-body">
              <p class="small muted">Node safety caps: local VRAM {presenter.vramPercent}%, RAM {presenter.ramPercent}%. <a class="text-button" href={routes.settings}>Node settings →</a></p>
              <div class="field-grid">{#each ['cache_type_k', 'cache_type_v', 'flash_attention', 'vram_percent', 'ram_percent', 'mmap_mlock', 'checkpoints', 'cache_ram'] as name}<ParameterControl {presenter} {name} />{/each}</div>
            </div>
          </details>
          <details class="advanced">
            <summary>Generation defaults</summary>
            <div class="advanced-body">
              <p class="small muted">Defaults for requests to the next deployed model. Clients and chat sessions can override them.</p>
              <div class="field-grid">{#each ['temperature', 'top_p', 'top_k', 'min_p', 'presence_penalty', 'repeat_penalty', 'frequency_penalty', 'seed'] as name}<ParameterControl {presenter} {name} />{/each}</div>
              <label class="field" for="run-stop">Stop sequences (JSON array)
                <textarea id="run-stop" class="field mono" value={presenter.stopSequences === null ? '' : JSON.stringify(presenter.stopSequences)} placeholder={'["END", "User:"]'}
                  oninput={(e) => { try { const value = e.currentTarget.value.trim() ? JSON.parse(e.currentTarget.value) : null; if (value !== null && (!Array.isArray(value) || value.some(item => typeof item !== 'string' || !item))) throw new Error(); presenter.stopSequences = value; presenter.stopParseError = ''; presenter.scheduleEstimate(); } catch { presenter.stopParseError = 'Enter a JSON array of nonempty literal strings.'; presenter.validateRunSettings(true); } }}></textarea>
              </label>
              <p class="small muted">Empty inherits defaults; [] clears stop sequences.</p>
            </div>
          </details>
          <details class="advanced" bind:open={featureOpen}>
            <summary>Model features & templates</summary>
            <div class="advanced-body">
              <div class="field-grid">{#each ['reasoning_effort', 'reasoning_budget', 'reasoning_format', 'moe_offload'] as name}<ParameterControl {presenter} {name} />{/each}</div>
              <label class="field" for="run-speculative">Speculative decoding
                <select id="run-speculative" class="field" bind:value={presenter.speculative} onchange={() => presenter.scheduleEstimate()}>
                  <option value="auto">Model / engine default</option><option value="off">Off</option><option value="draft">Draft model</option>
                </select>
              </label>
              {#if presenter.speculative === 'draft' || presenter.draftModel}
                <label class="field" for="run-draft">Draft model path<input id="run-draft" class="field mono" bind:value={presenter.draftModel} oninput={() => presenter.scheduleEstimate()} /></label>
                <ParameterControl {presenter} name="draft_max" />
              {/if}
              <label class="check-row"><input type="checkbox" bind:checked={presenter.vision} onchange={() => presenter.scheduleEstimate()} />Enable vision projector</label>
              {#if presenter.vision}
                <label class="field" for="run-mmproj">Vision projector
                  <input id="run-mmproj" class="field mono" list="projectors" bind:value={presenter.mmproj} oninput={() => presenter.scheduleEstimate()} placeholder="Choose an installed projector or enter its path" />
                  <datalist id="projectors">{#each presenter.projectors as path}<option value={path}></option>{/each}</datalist>
                </label>
                <p class="small muted">{presenter.projectors.length ? 'Projectors discovered beside the model.' : 'No projector discovered. Supply a compatible local file.'}</p>
              {/if}
              <label class="field" for="run-template">Chat template
                <select id="run-template" class="field" bind:value={presenter.chatTemplate} onchange={() => { if (presenter.chatTemplate !== 'custom') presenter.chatTemplateText = ''; presenter.scheduleEstimate(); }}>
                  <option value="default">{presenter.templateEmbedded ? 'Embedded model template' : 'Engine fallback'}</option>
                  {#each presenter.templatePresets as template}<option value={template.id}>{template.label}</option>{/each}
                  <option value="custom">Custom Jinja</option>
                </select>
              </label>
              {#if presenter.chatTemplate === 'custom'}<button class="btn" onclick={() => showJinjaEditor = true}>Edit Jinja template</button>{/if}
            </div>
          </details>
          <details class="advanced"><summary>Expert engine options</summary><div class="advanced-body"><ExpertArguments {presenter} /></div></details>
          <div class="deploy-section">
            <div class="actions">
              <button class="btn" disabled={!presenter.savedModelConfig || !!presenter.busy} onclick={() => presenter.revertSavedSettings()}>Revert to saved</button>
              <button class="btn" disabled={!!presenter.busy} onclick={() => presenter.useDefaultSettings()}>Restore defaults</button>
              <button class="btn" class:btn-accent={presenter.settingsDirty()} disabled={!!presenter.busy || presenter.settingsLoading || !!presenter.settingsLoadError}
                onclick={() => presenter.saveModelSettings()}>Save model defaults</button>
            </div>
            <p class="small muted">{presenter.settingsDirty() ? 'Unsaved changes' : 'Saved configuration loaded'} · saving affects future deployments.</p>
          </div>
        {:else}
          <div class="deploy-section">
            <p class="eyebrow">03 / Review</p><h2>Review & deploy</h2>
            <strong>{presenter.selectedModel}</strong>
            <p class="small muted">{presenter.status?.inference_up ? `Deploying replaces ${presenter.status.model_name}.` : 'Launch using the resolved configuration below.'}</p>
            {#if effective}
              <h3>Changes from saved configuration</h3>
              <p class="small muted">{changes.length} changed setting(s). Resolved values include automatic adjustments.</p>
              <div class="review-table"><table><thead><tr><th>Setting</th><th>Requested</th><th>Effective</th><th>Source</th></tr></thead><tbody>
                {#each Object.entries(effective.requested).filter(([key]) => key !== 'extra_args') as [key, value]}
                  <tr class:changed={changes.some(([name]) => name === key)}><td>{PARAMETER_LABELS[key] ?? key.replaceAll('_', ' ')}</td><td>{display(value)}</td>
                    <td>{display(effective.values[key])}{#if effective.adjustments[key]}<small>{effective.adjustments[key]}</small>{/if}</td><td>{effective.sources[key] ?? 'default'}</td></tr>
                {/each}
              </tbody></table></div>
            {:else}<p class="notice small">Refresh the estimate to review effective values.</p>{/if}
            {#if allocations.length}
              <h3>Device allocation</h3><div class="review-table"><table><thead><tr><th>Node / device</th><th>Layers</th><th>Estimated / usable budget</th></tr></thead><tbody>
                {#each allocations as allocation}<tr><td>{allocation.node_id} / {allocation.device ?? allocation.backend}</td><td>{allocation.layer_start}–{allocation.layer_end - 1}</td><td>{formatGib(allocation.estimated_bytes)} / {formatGib(allocation.budget_bytes)}</td></tr>{/each}
              </tbody></table></div>
            {/if}
            <details class="advanced"><summary>Export configuration</summary><div class="advanced-body">
              <div class="actions"><button class="btn" onclick={() => presenter.copyConfiguration('cli')}>Copy CLI</button><button class="btn" onclick={() => presenter.copyConfiguration('json')}>Copy JSON</button></div>
              <label class="field">CLI command<textarea class="field mono" readonly value={cliExport}></textarea></label>
              <label class="field">API JSON<textarea class="field mono" readonly value={jsonExport}></textarea></label>
            </div></details>
            <label class="check-row"><input type="checkbox" bind:checked={presenter.remember} />Save as model default after successful deployment</label>
          </div>
        {/if}
        {#if Object.keys(presenter.fieldErrors).length}<div class="notice small" role="alert">{Object.values(presenter.fieldErrors).join(' ')}</div>{/if}
        {#if runtimeBlocked}<div class="notice small">{runtimeBlocked} <a href={routes.settingsLlama}>Open llama.cpp settings →</a></div>{/if}
        <div class="form-foot">
          <button class="btn" disabled={deployStep === 1} onclick={() => nextStep(deployStep === 3 ? 2 : 1)}>Back</button>
          {#if deployStep < 3}<button class="btn btn-accent" disabled={!presenter.selectedModel || presenter.settingsLoading || !!presenter.settingsLoadError}
            onclick={() => nextStep(deployStep === 1 ? 2 : 3)}>{deployStep === 1 ? 'Configure' : 'Review deployment'}</button>
          {:else}<button class="btn btn-accent" disabled={!ready || !!presenter.busy} onclick={() => presenter.run()}>{presenter.status?.inference_up ? 'Replace running model' : 'Deploy model'}</button>{/if}
        </div>
      </section>
      <LoadProgressPanel {presenter} />
    </div>
    <DeploymentPlan {presenter} />
  </div>
</div>
{#if showJinjaEditor}<JinjaEditor initialText={presenter.chatTemplateText || presenter.modelTemplateText || ''}
  onApply={(text) => { presenter.chatTemplate = 'custom'; presenter.chatTemplateText = text; presenter.scheduleEstimate(); }} onClose={() => showJinjaEditor = false} />{/if}

<style>
  .deployment-form { overflow: hidden; }
  h2 { font-size: 20px; font-weight: 600; } h3 { font-size: 15px; font-weight: 600; }
  .deploy-section { padding: 24px; display: flex; flex-direction: column; gap: 18px; }
  .advanced { border-top: 1px solid var(--border); border-bottom: 0; border-radius: 0; margin: 0; }
  .advanced summary { padding: 20px 24px; cursor: pointer; font-size: 14px; }
  .advanced-body { padding: 0 24px 24px; display: flex; flex-direction: column; gap: 20px; }
  .advanced-body.field-grid { display: grid; }
  .field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; }
  .field { display: flex; flex-direction: column; gap: 8px; font-size: 13px; }
  .node-option { padding: 16px; border: 1px solid var(--border); border-radius: 8px; display: flex; flex-direction: column; gap: 8px; }
  .node-option label { display: flex; gap: 10px; align-items: center; font-size: 13px; }
  .actions, .running-model { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .running-model { padding: 18px 24px; justify-content: space-between; }.running-model strong { margin-left: 12px; }
  .notice { padding: 14px; border: 1px solid var(--border); border-radius: 8px; }
  .review-table { overflow-x: auto; } table { width: 100%; font-size: 12px; text-align: left; border-collapse: collapse; }
  td, th { padding: 12px 8px; border-bottom: 1px solid var(--border); max-width: 240px; overflow-wrap: anywhere; }
  td small { display: block; color: var(--muted); margin-top: 4px; } tr.changed td:first-child { color: var(--accent); }
  textarea { min-height: 90px; resize: vertical; }.form-foot { padding: 20px 24px; }
  @media (max-width: 700px) { .field-grid { grid-template-columns: 1fr; }.deploy-section { padding: 18px; }.advanced summary { padding: 18px; }.advanced-body { padding: 0 18px 18px; } }
</style>
