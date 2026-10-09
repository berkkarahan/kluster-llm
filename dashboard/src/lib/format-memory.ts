import type { GpuInfo } from './types';

export function formatGib(bytes: number | null | undefined): string {
  return bytes == null || !Number.isFinite(bytes) || bytes < 0
    ? '—'
    : `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
}

export function formatMemoryComponent(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`;
  return bytes < 1024 ** 3 ? `${(bytes / 1024 ** 2).toFixed(2)} MiB` : formatGib(bytes);
}

export function selectedCapacity(gpus: GpuInfo[], selectedIds: number[]): number | null {
  if (!selectedIds.length) return null;
  const byIndex = new Map(gpus.map((gpu) => [gpu.index, gpu]));
  let capacity = 0;
  for (const index of new Set(selectedIds)) {
    const bytes = byIndex.get(index)?.vram_bytes;
    if (bytes == null || !Number.isFinite(bytes) || bytes <= 0) return null;
    capacity += bytes;
  }
  return capacity;
}
