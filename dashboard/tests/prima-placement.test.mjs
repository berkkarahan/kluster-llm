import assert from 'node:assert/strict';
import test from 'node:test';
import { primaPlacementError } from '../src/lib/modules/runtimes/presenter.ts';

test('preparation reports placement blockers separately from inference qualification', () => {
  assert.equal(primaPlacementError(null), '');
  assert.equal(primaPlacementError({prima_topology: {ranks: [{}]}, placement_errors: [],
    qualification_errors: ['Inference is unqualified']}), '');
  assert.equal(primaPlacementError({prima_topology: null,
    placement_errors: ['Remote MoE placement is unsupported']}), 'Remote MoE placement is unsupported');
  assert.equal(primaPlacementError({prima_topology: {ranks: [{}]},
    placement_errors: ['Missing per-visit graph reservations']}), 'Missing per-visit graph reservations');
  assert.equal(primaPlacementError({prima_topology: null,
    qualification_errors: ['Mixed GPU backends']}), 'Mixed GPU backends');
});
