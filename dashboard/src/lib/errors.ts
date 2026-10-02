import { writable } from 'svelte/store';

type Entry = { message: string; identity: string; visible: boolean };
type State = { generation: number; started: boolean; entries: Record<string, Entry> };

/** Presentation only: callers retain their authoritative failure and validation state. */
export function createErrorPresentation() {
  let state: State = { generation: 0, started: false, entries: {} };
  const store = writable(state);
  const commit = () => store.set(state = { ...state, entries: { ...state.entries } });
  return {
    subscribe: store.subscribe,
    get generation() { return state.generation; },
    start() {
      state = { generation: state.generation + 1, started: true, entries: {} };
      commit();
    },
    clear() {
      state.generation++;
      for (const [source, entry] of Object.entries(state.entries)) {
        state.entries[source] = { ...entry, visible: false };
      }
      commit();
    },
    observe(source: string, message = '', identity = '', snapshot = true, generation = state.generation) {
      if (generation !== state.generation) return;
      const previous = state.entries[source];
      if (previous?.message === message && previous.identity === identity) return;
      state.entries[source] = { message, identity,
        visible: !!message && (!snapshot || !!previous || !state.started) };
      commit();
    },
    publish(source: string, message: string, generation = state.generation, identity = '') {
      if (generation !== state.generation) return;
      state.entries[source] = { message, identity, visible: true };
      commit();
    },
    dismiss(source: string) {
      const entry = state.entries[source];
      if (!entry) return;
      // An inline diagnostic and its toast may describe the same failure.
      for (const [key, other] of Object.entries(state.entries)) {
        if (key === source || other.message === entry.message) {
          state.entries[key] = { ...other, visible: false };
        }
      }
      commit();
    }
  };
}

export const errors = createErrorPresentation();
