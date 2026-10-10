import { errors } from '../../shared/errors';
import { pushToast } from '../../shared/toast';
import type { HttpClient } from '../../shared/http.svelte';
import type { ChatMessage, ConnectionSnapshot } from './entities';
import type { ClusterInteractor } from '../cluster/interactor.svelte';
import type { ModelsInteractor } from '../models/interactor.svelte';
import type { RunInteractor } from '../run/interactor.svelte';

export class IntegrationsInteractor {
  chat = $state<ChatMessage[]>([]);
  chatInput = $state('');
  chatStreaming = $state(false);
  chatTemperature = $state<number | null>(null);
  chatThinking = $state<boolean | null>(null);
  chatOutputLimit = $state<number | null>(null);
  chatResponseFormat = $state<'text' | 'json_object' | 'json_schema'>('text');
  chatSchemaText = $state('');
  private abort: AbortController | null = null;

  constructor(
    readonly http: HttpClient,
    readonly cluster: ClusterInteractor,
    readonly catalog: ModelsInteractor,
    readonly run: RunInteractor
  ) {}

  get status() { return this.cluster.status; }
  get selectedModel() { return this.catalog.selectedModel; }
  get apiToken() { return this.http.apiToken; }
  get activeRunConfig() { return this.run.activeRunConfig; }

  private loadToken() {
    return this.http.loadToken();
  }

  onRunIdentityChanged() {
    this.chatTemperature = null;
    this.chatThinking = null;
    this.chatOutputLimit = null;
  }

  async connectionSnapshot(): Promise<ConnectionSnapshot> {
    return this.http.networkRequest<ConnectionSnapshot>('/v2/inference/connection');
  }

    stopChat() {
    this.abort?.abort();
    this.abort = null;
    this.chatStreaming = false;
  }

  async sendChat() {
    const errorGeneration = errors.generation;
    const content = this.chatInput.trim();
    if (!content || this.chatStreaming || !this.status?.inference_up) return;
    if (this.chatResponseFormat === 'json_schema') {
      try {
        const schema = JSON.parse(this.chatSchemaText);
        if (!schema || typeof schema !== 'object' || Array.isArray(schema)) throw new Error('Schema must be a JSON object.');
      } catch (error) { pushToast('danger', `Invalid output schema: ${String(error)}`); return; }
    }
    if (!this.apiToken) await this.loadToken();
    this.chat = [...this.chat, { role: 'user', content }, { role: 'assistant', content: '' }];
    this.chatInput = '';
    this.chatStreaming = true;
    this.abort = new AbortController();
    try {
      const r = await fetch('/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${this.apiToken}`
        },
        body: JSON.stringify({
          model: this.status?.model_name || this.selectedModel || 'default',
          messages: this.chat.slice(0, -1),
          stream: true,
          ...(this.chatTemperature !== null ? { temperature: this.chatTemperature } : {}),
          ...(this.chatOutputLimit !== null ? { max_tokens: this.chatOutputLimit } : {}),
          ...(this.chatThinking !== null ? { chat_template_kwargs: { enable_thinking: this.chatThinking } } : {}),
          ...(this.chatResponseFormat === 'json_object' ? { response_format: { type: 'json_object' } } : {}),
          ...(this.chatResponseFormat === 'json_schema' ? { response_format: {
            type: 'json_schema', json_schema: { name: 'chat_output', strict: true, schema: JSON.parse(this.chatSchemaText) }
          } } : {})
        }),
        signal: this.abort.signal
      });
      if (!r.ok || !r.body) {
        const t = await r.text();
        pushToast('danger', t || `chat ${r.status}`, 4000, { generation: errorGeneration });
        this.chatStreaming = false;
        return;
      }
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split('\n');
        buf = parts.pop() ?? '';
        for (const line of parts) {
          if (!line.startsWith('data: ')) continue;
          const payload = line.slice(6).trim();
          if (payload === '[DONE]') continue;
          try {
            const json = JSON.parse(payload);
            const delta = json.choices?.[0]?.delta?.content;
            if (delta) {
              const copy = [...this.chat];
              const last = copy[copy.length - 1];
              if (last?.role === 'assistant') {
                copy[copy.length - 1] = { ...last, content: last.content + delta };
                this.chat = copy;
              }
            }
          } catch {
            /* ignore partial */
          }
        }
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') pushToast('danger', String(e), 4000, { generation: errorGeneration });
    } finally {
      this.chatStreaming = false;
      this.abort = null;
    }
  }
}

