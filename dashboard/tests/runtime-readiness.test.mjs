import assert from 'node:assert/strict';
import test from 'node:test';
import { runtimeRunReason } from '../src/lib/modules/run/runtime-readiness.ts';

const library = {
  target: {version: {runtime_id: 'target'}},
  nodes: [
    {node_id: 'master', phase: 'prepared', prepared: {runtime_id: 'target'}},
    {node_id: 'selected', phase: 'prepared', prepared: {runtime_id: 'target'}},
    {node_id: 'unused', phase: 'offline'}
  ]
};

test('unused offline children do not block selected matching participants', () => {
  assert.equal(runtimeRunReason(library, 'master', {runtime_required_nodes: ['master', 'selected']}), '');
});

test('wrong source identity and stale plans remain blocking', () => {
  const changed = structuredClone(library);
  changed.nodes[1].prepared.runtime_id = 'other-fork';
  assert.match(runtimeRunReason(changed, 'master', {runtime_required_nodes: ['master', 'selected']}), /selected/);
  assert.match(runtimeRunReason(library, 'master', {runtime_id: 'old-target'}), /Refresh/);
});

test('missing selection is actionable without treating unknown data as readiness', () => {
  assert.match(runtimeRunReason({nodes: []}, 'master', null), /Choose and prepare/);
  assert.match(runtimeRunReason({target: {version: {runtime_id: 'target'}}, nodes: []}, 'master', null), /master/);
});

test('the readiness reason names the selected engine', () => {
  const prima = structuredClone(library);
  prima.target.version.engine = 'primacpp';
  prima.nodes[1].prepared.runtime_id = 'stale';
  assert.match(runtimeRunReason(prima, 'master', {runtime_required_nodes: ['master', 'selected']}),
    /Prepare the target Prima version on selected\./);
});
