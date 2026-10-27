import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';

const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise,resolve,reject}; };

test('Prima requires an idle live lifecycle before review and guarded dispatch', async (t) => {
  const server = await createServer({ server: { middlewareMode:true, hmr:false, ws:false, watch:null }, appType:'custom' });
  try {
    const { PrimaDeployment } = await server.ssrLoadModule('/src/lib/modules/run/prima-deployment.svelte.ts');
    const runtimeId='a'.repeat(64), planDigest='b'.repeat(64);
    function fixture() {
      const commands=[];
      let tokenGate;
      const run={
        settingsScope:'primacpp',settingsRuntimeId:runtimeId,selectedModel:'fixture.gguf',selectionGeneration:1,
        live:{connected:true},localDraftErrors:{},stopParseError:'',busy:'',loadProgress:null,
        status:{role:'master',node_id:'master',inference_up:false,process_pid:null,members:{},
          runtimes:{target:{version:{runtime_id:runtimeId,engine:'primacpp'}},nodes:[{node_id:'master',phase:'ready',prepared:{runtime_id:runtimeId}}]}},
        networkPlanPayload:()=>({model:'fixture.gguf',context:4096}),validateRunSettings:()=>true,
        http:{loadToken:async()=>{if(tokenGate) await tokenGate.promise;},command:async(path,body)=>{commands.push({path,body});return {};}}
      };
      const runtimes={primaPlan:async()=>({runtime_id:runtimeId,plan_digest:planDigest,deployment_review_version:1,
        inference_supported:true,fit_status:'fits',prima_topology:{ranks:[{node_id:'master'}]}}),preparation:{active:false}};
      const deployment=new PrimaDeployment(run,runtimes);deployment.runtimeId=runtimeId;
      return {run,deployment,commands,holdToken:()=>tokenGate=deferred()};
    }
    const lifecycles=[
      ['active inference',run=>{run.status.inference_up=true;}],
      ['owned process before readiness',run=>{run.status.process_pid=321;}],
      ['starting load',run=>{run.loadProgress={phase:'starting',operation_id:'new-load'};}],
      ['loading process',run=>{run.loadProgress={phase:'loading',operation_id:'new-load'};}],
      ['busy launch',run=>{run.busy='/v2/inference/runs';}],
      ['legacy busy launch',run=>{run.busy='run';}]
    ];
    for(const [name,activate] of lifecycles) {
      await t.test(name+' blocks current qualified review without stopping or posting',async()=>{
        const {run,deployment,commands}=fixture();
        try {
          activate(run);await deployment.inspect();
          assert.equal(deployment.fresh,true,'a valid qualified preview alone is not permission to replace');
          assert.equal(await deployment.openReview(),null);
          assert.match(deployment.reviewReason,/stop model/i);
          await deployment.launch();
          assert.deepEqual(commands,[],'neither stop nor run is automatic');
        } finally {deployment.stop();}
      });
      await t.test(name+' appearing during auth revokes dispatch',async()=>{
        const {run,deployment,commands,holdToken}=fixture();
        try {
          await deployment.inspect();assert.ok(await deployment.openReview());
          const token=holdToken(), launch=deployment.launch();
          assert.equal(deployment.launchState,'sending');
          activate(run);token.resolve();await launch;
          assert.deepEqual(commands,[],'post-auth lifecycle recheck must prevent POST without a UI effect');
          assert.equal(deployment.review,null);
          assert.equal(deployment.launchState,'idle');
          assert.match(deployment.error,/stop model/i);
        } finally {deployment.stop();}
      });
    }
    await t.test('lifecycle invalidates review immediately; settled stop requires fresh inspection',async()=>{
      const {run,deployment,commands}=fixture();
      try {
        await deployment.inspect();assert.ok(await deployment.openReview());
        run.status.inference_up=true;run.status.process_pid=321;
        assert.equal(deployment.fresh,false);
        deployment.changed();assert.equal(deployment.review,null);
        // Model the status reconciliation after the user's explicit existing Stop action.
        run.status.inference_up=false;run.status.process_pid=null;
        assert.equal(deployment.review,null,'stopping does not restore old approval');
        for(const phase of ['ready','error','cancelled','unknown',null]) {
          run.loadProgress={phase,operation_id:'historical-load',fraction:1};
          await deployment.inspect();
          assert.ok(await deployment.openReview(),`historical ${phase} load is not active`);
        }
        const approved=deployment.review, key=deployment.semanticKey();
        run.loadProgress.fraction=0.5;run.loadProgress.updated_at='later';run.status.tokens_per_second=12;
        deployment.changed();
        assert.equal(deployment.semanticKey(),key,'telemetry does not change lifecycle identity');
        assert.equal(deployment.review,approved);
        await deployment.launch();
        assert.equal(deployment.launchState,'accepted','own sending state must not block dispatch');
        assert.deepEqual(commands,[{path:'/v2/inference/runs',body:{model:'fixture.gguf',context:4096,
          settings_source:'defaults',remember:false,advanced_open:false,review:{runtime_id:runtimeId,plan_digest:planDigest}}}]);
      } finally {deployment.stop();}
    });
  } finally {await server.close();}
});
test('Prima snapshots fence edits and runtime epochs, coalesce inspection and guard frozen launch', async () => {
  const cwd = process.cwd(); let server, deployment;
  try {
    process.chdir(fileURLToPath(new URL('..', import.meta.url)));
    server = await createServer({ server: { middlewareMode:true, hmr:false, ws:false, watch:null }, appType:'custom' });
    const { PrimaDeployment } = await server.ssrLoadModule('/src/lib/modules/run/prima-deployment.svelte.ts');
    const { PrimaPreparationObserver } = await server.ssrLoadModule('/src/lib/modules/runtimes/preparation.svelte.ts');
    const { RuntimesInteractor } = await server.ssrLoadModule('/src/lib/modules/runtimes/interactor.svelte.ts');
    const wire=[];
    const transport=new RuntimesInteractor({networkRequest:async(...args)=>{wire.push(args);return {};}},{status:null},{});
    const readSignal=new AbortController().signal;
    await transport.primaPlan('a'.repeat(64),{model:'a.gguf'},readSignal);
    await transport.preparePrima('a'.repeat(64),{model:'a.gguf'},'b'.repeat(64),true,'11111111-1111-1111-1111-111111111111');
    await transport.primaJob('job/id',readSignal);
    assert.deepEqual(wire[0],[`/v2/node/runtimes/${'a'.repeat(64)}/prima-plan`,'POST',{model:'a.gguf'},readSignal]);
    assert.deepEqual(wire[1],['/v2/cluster/prima-preparations','POST',{runtime_id:'a'.repeat(64),settings:{model:'a.gguf'},plan_digest:'b'.repeat(64),request_id:'11111111-1111-1111-1111-111111111111',include_profile:true}]);
    assert.deepEqual(wire[2],['/v2/cluster/prima-preparations/job%2Fid','GET',undefined,readSignal]);
    let polls=0, cancellations=0;
    const deniedObserver=new PrimaPreparationObserver({primaJob:async()=>({}),cancelPrima:async()=>{cancellations++;return {};}});
    deniedObserver.observe({job_id:'worker-job',phase:'preparing',local_work_pending:true});
    await deniedObserver.cancel();
    assert.equal(cancellations,0,'observer defaults to observation-only');
    assert.equal(deniedObserver.cancellationRequested,false);
    deniedObserver.stop();
    const cancellationWire=[], auth=deferred();
    const roleState={status:{role:'worker'}};
    const cancellationHttp={apiToken:'',loadToken:async()=>{await auth.promise;cancellationHttp.apiToken='token';},networkRequest:async(...args)=>{cancellationWire.push(args);return {job_id:'worker-job',phase:'cancelled'};}};
    const roleTransport=new RuntimesInteractor(cancellationHttp,roleState,{});
    roleTransport.preparation.observe({job_id:'worker-job',phase:'preparing',local_work_pending:true});
    await roleTransport.preparation.cancel();
    await roleTransport.cancelPrima('worker-job');
    assert.equal(cancellationWire.length,0,'workers cannot cancel even by direct invocation');
    roleState.status.role='master';
    const cancelAfterAuth=roleTransport.preparation.cancel();
    roleState.status.role='worker';auth.resolve();await cancelAfterAuth;
    assert.equal(cancellationWire.length,0,'role downgrade while loading auth prevents cancel POST');
    assert.equal(roleTransport.preparation.cancellationRequested,false);
    roleTransport.preparation.stop();roleTransport.deployment.stop();
    const poll=deferred();
    const observer=new PrimaPreparationObserver({primaJob:async()=>{polls++;return poll.promise;},cancelPrima:async()=>{cancellations++;return {job_id:'job',phase:'cancelled',local_work_pending:true};}},()=>true);
    observer.observe({job_id:'job',phase:'preparing',local_work_pending:true});
    const releaseA=observer.acquire(), releaseB=observer.acquire();
    await Promise.resolve();
    assert.equal(polls,1,'two views share one in-flight read');
    await observer.cancel();
    assert.equal(observer.active,true,'cancel phase alone cannot prove settlement');
    poll.resolve({job_id:'job',phase:'cancelled',local_work_pending:false});
    await Promise.resolve(); await Promise.resolve();
    assert.equal(observer.active,false);
    releaseA();releaseB();observer.stop();
    assert.equal(cancellations,1,'unmount never cancels remote work');
    let context=4096, tokenGate=null;
    const plans=[], sends=[], prepares=[];
    const run = { settingsScope:'primacpp', settingsRuntimeId:'a'.repeat(64), settingsLoading:false, settingsLoadError:'', selectedModel:'a.gguf', selectionGeneration:1,
      live:{connected:true}, localDraftErrors:{}, fieldErrors:{}, stopParseError:'', extraArgsText:'[]',
      status:{role:'master',node_id:'m', members:{m:{node_id:'m'}},runtimes:{target:{version:{runtime_id:'a'.repeat(64),engine:'primacpp'}},nodes:[{node_id:'m',phase:'ready',prepared:{runtime_id:'a'.repeat(64)}}]}},
      networkPlanPayload:() => ({model:'a.gguf',context,remember:true,advanced_open:true,settings_source:'saved'}),
      validateRunSettings:()=>true, setSettingsScope:async()=>{},
      http:{apiToken:'token',loadToken:async()=>{if(tokenGate)await tokenGate.promise;},command:async(path,body)=>{sends.push(body);return {};},refresh:async()=>{}},
    };
    const runtimes = { primaPlan: (id, settings, signal) => { const gate=deferred(); plans.push({id,settings,signal,...gate}); return gate.promise; },
      preparePrima:async(...args)=>{prepares.push(args);return {job_id:'job',phase:'prepared',local_work_pending:false};},
      preparation:{job:null,active:false,observe:()=>{},acquire:()=>()=>{}},refresh:async()=>{} };
    deployment = new PrimaDeployment(run,runtimes);
    deployment.runtimeId='a'.repeat(64);
    const pending=deployment.inspect();
    const same=deployment.inspect();
    assert.equal(plans.length,1,'identical in-flight inspection is coalesced');
    context=8192; deployment.changed();
    assert.equal(plans[0].signal.aborted,true);
    plans[0].resolve({runtime_id:'a'.repeat(64),plan_digest:'b'.repeat(64),deployment_review_version:1});
    await Promise.all([pending,same]);
    assert.equal(deployment.fresh,false,'late plan must not authorize changed draft');
    const next=deployment.inspect();
    await deployment.setRuntime('c'.repeat(64));
    await deployment.setRuntime('a'.repeat(64));
    plans[1].resolve({runtime_id:'a'.repeat(64),plan_digest:'b'.repeat(64),deployment_review_version:1}); await next;
    assert.equal(deployment.fresh,false,'A to B to A must not resurrect an old response');
    const current=deployment.inspect();
    plans.at(-1).resolve({runtime_id:'a'.repeat(64),plan_digest:'b'.repeat(64),deployment_review_version:1,inference_supported:true,fit_status:'fits',prima_topology:{ranks:[{node_id:'m'}]},effective_config:{parameters:[]}}); await current;
    assert.equal(deployment.fresh,true);
    assert.equal(plans.at(-1).settings.context,8192);
    assert.equal(plans.at(-1).settings.remember,false);
    assert.equal(plans.at(-1).settings.advanced_open,false);
    assert.equal(plans.at(-1).settings.settings_source,'defaults');
    assert.ok(await deployment.openReview());
    run.live.areaHealth={status:{availability:'unavailable'}};
    deployment.changed();
    assert.equal(deployment.review,null,'a connected socket with unavailable status must revoke review');
    assert.match(deployment.inspectReason,/status|unavailable/i);
    run.live.areaHealth={status:{availability:'available'}};
    deployment.changed();
    const restored=deployment.inspect();
    plans.at(-1).resolve({runtime_id:'a'.repeat(64),plan_digest:'b'.repeat(64),deployment_review_version:1,inference_supported:true,fit_status:'fits',prima_topology:{ranks:[{node_id:'m'}]}});await restored;
    assert.ok(await deployment.openReview());
    tokenGate=deferred(); const sending=deployment.launch();
    context=16384; deployment.changed(); tokenGate.resolve(); await sending;
    assert.equal(sends.length,0,'edit during auth revokes launch before POST');
    tokenGate=null;
    const final=deployment.inspect();
    plans.at(-1).resolve({runtime_id:'a'.repeat(64),plan_digest:'d'.repeat(64),deployment_review_version:1,inference_supported:true,fit_status:'fits',prima_topology:{ranks:[{node_id:'m'}]}}); await final;
    assert.ok(await deployment.openReview()); await deployment.launch();
    assert.equal(sends.length,1);
    assert.equal(sends[0].context,16384);
    assert.deepEqual(sends[0].review,{runtime_id:'a'.repeat(64),plan_digest:'d'.repeat(64)});
    assert.equal(sends[0].remember,false);
    deployment.launchState = 'idle';
    await deployment.prepare(false);
    assert.equal(prepares.length,1);
    assert.deepEqual(prepares[0][1],plans.at(-1).settings,'preparation uses inspected frozen settings');
    const uncertain = new Error('connection lost');
    runtimes.preparePrima=async(...args)=>{prepares.push(args);throw uncertain;};
    await deployment.prepare(true);
    assert.equal(deployment.uncertainPreparation,true);
    context=24576; deployment.changed();
    await deployment.prepare(true,true);
    assert.equal(prepares.length,3,'an explicit uncertain retry reconciles the original request, even after an edit');
    assert.equal(prepares.at(-1)[4],prepares.at(-2)[4],'uncertain retry retains its UUID');
    assert.deepEqual(prepares.at(-1)[1],prepares.at(-2)[1]);
    run.status.inference_up=true;
    const beforePrepare=prepares.length; await deployment.prepare(false);
    assert.equal(prepares.length,beforePrepare,'active inference is never stopped for preparation');
    run.status.inference_up=false;
    runtimes.preparePrima=async(...args)=>{prepares.push(args);return {job_id:'settled-old',phase:'failed',local_work_pending:false};};
    await deployment.prepare(true,true);
    const afterRetry=deployment.inspect();
    plans.at(-1).resolve({runtime_id:'a'.repeat(64),plan_digest:'f'.repeat(64),deployment_review_version:1,inference_supported:true,fit_status:'fits',prima_topology:{ranks:[{node_id:'m'}]}}); await afterRetry;
    // Definitive pre-dispatch HTTP rejections permit a new inspection, not a replay.
    const {HttpClient}=await server.ssrLoadModule('/src/lib/shared/http.svelte.ts');
    const originalFetch=globalThis.fetch, originalHttp=run.http;
    const http=new HttpClient();http.apiToken='token';http.loadToken=async()=>{};
    const httpCommand=http.command.bind(http);
    let responseStatus=409,responseCode='DEPLOYMENT_REVIEW_CHANGED',httpError;
    const launchRequests=[];
    http.command=async(...args)=>{try{return await httpCommand(...args);}catch(error){httpError=error;throw error;}};
    globalThis.fetch=async(path,options)=>{
      launchRequests.push(JSON.parse(options.body));
      return new Response(JSON.stringify({error:{code:'HTTP_ERROR',message:'Request failed',details:{code:responseCode,message:'Review rejected'}}}),{status:responseStatus});
    };
    run.http=http;
    try {
      for(const [status,code,recoverable] of [[409,'DEPLOYMENT_REVIEW_CHANGED',true],[422,'VALIDATION_ERROR',true],[401,'UNAUTHORIZED',true],[403,'FORBIDDEN',true],[500,'INTERNAL_ERROR',false],[409,'PRIMA_RUN_REFUSED',false]]) {
        responseStatus=status;responseCode=code;
        const inspection=deployment.inspect(true);
        plans.at(-1).resolve({runtime_id:'a'.repeat(64),plan_digest:'f'.repeat(64),deployment_review_version:1,inference_supported:true,fit_status:'fits',prima_topology:{ranks:[{node_id:'m'}]}});await inspection;
        assert.ok(await deployment.openReview());
        const before=launchRequests.length;await deployment.launch();
        assert.equal(deployment.launchState,recoverable?'idle':'unknown',`HTTP ${status} ${code} recovery classification`);
        assert.ok(httpError instanceof Error,'generic error consumers stay compatible');
        assert.equal(httpError.status,status);assert.equal(httpError.code,code);
        assert.equal(deployment.review,null);
        assert.equal(launchRequests.length,before+1,'no automatic retry');
        assert.deepEqual(launchRequests.at(-1).review,{runtime_id:'a'.repeat(64),plan_digest:'f'.repeat(64)});
        if(recoverable) {
          assert.equal(deployment.fresh,false,'definitive refusal invalidates the old preview');
          assert.equal(deployment.review,null,'refusal does not restore old approval');
          assert.equal(deployment.inspectReason,'','recovery through inspection remains available');
        } else {
          assert.equal(await deployment.openReview(),null,'ambiguous response cannot authorize replay');
          deployment.launchState='idle'; // isolate the next independent response case
        }
      }
    } finally {globalThis.fetch=originalFetch;run.http=originalHttp;}
    const recovered=deployment.inspect(true);
    plans.at(-1).resolve({runtime_id:'a'.repeat(64),plan_digest:'f'.repeat(64),deployment_review_version:1,inference_supported:true,fit_status:'fits',prima_topology:{ranks:[{node_id:'m'}]}});await recovered;
    await deployment.openReview();
    run.http.command=async()=>{throw uncertain;};
    await deployment.launch();
    assert.equal(deployment.launchState,'unknown');
    assert.equal(await deployment.openReview(),null,'unknown run cannot be replayed');
    deployment.launchState='idle'; context=20480; deployment.changed();
    const wrongRuntime=deployment.inspect();
    plans.at(-1).resolve({runtime_id:'c'.repeat(64),plan_digest:'e'.repeat(64),deployment_review_version:1}); await wrongRuntime;
    assert.equal(deployment.fresh,false,'a response for a different runtime cannot authorize this draft');
    const oldServer=deployment.inspect();
    plans.at(-1).resolve({runtime_id:'a'.repeat(64),plan_digest:'e'.repeat(64),inference_supported:true}); await oldServer;
    assert.equal(await deployment.openReview(),null,'old server cannot downgrade to an unguarded run');
  } finally { deployment?.stop(); await server?.close(); process.chdir(cwd); }
});


test('Prima Prepare and Review acquire current plans without an inspection step', async (t) => {
  const server = await createServer({server:{middlewareMode:true,hmr:false,ws:false,watch:null},appType:'custom'});
  try {
    const {PrimaDeployment} = await server.ssrLoadModule('/src/lib/modules/run/prima-deployment.svelte.ts');
    const runtimeId='a'.repeat(64), digest='b'.repeat(64);
    const plan=()=>({runtime_id:runtimeId,plan_digest:digest,deployment_review_version:1,inference_supported:true,
      fit_status:'fits',prima_topology:{ranks:[{node_id:'master'}]}});
    function fixture() {
      const reads=[],prepares=[],commands=[];
      let context=4096;
      const run={settingsScope:'primacpp',settingsRuntimeId:runtimeId,selectedModel:'fixture.gguf',selectionGeneration:1,
        live:{connected:true},localDraftErrors:{},stopParseError:'',busy:'',loadProgress:null,
        status:{role:'master',node_id:'master',members:{},runtimes:{target:{version:{runtime_id:runtimeId,engine:'primacpp'}},
          nodes:[{node_id:'master',phase:'ready',prepared:{runtime_id:runtimeId}}]}},
        networkPlanPayload:()=>({model:'fixture.gguf',context}),validateRunSettings:()=>true,
        http:{loadToken:async()=>{},command:async(...args)=>commands.push(args)}};
      const runtimes={primaPlan:(id,settings,signal)=>{const gate=deferred();reads.push({...gate,id,settings,signal});return gate.promise;},
        preparePrima:async(...args)=>{prepares.push(args);return {job_id:'job',phase:'prepared'};},
        preparation:{active:false,observe:()=>{}}};
      const deployment=new PrimaDeployment(run,runtimes);deployment.runtimeId=runtimeId;
      return {run,deployment,reads,prepares,commands,edit:()=>{context+=1024;deployment.changed();}};
    }
    for(const action of ['prepare','review']) {
      const invoke=d=>action==='prepare'?d.prepare(true):d.openReview();
      await t.test(action+' calculates once, joins background planning and ignores duplicate clicks',async()=>{
        const f=fixture(),d=f.deployment;
        try {
          assert.equal(action==='prepare'?d.prepareActionReason:d.reviewActionReason,'');
          const background=d.inspect(), pending=invoke(d);
          assert.equal(d.pendingAction,action);
          await invoke(d);
          assert.equal(f.reads.length,1);
          f.reads[0].resolve(plan());await background;await pending;
          assert.equal(d.pendingAction,null);
          if(action==='prepare') {assert.equal(f.prepares.length,1);assert.equal(f.prepares[0][3],true);}
          else {assert.ok(d.review);assert.equal(d.review.settings.context,4096);}
          assert.deepEqual(f.commands,[],'planning/review/preparation never launches');
          // A new explicit action can calculate a changed draft without manual inspection.
          f.edit();const updated=invoke(d);
          assert.equal(f.reads.length,2);
          f.reads[1].resolve(plan());await updated;
          if(action==='prepare') assert.equal(f.prepares[1][1].context,5120);
          else assert.equal(d.review.settings.context,5120);
        } finally {d.stop();}
      });
      for(const change of ['draft','runtime','connection','lifecycle','page']) {
        await t.test(action+' cancels on '+change+' while calculating',async()=>{
          const f=fixture(),d=f.deployment;
          try {
            const pending=invoke(d);
            if(change==='draft') f.edit();
            if(change==='runtime') {d.runtimeId='c'.repeat(64);d.changed();}
            if(change==='connection') {f.run.live.connected=false;d.changed();}
            if(change==='lifecycle') {f.run.status.inference_up=true;d.changed();}
            if(change==='page') d.stop();
            f.reads[0].resolve(plan());await pending;
            assert.equal(d.review,null);assert.equal(f.prepares.length,0);assert.equal(f.commands.length,0);
            assert.equal(d.pendingAction,null);
          } finally {d.stop();}
        });
      }
      await t.test(action+' failure allows plan-only retry without replaying intent',async()=>{
        const f=fixture(),d=f.deployment;
        try {
          const pending=invoke(d);f.reads[0].reject(new Error('planner unavailable'));await pending;
          assert.match(d.planningError,/planner unavailable/);
          const retry=d.inspect(true);f.reads[1].resolve(plan());await retry;
          assert.equal(d.planningError,'');assert.equal(d.review,null);assert.equal(f.prepares.length,0);
          await invoke(d);
          assert.equal(f.reads.length,2,'fresh plan is reused');
          if(action==='prepare') assert.equal(f.prepares.length,1);else assert.ok(d.review);
        } finally {d.stop();}
      });
      await t.test(action+' rejects invalid placement discovered automatically',async()=>{
        const f=fixture(),d=f.deployment;
        try {
          const pending=invoke(d);f.reads[0].resolve({...plan(),placement_errors:['Insufficient memory']});await pending;
          assert.match(d.error,/Insufficient memory/);assert.equal(d.review,null);assert.equal(f.prepares.length,0);
        } finally {d.stop();}
      });
    }
  } finally {await server.close();}
});
