import type { ChatMessage, ModelInfo, Status } from './types';
import { pushToast } from './toast';

let fingerprint = '';

function fp(s: Status | null): string {
  if (!s) return '';
  return JSON.stringify({
    role: s.role,
    members: s.members,
    model_name: s.model_name,
    inference_up: s.inference_up,
    last_plan: s.last_plan,
    process_pid: s.process_pid,
    rpc_bind: s.rpc_bind,
    overlay: s.overlay,
    wg_address: s.wg_address,
    overlay_status: s.overlay_status,
    netsec_warnings: s.netsec_warnings
  });
}

class AppStore {
  status = $state<Status | null>(null);
  connected = $state(false);
  models = $state<ModelInfo[]>([]);
  apiToken = $state('');
  busy = $state('');
  joinKeyInput = $state('');
  masterHost = $state('');
  rpcBindInput = $state('');
  overlay = $state<'none' | 'wireguard'>('none');
  modelSource = $state('');
  selectedModel = $state('');
  context = $state(4096);
  device = $state<'cpu' | 'gpu'>('gpu');
  planResult = $state<Record<string, unknown> | null>(null);
  chat = $state<ChatMessage[]>([]);
  chatInput = $state('');
  chatStreaming = $state(false);
  private timer: ReturnType<typeof setInterval> | null = null;
  private abort: AbortController | null = null;

  start() {
    void this.refresh();
    void this.loadToken();
    void this.loadModels();
    void this.loadConfig();
    this.timer = setInterval(() => void this.refresh(), 1000);
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  async refresh() {
    try {
      const r = await fetch('/v1/status');
      if (!r.ok) throw new Error(`status ${r.status}`);
      const data = (await r.json()) as Status;
      const next = fp(data);
      if (next !== fingerprint) {
        fingerprint = next;
        this.status = data;
        if (data.model_name && !this.selectedModel) this.selectedModel = data.model_name;
        if (data.join_key && !this.joinKeyInput) this.joinKeyInput = data.join_key;
      } else if (!this.status) {
        this.status = data;
      }
      this.connected = true;
    } catch {
      this.connected = false;
    }
  }

  async loadToken() {
    try {
      const r = await fetch('/v1/api-token');
      if (!r.ok) return;
      const data = await r.json();
      this.apiToken = data.api_token ?? '';
    } catch {
      /* ignore */
    }
  }

  async loadModels() {
    try {
      const r = await fetch('/v1/models');
      if (!r.ok) return;
      const data = await r.json();
      this.models = data.data ?? [];
      if (!this.selectedModel && this.models[0]) this.selectedModel = this.models[0].id;
    } catch {
      /* ignore */
    }
  }

  async loadConfig() {
    try {
      const r = await fetch('/v1/config');
      if (!r.ok) return;
      const data = await r.json();
      if (data.device === 'cpu' || data.device === 'gpu') this.device = data.device;
    } catch {
      /* ignore */
    }
  }

  async setDevice(device: 'cpu' | 'gpu') {
    this.device = device;
    this.busy = '/v1/config';
    try {
      const r = await fetch('/v1/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device })
      });
      const text = await r.text();
      if (!r.ok) {
        pushToast('danger', text || `config ${r.status}`);
        throw new Error(text);
      }
      pushToast('ok', `device=${device}`);
      await this.refresh();
    } finally {
      this.busy = '';
    }
  }

  async post(path: string, body?: unknown) {
    this.busy = path;
    try {
      const r = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body)
      });
      const text = await r.text();
      let data: unknown = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text;
      }
      if (!r.ok) {
        pushToast('danger', typeof data === 'string' ? data : JSON.stringify(data));
        throw new Error(String(text));
      }
      pushToast('ok', `${path} ok`);
      await this.refresh();
      await this.loadModels();
      return data;
    } finally {
      this.busy = '';
    }
  }

  async startMaster() {
    await this.post('/v1/start', {
      key: this.joinKeyInput || null,
      rpc_bind: this.rpcBindInput || null,
      overlay: this.overlay
    });
  }

  async stopCluster() {
    await this.post('/v1/stop');
  }

  async joinCluster() {
    await this.post('/v1/join', {
      key: this.joinKeyInput,
      master_host: this.masterHost,
      master_port: 18765,
      rpc_port: 50052,
      rpc_bind: this.rpcBindInput || null,
      overlay: this.overlay
    });
  }

  async leaveCluster() {
    await this.post('/v1/leave');
  }

  async downloadModel() {
    await this.post('/v1/models/download', { source: this.modelSource });
    this.modelSource = '';
  }

  async distributeModel() {
    if (!this.selectedModel) return;
    await this.post('/v1/models/distribute', { name: this.selectedModel });
  }

  async plan() {
    if (!this.selectedModel) return;
    this.planResult = (await this.post('/v1/plan', {
      model: this.selectedModel,
      context: this.context,
      device: this.device
    })) as Record<string, unknown>;
  }

  async run() {
    if (!this.selectedModel) return;
    await this.post('/v1/run', {
      model: this.selectedModel,
      context: this.context,
      device: this.device
    });
  }

  stopChat() {
    this.abort?.abort();
    this.abort = null;
    this.chatStreaming = false;
  }

  async sendChat() {
    const content = this.chatInput.trim();
    if (!content || this.chatStreaming) return;
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
          stream: true
        }),
        signal: this.abort.signal
      });
      if (!r.ok || !r.body) {
        const t = await r.text();
        pushToast('danger', t || `chat ${r.status}`);
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
      if ((e as Error).name !== 'AbortError') pushToast('danger', String(e));
    } finally {
      this.chatStreaming = false;
      this.abort = null;
    }
  }
}

export const appStore = new AppStore();
