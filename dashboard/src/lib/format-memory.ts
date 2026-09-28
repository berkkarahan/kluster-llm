import type { GpuInfo } from './types';

export function formatGib(bytes: number | null | undefined): string {
  return bytes == null || !Number.isFinite(bytes) || bytes < 0
    ? '—'
    : `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
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
