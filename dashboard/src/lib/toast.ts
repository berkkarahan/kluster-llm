import { writable } from 'svelte/store';

export type Toast = { id: number; kind: 'ok' | 'warn' | 'danger'; text: string };

let seq = 0;
export const toasts = writable<Toast[]>([]);

export function pushToast(kind: Toast['kind'], text: string, ms = 4000) {
  const id = ++seq;
  toasts.update((t) => [...t, { id, kind, text }]);
  setTimeout(() => {
    toasts.update((t) => t.filter((x) => x.id !== id));
  }, ms);
}
