import assert from 'node:assert/strict';
import test from 'node:test';
import { formatGib, selectedCapacity } from '../src/lib/format-memory.ts';

const gib = 1024 ** 3;
const gpus = [
  { index: 0, name: '16 GiB', vram_bytes: 16 * gib },
  { index: 1, name: '8 GiB', vram_bytes: 8 * gib }
];

test('measured binary memory displays in one-decimal GiB', () => {
  for (const capacity of [8, 16, 24]) {
    assert.equal(formatGib(capacity * gib), `${capacity}.0 GiB`);
  }
  assert.equal(formatGib(15.2 * gib), '15.2 GiB');
  assert.equal(formatGib(15.5 * gib), '15.5 GiB');
});

test('selected capacity counts each measured device once and follows selection', () => {
  assert.equal(formatGib(selectedCapacity(gpus, [0, 1, 0])), '24.0 GiB');
  assert.equal(formatGib(selectedCapacity(gpus, [1])), '8.0 GiB');
  assert.equal(formatGib(selectedCapacity([{ ...gpus[0], vram_bytes: 15 * gib }], [0])), '15.0 GiB');
});

test('missing or invalid memory never displays as a plausible total', () => {
  for (const value of [null, undefined, -1, NaN, Infinity]) {
    assert.equal(formatGib(value), '—');
  }
  assert.equal(formatGib(selectedCapacity([...gpus, { index: 2, name: 'unknown' }], [0, 2])), '—');
  assert.equal(formatGib(selectedCapacity(gpus, [0, 3])), '—');
});
