<script lang="ts">
  import { createApp } from '#lib/app.svelte';
  import Icon from '#lib/shared/Icon.svelte';
  import type { IntegrationsInteractor } from '../interactor.svelte';
  let { presenter = createApp().integrations }: { presenter?: IntegrationsInteractor } = $props();
</script>

<section class="panel" aria-label="Cluster chat">
  <div class="panel-head">
    <h2>Chat</h2>
    <span class="tag">{presenter.status?.model_name ?? 'no model'}</span>
  </div>
  <div class="chat-messages">
    {#each presenter.chat as msg}
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
          placeholder={String(presenter.activeRunConfig?.generation_defaults.temperature ?? 'Run default')}
          value={presenter.chatTemperature ?? ''}
          disabled={!presenter.status?.inference_up || presenter.chatStreaming}
          onchange={(e) => {
            const raw = e.currentTarget.value.trim();
            presenter.chatTemperature = raw === '' ? null : Number(raw);
          }}
        />
      </label>
      <label class="field od-fill" for="chat-think">
        Thinking
        <select
          id="chat-think"
          class="field mono"
          disabled={!presenter.status?.inference_up || presenter.chatStreaming}
          value={presenter.chatThinking === null ? 'auto' : presenter.chatThinking ? 'on' : 'off'}
          onchange={(e) => {
            const value = e.currentTarget.value;
            presenter.chatThinking = value === 'auto' ? null : value === 'on';
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
        <input id="chat-output-limit" class="field" type="number" min="1" step="1" value={presenter.chatOutputLimit ?? ''}
          placeholder="Running model default" oninput={(e) => presenter.chatOutputLimit = e.currentTarget.value === '' ? null : Number(e.currentTarget.value)} />
      </label>
      <label class="field" for="chat-response-format">Output format
        <select id="chat-response-format" class="field" bind:value={presenter.chatResponseFormat}>
          <option value="text">Text</option><option value="json_object">JSON object</option><option value="json_schema">JSON schema</option>
        </select>
      </label>
      {#if presenter.chatResponseFormat === 'json_schema'}
        <label class="field" for="chat-schema">JSON schema<textarea id="chat-schema" class="field mono" bind:value={presenter.chatSchemaText}></textarea></label>
      {/if}
    </details>
    <div class="od-row">
      <input
        class="field mono od-fill"
        placeholder={presenter.status?.inference_up ? 'Message…' : 'Start inference first'}
        bind:value={presenter.chatInput}
        disabled={!presenter.status?.inference_up || presenter.chatStreaming}
        onkeydown={(e) => e.key === 'Enter' && presenter.sendChat()}
      />
      {#if presenter.chatStreaming}
        <button class="btn btn-danger" type="button" onclick={() => presenter.stopChat()}>
          <Icon name="stop" small /> Stop
        </button>
      {:else}
        <button class="btn btn-accent" type="button" disabled={!presenter.status?.inference_up} onclick={() => presenter.sendChat()}>
          <Icon name="send" small /> Send
        </button>
      {/if}
    </div>
  </div>
</section>
