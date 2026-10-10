import assert from 'node:assert/strict';
import test from 'node:test';
import { get } from 'svelte/store';
import { createErrorPresentation } from '../src/lib/shared/errors.ts';

test('startup acknowledges initial failures, including sources first opened on another route', () => {
  const errors = createErrorPresentation();
  errors.start();
  errors.observe('apply', 'previous run failed', 'run-1');
  errors.observe('network-details', 'previous setup failed', 'setup-1');
  assert.equal(Object.values(get(errors).entries).filter(e => e.visible).length, 0);
  errors.observe('apply', '', '');
  errors.observe('apply', 'new run failed', 'run-2');
  assert.equal(get(errors).entries.apply.visible, true);
  errors.start();
  errors.observe('apply', 'new run failed', 'run-2');
  assert.equal(get(errors).entries.apply.visible, false);
});

test('dismissal survives polling; a new operation, changed failure, recovery or explicit retry re-arms it', () => {
  const errors = createErrorPresentation();
  errors.start();
  errors.observe('load', '');
  errors.observe('load', 'out of memory', 'run-1');
  assert.equal(get(errors).entries.load.visible, true);
  errors.dismiss('load');
  errors.observe('load', 'out of memory', 'run-1');
  assert.equal(get(errors).entries.load.visible, false);
  errors.observe('load', 'out of memory', 'run-2');
  assert.equal(get(errors).entries.load.visible, true);
  errors.dismiss('load');
  errors.observe('load', 'device unavailable', 'run-2');
  assert.equal(get(errors).entries.load.visible, true);
  errors.dismiss('load');
  errors.observe('load', '');
  errors.observe('load', 'device unavailable', 'run-2');
  assert.equal(get(errors).entries.load.visible, true);
  errors.dismiss('load');
  errors.publish('load', 'device unavailable');
  assert.equal(get(errors).entries.load.visible, true);
});

test('clearing hides all messages and late responses, while failures of the new action can appear', () => {
  const errors = createErrorPresentation();
  errors.start();
  const oldRequest = errors.generation;
  errors.publish('search', 'search failed');
  errors.publish('validation', 'context invalid');
  errors.clear();
  errors.observe('search', 'search failed', '', false);
  errors.publish('old-request', 'late failure', oldRequest);
  errors.observe('late-snapshot', 'late diagnostic', 'old-op', true, oldRequest);
  assert.equal(Object.values(get(errors).entries).filter(e => e.visible).length, 0);
  errors.publish('stop', 'stop failed', errors.generation);
  assert.equal(get(errors).entries.stop.visible, true);
  assert.equal(get(errors).entries.validation.message, 'context invalid');
});

test('dismissing an inline failure also hides its duplicate toast', () => {
  const errors = createErrorPresentation();
  errors.start();
  errors.publish('search', 'search failed');
  errors.publish('toast:search failed', 'search failed');
  errors.publish('unrelated', 'different failure');
  errors.dismiss('search');
  assert.equal(get(errors).entries.search.visible, false);
  assert.equal(get(errors).entries['toast:search failed'].visible, false);
  assert.equal(get(errors).entries.unrelated.visible, true);
});
