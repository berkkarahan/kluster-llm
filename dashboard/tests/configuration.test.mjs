import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

test('configuration round-trips, isolates models and chat, and preserves explicit save semantics', async () => {
  const cwd = process.cwd();
  const originalFetch = globalThis.fetch;
  let server, store;
  const requests = [];
  let failSettings = false;
  let resolveSave;
  let delaySave = false;
  const profiles = {
    'a.gguf': { context: 8192, temperature: 1.5, draft_model: '/a/draft.gguf', mmproj: '/a/projector.gguf',
      chat_template: 'custom', chat_template_text: 'MODEL A TEMPLATE', vram_percent: 55, ram_percent: 60,
      seed: 17, max_output_tokens: 128, frequency_penalty: -0.5, stop: [' END\n'], gpu_ids: [1],
      extra_args: ['--lora', 'C:\\models\\my adapter.gguf'] },
    'b.gguf': { context: 4096, temperature: null, draft_model: null, mmproj: null,
      chat_template: 'default', chat_template_text: null, vram_percent: null, ram_percent: null },
  };
  const respond = (data, status = 200) => new Response(JSON.stringify(data), { status });
  try {
    process.chdir(fileURLToPath(new URL('..', import.meta.url)));
    server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false, watch: null }, appType: 'custom' });
    globalThis.fetch = async (path, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push({ path, method: options.method ?? 'GET', body });
      if (path.startsWith('/v2/inference/capabilities')) return respond({ threads_max: 8, parameters: [], templates: [] });
      if (path.startsWith('/v2/inference/model-settings/')) {
        const model = decodeURIComponent(path.split('/').at(-1));
        if (options.method === 'PUT') {
          if (delaySave) return new Promise(resolve => { resolveSave = () => resolve(respond({ parameter_set: body.parameter_set })); });
          profiles[model] = body.parameter_set;
          return respond({ model, parameter_set: profiles[model] });
        }
        return failSettings ? respond({ error: 'unavailable' }, 503) : respond({ parameter_set: profiles[model] ?? {} });
      }
      if (path.startsWith('/v2/inference/plans')) return respond({ fit_status: 'fits', estimated_bytes: 1024, budget_bytes: 4096 });
      if (path === '/v1/chat/completions') return new Response('data: {"choices":[{"delta":{"content":"done"}}]}\ndata: [DONE]\n',
        { headers: { 'Content-Type': 'text/event-stream' } });
      if (path === '/v2/models') return respond({ data: [{ id: 'a.gguf' }, { id: 'b.gguf' }] });
      return respond({ role: 'master', node_id: 'm', members: { m: { node_id: 'm' } } });
    };
    store = (await server.ssrLoadModule('/src/lib/app.svelte.ts')).createApp();
    const { parseArgumentText, formatArguments } = await server.ssrLoadModule('/src/lib/modules/run/configuration.ts');
    assert.deepEqual(parseArgumentText('--lora "/models/my adapter.gguf" --frequency-penalty -0.5'),
      ['--lora', '/models/my adapter.gguf', '--frequency-penalty', '-0.5']);
    const literal = ['--chat-template-kwargs', '{"value":"a\\nb; \\\"quoted\\\""}', '--lora', 'C:\\a b\\adapter.gguf'];
    assert.deepEqual(parseArgumentText(formatArguments(literal)), literal);
    assert.throws(() => parseArgumentText('--lora "unterminated'), /Close the quote/);
    store.cluster.status = { role: 'master', node_id: 'm', members: { m: { node_id: 'm' } } };
    store.models.models = [{ id: 'a.gguf' }, { id: 'b.gguf' }];
    await store.run.selectModel('a.gguf');
    assert.equal(store.run.settingsDirty(), false);
    assert.equal(store.run.runVramPercent, 55);
    assert.equal(store.run.runRamPercent, 60);
    assert.deepEqual(store.run.runGpuIds, [1]);
    assert.equal(store.settings.vramPercent, 80, 'model budget must not overwrite node settings');
    assert.deepEqual(store.run.planPayload().extra_args, profiles['a.gguf'].extra_args);
    assert.deepEqual(store.run.planPayload().stop, [' END\n']);
    assert.equal(store.run.seed, 17);
    store.run.resetRunSettings();
    assert.equal(store.run.settingsDirty(), true, 'restoring defaults leaves a dirty draft');
    store.run.revertSavedSettings();
    assert.equal(store.run.temperature, 1.5);
    assert.equal(store.run.settingsDirty(), false);
    await store.run.selectModel('b.gguf');
    assert.equal(store.run.draftModel, '');
    assert.equal(store.run.mmproj, '');
    assert.equal(store.run.chatTemplateText, '');
    assert.equal(store.run.seed, null);
    assert.equal(store.run.runGpuIds, null);
    assert.equal(store.run.planPayload().chat_template_text, null);
    assert.equal(store.run.settingsDirty(), false);

    failSettings = true;
    await store.run.selectModel('a.gguf');
    assert.equal(store.run.draftModel, '', 'failed fetch starts from this model’s clean defaults');
    assert.match(store.run.settingsLoadError, /Could not load saved settings/);
    assert.equal(store.run.planRunBlockedReason(), store.run.settingsLoadError);
    failSettings = false;
    store.run.useDefaultSettings();
    assert.equal(store.run.settingsLoadError, '');
    store.run.temperature = 0.4;
    await store.run.saveModelSettings();
    assert.equal(store.run.settingsDirty(), false);
    const saved = requests.findLast(item => item.method === 'PUT');
    assert.equal(saved.body.parameter_set.temperature, 0.4);
    assert.equal('settings_source' in saved.body.parameter_set, false);
    assert.equal('remember' in saved.body.parameter_set, false);
    assert.equal(requests.some(item => item.path === '/v2/node/config' && item.method === 'PUT'), false);
    store.run.remember = true;
    await store.run.plan();
    assert.equal(requests.findLast(item => item.path === '/v2/inference/plans').body.remember, false);

    delaySave = true;
    store.run.temperature = 1.7;
    const oldSave = store.run.saveModelSettings();
    await store.run.selectModel('b.gguf');
    resolveSave();
    await oldSave;
    assert.equal(store.models.selectedModel, 'b.gguf');
    assert.equal(store.run.temperature, null, 'late save of A must not hydrate B');

    store.cluster.status = { role: 'master', node_id: 'm', model_name: 'active-a.gguf', inference_up: true,
      members: { m: { node_id: 'm' } } };
    store.http.apiToken = 'fixture-token';
    store.run.temperature = 1.9;
    store.run.enableThinking = true;
    store.integrations.chatInput = 'Hello';
    await store.integrations.sendChat();
    const chat = requests.at(-1).body;
    assert.equal(chat.model, 'active-a.gguf');
    assert.equal('temperature' in chat, false);
    assert.equal('top_p' in chat, false);
    assert.equal('chat_template_kwargs' in chat, false);
    store.integrations.chatTemperature = 0.3;
    store.integrations.chatThinking = false;
    store.integrations.chatOutputLimit = 32;
    store.integrations.chatResponseFormat = 'json_schema';
    store.integrations.chatSchemaText = '{"type":"object"}';
    store.integrations.chatInput = 'JSON please';
    await store.integrations.sendChat();
    assert.equal(requests.at(-1).body.temperature, 0.3);
    assert.equal(requests.at(-1).body.max_tokens, 32);
    assert.equal(requests.at(-1).body.chat_template_kwargs.enable_thinking, false);
    assert.deepEqual(requests.at(-1).body.response_format.json_schema.schema, { type: 'object' });
    const before = requests.length;
    store.integrations.chatSchemaText = '{invalid';
    store.integrations.chatInput = 'No invalid request';
    await store.integrations.sendChat();
    assert.equal(requests.length, before);
  } finally {
    store?.stop();
    globalThis.fetch = originalFetch;
    await server?.watcher?.close(); await server?.close();
    process.chdir(cwd);
  }
});
