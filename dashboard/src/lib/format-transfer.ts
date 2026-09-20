/**
 * Transfer label helpers inspired by Unsloth Studio's
 * `studio/frontend/src/features/chat/utils/format-transfer.ts`.
 */

export function formatGb(bytes?: number | null, digits = 2): string {
  if (bytes == null || bytes < 0) return '—';
  return `${(bytes / 1e9).toFixed(digits)} GB`;
}

export function formatRate(bps?: number | null): string {
  if (bps == null || bps <= 0) return '';
  if (bps >= 1e9) return `${(bps / 1e9).toFixed(1)} GB/s`;
  if (bps >= 1e6) return `${(bps / 1e6).toFixed(1)} MB/s`;
  if (bps >= 1e3) return `${(bps / 1e3).toFixed(0)} KB/s`;
  return `${Math.round(bps)} B/s`;
}

export function formatEta(seconds?: number | null): string {
  if (seconds == null || seconds < 0) return '';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s left`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m < 60) return rem > 0 ? `${m}m ${rem}s left` : `${m}m left`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m left`;
}

/** Compose "12.3 / 40.0 GB • 85.3 MB/s • 4m 12s left" like Unsloth's chat toast. */
export function composeProgressLabel(
  bytes: number | null | undefined,
  total: number | null | undefined,
  rateBps?: number | null,
  etaSeconds?: number | null
): string {
  const size =
    total != null && total > 0
      ? `${formatGb(bytes)} / ${formatGb(total)}`
      : formatGb(bytes);
  const parts = [size];
  const rate = formatRate(rateBps);
  if (rate) parts.push(rate);
  const eta = formatEta(etaSeconds);
  if (eta) parts.push(eta);
  return parts.join(' • ');
}
