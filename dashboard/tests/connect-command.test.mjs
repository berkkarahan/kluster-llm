import assert from 'node:assert/strict';
import test from 'node:test';
import { connectCommand, shellQuote } from '../src/lib/modules/integrations/connect-command.ts';

test('CONNECT quotes model and endpoint in both supported shells', () => {
  const model = "a 'quoted' $HOME `model`.gguf";
  assert.equal(shellQuote(model, 'bash'), "'a '\\''quoted'\\'' $HOME `model`.gguf'");
  assert.equal(shellQuote(model, 'powershell'), "'a ''quoted'' $HOME `model`.gguf'");
  for (const shell of ['bash', 'powershell']) {
    const live = connectCommand('opencode', model, 'http://master:1234', "secret 'token", shell);
    assert.match(live, /--url 'http:\/\/master:1234' --model/);
    assert.match(live, /--no-serve/);
    assert.match(live, /secret/);
    assert.equal(connectCommand('opencode', model, 'http://master:1234', "secret 'token", shell, true), `${live} --no-launch`);
    assert.equal(connectCommand('opencode', model, 'http://master:1234', "secret 'token", shell, false, true).includes("secret"), false);
    assert.equal(connectCommand('codex', '', 'http://master', 'token', shell), '');
    assert.equal(connectCommand('codex', 'm', 'http://master', '', shell), '');
  }
});
