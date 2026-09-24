<script lang="ts">
  /**
   * System / hardware settings: device (CPU|GPU), accel backend (CUDA/Vulkan/…),
   * multi-GPU selection, and VRAM/RAM budget. Persists via PUT /v1/config —
   * no hard reload required (mirrors Unsloth's hardware info + engine toggles).
   */
  import SliderField from '$lib/components/SliderField.svelte';
  import { appStore } from '$lib/store.svelte';
  import { formatGb } from '$lib/format-transfer';

  type Props = {
    /** When true, backend switch is locked (cluster already live). */
    lockBackend?: boolean;
    /** Fired after a persisted change so parents can re-estimate VRAM. */
    onChanged?: () => void;
  };

  let { lockBackend = false, onChanged }: Props = $props();

  function afterChange() {
    onChanged?.();
  }
</script>

<div class="flex flex-col gap-2">
  <label class="mono text-[11px]" style="color: var(--muted)" for="hw-device">Device</label>
  <select
    id="hw-device"
    class="field mono"
    value={appStore.device}
    disabled={!!appStore.busy}
    onchange={(e) => {
      const v = (e.currentTarget as HTMLSelectElement).value;
      if (v === 'cpu' || v === 'gpu') void appStore.setDevice(v).then(afterChange);
    }}
  >
    <option value="gpu">GPU</option>
    <option value="cpu">CPU</option>
  </select>

  {#if appStore.device === 'gpu'}
    {#if appStore.allowedBackends.length > 0}
      <label class="mono text-[11px]" style="color: var(--muted)" for="hw-backend">
        Engine backend
      </label>
      <select
        id="hw-backend"
        class="field mono"
        value={appStore.accelBackend ?? ''}
        disabled={lockBackend || !!appStore.busy}
        onchange={(e) => {
          const v = (e.currentTarget as HTMLSelectElement).value;
          if (v === '') {
            void appStore.setAccelBackend(null).then(afterChange);
          } else if (v === 'metal' || v === 'cuda' || v === 'rocm' || v === 'vulkan') {
            void appStore.setAccelBackend(v).then(afterChange);
          }
        }}
      >
        <option value="">auto (all devices)</option>
        {#each appStore.allowedBackends as b}
          <option value={b}>{appStore.accelBackendLabel(b)}</option>
        {/each}
      </select>
      <p class="mono text-[10px]" style="color: var(--muted)">
        Probed {appStore.probedBackend}. RPC clusters may mix Metal, CUDA, ROCm, and Vulkan.
        {#if lockBackend} Switch before Start.{/if}
      </p>
    {/if}

    {#if appStore.gpus.length > 0}
      <div class="mono text-[11px]" style="color: var(--muted)">
        GPUs ({appStore.gpuBackend}) — heterogeneous shards OK
      </div>
      <div class="flex flex-col gap-1">
        {#each appStore.gpus as g}
          <label class="mono text-[11px] flex items-start gap-2 cursor-pointer" style="color: var(--fg)">
            <input
              type="checkbox"
              class="mt-0.5"
              checked={appStore.selectedGpuIds.includes(g.index)}
              disabled={appStore.gpus.length === 1 || !!appStore.busy}
              onchange={() => {
                appStore.toggleGpu(g.index);
                void appStore.persistGpuSelection().then(afterChange);
              }}
            />
            <span>
              [{g.index}] {g.name}
              <span style="color: var(--muted)"> {formatGb(g.vram_bytes ?? null, 1)}</span>
            </span>
          </label>
        {/each}
      </div>
    {:else}
      <p class="mono text-[11px]" style="color: var(--muted)">
        No local GPUs detected — inference will fall back to CPU layers.
      </p>
      {#if appStore.gpuProbeError}
        <p class="mono text-[11px]" style="color: var(--danger, #c44)">
          {appStore.gpuProbeError}
        </p>
        <p class="mono text-[10px]" style="color: var(--muted)">
          Run: {appStore.gpuInstallHint || 'python scripts/install.py && python scripts/install.py --check'}
        </p>
      {/if}
    {/if}

    <SliderField
      id="hw-vram"
      label="VRAM budget (%)"
      value={appStore.vramPercent}
      min={10}
      max={100}
      step={1}
      numberWidth="4.5rem"
      disabled={!!appStore.busy}
      onInput={(v) => {
        appStore.vramPercent = v;
      }}
      onChange={(v) => {
        void appStore.setVramPercent(v).then(afterChange);
      }}
    />
  {:else}
    <SliderField
      id="hw-ram"
      label="RAM budget (%)"
      value={appStore.ramPercent}
      min={10}
      max={100}
      step={1}
      numberWidth="4.5rem"
      disabled={!!appStore.busy}
      onInput={(v) => {
        appStore.ramPercent = v;
      }}
      onChange={(v) => {
        void appStore.setRamPercent(v).then(afterChange);
      }}
    />
  {/if}
</div>
