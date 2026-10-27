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
  let conflictScoped = false;
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
      requests.push({ path, method: options.method ?? 'GET', body, authorization: options.headers?.Authorization });
      if (path === '/v2/credentials/api-token') return respond({api_token:'fixture-token'});
      if (path.startsWith('/v2/inference/capabilities')) return respond({ threads_max: 8, parameters: [], templates: [] });
      if (path.startsWith('/v2/inference/engine-model-settings/primacpp/')) {
        if (options.headers?.Authorization !== 'Bearer fixture-token' || failSettings) return respond({detail:{code:'UNAUTHORIZED',message:'Token denied'}},401);
        if (options.method === 'PUT') return conflictScoped ? respond({detail:{code:'MODEL_SETTINGS_CHANGED',message:'Profile changed'}},409) : respond({ revision: 2, requested_parameter_set: body.parameter_set, parameter_set: body.parameter_set });
        return respond({ revision: 1, requested_parameter_set: { context: 4096, cache_type_k: 'f16', cache_type_v: 'f16', flash_attention: 'off', node_shares: { m: 1 }, extra_args: ['--lora', ' spaced '] }, parameter_set: {}, legacy_parameter_set: { context: 1234 } });
      }
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
    store.run.temperature = 0.7;
    const editingSave = store.run.saveModelSettings();
    store.run.temperature = 0.9;
    resolveSave();
    await editingSave;
    assert.equal(store.run.temperature, 0.9, 'save acknowledgment must retain a newer same-model draft');
    assert.equal(store.run.savedModelConfig.temperature, 0.7);
    assert.equal(store.run.settingsDirty(), true);
    store.run.revertSavedSettings();
    assert.equal(store.run.temperature, 0.7);
    assert.equal(store.run.settingsDirty(), false);

    store.run.temperature = 1.7;
    const oldSave = store.run.saveModelSettings();
    await store.run.selectModel('b.gguf');
    resolveSave();
    await oldSave;
    assert.equal(store.models.selectedModel, 'b.gguf');
    assert.equal(store.run.temperature, null, 'late save of A must not hydrate B');
    assert.equal(store.run.busy,'','a stale save releases only its own busy state');

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

    store.http.apiToken = '';
    await store.run.setSettingsScope('primacpp', 'a'.repeat(64));
    assert.equal(store.run.settingsLoadError, '', 'engine GET must use authenticated transport');
    assert.equal(requests.findLast(r=>r.path.includes('engine-model-settings') && r.method==='GET').authorization,'Bearer fixture-token');
    failSettings = true;
    const deniedStart = requests.length;
    await store.run.selectModel('b.gguf');
    assert.match(store.run.settingsLoadError,/Token denied/);
    assert.equal(store.run.savedModelConfig,null);
    store.run.useDefaultSettings();
    assert.equal(store.run.settingsLoadError,'','explicit defaults recover after an actual denied load');
    assert.equal(store.run.settingsDirty(),true);
    assert.equal(requests.slice(deniedStart).some(r=>r.path.startsWith('/v2/inference/model-settings/')),false,'authorization denial must not fall back to legacy');
    failSettings = false;
    await store.run.selectModel('b.gguf');
    assert.equal(store.run.cacheTypeK, 'f16');
    assert.equal(store.run.flashAttention, 'off');
    assert.deepEqual(store.run.nodeShares, { m: 1 }, 'manual shares are retained, never converted');
    assert.equal(store.run.nodePriority, null);
    assert.deepEqual(store.run.planPayload().extra_args, ['--lora', ' spaced ']);
    assert.equal(store.run.legacyModelConfig.context, 1234);
    assert.equal(store.run.settingsRevision, 1);
    assert.ok(requests.some(r => r.path.includes('runtime_id=' + 'a'.repeat(64))));
    store.run.temperature = 0;
    store.run.enableThinking = false;
    await store.run.saveModelSettings();
    const scopedSave = requests.findLast(r => r.method === 'PUT');
    assert.match(scopedSave.path, /engine-model-settings\/primacpp\/b.gguf$/);
    assert.equal(scopedSave.body.runtime_id, 'a'.repeat(64));
    assert.equal(scopedSave.body.expected_revision, 1);
    assert.equal(scopedSave.body.parameter_set.temperature, 0);
    assert.equal(scopedSave.body.parameter_set.enable_thinking, false);
    assert.equal(store.run.settingsRevision, 2);
    conflictScoped=true; store.run.temperature=1.1;
    await store.run.saveModelSettings();
    assert.equal(store.run.temperature,1.1);
    assert.equal(store.run.savedModelConfig.temperature,0);
    assert.equal(store.run.settingsRevision,2);
    assert.equal(store.run.settingsDirty(),true,'409 preserves the draft and acknowledged revision');
    conflictScoped=false;
    store.run.temperature = 1.25;
    await store.run.setSettingsScope('primacpp', 'c'.repeat(64));
    assert.equal(store.run.temperature,1.25,'changing preview runtime must not overwrite unsaved intent');
    assert.equal(store.run.settingsDirty(),true);
    store.run.applyParameterSet({cache_type_v:'future-cache',future_knob:7,node_shares:{m:1},required_nodes:['m'],node_priority:['m'],placement_strategy:'auto'});
    assert.deepEqual(store.run.planPayload().required_nodes,['m'],'saved shares must not silently erase explicit required nodes');
    assert.equal(store.run.planPayload().placement_strategy,'auto','contradictory saved intent is not silently repaired');
    assert.equal(store.run.cacheTypeV,'future-cache');
    assert.deepEqual(store.run.unrepresentedSettings,{future_knob:7});
    assert.equal(store.run.validateRunSettings(),false,'unrepresented saved keys must remain visible and block execution');
    const defaultsDiff=store.run.defaultSettingsChanges();
    assert.ok(defaultsDiff.some(row=>row.field==='cacheTypeV' && row.before==='future-cache' && row.after==='f16'));
    assert.equal(store.run.cacheTypeV,'future-cache','previewing Defaults must not mutate the draft');
    store.run.useDefaultSettings();
    for (const row of defaultsDiff) assert.deepEqual(store.run[row.field],row.after,`confirmed defaults match diff: ${row.field}`);
    assert.equal(store.run.settingsDirty(), true);
    assert.equal(store.run.cacheTypeK, 'f16');
    await store.run.setSettingsScope('legacy');
    assert.equal(store.run.settingsScope, 'legacy');
    // Explicit reset actions cannot race an outstanding profile read.
    const beforeLoadFetch=globalThis.fetch;
    let releaseLoad;
    globalThis.fetch=(path,options)=>path.startsWith('/v2/inference/model-settings/') && (!options?.method || options.method==='GET')
      ? new Promise(resolve=>{releaseLoad=()=>resolve(respond({parameter_set:{context:8192}}));}) : beforeLoadFetch(path,options);
    const loading=store.run.selectModel('b.gguf');
    while(!releaseLoad) await new Promise(resolve=>setTimeout(resolve,0));
    const loadingRevision=store.run.editorRevision;
    store.run.useDefaultSettings();
    store.run.revertSavedSettings();
    assert.equal(store.run.editorRevision,loadingRevision,'Defaults and Revert are guarded during loading');
    releaseLoad(); await loading;
    assert.equal(store.run.context,8192);
    assert.equal(store.run.settingsDirty(),false,'ignored reset does not become a false dirty draft');
    globalThis.fetch=beforeLoadFetch;
    const fetchFixture=globalThis.fetch;
    let resolveOldCapabilities,holdCapabilities=true;
    globalThis.fetch=(path,options)=>{
      if (holdCapabilities && path.startsWith('/v2/inference/capabilities')) {
        holdCapabilities=false;
        return new Promise(resolve=>{resolveOldCapabilities=()=>resolve(respond({threads_max:99,parameters:[{id:'stale'}]}));});
      }
      return fetchFixture(path,options);
    };
    const lateA=store.run.selectModel('a.gguf');
    await store.run.selectModel('b.gguf');
    await store.run.selectModel('a.gguf');
    resolveOldCapabilities(); await lateA;
    assert.equal(store.run.threadsMax,8,'A to B to A fences late capability hydration');
    assert.deepEqual(store.run.parameters,[]);
  } finally {
    store?.stop();
    globalThis.fetch = originalFetch;
    await server?.watcher?.close(); await server?.close();
    process.chdir(cwd);
  }
});
