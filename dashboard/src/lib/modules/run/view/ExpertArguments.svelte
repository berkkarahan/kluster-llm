<script lang="ts">
  import { createApp } from '#lib/app.svelte';
  import { formatArguments, parseArgumentText } from '#lib/modules/run/configuration';
  import type { RunInteractor } from '../interactor.svelte';
  let { presenter = createApp().run }: { presenter?: RunInteractor } = $props();
  let search = $state('');
  let selected = $state('');
  let values = $state<string[]>([]);
  let importOpen = $state(false);
  const matches = $derived(presenter.expertFlags.filter(item => `${item.flag} ${item.description}`.toLowerCase().includes(search.toLowerCase())));
  const spec = $derived(presenter.expertFlags.find(item => item.flag === selected));
  const tokens = $derived.by(() => { try { return parseArgumentText(presenter.extraArgsText); } catch { return []; } });
  const rows = $derived.by(() => {
    const result: { flag: string; values: string[]; start: number; count: number }[] = [];
    for (let index = 0; index < tokens.length;) {
      const flag = tokens[index];
      const definition = presenter.expertFlags.find(item => item.flag === flag);
      let count = definition?.arguments.length;
      if (count === undefined) {
        count = 0;
        while (index + count + 1 < tokens.length && !/^--?[a-z]/i.test(tokens[index + count + 1])) count++;
      }
      result.push({ flag, values: tokens.slice(index + 1, index + 1 + count), start: index, count });
      index += 1 + count;
    }
    return result;
  });

  function add() {
    if (!spec || values.length < spec.arguments.length || values.some(value => value === '')) return;
    presenter.extraArgsText = formatArguments([...tokens, spec.flag, ...values.slice(0, spec.arguments.length)]);
    selected = ''; values = []; presenter.scheduleEstimate();
  }
</script>

<div class="stack-sm">
  <p class="small muted">Options from the installed llama-server. Model, network, logging and placement are managed by Kluster. Additional options may make the memory estimate incomplete.</p>
  <label class="field">Find an engine option<input class="field" type="search" placeholder="Search flags or descriptions" bind:value={search} /></label>
  <label class="field">Engine option
    <select class="field mono" value={selected} onchange={(e) => { selected = e.currentTarget.value; values = Array(presenter.expertFlags.find(item => item.flag === selected)?.arguments.length ?? 0).fill(''); }}>
      <option value="">Choose an option</option>
      {#each matches as item}<option value={item.flag}>{item.flag}</option>{/each}
    </select>
  </label>
  {#if spec}
    <p class="small muted">{spec.description}</p>
    {#each spec.arguments as argument, index}
      <label class="field">{argument}<input class="field mono" aria-label={`${spec.flag} ${argument}`} value={values[index] ?? ''}
        oninput={(e) => values[index] = e.currentTarget.value} /></label>
    {/each}
    <button class="btn" type="button" onclick={add}>Add argument</button>
  {/if}
  {#each rows as row}
    <div class="argument-row">
      <code>{row.flag}</code>
      {#each row.values as value, index}
        <input class="field mono" aria-label={`${row.flag} value ${index + 1}`} {value}
          oninput={(e) => { const next = [...tokens]; next[row.start + 1 + index] = e.currentTarget.value; presenter.extraArgsText = formatArguments(next); presenter.scheduleEstimate(); }} />
      {/each}
      <button class="btn" type="button" aria-label={`Remove ${row.flag}`} onclick={() => {
        presenter.extraArgsText = formatArguments(tokens.filter((_, index) => index < row.start || index > row.start + row.count)); presenter.scheduleEstimate();
      }}>Remove</button>
    </div>
  {/each}
  <button class="text-button" type="button" onclick={() => importOpen = !importOpen}>Import or inspect argv</button>
  {#if importOpen}
    <label class="field" for="run-extra">Extra arguments
      <textarea id="run-extra" class="field mono" bind:value={presenter.extraArgsText} oninput={() => presenter.scheduleEstimate()} placeholder={'["--lora", "/models/my adapter.gguf"]'}></textarea>
    </label>
    <p class="small muted">JSON arrays preserve every value exactly. Quoted argument text is also accepted.</p>
  {/if}
  {#if presenter.fieldErrors.extra_args}<p class="small" role="alert">{presenter.fieldErrors.extra_args}</p>{/if}
</div>

<style>
  .argument-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .argument-row input { flex: 1; min-width: 140px; }
  code { font-size: 12px; overflow-wrap: anywhere; }
</style>
