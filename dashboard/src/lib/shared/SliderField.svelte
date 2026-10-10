<script lang="ts">
  /**
   * Dual range + number control. Keeps the numeric input and slider in sync.
   * Prefer this for continuous budgets (VRAM %, context, sampling, weights).
   *
   * `onInput` fires while dragging (live preview); `onChange` fires on commit
   * (mouseup / number blur). If only `onChange` is provided it receives both.
   */
  type Props = {
    id?: string;
    label?: string;
    value: number;
    min: number;
    max: number;
    step?: number;
    disabled?: boolean;
    /** Compact hint under the control (e.g. "Max 131072 (model)"). */
    hint?: string;
    /** Width of the number box (CSS). */
    numberWidth?: string;
    onInput?: (value: number) => void;
    onChange?: (value: number) => void;
  };

  let {
    id,
    label = '',
    value,
    min,
    max,
    step = 1,
    disabled = false,
    hint = '',
    numberWidth = '5.5rem',
    onInput,
    onChange
  }: Props = $props();

  function clamp(raw: number): number {
    if (Number.isNaN(raw)) return min;
    const stepped = step > 0 ? Math.round(raw / step) * step : raw;
    return Math.min(max, Math.max(min, stepped));
  }

  function live(raw: number) {
    const v = clamp(raw);
    if (onInput) onInput(v);
    else onChange?.(v);
  }

  function commit(raw: number) {
    onChange?.(clamp(raw));
  }
</script>

<div class="flex flex-col gap-1">
  {#if label}
    <label class="mono text-[11px]" style="color: var(--muted)" for={id}>{label}</label>
  {/if}
  <div class="flex items-center gap-2">
    <input
      {id}
      aria-label={label || id}
      class="flex-1"
      type="range"
      {min}
      {max}
      {step}
      {value}
      {disabled}
      oninput={(e) => live(Number((e.currentTarget as HTMLInputElement).value))}
      onchange={(e) => commit(Number((e.currentTarget as HTMLInputElement).value))}
    />
    <input
      class="field mono"
      aria-label={`${label || id || 'Value'} value`}
      style:width={numberWidth}
      type="number"
      {min}
      {max}
      {step}
      {value}
      {disabled}
      onchange={(e) => commit(Number((e.currentTarget as HTMLInputElement).value))}
    />
  </div>
  {#if hint}
    <p class="mono text-[10px]" style="color: var(--muted)">{hint}</p>
  {/if}
</div>
