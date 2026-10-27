<script lang="ts">
  import type { Snippet } from 'svelte';
  let {
    tab = $bindable('settings'),
    section = $bindable('overview'),
    search = $bindable(''),
    technical = $bindable(false),
    dirty,
    issues = [],
    focus,
    matches = [],
    running = false,
    modelName = '',
    reason = '',
    onStop,
    runningModel,
    children,
    nextDeployment,
    footer
  }: {
    tab?: string;
    section?: string;
    search?: string;
    technical?: boolean;
    dirty: boolean;
    issues?: { field: string; message: string; section: string }[];
    focus: (field: string) => void;
    matches?: { field: string; label: string }[];
    running?: boolean;
    modelName?: string;
    reason?: string;
    onStop?: () => void;
    runningModel?: Snippet;
    children: Snippet;
    nextDeployment: Snippet;
    footer: Snippet;
  } = $props();
  const tabs = [['settings','Deployment settings'],['deployment','Next deployment']];
  function navigateTab(event: KeyboardEvent, index: number) {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    tab = tabs[next][0];
    document.getElementById(`prima-tab-${tab}`)?.focus();
  }
  const sections = [['overview','Overview'],['computers','Computers'],['memory','Memory & speed'],['features','Responses & features'],['expert','Expert']];
</script>
<div class="workspace">
  {#if runningModel}
    {@render runningModel()}
  {:else if running || reason}
    <section class="panel running-model" aria-label="Running model">
      <div>
        <span class="tag">{running ? 'Running' : 'Starting or stopping'}</span>
        <strong>{modelName || 'Loading model'}</strong>
        {#if reason}<p class="small muted">{reason}</p>{/if}
      </div>
      <button
        class="btn btn-danger"
        onclick={() => onStop?.()}>Stop model</button>
    </section>
  {/if}
  <div class="main-tabs" role="tablist" aria-label="Prima deployment">
    {#each tabs as [id,title], index}
      <button type="button" role="tab" id={`prima-tab-${id}`} aria-controls={`prima-panel-${id}`} aria-selected={tab === id} tabindex={tab === id ? 0 : -1} onclick={() => tab=id} onkeydown={event => navigateTab(event,index)}>{title}</button>
    {/each}
  </div>
  <div role="tabpanel" id="prima-panel-settings" aria-labelledby="prima-tab-settings" hidden={tab !== 'settings'} tabindex="0">
  <section class="panel editor" aria-labelledby="settings-heading">
    <div class="editor-head">
      <div class="heading"><h2 id="settings-heading">Deployment settings</h2><span class="tag">{dirty ? 'Unsaved changes' : 'Saved draft'}</span></div>
      <div class="tools">
        <label class="search">Find a setting or native flag
          <input id="prima-setting-search" class="field" type="search" bind:value={search} placeholder="Context, --cache-type-k…" onkeydown={e => { if(e.key === 'Escape') search=''; }} />
        </label>
        <label class="technical"><input type="checkbox" bind:checked={technical} />Technical details</label>
      </div>
      {#if search.trim()}
        <div class="matches" aria-label="Matching settings">
          {#each matches as match}<button type="button" class="text-button" onclick={() => { focus(match.field); search=''; }}>{match.label}</button>{/each}
          {#if !matches.length}<p>No matching setting.</p>{/if}
        </div>
      {/if}
    </div>
    <nav aria-label="Settings sections">
      {#each sections as [id,title]}
        <button type="button" aria-current={section === id ? 'page' : undefined} onclick={() => section=id}>{title}{#if issues.filter(i=>i.section===id).length}<span class="issue-count"> · {issues.filter(i=>i.section===id).length} issues</span>{/if}</button>
      {/each}
    </nav>
    {#if issues.length}<div class="issues" role="alert" tabindex="-1"><strong>Check these settings before continuing</strong><ul>{#each issues as issue}<li><button type="button" class="text-button" onclick={() => focus(issue.field)}>{issue.message}</button></li>{/each}</ul></div>{/if}
    {@render children()}
    <footer>{@render footer()}</footer>
  </section>
  </div>
  <div class="deployment-panel" role="tabpanel" id="prima-panel-deployment" aria-labelledby="prima-tab-deployment" hidden={tab !== 'deployment'} tabindex="0">{@render nextDeployment()}</div>
</div>
<style>
  .workspace { display:grid;grid-template-columns:minmax(0,1fr);gap:20px;align-items:start;min-width:0; }
  [hidden] { display:none !important; }
  [role=tabpanel] { min-width:0; }
  .main-tabs { display:flex;border-bottom:1px solid var(--border);gap:12px; }
  .main-tabs button { min-height:44px;padding:12px;border-bottom:2px solid transparent; }
  .main-tabs button[aria-selected=true] { color:var(--accent);border-color:var(--accent); }
  .deployment-panel { display:grid;gap:20px; }
  .editor { min-width:0;overflow:hidden;border-radius:8px; }
  .editor-head,footer { padding:20px; }
  .heading,.tools { display:flex;align-items:center;gap:12px;flex-wrap:wrap; }
  .heading { justify-content:space-between;margin-bottom:18px; }
  h2 { font-size:18px;font-weight:600; }
  .search { display:grid;gap:6px;flex:1;min-width:150px;font-size:14px; }
  input[type=search] { min-height:44px;min-width:0;width:100%; }
  .technical { display:flex;gap:8px;align-items:center;font-size:14px; }
  nav { display:flex;overflow-x:auto;border-bottom:1px solid var(--border);padding:0 12px; }
  nav button { flex-shrink:0;min-height:44px;padding:12px 10px;font-size:13px;border-bottom:2px solid transparent; }
  nav button[aria-current] { color:var(--accent);border-color:var(--accent); }
  .issue-count,.issues { color:var(--danger); }
  .issues { padding:20px; }
  .matches { display:grid;gap:8px;margin-top:12px; }
  footer { border-top:1px solid var(--border); }
  .running-model { padding: 18px 24px; display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; border: 1px solid var(--border); border-radius: 8px; }
  .running-model strong { margin-left: 12px; }
</style>
