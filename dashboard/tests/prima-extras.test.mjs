import assert from 'node:assert/strict';
import test from 'node:test';
import { parseArgumentText, patchPrimaExtras, parsePrimaExtras, serializePrimaExtras } from '../src/lib/modules/run/configuration.ts';
const edit = (tokens, patch) => parseArgumentText(patchPrimaExtras(JSON.stringify(tokens), patch));

test('legacy parsing and serialization exports retain compatibility and alias precedence', () => {
  const state = parsePrimaExtras('["--embeddings","--reranking","--pooling=rank","--lora=a","--future",""]');
  assert.equal(state.headMode, 'reranking');
  assert.equal(state.pooling, 'rank');
  assert.deepEqual(state.loraAdapters, [{ path: 'a', scale: '1' }]);
  assert.deepEqual(state.otherTokens, ['--future', '']);
  assert.deepEqual(parseArgumentText(serializePrimaExtras(state)), ['--rerank', '--lora', 'a', '--future', '']);
});

test('LoRA scales accept signed decimal exponents but reject blank and nonnumeric forms', () => {
  for (const scale of ['-0.5', '0', '-2e-3', '+.5', ' 2 ']) {
    assert.deepEqual(edit([], { loraAdapters: [{ path: ' a ', scale }] }), ['--lora-scaled', ' a ', scale]);
  }
  for (const scale of ['', ' ', '0x10', null, Infinity]) {
    assert.throws(() => edit([], { loraAdapters: [{ path: 'a', scale }] }), /finite/i);
  }
});

test('no-op preserves exact source text, including invalid raw text', () => {
  for (const raw of ['[ "--lora", "a b.gguf" ]', '  --future=""   ', '[invalid']) {
    assert.equal(patchPrimaExtras(raw, {}), raw);
  }
});

test('owned duplicate or malformed SWA flags require raw repair', () => {
  for (const tokens of [['--swa-full', '--swa-full'], ['--swa-full', '--swa-compact'], ['--swa-full=true']]) {
    assert.throws(() => edit(tokens, { swaMode: 'compact' }), /swaMode.*Extra arguments/i);
  }
});

test('unrelated invalid groups and duplicates remain opaque', () => {
  const before = ['--pooling=invalid', '--pooling', '', '--chat-template-kwargs={broken', '--lora-scaled', 'a', 'Infinity', '--swa-full'];
  assert.deepEqual(edit(before, { swaMode: 'compact' }), [...before.slice(0, -1), '--swa-compact']);
});

test('head and pooling changes are independent and replace inline values in place', () => {
  const before = ['--future', '', '--embeddings', '--pooling=mean', '--tail'];
  assert.deepEqual(edit(before, { headMode: 'reranking' }), ['--future', '', '--rerank', '--pooling=mean', '--tail']);
  assert.deepEqual(edit(before, { pooling: 'rank' }), ['--future', '', '--embeddings', '--pooling', 'rank', '--tail']);
  assert.deepEqual(edit(before, { headMode: 'generation', pooling: '' }), ['--future', '', '--tail']);
});

test('owned pooling rejects invalid, missing, duplicate, and flag-like values', () => {
  for (const tokens of [['--pooling=bogus'], ['--pooling'], ['--pooling', '--swa-full'], ['--pooling='], ['--pooling=mean', '--pooling=rank']]) {
    assert.throws(() => edit(tokens, { pooling: 'mean' }), /pooling.*Extra arguments/i);
  }
});

test('owned head aliases cannot silently resolve duplicates or inline booleans', () => {
  for (const tokens of [['--embedding', '--rerank'], ['--embeddings', '--embedding'], ['--reranking=false']]) {
    assert.throws(() => edit(tokens, { headMode: 'generation' }), /headMode.*Extra arguments/i);
  }
});

test('inline template kwargs are replaced in place without trimming JSON text', () => {
  const before = ['--x', '', '--chat-template-kwargs={"a":false}', '--tail'];
  const value = ' {"b":0,"__proto__":{"safe":"data"}} ';
  assert.deepEqual(edit(before, { templateKwargsText: value }), ['--x', '', '--chat-template-kwargs', value, '--tail']);
  assert.deepEqual(edit(before, { templateKwargsText: '' }), ['--x', '', '--tail']);
});

test('template kwargs must be a JSON object on both sides of an owned edit', () => {
  for (const value of ['{broken', 'null', '[]', 'true', '"text"', '1']) {
    assert.throws(() => edit([], { templateKwargsText: value }), /templateKwargsText.*JSON object/i);
    assert.throws(() => edit([`--chat-template-kwargs=${value}`], { templateKwargsText: '{}' }), /templateKwargsText.*Extra arguments/i);
  }
  assert.throws(() => edit(['--chat-template-kwargs', '--swa-full'], { templateKwargsText: '{}' }), /templateKwargsText.*Extra arguments/i);
  assert.throws(() => edit(['--chat-template-kwargs={}', '--chat-template-kwargs={}'], { templateKwargsText: '{}' }), /duplicate/i);
});

test('repeated interleaved LoRA edits retain unrelated token positions and literal paths', () => {
  const before = ['--lora=old', '--future', '', '--lora-scaled', 'second', '-0.5', '--swa-full'];
  const adapters = [{ path: ' C:\\models\\a.gguf ', scale: '1' }, { path: 'second', scale: '-2' }];
  assert.deepEqual(edit(before, { loraAdapters: adapters }), ['--lora', ' C:\\models\\a.gguf ', '--future', '', '--lora-scaled', 'second', '-2', '--swa-full']);
  assert.deepEqual(edit(before, { loraAdapters: [] }), ['--future', '', '--swa-full']);
  assert.throws(() => edit(before, { loraAdapters: adapters.slice(0, 1) }), /interleaved.*Extra arguments/i);
});

test('contiguous LoRA groups can change size and inline scaled syntax is understood', () => {
  const before = ['--x', '--lora-scaled=a', '-1', '--lora', 'b', '--tail'];
  assert.deepEqual(edit(before, { loraAdapters: [{ path: 'z', scale: '0' }] }), ['--x', '--lora-scaled', 'z', '0', '--tail']);
});

test('owned LoRA rejects missing paths, scales, and nonfinite scales without consuming flags', () => {
  for (const tokens of [['--lora'], ['--lora', ''], ['--lora', '--swa-full'], ['--lora-scaled', 'a'], ['--lora-scaled=a', '--swa-full'], ['--lora-scaled', 'a', 'Infinity'], ['--lora-scaled', 'a', 'NaN']]) {
    assert.throws(() => edit(tokens, { loraAdapters: [] }), /loraAdapters.*Extra arguments/i);
  }
  for (const scale of ['Infinity', '-Infinity', 'NaN', 'bad', '1e999']) {
    assert.throws(() => edit([], { loraAdapters: [{ path: 'a', scale }] }), /loraAdapters.*finite/i);
  }
  assert.throws(() => edit([], { loraAdapters: [{ path: '', scale: '1' }] }), /loraAdapters.*path/i);
});

test('flag-like literal LoRA paths use explicit inline syntax; ambiguous separated paths refuse', () => {
  assert.deepEqual(edit(['--lora=--swa-full', '--swa-full'], { swaMode: 'compact' }), ['--lora=--swa-full', '--swa-compact']);
  assert.deepEqual(edit([], { loraAdapters: [{ path: '-adapter.gguf', scale: '-1' }] }), ['--lora-scaled=-adapter.gguf', '-1']);
  assert.throws(() => edit(['--lora', '--swa-full'], { swaMode: 'compact' }), /ambiguous.*Extra arguments/i);
});

test('patch validation refuses null, inherited fields, unknown keys, and invalid values', () => {
  for (const patch of [null, [], Object.create({ swaMode: 'full' }), JSON.parse('{"__proto__":{}}'), { otherTokens: [] }, { constructor: 'x' }, { swaMode: null }, { swaMode: undefined }, { swaMode: 'bogus' }, { headMode: 'bogus' }, { pooling: 'bogus' }, { loraAdapters: null }, { templateKwargsText: null }]) {
    assert.throws(() => patchPrimaExtras('[]', patch), /Cannot edit.*Extra arguments/i);
  }
  assert.equal(patchPrimaExtras(' untouched ', Object.create(null)), ' untouched ');
});

test('bad raw JSON and open quotes fail actionable edits but not no-ops', () => {
  for (const raw of ['[broken', '[1]', '--future "open']) {
    assert.throws(() => patchPrimaExtras(raw, { swaMode: 'full' }), /Extra arguments/i);
    assert.equal(patchPrimaExtras(raw, {}), raw);
  }
});

test('missing recognized values are preserved on safe unrelated edits', () => {
  assert.deepEqual(edit(['--pooling', '--swa-full'], { swaMode: 'compact' }), ['--pooling', '--swa-compact']);
  assert.deepEqual(edit(['--swa-full', '--lora-scaled', 'a', '--future'], { swaMode: 'compact' }), ['--swa-compact', '--lora-scaled', 'a', '--future']);
});

test('end-of-options literals stay opaque and new flags are inserted before them', () => {
  assert.deepEqual(edit(['--', '--swa-full', ''], { swaMode: 'compact' }), ['--swa-compact', '--', '--swa-full', '']);
});

test('multiple newly introduced groups keep patch order and all edits are atomic', () => {
  assert.deepEqual(edit(['--future', 'x'], { swaMode: 'full', pooling: 'mean' }), ['--future', 'x', '--swa-full', '--pooling', 'mean']);
  const original = '[ "--swa-full", "--pooling=bogus" ]';
  const patch = Object.freeze({ swaMode: 'compact', pooling: 'mean' });
  assert.throws(() => patchPrimaExtras(original, patch), /pooling.*Extra arguments/i);
  assert.equal(original, '[ "--swa-full", "--pooling=bogus" ]');
});

test('free-text input uses the existing tokenizer and preserves literal Windows paths', () => {
  const raw = String.raw`--future '' --lora ' C:\models\adapter.gguf ' --swa-full`;
  assert.deepEqual(parseArgumentText(patchPrimaExtras(raw, { swaMode: 'compact' })), ['--future', '', '--lora', ' C:\\models\\adapter.gguf ', '--swa-compact']);
});

test('unrelated Prima edit preserves token order and literal adapter path', () => {
  const before = ['--future-option', 'x', '--lora', ' C:\\models\\adapter.gguf ', '--swa-full', '--another', ''];
  assert.deepEqual(edit(before, { swaMode: 'compact' }), [
    '--future-option', 'x', '--lora', ' C:\\models\\adapter.gguf ', '--swa-compact', '--another', '',
  ]);
});
