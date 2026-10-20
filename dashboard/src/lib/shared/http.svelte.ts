import { errors } from './errors';
import { pushToast } from './toast';

export function v2ErrorDetailMessage(details: unknown): string {
  if (typeof details === 'string') return details;
  if (Array.isArray(details)) {
    return details
      .map((item) => (typeof item === 'object' && item !== null && typeof (item as { msg?: unknown }).msg === 'string'
        ? String((item as { msg: string }).msg) : ''))
      .filter(Boolean)
      .slice(0, 3)
      .join('; ');
  }
  if (typeof details === 'object' && details !== null) {
    const detail = details as { code?: unknown; message?: unknown };
    if (typeof detail.message === 'string' && detail.message) return detail.message;
    return Object.values(details as Record<string, unknown>)
      .filter((value): value is string => typeof value === 'string' && value.length > 0)
      .join('; ');
  }
  return '';
}

export function apiErrorMessage(data: unknown, text: string, status: number, fallback: string): string {
  if (typeof data === 'object' && data !== null && 'error' in data) {
    const error = (data as { error?: { message?: unknown; details?: unknown } }).error;
    // The v2 envelope keeps the actionable text in details; message alone is
    // the generic "The request could not be completed." placeholder.
    const details = v2ErrorDetailMessage(error?.details);
    if (details) return details;
    if (typeof error?.message === 'string') return error.message;
  }
  if (typeof data === 'object' && data !== null && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === 'object' && detail !== null && 'message' in detail && typeof detail.message === 'string') {
      return `${'code' in detail ? String(detail.code) + ': ' : ''}${detail.message}`;
    }
    return String(detail);
  }
  if (typeof data === 'string') return data;
  return text || `${fallback} ${status}`;
}

/** HTTP responses are distinguishable from transport failures without changing Error rendering. */
export class HttpResponseError extends Error {
  readonly code: string | undefined;
  constructor(message: string, readonly status: number, data?: unknown) {
    super(message);
    const body = data && typeof data === 'object' ? data as Record<string, any> : {};
    const detail = body.error?.details ?? body.detail ?? body.error ?? body;
    this.code = typeof detail?.code === 'string' ? detail.code : undefined;
  }
}

/** Shared fetch gateway: bearer token, busy flag, and the command side effects. */
export class HttpClient {
  apiToken = $state('');
  busy = $state('');
  refresh: () => Promise<unknown> = async () => {};
  loadModels: () => Promise<unknown> = async () => {};
  onDownloadAccepted: (operationId: string) => void = () => {};

  async loadToken() {
    try {
      const r = await fetch('/v2/credentials/api-token');
      if (!r.ok) return;
      const data = await r.json();
      this.apiToken = data.api_token ?? '';
    } catch {
      /* ignore */
    }
  }

  async networkRequest<T>(path: string, method = 'GET', value?: unknown, signal?: AbortSignal): Promise<T> {
    if (!this.apiToken) await this.loadToken();
    const response = await fetch(path, {
      method, signal,
      headers: { Authorization: `Bearer ${this.apiToken}`, 'Content-Type': 'application/json' },
      ...(value === undefined ? {} : { body: JSON.stringify(value) })
    });
    let data;
    try { data = await response.json(); }
    catch {
      throw new Error(`Network request ${method} ${path} returned ${response.status}: ${response.ok ? 'Invalid JSON response' : response.statusText || 'Request failed'}`);
    }
    if (!response.ok) {
      const detail = data?.error?.details ?? data?.detail ?? data?.error;
      throw new Error(typeof detail === 'object' && detail !== null
        ? `${detail.code ?? response.status}: ${detail.message ?? 'Network request failed'}`
        : String(detail ?? `Network request ${response.status}`));
    }
    return data as T;
  }

  async command(path: string, body?: unknown, method: 'POST' | 'DELETE' = 'POST') {
    const errorGeneration = errors.generation;
    this.busy = path;
    try {
      const r = await fetch(path, {
        method,
        headers: { 'Content-Type': 'application/json', ...(this.apiToken ? { Authorization: `Bearer ${this.apiToken}` } : {}) },
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
        throw new HttpResponseError(apiErrorMessage(data, text, r.status, path), r.status, data);
      }
      if (path === '/v2/models/downloads' && data && typeof data === 'object'
        && typeof (data as { operation_id?: unknown }).operation_id === 'string') {
        this.onDownloadAccepted((data as { operation_id: string }).operation_id);
      }
      pushToast('ok', `${path} ok`);
      await this.refresh();
      await this.loadModels();
      return data;
    } catch (error) {
      pushToast('danger', String(error), 4000, { generation: errorGeneration });
      throw error;
    } finally {
      this.busy = '';
    }
  }
}
