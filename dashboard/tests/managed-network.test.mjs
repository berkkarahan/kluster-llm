import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer as createHttpServer } from 'node:http';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'vite';
import { compile } from 'svelte/compiler';
import { removeBrowserProfile } from './browser-profile.mjs';

const chrome = [process.env.CHROME_BIN, 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].find((path) => path && existsSync(path));


// Controlled HTTP contract fixture: platform facts, runtime, operation receipts,
// and status sampling are substituted. Python checks authenticate real agents;
// Rust checks real socket forwarding. This browser case proves app actions only.
test('private network app setup, selected run, blocked stop and cleanup',
  {skip: !chrome && 'Install Chrome/Edge or set CHROME_BIN'}, async () => {
    const cwd = process.cwd();
    process.chdir(fileURLToPath(new URL('..', import.meta.url)));
    const profile = mkdtempSync(join(tmpdir(), 'kllm-network-'));
    let browser, vite, server, ws;
    const requests = [];
    const networkId = '00000000-0000-4000-8000-000000000012';
    const operationId = '00000000-0000-4000-8000-000000000013';
    const adapter = {adapter_id:'eth-test', os_interface:'eth-test', inventory_revision:'a'.repeat(64), link_speed_mbps:10000, dhcp:false, permission:true, physical_ethernet:true, addresses:[]};
    let network = null;
    let operationPending = false;
    let operationRevision = 1;
    let administratorEnabled = false;
    const status = () => ({role:'master',node_id:'master',members:{master:{host:'192.168.68.1'},worker:{host:'192.168.68.2'}},managed_networks:network ? [network] : []});
    const model = {name:'fixture.gguf',path:'fixture.gguf',size_bytes:100};
    try {
      const entry = compile(`<script>
        import ManagedNetwork from '/src/lib/components/ManagedNetwork.svelte';
        import Run from '/src/routes/run/+page.svelte';
        import {appStore} from '/src/lib/store.svelte.ts';
        window.networkStore = appStore;
        appStore.status = ${JSON.stringify(status())};
        appStore.models = [${JSON.stringify(model)}];
        appStore.selectedModel = 'fixture.gguf';
      </script><ManagedNetwork/><Run/>`, {generate:'client',filename:'network-fixture.svelte'}).js.code;
      vite = await createServer({plugins:[{name:'network-fixture',resolveId(id){if(id==='/__network-entry.js'||id==='/__network-mount.js') return id;},load(id){if(id==='/__network-entry.js') return entry;if(id==='/__network-mount.js') return `import {mount} from 'svelte';import App from '/__network-entry.js';mount(App,{target:document.getElementById('app')});`;}}],server:{middlewareMode:true,hmr:false,ws:false,watch:null},appType:'custom'});
      server = createHttpServer((req,res) => { void (async () => {
        const path = req.url.split('?')[0];
        const json = (payload, code=200) => res.writeHead(code,{'Content-Type':'application/json'}).end(JSON.stringify(payload));
        if(path==='/__network-test') {
          res.writeHead(200,{'Content-Type':'text/html'}).end(`<html><body><div id="app"></div><script type="module" src="/__network-mount.js"></script></body></html>`);
          return;
        }
        if(!path.startsWith('/v2/')) {vite.middlewares(req,res,()=>res.writeHead(404).end());return;}
        const chunks=[];for await(const chunk of req) chunks.push(chunk);
        const body=chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : null;
        requests.push({path,method:req.method,body,authorization:req.headers.authorization});
        if(path==='/v2/credentials/api-token') return json({api_token:'fixture-token'});
        if(path==='/v2/node/network-setup/administrator' && req.method==='POST') {
          assert.equal(body.acknowledged,true,'Administrator notice must be acknowledged first');
          administratorEnabled=true; return json({ready:true});
        }
        if(path==='/v2/node/network-setup') return json({administrator_enabled:administratorEnabled,cleanup_pending:[],administrator_notice:'Administrator access is required on each computer. Original settings will be restored.'});
        if(path==='/v2/cluster/network-setup' && req.method==='GET') return json({administrator_notice:'Administrator access is required on each computer. Original settings will be restored.',assessment_revision:'a'.repeat(64),master_node_id:'master',networks:network?[network]:[],computers:['master','worker','worker-b'].map(node_id=>({node_id,name:node_id,host:'192.168.68.1',master:node_id==='master',administrator_enabled:administratorEnabled,adapters:[{...adapter,reason:''}],selected_adapter:'eth-test',ready:administratorEnabled,error_code:administratorEnabled?null:'ADMIN_REQUIRED',message:administratorEnabled?'Ready':'Enable administrator access'}))});
        if(path==='/v2/cluster/network-setup' && req.method==='POST') {
          assert.equal(body.assessment_revision,'a'.repeat(64));
          assert.deepEqual(body.nodes,['master','worker','worker-b']);
          assert.equal(body.subnet,undefined,'Private subnet should be chosen automatically');
          operationPending=false;
          network={network_id:networkId,subnet:'192.168.234.0/24',revision:1,observed_state:'ready',desired_state:'active',participants:body.nodes.map((node_id,i)=>({node_id,address:`192.168.234.${i+1}`,prefix_length:24,state:'ready',ownership:'app_owned',verification_freshness:'fresh'})),blocking_runs:[]};
          return json({network_id:networkId,operation_id:operationId},202);
        }
        if(path===`/v2/cluster/network-setup/operations/${operationId}`) return json({phase:operationPending?'preparing':'complete',operation_revision:operationRevision,terminal_result:operationPending?null:{state:network.observed_state},participants:[]});
        if(path==='/v2/network-test/plain-error') return res.writeHead(500,{'Content-Type':'text/plain'}).end('Internal Server Error');
        if(path==='/v2/network-test/invalid-json') return res.writeHead(200,{'Content-Type':'text/plain'}).end('invalid');
        if(path==='/v2/network-test/refusal') return json({error:{code:'unprocessable_entity',message:'The request could not be completed.',details:{code:'LINK_TOO_SLOW',message:'Negotiated speed must be at least 10000 Mbps'}}},422);
        if(path==='/v2/node/network-identity') return json({node_id:'master',key_id:'b'.repeat(64),public_key:'fake-public-key',cluster_binding_id:networkId,permission:true,paired_peers:[]});
        if(path.startsWith('/v2/node/network-identity/peers/')) return json(body);
        if(path==='/v2/node/network-adapters') return json({node_id:'master',adapters:[adapter],selection:JSON.stringify({adapter_id:'eth-test'})});
        if(path==='/v2/node/network-selection') return json(body);
        if(path==='/v2/node/network-cleanup') return json({journals:[]});
        if(path==='/v2/cluster/network-adapters') return json({nodes:Object.fromEntries(['master','worker','worker-b'].map(node=>[node,{node_id:node,adapters:[adapter],selection:JSON.stringify({adapter_id:'eth-test'})}]))});
        if(path==='/v2/cluster/networks' && req.method==='POST') {
          operationPending=true;
          network={network_id:networkId,subnet:body.subnet,revision:4,observed_state:'preparing',desired_state:'active',current_operation_id:operationId,participants:body.participants.map((p,i)=>({...p,address:`192.168.234.${i+1}`,prefix_length:24,state:'ready',ownership:'app_owned',verification_freshness:'fresh'})),blocking_runs:[]};
          return json({network_id:networkId,operation_id:operationId},202);
        }
        if(path==='/v2/cluster/networks') return json({networks:network?[network]:[]});
        if(path===`/v2/cluster/network-operations/${operationId}`) return json({phase:operationPending?'preparing':'complete',operation_revision:operationRevision,terminal_result:operationPending?null:{state:network.observed_state}});
        if(path===`/v2/cluster/network-operations/${operationId}/cancel`) {operationPending=false;operationRevision=2;network.observed_state='failed';return json({network_id:networkId,operation_id:operationId},202);}
        if(path===`/v2/cluster/networks/${networkId}/operations`) {
          assert.equal(network.blocking_runs.length,0,'UI must stop dependent inference before network cleanup');
          operationPending=false;
          if(body.action==='add') network.participants.push(...body.participants.map((p,i)=>({...p,address:`192.168.234.${network.participants.length+i+1}`,prefix_length:24,state:'ready',ownership:'app_owned',verification_freshness:'fresh'})));
          network={...network,observed_state:body.action==='stop'?'stopped':'ready',desired_state:body.action==='stop'?'stopped':'active',revision:network.revision+1};
          return json({network_id:networkId,operation_id:operationId},202);
        }
        if(path==='/v2/inference/plans') return json({fit_status:'fits'});
        if(path==='/v2/inference/runs') {
          assert.equal(body.network_id,networkId);
          network.blocking_runs=[{run_id:operationId,state:'active'}];
          return json({plan:{fit_status:'ok',network_id:networkId,network_run_id:operationId},launch:{pid:123}});
        }
        if(path==='/v2/inference/runs/stop') {network.blocking_runs=[];return json({ok:true});}
        if(path==='/v2/status'||path==='/v2/node') return json(status());
        if(path==='/v2/models') return json({data:[model]});
        return json({});
      })().catch(error=>res.writeHead(500).end(String(error)));});
      await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
      const base=`http://127.0.0.1:${server.address().port}`;
      browser = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', '--remote-allow-origins=*',
        '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
      const portfile = join(profile, 'DevToolsActivePort');
      for (let i = 0; i < 100 && !existsSync(portfile); i++) await new Promise((resolve) => setTimeout(resolve, 100));
      assert.ok(existsSync(portfile), 'Chrome DevTools did not start');
      const port = Number(readFileSync(portfile, 'utf8').split('\n')[0]);
      const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      ws = new WebSocket(pages.find((page) => page.type === 'page').webSocketDebuggerUrl);
      await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
      const browserErrors=[];
      let id = 0;
      const awaiting = new Map();
      ws.onmessage = ({ data }) => {
        const message = JSON.parse(data);
        if(message.method==='Runtime.exceptionThrown') browserErrors.push(message.params);
        if (awaiting.has(message.id)) { awaiting.get(message.id)(message); awaiting.delete(message.id); }
      };
      async function call(method, params = {}) {
        const requestId = ++id;
        const answer = new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error(`CDP timeout: ${method}`)), 10000);
          awaiting.set(requestId, (message) => { clearTimeout(timer); resolve(message); });
        });
        ws.send(JSON.stringify({ id: requestId, method, params }));
        const response = await answer;
        assert.equal(response.error, undefined, JSON.stringify(response.error));
        return response.result;
      }
      async function evaluate(expression, awaitPromise = false) {
        const response = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise });
        assert.equal(response.exceptionDetails, undefined, JSON.stringify(response.exceptionDetails));
        return response.result.value;
      }
      async function wait(expression) {
        for (let i = 0; i < 80; i++) {
          if (await evaluate(`Boolean(${expression})`)) return;
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        assert.fail(`Browser timed out: ${expression}\n${await evaluate('document.body.innerText')}\n${JSON.stringify(browserErrors)}`);
      }

      await call('Runtime.enable');
      await call('Page.navigate',{url:base+'/__network-test'});
      await wait("document.querySelector('.managed-network summary')");
      for (const [path, expected] of [
        ['plain-error', 'Network request GET /v2/network-test/plain-error returned 500: Internal Server Error'],
        ['invalid-json', 'Network request GET /v2/network-test/invalid-json returned 200: Invalid JSON response'],
        ['refusal', 'LINK_TOO_SLOW: Negotiated speed must be at least 10000 Mbps']
      ]) {
        const message = await evaluate(`window.networkStore.networkRequest('/v2/network-test/${path}').then(() => 'unexpected success', error => error.message)`, true);
        assert.equal(message, expected);
      }
      const click = async text => {await wait(`Array.from(document.querySelectorAll('button')).some(b=>b.textContent.trim()===${JSON.stringify(text)} && !b.disabled)`);return evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()===${JSON.stringify(text)}).click()`);};
      await evaluate("document.querySelector('.managed-network').open=true");
      await wait("document.body.textContent.includes('eth-test')");
      assert.equal(await evaluate("document.querySelector('.managed-network .advanced').open"),false);
      assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='Enable administrator access on this computer').disabled"),true);
      await evaluate("document.querySelector('.admin-notice input[type=checkbox]').click()");
      await click('Enable administrator access on this computer');
      await click('Set up private network');
      await wait("document.querySelector('.managed-network summary').textContent.includes('Private network ready')");
      assert.ok(!requests.some(r=>r.path==='/v2/node/network-selection'||r.path.startsWith('/v2/node/network-identity/peers/')), 'Guided setup needs no manual pairing or selection');
      await click('Stop private network and restore settings');
      await wait("document.querySelector('.managed-network summary').textContent.includes('Not set up')");
      await evaluate("document.querySelector('.managed-network .advanced').open=true");
      await wait("document.querySelector('.legacy-network')");
      await evaluate("document.querySelector('.legacy-network').open=true");
      await wait("document.querySelector('.legacy-network textarea[readonly]')");
      await click('Save local adapter');
      await wait("document.querySelector('.managed-network textarea:not([readonly])')");
      await evaluate(`(()=>{const t=document.querySelector('.managed-network textarea:not([readonly])');t.value=JSON.stringify({node_id:'worker',key_id:'c'.repeat(64),public_key:'fixture-key'});t.dispatchEvent(new Event('input',{bubbles:true}));})()`);
      await click('Pair node');
      await wait("document.querySelector('.managed-network textarea:not([readonly])').value===''");
      await evaluate(`(()=>{const label=Array.from(document.querySelectorAll('fieldset label')).find(l=>l.textContent.includes('worker-b'));const input=label.querySelector('input');input.checked=false;input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
      await click('Start private network');
      await wait("document.body.textContent.includes('Operation in progress')");
      await click('Cancel and roll back');
      await wait("document.body.textContent.includes('· failed ·')");
      await click('Retry verification and setup');
      await wait("document.body.textContent.includes('· ready ·')");
      await evaluate(`(()=>{const label=Array.from(document.querySelectorAll('fieldset label')).find(l=>l.textContent.includes('worker-b'));const input=label.querySelector('input');input.checked=true;input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
      await click('Add selected participants');
      await wait("document.querySelector('.network-status')?.textContent.includes('worker-b')");
      await evaluate("fetch('/v2/status').then(r=>r.json()).then(status=>window.networkStore.status=status)",true);
      await wait("document.querySelector('#run-network option[value=\""+networkId+"\"]')");
      await evaluate(`(()=>{const s=document.querySelector('#run-network');s.value=${JSON.stringify(networkId)};s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
      await click('Configure');
      await click('Review deployment');
      await click('Deploy model');
      await wait("window.networkStore.planResult?.network_run_id");
      await click('Refresh prerequisites and status');
      await wait("document.body.textContent.includes('Stop dependent inference first')");
      assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='Stop private network').disabled"),true);
      await evaluate("window.networkStore.stopInference()",true);
      await click('Refresh prerequisites and status');
      await wait("!Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='Stop private network').disabled");
      await click('Stop private network');
      await wait("document.body.textContent.includes('· stopped ·')");
      assert.ok(requests.some(r=>r.path.endsWith('/peers/worker')));
      assert.ok(requests.some(r=>r.path==='/v2/node/network-selection'));
      assert.ok(requests.filter(r=>(r.method==='POST'||r.method==='PUT') && (r.path.includes('network') || r.path.includes('/inference/runs'))).every(r=>r.authorization==='Bearer fixture-token'), JSON.stringify(requests.filter(r=>(r.method==='POST'||r.method==='PUT') && (r.path.includes('network') || r.path.includes('/inference/runs')))));
    } finally {
      try { ws?.close(); } catch { /* ignore */ }
      if (browser) {
        await new Promise((resolve, reject) => {
          if (browser.exitCode !== null) { resolve(); return; }
          const timer = setTimeout(() => reject(new Error('Chrome did not exit')), 2000);
          browser.once('exit', () => { clearTimeout(timer); resolve(); });
          browser.kill();
        });
      }
      if (server) await new Promise((resolve) => server.close(resolve));
      try { await vite?.watcher?.close(); } catch { /* ignore */ }
      try { await vite?.close(); } catch { /* ignore */ }
      await removeBrowserProfile(profile);
      process.chdir(cwd);
    }
  });
