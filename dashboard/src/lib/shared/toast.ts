import { get, writable } from 'svelte/store';
import { errors } from './errors';

export type Toast = { id: number; kind: 'ok' | 'warn' | 'danger'; text: string; source: string };

let seq = 0;
export const toasts = writable<Toast[]>([]);

export function pushToast(kind: Toast['kind'], text: string, ms = 4000,
  options: { source?: string; generation?: number; snapshot?: boolean } = {}) {
  if (options.generation !== undefined && options.generation !== errors.generation) return;
  const id = ++seq;
  const source = options.source ?? `toast:${text}`;
  if (kind === 'danger') {
    if (options.snapshot) errors.observe(source, text);
    else errors.publish(source, text, options.generation);
    if (!get(errors).entries[source]?.visible) return;
  }
  toasts.update((t) => [...t.filter(x => x.source !== source), { id, kind, text, source }]);
  setTimeout(() => {
    toasts.update((t) => t.filter((x) => x.id !== id));
  }, ms);
}

export function dismissToast(id: number) {
  toasts.update(items => items.filter(item => item.id !== id));
}

errors.subscribe(state => {
  toasts.update(items => items.filter(item => item.kind !== 'danger' || state.entries[item.source]?.visible));
});
