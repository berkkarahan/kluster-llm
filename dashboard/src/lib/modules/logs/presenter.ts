import type { LogTag } from './entities';

type LogTrailState = 'loading' | 'ready' | 'empty' | 'cleared' | 'error';

/** List copy when nothing is visible. Tag sentences apply only when the buffer holds rows. */
export function logListEmptyCopy(input: {
  state: LogTrailState;
  buffered: number;
  selectedNodeId: string;
  selectedTag: '' | LogTag;
}): string {
  if (input.state === 'cleared') return 'Display cleared — waiting for a new entry.';
  if (input.buffered > 0) {
    if (input.selectedNodeId && input.selectedTag) {
      return 'No entries match the selected node and tag in the current buffer.';
    }
    if (input.selectedTag) return 'No entries for the selected tag in the current buffer.';
    if (input.selectedNodeId) return 'No entries for the selected node in the current buffer.';
  } else if (!input.selectedTag && input.selectedNodeId) {
    return 'No entries for the selected node in the current buffer.';
  }
  return 'No log entries yet.';
}
