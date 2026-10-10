<script lang="ts">
  import { createApp } from '$lib/app.svelte';
  import ErrorMessage from '$lib/shared/ErrorMessage.svelte';
  /**
   * System / hardware settings: device (CPU|GPU), accel backend (CUDA/Vulkan/…),
   * multi-GPU selection, and VRAM/RAM budget. Persists via PUT /v2/node/config —
   * no hard reload required (mirrors Unsloth's hardware info + engine toggles).
   */
  import SliderField from '$lib/shared/SliderField.svelte';
  import { formatGib, selectedCapacity } from '$lib/shared/format-memory';
  import type { NodeSettingsInteractor } from '../interactor.svelte';
  let { presenter = createApp().settings, lockBackend = false, onChanged }: {
    presenter?: NodeSettingsInteractor;
    lockBackend?: boolean;
    onChanged?: () => void;
  } = $props();
  const sharedMemory = $derived(
    presenter.gpuUnifiedMemory ?? (
      presenter.status?.capacity?.unified_memory === true ||
      presenter.status?.members?.[presenter.status?.node_id ?? '']?.unified_memory === true
    )
  );
  // "Selected VRAM capacity" counts discrete accelerators; an integrated
  // adapter shares system RAM, so it is listed but not counted as VRAM when a
  // discrete card is selected. An integrated-only machine falls back to it.
  const selectedVram = $derived.by(() => {
    const selected = presenter.gpus.filter((g) => presenter.selectedGpuIds.includes(g.index));
    const discrete = selected.filter((g) => !g.integrated);
    const counted = discrete.length > 0 ? discrete : selected;
    return formatGib(selectedCapacity(counted, counted.map((g) => g.index)));
  });

  function afterChange() {
    onChanged?.();
  }
</script>

<div class="flex flex-col gap-2">
  <label class="mono text-[11px]" style="color: var(--muted)" for="hw-device">Device</label>
  <select
    id="hw-device"
    class="field mono"
    value={presenter.device}
    disabled={!!presenter.busy}
    onchange={(e) => {
      const v = (e.currentTarget as HTMLSelectElement).value;
      if (v === 'cpu' || v === 'gpu') void presenter.setDevice(v).then(afterChange);
    }}
  >
    <option value="gpu">GPU</option>
    <option value="cpu">CPU</option>
  </select>

  {#if presenter.device === 'gpu'}
    {#if presenter.allowedBackends.length > 0}
      <label class="mono text-[11px]" style="color: var(--muted)" for="hw-backend">
        Engine backend
      </label>
      <select
        id="hw-backend"
        class="field mono"
        value={presenter.accelBackend ?? ''}
        disabled={lockBackend || !!presenter.busy}
        onchange={(e) => {
          const v = (e.currentTarget as HTMLSelectElement).value;
          if (v === '') {
            void presenter.setAccelBackend(null).then(afterChange);
          } else if (v === 'metal' || v === 'cuda' || v === 'rocm' || v === 'vulkan') {
            void presenter.setAccelBackend(v).then(afterChange);
          }
        }}
      >
        <option value="">auto (all devices)</option>
        {#each presenter.allowedBackends as b}
          <option value={b}>{presenter.accelBackendLabel(b)}</option>
        {/each}
      </select>
      <p class="mono text-[10px]" style="color: var(--muted)">
        Probed {presenter.probedBackend}. RPC clusters may mix Metal, CUDA, ROCm, and Vulkan.
        {#if lockBackend} Switch before Start.{/if}
      </p>
    {/if}

    {#if presenter.gpuBackendWarning}
      <p class="mono text-[11px]" style="color: var(--warn, #b80)">
        {presenter.gpuBackendWarning}
      </p>
    {/if}
    {#if presenter.installedBackends.length > 0}
      <p class="mono text-[10px]" style="color: var(--muted)">
        Installed on this node: {presenter.installedBackends.join(', ')}
      </p>
    {/if}

    {#if presenter.gpus.length > 0}
      <div class="mono text-[11px]" style="color: var(--muted)">
        GPUs ({presenter.gpuBackend}) — heterogeneous shards OK
      </div>
      <div class="flex flex-col gap-1">
        {#each presenter.gpus as g}
          <label class="mono text-[11px] flex items-start gap-2 cursor-pointer" style="color: var(--fg)">
            <input
              type="checkbox"
              class="mt-0.5"
              checked={presenter.selectedGpuIds.includes(g.index)}
              disabled={presenter.gpus.length === 1 || !!presenter.busy}
              onchange={() => {
                presenter.toggleGpu(g.index);
                void presenter.persistGpuSelection().then(afterChange);
              }}
            />
            <span>
              [{g.index}] {g.name}
              {#if g.integrated}
                <span style="color: var(--muted)">
                  · integrated · shares system RAM{presenter.selectedGpuIds.includes(g.index) ? '' : ' · not configured'}
                </span>
              {:else if sharedMemory}
                <span style="color: var(--muted)">
                  · shared capacity {formatGib(selectedCapacity([g], [g.index]))}
                </span>
              {:else}
                <span style="color: var(--muted)">
                  · VRAM capacity {formatGib(selectedCapacity([g], [g.index]))}
                </span>
              {/if}
            </span>
          </label>
        {/each}
      </div>
      {#if sharedMemory}
        <p class="mono text-[10px]" style="color: var(--muted)">
          Shared GPU capacity uses system RAM; it is not additional VRAM.
        </p>
      {:else}
        <p class="mono text-[11px]" style="color: var(--muted)">
          Selected VRAM capacity: {selectedVram}
        </p>
      {/if}
    {:else if !presenter.gpuBackendWarning}
      <p class="mono text-[11px]" style="color: var(--muted)">
        No local GPUs detected — inference will fall back to CPU layers.
      </p>
      {#if presenter.gpuProbeError}
        <ErrorMessage source="gpu-probe" message={presenter.gpuProbeError} />
        <p class="mono text-[10px]" style="color: var(--muted)">
          Run: {presenter.gpuInstallHint || 'python scripts/install.py && python scripts/install.py --check'}
        </p>
      {/if}
    {:else}
      <p class="mono text-[10px]" style="color: var(--muted)">
        Run: {presenter.gpuInstallHint || 'python scripts/install.py && python scripts/install.py --check'}
      </p>
    {/if}

    <SliderField
      id="hw-vram"
      label="VRAM budget (%)"
      value={presenter.vramPercent}
      min={10}
      max={100}
      step={1}
      numberWidth="4.5rem"
      disabled={!!presenter.busy}
      onInput={(v) => {
        presenter.vramPercent = v;
      }}
      onChange={(v) => {
        void presenter.setVramPercent(v).then(afterChange);
      }}
    />
  {:else}
    <SliderField
      id="hw-ram"
      label="RAM budget (%)"
      value={presenter.ramPercent}
      min={10}
      max={100}
      step={1}
      numberWidth="4.5rem"
      disabled={!!presenter.busy}
      onInput={(v) => {
        presenter.ramPercent = v;
      }}
      onChange={(v) => {
        void presenter.setRamPercent(v).then(afterChange);
      }}
    />
  {/if}
</div>
