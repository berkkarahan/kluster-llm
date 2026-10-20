import assert from 'node:assert/strict';
import test from 'node:test';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { createServer as createHttpServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { removeBrowserProfile } from './browser-profile.mjs';

const chrome = [process.env.CHROME_BIN, '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium', 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find((path) => path && existsSync(path));

test('run page cache dropdowns and required-node checkbox update the visible plan',
  { skip: !chrome && 'Install Chrome/Edge or set CHROME_BIN' }, async () => {
    const cwd = process.cwd();
    process.chdir(fileURLToPath(new URL('..', import.meta.url)));
    const profile = mkdtempSync(join(tmpdir(), 'kllm-run-planning-'));
    const requests = [];
    const runRequests = [];
    const stopRequests = [];
    const settingsRequests = [];
    let releaseSave, releaseSettingsRead;
    let holdSettingsRead=false;
    const gib = 1024 ** 3;
    let browser, vite, server, ws;
    try {
      vite = await createServer({ server: { middlewareMode: true, hmr: false, ws: false, watch: null }, appType: 'custom' });
      server = createHttpServer((req, res) => {
        void (async () => {
          if (!req.url.startsWith('/v2/')) {
            vite.middlewares(req, res, () => res.writeHead(404).end());
            return;
          }
          let result = {};
          if (req.url.startsWith('/v2/events')) result = {version:1,transport:'websocket',epoch:'11111111-1111-1111-1111-111111111111',server_time:new Date().toISOString(),areas:{},logs:null};
          if (req.url === '/v2/credentials/api-token') result = {api_token:'fixture-token'};
          if (req.url === '/v2/inference/runs/stop' && req.method === 'POST') {
            stopRequests.push(req.url);result={stopped:true};
          }
          if (req.url === '/v2/inference/runs' && req.method === 'POST') {
            const chunks=[]; for await (const chunk of req) chunks.push(chunk);
            const body=JSON.parse(Buffer.concat(chunks).toString()); runRequests.push(body);
            assert.equal(body.settings_source,'defaults');assert.equal(body.remember,false);assert.equal(body.advanced_open,false);
            assert.deepEqual(body.review,{runtime_id:'a'.repeat(64),plan_digest:'b'.repeat(64)});
            result={accepted:true};
          }
          if (req.url.startsWith('/v2/inference/engine-model-settings/primacpp/')) {
            settingsRequests.push({method:req.method,authorization:req.headers.authorization});
            if (req.headers.authorization !== 'Bearer fixture-token') {
              res.writeHead(401,{'Content-Type':'application/json'}).end(JSON.stringify({detail:{code:'UNAUTHORIZED',message:'Token required'}})); return;
            }
            if (req.method === 'GET' && holdSettingsRead) await new Promise(resolve=>{releaseSettingsRead=resolve;});
            if (req.method === 'PUT') {
              const chunks=[]; for await (const chunk of req) chunks.push(chunk);
              const body=JSON.parse(Buffer.concat(chunks).toString());
              await new Promise(resolve=>{releaseSave=resolve;});
              result={revision:1,requested_parameter_set:body.parameter_set,parameter_set:body.parameter_set};
            } else result = {engine:'primacpp',model:'fixture.gguf',revision:0,parameter_set:{},requested_parameter_set:{context:4096,cache_type_k:'f16',cache_type_v:'f16',flash_attention:'off'},legacy_parameter_set:null};
          }
          if (req.url.startsWith('/v2/inference/capabilities')) result = {runtime_id:'a'.repeat(64),engine:'primacpp',deployment_review_version:1,parameters:[],threads_max:8};
          if (req.url.startsWith('/v2/inference/plans') || req.url.endsWith('/prima-plan')) {
            const chunks = [];
            for await (const chunk of req) chunks.push(chunk);
            const body = JSON.parse(Buffer.concat(chunks).toString());
            requests.push(body);
            const quantized = body.cache_type_k === 'q4_0' && body.cache_type_v === 'q4_0';
            result = { ...(req.url.endsWith('/prima-plan') ? { runtime_id:'a'.repeat(64),deployment_review_version:1,plan_digest:'b'.repeat(64),inference_supported:true,
              prima_topology:{ranks:[{node_id:'master',rank:0,layers:[{start:0,end:32}],devices:[],ngl:0}]} } : {}),
              estimated_bytes: (quantized ? 35 : 38) * gib, budget_bytes: 48 * gib,
              fit_status: 'fits', kv_estimate_bytes: (quantized ? 3 : 6) * gib,
              weights_estimate_bytes: 27 * gib, checkpoint_estimate_bytes: 4 * gib,
              cpu_alloc_bytes: 5 * gib, ram_alloc_bytes: 5 * gib, vram_alloc_bytes: 30 * gib,
              node_ram_alloc_bytes: { master: 5 * gib, worker: 0 },
              node_vram_alloc_bytes: { master: 10 * gib, worker: 20 * gib },
              device_allocations: [{ node_id: 'worker', layers: 20, output_layer: true }],
              cpu_layers: 0, estimated_layers: 32,
              resolved_launch_settings: { context: body.context, batch_size: 512, micro_batch: 128,
                parallel_slots: 1, cache_type_k: body.cache_type_k, cache_type_v: body.cache_type_v } };
          }
          res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(result));
        })().catch((error) => { res.writeHead(500).end(String(error)); });
      });
      await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
      const base = `http://127.0.0.1:${server.address().port}`;
      browser = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', '--remote-allow-origins=*',
        '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
      const portfile = join(profile, 'DevToolsActivePort');
      for (let i = 0; i < 100 && !existsSync(portfile); i++) await new Promise((resolve) => setTimeout(resolve, 100));
      assert.ok(existsSync(portfile), 'Chrome DevTools did not start');
      const port = Number(readFileSync(portfile, 'utf8').split('\n')[0]);
      const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      ws = new WebSocket(pages.find((page) => page.type === 'page').webSocketDebuggerUrl);
      await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
      let id = 0;
      const pending = new Map();
      ws.onmessage = ({ data }) => {
        const message = JSON.parse(data);
        pending.get(message.id)?.(message);
      };
      async function call(method, params = {}) {
        const requestId = ++id;
        const answer = new Promise((resolve, reject) => {
          const timer = setTimeout(() => { pending.delete(requestId); reject(new Error(`CDP timeout: ${method}`)); }, 10000);
          pending.set(requestId, (message) => { clearTimeout(timer); pending.delete(requestId); resolve(message); });
        });
        ws.send(JSON.stringify({ id: requestId, method, params }));
        const response = await answer;
        assert.equal(response.error, undefined);
        return response.result;
      }
      async function evaluate(expression) {
        const response = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
        assert.equal(response.exceptionDetails, undefined, JSON.stringify(response.exceptionDetails));
        return response.result.value;
      }
      async function wait(expression) {
        for (let i = 0; i < 200; i++) {
          if (await evaluate(`Boolean(${expression})`)) return;
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        assert.fail(`Browser timed out: ${expression}\n${await evaluate('document.body.innerText')}`);
      }
      await call('Page.navigate', { url: base + '/run' });
      await wait("document.querySelector('#run-placement-mode')");
      await evaluate(`(async () => {
        const { createApp } = await import('/src/lib/app.svelte.ts');
        const appStore = createApp();
        appStore.stop();
        appStore.cluster.status = { role: 'master', node_id: 'master', members: {
          master: { node_id: 'master', role: 'master', host: 'master', devices: [{ index: 0, name: 'Apple M4' }] },
          worker: { node_id: 'worker', role: 'worker', host: 'worker', devices: [{ index: 0, name: 'AMD Radeon 8060S' }] }
        }};
        appStore.models.models = [{ id: 'fixture.gguf' }];
        appStore.models.selectedModel = 'fixture.gguf';
        appStore.live.connected = true;
        appStore.live.deliveryHealth = 'live';
        appStore.live.areaHealth = {status:{availability:'available',coverage:'cluster',last_success_at:new Date().toISOString(),error:null}};
        appStore.run.resetRunSettings();
        appStore.run.advancedOpen = true;
        appStore.run.scheduleEstimate(0);
      })()`);
      await wait("document.body.innerText.includes('K cache: q8_0') && document.body.innerText.includes('6.0 GiB')");
      await evaluate("[...document.querySelectorAll('button')].find(button => button.textContent.trim() === 'Configure').click()");
      await wait("document.querySelector('#run-cache-k')");
      await evaluate("[...document.querySelectorAll('summary')].find(summary => summary.textContent.includes('Memory & loading')).click()");
      await evaluate(`(() => {
        for (const select of [document.querySelector('#run-cache-k'), document.querySelector('#run-cache-v')]) {
          select.value = 'q4_0'; select.dispatchEvent(new Event('change', { bubbles: true }));
        }
      })()`);
      await wait("document.body.innerText.includes('K cache: q4_0') && document.body.innerText.includes('KV cache decreased by 3.0 GiB')");
      assert.equal(requests.at(-1).cache_type_k, 'q4_0');
      assert.equal(requests.at(-1).cache_type_v, 'q4_0');
      await evaluate("[...document.querySelectorAll('button')].find(button => button.textContent.trim() === 'Back').click()");
      await evaluate("document.querySelector('#required-worker').click()");
      await wait("document.querySelector('#required-worker').checked && document.body.innerText.includes('Preview recalculated.')");
      assert.deepEqual(requests.at(-1).required_nodes, ['worker']);
      const text = await evaluate('document.body.innerText');
      assert.match(text, /Apple M4/);
      assert.match(text, /AMD Radeon 8060S/);
      assert.match(text, /CPU RAM: 5\.0 GiB/);
      assert.match(text, /19 model layers/);
      await evaluate(`(() => {
        const select = document.querySelector('#run-placement-mode');
        select.value = 'priority'; select.dispatchEvent(new Event('change', { bubbles: true }));
      })()`);
      await wait("document.querySelector('[aria-label=\"Move worker up\"]')");
      await evaluate("document.querySelector('[aria-label=\"Move worker up\"]').click()");
      await wait(`document.querySelector('[aria-label="Move worker up"]').disabled && document.body.innerText.includes('Preview recalculated.')`);
      assert.deepEqual(requests.at(-1).node_priority, ['worker', 'master']);
      assert.equal(requests.at(-1).node_shares, null);
      assert.deepEqual(requests.at(-1).required_nodes, ['worker']);
      assert.equal(await evaluate("Boolean(document.querySelector('[id^=share-]'))"), false);

      // Switch to Prima mode and verify f16 KV + flash_attention='off' defaults and progress bars.
      await evaluate(`(async () => {
        const { createApp } = await import('/src/lib/app.svelte.ts');
        const appStore = createApp();
        const primaVersion = {
          runtime_id: 'a'.repeat(64),
          name: 'Prima.cpp 0.0.1',
          engine: 'primacpp',
          repository: 'https://github.com/berkkarahan/prima.cpp.git',
          ref: '0.0.1',
          ref_type: 'tag',
          commit: '1'.repeat(40)
        };
        appStore.cluster.status = {
          ...appStore.cluster.status,
          engine: 'primacpp',
          runtimes: {
            versions: [{ version: primaVersion, build: { runtime_id: primaVersion.runtime_id, build_id: 'b1', backend: 'vulkan', commit: primaVersion.commit } }],
            jobs: [],
            selected: primaVersion.runtime_id,
            target: { version: primaVersion, revision: 1, cluster_binding_id: 'c1', master_node_id: 'master' },
            running: null,
            prima_preparation: {
              job_id: 'prep-1',
              phase: 'profiling',
              progress: {
                fraction: 0.65,
                step: 2,
                steps_total: 3,
                label: 'Profiling devices (1/1): master',
                detail: 'Pass 2/2 (VULKAN): running benchmark request…',
                bytes_done: 536870912,
                bytes_total: 1073741824,
                rate_bps: 268435456,
                eta_seconds: 2
              },
              nodes: {
                master: {
                  phase: 'profiling',
                  progress: {
                    fraction: 0.65,
                    step: 2,
                    steps_total: 3,
                    label: 'Profiling Vulkan0',
                    detail: 'Pass 2/2 (VULKAN)'
                  }
                }
              }
            }
          }
        };
        appStore.live.connected = true;
        appStore.live.deliveryHealth = 'live';
        appStore.live.areaHealth = {status:{availability:'available',coverage:'cluster',last_success_at:new Date().toISOString(),error:null}};
        appStore.run.resetRunSettings();
      })()`);
      await wait("document.querySelector('.prima-deploy-page')");
      assert.deepEqual(await evaluate("[...document.querySelectorAll('nav[aria-label=\"Settings sections\"] button')].map(b=>b.textContent.trim())"), ['Overview','Computers','Memory & speed','Responses & features','Expert']);
      assert.deepEqual(await evaluate("[...document.querySelectorAll('[role=tab]')].map(b=>b.textContent)"),['Deployment settings','Next deployment']);
      assert.equal(await evaluate("document.querySelector('#prima-tab-settings').getAttribute('aria-selected')"),'true');
      await evaluate("document.querySelector('#prima-tab-deployment').click()");
      await wait("document.querySelector('.prima-deploy-page') && document.body.innerText.includes('K cache: f16')");
      assert.equal(requests.at(-1).cache_type_k, 'f16');
      assert.equal(requests.at(-1).cache_type_v, 'f16');
      assert.equal(requests.at(-1).flash_attention, 'off');

      // Preparation evidence is visible in the main deployment tab.
      await evaluate("[...document.querySelectorAll('nav[aria-label=\"Settings sections\"] button')].find(b=>b.textContent==='Memory & speed').click()");
      await wait("document.querySelector('[aria-label=\"Prima preparation and profiling progress\"]')");
      const prepVal = await evaluate("document.querySelector('[aria-label=\"Prima preparation and profiling progress\"]').getAttribute('aria-valuenow')");
      assert.equal(prepVal, '65');
      const step3Text = await evaluate('document.body.innerText');
      assert.match(step3Text, /Step 2 of 3/);
      assert.match(step3Text, /Pass 2\/2 \(VULKAN\)/);

      assert.ok(settingsRequests.some(r=>r.method==='GET' && r.authorization==='Bearer fixture-token'));
      await evaluate("import('/src/lib/app.svelte.ts').then(({createApp})=>{window.testApp=createApp();})");
      // Loading blocks explicit resets in the real UI as well as at the action boundary.
      holdSettingsRead=true;
      await evaluate("window.pendingSettings=window.testApp.run.selectModel('fixture.gguf');void 0");
      for(let i=0;i<100 && !releaseSettingsRead;i++) await new Promise(resolve=>setTimeout(resolve,25));
      assert.equal(typeof releaseSettingsRead,'function');
      for(const label of ['Defaults…','Revert']) {
        assert.equal(await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent===${JSON.stringify(label)}).disabled`),true,label+' disabled during loading');
        await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent===${JSON.stringify(label)}).click()`);
      }
      assert.equal(await evaluate("!!document.querySelector('dialog[open]')"),false);
      holdSettingsRead=false;releaseSettingsRead();
      await evaluate('window.pendingSettings');
      assert.equal(await evaluate('window.testApp.run.settingsDirty()'),false,'no reset was accepted while loading');
      assert.equal(await evaluate('window.testApp.run.context'),4096);
      // Shared status controls are observation-only after a role downgrade.
      await evaluate("window.testApp.cluster.status.role='worker'");
      assert.equal(await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Cancel preparation').disabled"),true);
      assert.match(await evaluate('document.body.innerText'),/Only the master can cancel preparation/);
      await evaluate("window.testApp.cluster.status.role='master'");
      assert.equal(await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Cancel preparation').disabled"),false);
      await evaluate("document.querySelector('#prima-tab-settings').click()");
      // Search must focus an enabled prerequisite without changing draft intent.
      await evaluate("import('/src/lib/app.svelte.ts').then(({createApp})=>{window.testApp=createApp();window.testApp.run.contextAuto=true;})");
      const searchPayload=await evaluate('JSON.stringify(window.testApp.run.planPayload())');
      for(const [query,target] of [['Total context','run-context-auto'],['Maximum draft tokens','run-speculative'],['Pooling','run-prima-head-mode']]) {
        await evaluate(`(()=>{const e=document.querySelector('#prima-setting-search');e.value=${JSON.stringify(query)};e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
        await wait("document.querySelector('[aria-label=\"Matching settings\"] button')");
        await evaluate("document.querySelector('[aria-label=\"Matching settings\"] button').click()");
        assert.equal(await evaluate('document.activeElement?.id'),target,query+' focuses a visible enabling control');
        assert.equal(await evaluate('document.activeElement.matches(":disabled")'),false);
        assert.equal(await evaluate('document.activeElement.getBoundingClientRect().height > 0'),true);
        assert.equal(await evaluate('JSON.stringify(window.testApp.run.planPayload())'),searchPayload,'search never changes semantic intent');
      }
      await evaluate("window.testApp.run.contextAuto=false");
      await evaluate("(()=>{const e=document.querySelector('#prima-setting-search');e.value='--';e.dispatchEvent(new Event('input',{bubbles:true}));})()");
      const advertised=await evaluate("[...document.querySelectorAll('[aria-label=\"Matching settings\"] button')].map(b=>b.textContent)");
      assert.ok(advertised.length>20,'exercise the full advertised search surface');
      for(const label of advertised) {
        await evaluate(`(()=>{const e=document.querySelector('#prima-setting-search');e.value='--';e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
        await evaluate(`[...document.querySelectorAll('[aria-label="Matching settings"] button')].find(b=>b.textContent===${JSON.stringify(label)}).click()`);
        assert.equal(await evaluate("!!document.activeElement?.id && document.activeElement.id !== 'prima-setting-search' && !document.activeElement.matches(':disabled') && document.activeElement.getBoundingClientRect().height>0"),true,'accessible search target: '+label);
      }
      // Search reveals the real control; invalid local text remains mounted and blocks operations.
      await evaluate(`(() => { const e=document.querySelector('#prima-setting-search');e.value='--chat-template-kwargs';e.dispatchEvent(new Event('input',{bubbles:true})); })()`);
      await wait("document.querySelector('[aria-label=\"Matching settings\"] button')");
      await evaluate("document.querySelector('[aria-label=\"Matching settings\"] button').click()");
      await wait("document.activeElement?.id === 'run-prima-template-kwargs'");
      await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Save draft').click()");
      for(let i=0;i<100 && !releaseSave;i++) await new Promise(resolve=>setTimeout(resolve,25));
      assert.equal(typeof releaseSave,'function','PUT must be in flight');
      await evaluate(`(() => {const e=document.querySelector('#run-prima-template-kwargs');e.value='{first broken';e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
      await wait("document.querySelector('#run-prima-template-kwargs').getAttribute('aria-invalid') === 'true'");
      const firstError=await evaluate("document.querySelector('#run-prima-template-kwargs').getAttribute('aria-invalid')");
      const firstRawRevision=await evaluate('window.testApp.run.rawDraftRevision');
      await evaluate(`(() => {const e=document.querySelector('#run-prima-template-kwargs');e.value='{broken';e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
      assert.equal(await evaluate("document.querySelector('#run-prima-template-kwargs').getAttribute('aria-invalid')"),firstError);
      assert.ok(await evaluate('window.testApp.run.rawDraftRevision')>firstRawRevision,'different invalid text advances raw revision even with the same error');
      releaseSave();
      await wait("[...document.querySelectorAll('button')].some(b=>b.textContent==='Save draft') && !document.body.innerText.includes('Saving…')");
      await evaluate("import('/src/lib/app.svelte.ts').then(({createApp})=>{window.testApp=createApp();})");
      await wait("window.testApp.run.settingsRevision === 1 && !window.testApp.run.busy");
      assert.equal(await evaluate('window.testApp.run.settingsDirty()'),true,'newer invalid raw edit stays dirty');
      assert.equal(await evaluate("document.querySelector('#run-prima-template-kwargs').value"),'{broken','save acknowledgment must retain newer invalid raw text');
      await wait("document.querySelector('#run-prima-template-kwargs').getAttribute('aria-invalid') === 'true'");
      assert.equal(await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Save draft').disabled"),true);
      await evaluate("document.querySelector('#prima-tab-deployment').click()");
      assert.equal(await evaluate("document.querySelector('#run-prima-template-kwargs').value"),'{broken','tab switch preserves invalid raw input');
      await evaluate("[...document.querySelectorAll('#prima-panel-deployment button')].find(b=>b.textContent.includes('JSON object')).click()");
      await wait("document.activeElement?.id === 'run-prima-template-kwargs'");
      assert.equal(await evaluate("document.querySelector('#prima-tab-settings').getAttribute('aria-selected')"),'true');
      await evaluate("[...document.querySelectorAll('nav[aria-label=\"Settings sections\"] button')].find(b=>b.textContent.startsWith('Overview')).click()");
      await evaluate("[...document.querySelectorAll('.issues button')].find(b=>b.textContent.includes('JSON object')).click()");
      await wait("document.activeElement?.id === 'run-prima-template-kwargs'");
      assert.equal(await evaluate('document.activeElement.value'),'{broken');
      await evaluate(`(() => {const e=document.activeElement;e.value='{}';e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
      await wait("!document.querySelector('.issues')");
      // Stop/LoRA drafts also have raw state not represented by the executable payload.
      for(const rawKind of ['stop','lora']) {
        releaseSave=undefined;
        const acknowledgedBefore=await evaluate('window.testApp.run.editorRevision');
        await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Save draft').click()");
        for(let i=0;i<100 && !releaseSave;i++) await new Promise(resolve=>setTimeout(resolve,25));
        assert.equal(typeof releaseSave,'function',rawKind+' PUT started');
        if(rawKind==='stop') await evaluate("(()=>{const e=document.querySelector('#run-stop');e.closest('details').open=true;e.value='{invalid stop';e.dispatchEvent(new Event('input',{bubbles:true}));})()");
        else await evaluate("document.querySelector('#prima-lora-add').click()");
        await wait("window.testApp.run.settingsDirty() && document.querySelector('.issues')");
        releaseSave();
        await wait("!window.testApp.run.busy");
        assert.equal(await evaluate('window.testApp.run.editorRevision'),acknowledgedBefore,'save must not rehydrate newer '+rawKind+' draft');
        if(rawKind==='stop') {
          assert.equal(await evaluate("document.querySelector('#run-stop').value"),'{invalid stop');
          await evaluate("(()=>{const e=document.querySelector('#run-stop');e.value='';e.dispatchEvent(new Event('input',{bubbles:true}));})()");
        } else {
          assert.equal(await evaluate("document.querySelector('.lora-path').value"),'');
          await evaluate("document.querySelector('[aria-label=\"Remove LoRA adapter 1\"]').click()");
        }
        await wait("!document.querySelector('.issues')");
      }
      await evaluate(`(() => {const e=document.querySelector('#prima-setting-search');e.value='Concurrent requests';e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
      await wait("document.querySelector('[aria-label=\"Matching settings\"] button')");
      await evaluate("document.querySelector('[aria-label=\"Matching settings\"] button').click()");
      await wait("document.activeElement?.id === 'run-parallel-slots-mode'");
      await new Promise(resolve=>setTimeout(resolve,400));
      const beforeDisclosure=requests.length;
      await evaluate("document.querySelector('.technical input').click()");
      await new Promise(resolve=>setTimeout(resolve,400));
      assert.equal(requests.length,beforeDisclosure,'technical disclosure does not schedule a plan');

      // A failed placement preview must stop preparation before a job POST,
      // including the automatic inspection performed by Prepare & profile.
      await evaluate(`(async () => {
        const { createApp } = await import('/src/lib/app.svelte.ts');
        const appStore = createApp();
        appStore.cluster.status.runtimes.prima_preparation.phase = 'prepared';
        appStore.cluster.status.runtimes.prima_preparation.local_work_pending = false;
        appStore.runtimes.preparation.observe({...appStore.cluster.status.runtimes.prima_preparation});
        window.preparationCalls = 0;
        await new Promise(resolve=>setTimeout(resolve,0));
        let failPlanning = true;
        appStore.runtimes.primaPlan = async () => {
          if (failPlanning) { failPlanning=false; throw new Error('Fixture planning unavailable'); }
          return {runtime_id:'a'.repeat(64), plan_digest:'blocked',prima_topology:null,
            placement_errors:['Prima remote MoE tensor placement needs compatibility implementation']};
        };
        appStore.runtimes.preparePrima = async () => { window.preparationCalls++; return {}; };
        appStore.run.context = 8192;
        document.querySelector('#prima-tab-deployment').click();
        document.querySelectorAll('button').forEach(b=>{if(b.textContent==='Prepare & profile') b.click();});
      })()`);
      await wait("document.body.innerText.includes('Fixture planning unavailable')");
      assert.equal(await evaluate('window.preparationCalls'),0,'failed automatic planning never prepares');
      assert.equal(await evaluate("[...document.querySelectorAll('button')].some(b=>b.textContent==='Inspect placement')"),false,'inspection is not a user step');
      await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Retry planning').click()");
      await wait("document.body.innerText.includes('Prima remote MoE tensor placement needs compatibility implementation')");
      assert.equal(await evaluate('window.preparationCalls'), 0);
      for (const width of [320, 390, 768, 1440]) {
        await call('Emulation.setDeviceMetricsOverride', {width, height: 900, deviceScaleFactor: 1, mobile: false});
        assert.equal(await evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Prepare & profile').disabled"), true);
        assert.equal(await evaluate("[...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Inspect placement')"), false);
        assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true,`No deployment overflow at ${width}px`);
        await evaluate("document.querySelector('#prima-tab-settings').click()");
        for(const title of ['Overview','Computers','Memory & speed','Responses & features','Expert']) {
          await evaluate(`[...document.querySelectorAll('nav[aria-label="Settings sections"] button')].find(b=>b.textContent.startsWith(${JSON.stringify(title)})).click()`);
          assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true,`No page overflow at ${width}px in ${title}`);
        }
      }

      await evaluate("document.querySelector('#prima-tab-deployment').click()");
      // A qualified runtime opens an inline immutable review.
      await evaluate(`(async()=>{
        const {createApp}=await import('/src/lib/app.svelte.ts');const app=createApp();
        app.cluster.status.runtimes.nodes=[{node_id:'master',phase:'ready',prepared:{runtime_id:'a'.repeat(64)}},{node_id:'worker',phase:'offline'}];
        app.runtimes.primaPlan=Object.getPrototypeOf(app.runtimes).primaPlan.bind(app.runtimes);
        app.http.refresh=async()=>{};app.http.loadModels=async()=>{};
        app.run.context=12288;app.run.scheduleEstimate();
      })()`);
      await wait("[...document.querySelectorAll('button')].some(b=>b.textContent==='Review deployment' && !b.disabled)");
      // Live lifecycle revokes an open review and exposes the existing explicit Stop action.
      for(const activation of [
        "window.testApp.cluster.status.inference_up=true",
        "window.testApp.cluster.status.process_pid=321",
        "window.testApp.run.loadProgress={phase:'loading',model:'fixture.gguf',operation_id:'new-load',fraction:0.2,bytes_loaded:2,bytes_total:10}"
      ]) {
        await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Review deployment').click()");
        await wait("document.querySelector('.inline-review')");
        assert.doesNotMatch(await evaluate("document.querySelector('.inline-review').innerText"),/replaces the running model/i);
        const stopsBefore=stopRequests.length;
        await evaluate(activation);
        await wait("!document.querySelector('.inline-review')");
        assert.equal(await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Review deployment').disabled"),true);
        assert.match(await evaluate('document.body.innerText'),/stop model/i);
        assert.equal(runRequests.length,0,'live transition must not launch');
        assert.equal(stopRequests.length,stopsBefore,'live transition must not stop automatically');
        await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Stop model').click()");
        await wait("!window.testApp.run.busy");
        assert.equal(stopRequests.length,stopsBefore+1,'only the explicit Stop button sends the existing command');
        assert.equal(await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Review deployment').disabled"),true,'Stop response alone is not settled status');
        await evaluate("window.testApp.cluster.status.inference_up=false;window.testApp.cluster.status.process_pid=null;window.testApp.run.loadProgress={phase:'cancelled',operation_id:'new-load',fraction:0,bytes_loaded:0,bytes_total:10}");
        await evaluate("window.testApp.runtimes.deployment.inspect(true)");
        await wait("[...document.querySelectorAll('button')].some(b=>b.textContent==='Review deployment' && !b.disabled)");
      }
      if(process.env.KLLM_PRIMA_SCREENSHOTS) {
        mkdirSync(process.env.KLLM_PRIMA_SCREENSHOTS,{recursive:true});
        await evaluate("[...document.querySelectorAll('nav[aria-label=\"Settings sections\"] button')].find(b=>b.textContent==='Overview').click();window.scrollTo(0,0)");
        for(const width of [1440,390]) {
          await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
          await evaluate("document.querySelector('[aria-label=\"Close menu\"]')?.click();document.querySelectorAll('*').forEach(e=>{if(e.scrollTop)e.scrollTop=0;})");
          await new Promise(resolve=>setTimeout(resolve,350));
          const screenshot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
          writeFileSync(join(process.env.KLLM_PRIMA_SCREENSHOTS,`prima-${width}.png`),Buffer.from(screenshot.data,'base64'));
        }
        await call('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});
      }
      await evaluate("(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent==='Review deployment');b.focus();b.click();})()");
      await wait("document.querySelector('.inline-review')");
      assert.match(await evaluate("document.querySelector('.inline-review').innerText"),/12288/);
      assert.equal(await evaluate("[...document.querySelectorAll('.inline-review button')].find(b=>b.textContent==='Run reviewed deployment').disabled"),true);
      assert.equal(await evaluate("!!document.querySelector('dialog[open]')"),false,'review never opens a modal');
      await evaluate("[...document.querySelectorAll('.inline-review button')].find(b=>b.textContent==='Back to editing').click()");
      await wait("!document.querySelector('.inline-review')");
      assert.equal(await evaluate('document.activeElement.id'),'prima-tab-settings');
      assert.equal(await evaluate("document.querySelector('#run-context').value"),'12288');
      await evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}))");
      assert.equal(await evaluate('document.activeElement.id'),'prima-tab-deployment');
      await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Review deployment').click()");
      await wait("document.querySelector('.inline-review input[type=checkbox]')");
      for(const width of [390,768,1440]) {
        await call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});
        assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true,`No review overflow at ${width}px`);
        if(process.env.KLLM_PRIMA_SCREENSHOTS) {
          await evaluate("document.querySelector('[aria-label=\"Close menu\"]')?.click();document.querySelector('.inline-review').scrollIntoView()");
          await new Promise(resolve=>setTimeout(resolve,350));
          const screenshot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
          writeFileSync(join(process.env.KLLM_PRIMA_SCREENSHOTS,`prima-review-${width}.png`),Buffer.from(screenshot.data,'base64'));
        }
      }
      await evaluate("document.querySelector('.inline-review input[type=checkbox]').click()");
      await evaluate("document.querySelector('#prima-tab-settings').click();document.querySelector('#prima-tab-deployment').click()");
      assert.equal(await evaluate("document.querySelector('.inline-review input[type=checkbox]').checked"),true,'navigation preserves the current review');

      await evaluate("window.testApp.run.context=16384;window.testApp.run.scheduleEstimate()");
      await wait("!document.querySelector('.inline-review')");
      await evaluate("window.testApp.run.context=12288;window.testApp.run.scheduleEstimate()");
      await wait("[...document.querySelectorAll('button')].some(b=>b.textContent==='Review deployment' && !b.disabled)");
      await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Review deployment').click()");
      await wait("document.querySelector('.inline-review input[type=checkbox]')");
      assert.equal(await evaluate("document.querySelector('.inline-review input[type=checkbox]').checked"),false,'a new snapshot requires fresh confirmation');
      await evaluate("document.querySelector('.inline-review input[type=checkbox]').click()");
      await wait("[...document.querySelectorAll('.inline-review button')].some(b=>b.textContent==='Run reviewed deployment' && !b.disabled)");
      await evaluate("[...document.querySelectorAll('.inline-review button')].find(b=>b.textContent==='Run reviewed deployment').click()");
      await wait("!document.querySelector('.inline-review')");
      assert.equal(runRequests.length,1,'one explicit guarded launch through the real HTTP gateway: '+await evaluate("document.body.innerText"));
      assert.equal(runRequests[0].context,12288);

      await evaluate("document.querySelector('#prima-tab-settings').click()");
      // Defaults previews the complete diff without changing the draft, then resets invalid local text.
      await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Defaults…').click()");
      await wait("document.querySelector('dialog[open][aria-labelledby=\"prima-defaults-title\"]')");
      assert.match(await evaluate("document.querySelector('dialog[open]').innerText"),/12288.*4096/);
      assert.equal(await evaluate("document.querySelector('#run-context').value"),'12288');
      await evaluate("[...document.querySelectorAll('dialog[open] button')].find(b=>b.textContent==='Restore to draft').click()");
      await wait("document.querySelector('#run-context').value === '4096'");
      assert.match(await evaluate('document.body.innerText'),/Unsaved changes/);

      // Download/load progress remains visible without navigating away from the workspace.
      await evaluate(`(async () => {
        const { createApp } = await import('/src/lib/app.svelte.ts');
        const appStore = createApp();
        appStore.run.busy = '/v2/inference/runs';
        appStore.run.loadProgress = {
          phase: 'starting',
          model: 'fixture.gguf',
          bytes_loaded: 536870912,
          bytes_total: 1073741824,
          fraction: 0.42,
          rate_bps: 134217728,
          eta_seconds: 4,
          detail: 'Starting Ring-TCP rank 1/2 (master)…'
        };
      })()`);
      await wait("document.querySelector('.deploy-progress-slot [aria-label=\"Model load\"]')");
      const loadVal = await evaluate("document.querySelector('.deploy-progress-slot [aria-label=\"Model load\"]').getAttribute('aria-valuenow')");
      assert.equal(loadVal, '42');
      assert.match(await evaluate('document.body.innerText'), /Starting Ring-TCP rank 1\/2 \(master\)…/);
    } finally {
      releaseSave?.();releaseSettingsRead?.();
      ws?.close();
      if (browser && browser.exitCode === null) {
        const exited = new Promise((resolve) => browser.once('exit', resolve));
        browser.kill(); await exited;
      }
      await vite?.watcher?.close(); await vite?.close();
      if (server) await new Promise((resolve) => server.close(resolve));
      await removeBrowserProfile(profile);
      process.chdir(cwd);
    }
  });

