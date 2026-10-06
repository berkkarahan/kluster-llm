<script lang="ts">
  import Icon from '$lib/components/Icon.svelte';
  import { appStore } from '$lib/store.svelte';
</script>

<section class="panel" aria-label="Cluster chat">
  <div class="panel-head">
    <h2>Chat</h2>
    <span class="tag">{appStore.status?.model_name ?? 'no model'}</span>
  </div>
  <div class="chat-messages">
    {#each appStore.chat as msg}
      <div class="message" class:user={msg.role === 'user'}>
        <strong>{msg.role}</strong>
        <p>{msg.content || '…'}</p>
      </div>
    {:else}
      <p class="muted">Run a model, then chat through the OpenAI-compatible proxy.</p>
    {/each}
  </div>
  <div class="chat-composer">
    <div class="od-row" style="align-items: end">
      <label class="field" style="width: 7rem">
        Temp
        <input
          id="chat-temp"
          class="field mono"
          type="number"
          min="0"
          max="2"
          step="0.05"
          placeholder={String(appStore.activeRunConfig?.generation_defaults.temperature ?? 'Run default')}
          value={appStore.chatTemperature ?? ''}
          disabled={!appStore.status?.inference_up || appStore.chatStreaming}
          onchange={(e) => {
            const raw = e.currentTarget.value.trim();
            appStore.chatTemperature = raw === '' ? null : Number(raw);
          }}
        />
      </label>
      <label class="field od-fill" for="chat-think">
        Thinking
        <select
          id="chat-think"
          class="field mono"
          disabled={!appStore.status?.inference_up || appStore.chatStreaming}
          value={appStore.chatThinking === null ? 'auto' : appStore.chatThinking ? 'on' : 'off'}
          onchange={(e) => {
            const value = e.currentTarget.value;
            appStore.chatThinking = value === 'auto' ? null : value === 'on';
          }}
        >
          <option value="auto">Running model default</option>
          <option value="on">On</option>
          <option value="off">Off</option>
        </select>
      </label>
    </div>
    <details class="session-options">
      <summary>Chat session overrides</summary>
      <p class="small muted">These choices apply to this chat session.</p>
      <label class="field" for="chat-output-limit">Response token limit
        <input id="chat-output-limit" class="field" type="number" min="1" step="1" value={appStore.chatOutputLimit ?? ''}
          placeholder="Running model default" oninput={(e) => appStore.chatOutputLimit = e.currentTarget.value === '' ? null : Number(e.currentTarget.value)} />
      </label>
      <label class="field" for="chat-response-format">Output format
        <select id="chat-response-format" class="field" bind:value={appStore.chatResponseFormat}>
          <option value="text">Text</option><option value="json_object">JSON object</option><option value="json_schema">JSON schema</option>
        </select>
      </label>
      {#if appStore.chatResponseFormat === 'json_schema'}
        <label class="field" for="chat-schema">JSON schema<textarea id="chat-schema" class="field mono" bind:value={appStore.chatSchemaText}></textarea></label>
      {/if}
    </details>
    <div class="od-row">
      <input
        class="field mono od-fill"
        placeholder={appStore.status?.inference_up ? 'Message…' : 'Start inference first'}
        bind:value={appStore.chatInput}
        disabled={!appStore.status?.inference_up || appStore.chatStreaming}
        onkeydown={(e) => e.key === 'Enter' && appStore.sendChat()}
      />
      {#if appStore.chatStreaming}
        <button class="btn btn-danger" type="button" onclick={() => appStore.stopChat()}>
          <Icon name="stop" small /> Stop
        </button>
      {:else}
        <button class="btn btn-accent" type="button" disabled={!appStore.status?.inference_up} onclick={() => appStore.sendChat()}>
          <Icon name="send" small /> Send
        </button>
      {/if}
    </div>
  </div>
</section>
