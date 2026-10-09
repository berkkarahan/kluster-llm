import assert from 'node:assert/strict';
import test from 'node:test';
import { addressInSubnet, observedControlAddress } from '../src/lib/fast-subnet.ts';

test('a router address is outside the fast subnet and a private host is inside', () => {
  assert.equal(addressInSubnet('192.168.1.20', '10.10.10.0/24'), false);
  assert.equal(addressInSubnet('10.10.10.2', '10.10.10.0/24'), true);
  assert.equal(addressInSubnet('10.10.11.2', '10.10.10.0/24'), false);
  assert.equal(addressInSubnet('10.10.10.2', ''), false);
  assert.equal(addressInSubnet('not-an-ip', '10.10.10.0/24'), false);
});

test('the observed control peer outranks the advertised host', () => {
  assert.equal(observedControlAddress({
    node_id: 'w1',
    host: '10.10.10.2',
    lan_host: '10.10.10.2',
    control_source: '192.168.1.20'
  }), '192.168.1.20');
  assert.equal(observedControlAddress({
    node_id: 'w1',
    host: '192.168.1.20'
  }), '192.168.1.20');
  assert.equal(observedControlAddress(undefined), null);
});
