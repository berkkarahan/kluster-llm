import type { Member } from './entities';

/** True when an IPv4 address sits inside a CIDR such as 10.10.10.0/24. */
export function addressInSubnet(
  address: string | null | undefined,
  subnet: string | null | undefined
): boolean {
  if (!address || !subnet) return false;
  const [base, prefixText] = subnet.split('/');
  const prefix = Number(prefixText);
  if (!base || !Number.isInteger(prefix) || prefix < 0 || prefix > 32) return false;
  const ip = ipv4ToInt(address);
  const network = ipv4ToInt(base);
  if (ip == null || network == null) return false;
  if (prefix === 0) return true;
  const mask = prefix === 32 ? 0xffffffff : (~0 << (32 - prefix)) >>> 0;
  return (ip & mask) === (network & mask);
}

/** Address the worker is using to reach the master, preferring the observed TCP peer. */
export function observedControlAddress(member: Member | null | undefined): string | null {
  const address = member?.control_source || member?.lan_host || member?.host || '';
  const trimmed = address.trim();
  return trimmed || null;
}

function ipv4ToInt(address: string): number | null {
  const parts = address.split('.');
  if (parts.length !== 4) return null;
  let value = 0;
  for (const part of parts) {
    if (!/^\d{1,3}$/.test(part)) return null;
    const octet = Number(part);
    if (octet > 255) return null;
    value = ((value << 8) + octet) >>> 0;
  }
  return value;
}
