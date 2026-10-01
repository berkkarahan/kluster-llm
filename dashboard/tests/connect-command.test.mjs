import assert from 'node:assert/strict';
import test from 'node:test';
import { connectCommand, shellQuote } from '../src/lib/connect-command.ts';

test('CONNECT quotes model and endpoint in both supported shells', () => {
  const model = "a 'quoted' $HOME `model`.gguf";
  assert.equal(shellQuote(model, 'bash'), "'a '\\''quoted'\\'' $HOME `model`.gguf'");
  assert.equal(shellQuote(model, 'powershell'), "'a ''quoted'' $HOME `model`.gguf'");
  for (const shell of ['bash', 'powershell']) {
    const live = connectCommand('codex', model, 'http://master:1234', shell);
    assert.match(live, /--url 'http:\/\/master:1234' --model/);
    assert.equal(connectCommand('codex', model, 'http://master:1234', shell, true), `${live} --no-launch`);
    assert.equal(connectCommand('codex', '', 'http://master', shell), '');
  }
});
