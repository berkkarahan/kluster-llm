import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

test('Prima auto-deploy one-click UI, feature flag gating, and legacy preservation', async (t) => {
  process.chdir(fileURLToPath(new URL('..', import.meta.url)));
  const server = await createServer({
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    appType: 'custom',
  });

  try {
    const { render } = await server.ssrLoadModule('svelte/server');
    const { default: PrimaAutoDeployView } = await server.ssrLoadModule(
      '/src/lib/modules/run/view/PrimaAutoDeployView.svelte'
    );
    const { default: PrimaRunPage } = await server.ssrLoadModule(
      '/src/lib/modules/run/view/PrimaRunPage.svelte'
    );
    const { default: PrimaDeploymentShell } = await server.ssrLoadModule(
      '/src/lib/modules/run/view/PrimaDeploymentShell.svelte'
    );
    const { RunInteractor } = await server.ssrLoadModule(
      '/src/lib/modules/run/interactor.svelte.ts'
    );

    function createMockPresenter(options = {}) {
      const commands = [];
      const featureFlags = options.featureFlags ?? {};
      const status = {
        role: 'master',
        node_id: 'master1',
        model_name: options.modelName ?? 'test-model-q4.gguf',
        inference_up: options.inferenceUp ?? false,
        feature_flags: featureFlags,
        members: options.members ?? {
          master1: {
            role: 'master',
            host: '192.168.1.10',
            online: true,
            devices: [{ name: 'NVIDIA RTX 4090', index: 0, backend: 'vulkan' }],
            vram_bytes: 24 * 1024 * 1024 * 1024,
            total_ram_bytes: 64 * 1024 * 1024 * 1024,
            available_ram_bytes: 58 * 1024 * 1024 * 1024,
          },
          worker1: {
            role: 'worker',
            host: '192.168.1.11',
            online: true,
            devices: [{ name: 'AMD Radeon RX 7900', index: 0, backend: 'vulkan' }],
            vram_bytes: 16 * 1024 * 1024 * 1024,
            total_ram_bytes: 32 * 1024 * 1024 * 1024,
            available_ram_bytes: 28 * 1024 * 1024 * 1024,
          },
        },
      };

      const presenter = {
        status,
        models: [{ id: 'test-model-q4.gguf' }, { id: 'other-model-q8.gguf' }],
        selectedModel: 'test-model-q4.gguf',
        selectionGeneration: 1,
        capabilitiesError: '',
        settingsLoadError: '',
        planResult: null,
        settingsDirty: () => false,
        fieldErrors: {},
        localDraftErrors: {},
        stopSequences: null,
        stopParseError: '',
        extraArgsText: '',
        editorRevision: 0,
        nodePriority: null,
        requiredNodes: [],
        runGpuIds: null,
        runDevice: null,
        vramPercent: 90,
        ramPercent: 90,
        expertFlags: [],
        effectiveConfig: () => null,
        selectedModelMaxContext: () => 4096,
        context: 4096,
        contextAuto: false,
        busy: options.busy ?? '',
        unrepresentedSettings: {},
        templatePresets: [],
        chatTemplate: 'default',
        chatTemplateText: '',
        modelTemplateText: '',
        templateEmbedded: false,
        parameterDescriptor: (field) => ({ id: field, available: true }),
        parameterValue: (field) => null,
        setParameter: (name, value) => {},
        selectedModelDefaults: () => null,
        planUnsupportedReason: () => null,
        defaultSettingsChanges: () => [],
        useDefaultSettings: () => {},
        revertSavedSettings: () => {},
        selectModel: (id) => {
          presenter.selectedModel = id;
        },
        stopInference: async () => {
          commands.push({ path: '/v2/inference/runs/stop' });
          status.inference_up = false;
        },
        scheduleEstimate: () => {},
        saveModelSettings: () => {},
        planRunBlockedReason: () => null,
        noteRawDraftEdit: () => {},
        validateRunSettings: () => true,
        loadProgress: options.loadProgress ?? null,
        http: {
          command: async (path, body, method = 'POST') => {
            commands.push({ path, body, method });
            return { ok: true, plan: {} };
          },
          loadToken: async () => {},
        },
        live: { connected: true },
        cluster: { status, clearingLifecycle: false },
        commands,
      };

      const runtimes = {
        status: { runtimes: null },
        deployment: {
          lifecycleReason: options.lifecycleReason ?? (options.inferenceUp ? 'Active inference run in progress' : ''),
          runtimeId: 'primacpp-test-id',
          fresh: false,
          review: null,
          launchState: 'idle',
          semanticKey: () => 'test-key',
          stop: () => {},
          schedule: () => {},
          openReview: async () => null,
          changed: () => {},
        },
        preparation: {
          acquire: () => {},
          observe: () => {},
          job: null,
        },
        primaPreparation: null,
      };

      const cluster = presenter.cluster;

      return { presenter, runtimes, cluster, commands };
    }

    await t.test('1. Renders PrimaAutoDeployView when status.feature_flags["prima-auto-deploy"] === true', () => {
      const { presenter, runtimes, cluster } = createMockPresenter({
        featureFlags: { 'prima-auto-deploy': true },
      });

      const { body } = render(PrimaRunPage, {
        props: { presenter, runtimes, cluster },
      });

      // Verify One-Click Auto Deploy elements
      assert.match(body, /One-Click Auto Deploy/);
      assert.match(body, /auto-deploy/);
      assert.match(body, /Deploy Model/);
      assert.match(body, /Cluster Ready|Cluster readiness/i);
      assert.match(body, /Choose model/);
      assert.match(body, /test-model-q4\.gguf/);

      // Verify legacy manual multi-tab and stepper layout is NOT rendered
      assert.doesNotMatch(body, /data-section="overview"/);
      assert.doesNotMatch(body, /data-section="computers"/);
      assert.doesNotMatch(body, /data-section="memory"/);
      assert.doesNotMatch(body, /Deployment settings/);
    });

    await t.test('2. Renders legacy manual view when flag is false or omitted', () => {
      // Flag explicitly false
      const falseCase = createMockPresenter({
        featureFlags: { 'prima-auto-deploy': false },
      });
      const htmlFalse = render(PrimaRunPage, {
        props: { presenter: falseCase.presenter, runtimes: falseCase.runtimes, cluster: falseCase.cluster },
      }).body;

      assert.match(htmlFalse, /Deployment settings/);
      assert.match(htmlFalse, /Next deployment/);
      assert.match(htmlFalse, /data-section="overview"/);
      assert.doesNotMatch(htmlFalse, /One-Click Auto Deploy/);

      // Flag omitted / empty
      const omittedCase = createMockPresenter({
        featureFlags: {},
      });
      const htmlOmitted = render(PrimaRunPage, {
        props: { presenter: omittedCase.presenter, runtimes: omittedCase.runtimes, cluster: omittedCase.cluster },
      }).body;

      assert.match(htmlOmitted, /Deployment settings/);
      assert.match(htmlOmitted, /Next deployment/);
      assert.match(htmlOmitted, /data-section="overview"/);
      assert.doesNotMatch(htmlOmitted, /One-Click Auto Deploy/);
    });

    await t.test('3. Triggers deploy with prima_auto_deploy: true on dispatch', async () => {
      const executed = [];
      const fakeHttp = {
        command: async (path, body) => {
          executed.push({ path, body });
          return { ok: true, plan: {} };
        },
        loadToken: async () => {},
      };
      const fakeCluster = { status: { role: 'master', members: {} } };
      const fakeCatalog = { models: [{ id: 'auto-model.gguf' }], selectedModel: 'auto-model.gguf' };
      const fakeSettings = { vramPercent: 90, ramPercent: 90, device: 'gpu' };
      const fakeNetwork = { selectedNetworkId: 'net-10gbe' };
      const fakeLive = { connected: true };

      const interactor = new RunInteractor(
        fakeHttp,
        fakeLive,
        fakeCluster,
        fakeCatalog,
        fakeSettings,
        fakeNetwork
      );

      await interactor.autoDeploy('auto-model.gguf');

      assert.equal(executed.length, 1);
      assert.equal(executed[0].path, '/v2/inference/runs');
      assert.equal(executed[0].body.model, 'auto-model.gguf');
      assert.equal(executed[0].body.prima_auto_deploy, true);
      assert.equal(executed[0].body.network_id, 'net-10gbe');
    });

    await t.test('4. Renders error chips for unsupported model, corrupt model, and insufficient capacity', () => {
      const { presenter, runtimes, cluster } = createMockPresenter({
        featureFlags: { 'prima-auto-deploy': true },
      });

      // Unsupported model chip
      const htmlUnsupported = render(PrimaAutoDeployView, {
        props: {
          presenter,
          runtimes,
          cluster,
          errorCode: 'PRIMA_MODEL_UNSUPPORTED',
        },
      }).body;
      assert.match(htmlUnsupported, /PRIMA_MODEL_UNSUPPORTED/);
      assert.match(htmlUnsupported, /not supported by Prima/);
      assert.match(htmlUnsupported, /disabled/);

      // Insufficient capacity chip
      const htmlCapacity = render(PrimaAutoDeployView, {
        props: {
          presenter,
          runtimes,
          cluster,
          errorCode: 'PRIMA_INSUFFICIENT_CAPACITY',
        },
      }).body;
      assert.match(htmlCapacity, /PRIMA_INSUFFICIENT_CAPACITY/);
      assert.match(htmlCapacity, /Insufficient cluster memory/);
      assert.match(htmlCapacity, /disabled/);

      // Corrupt model chip
      const htmlCorrupt = render(PrimaAutoDeployView, {
        props: {
          presenter,
          runtimes,
          cluster,
          errorCode: 'PRIMA_MODEL_CORRUPT',
        },
      }).body;
      assert.match(htmlCorrupt, /PRIMA_MODEL_CORRUPT/);
      assert.match(htmlCorrupt, /corrupted, zero-byte, or not a valid GGUF/);
      assert.match(htmlCorrupt, /disabled/);
    });

    await t.test('5. Preserves running deployment display and stop controls across flag toggles', async () => {
      // 5.1 Auto-deploy enabled with running model
      const autoRunning = createMockPresenter({
        featureFlags: { 'prima-auto-deploy': true },
        inferenceUp: true,
        modelName: 'active-auto-model.gguf',
      });
      const htmlAutoRunning = render(PrimaRunPage, {
        props: { presenter: autoRunning.presenter, runtimes: autoRunning.runtimes, cluster: autoRunning.cluster },
      }).body;

      assert.match(htmlAutoRunning, /Running/);
      assert.match(htmlAutoRunning, /active-auto-model\.gguf/);
      assert.match(htmlAutoRunning, /Stop model/);
      assert.match(htmlAutoRunning, /Stop active model before deploying/);

      // 5.2 Auto-deploy toggled off while model still running
      const legacyRunning = createMockPresenter({
        featureFlags: { 'prima-auto-deploy': false },
        inferenceUp: true,
        modelName: 'active-auto-model.gguf',
      });
      const htmlLegacyRunning = render(PrimaRunPage, {
        props: { presenter: legacyRunning.presenter, runtimes: legacyRunning.runtimes, cluster: legacyRunning.cluster },
      }).body;

      assert.match(htmlLegacyRunning, /Running/);
      assert.match(htmlLegacyRunning, /active-auto-model\.gguf/);
      assert.match(htmlLegacyRunning, /Stop model/);
      assert.match(htmlLegacyRunning, /Deployment settings/);

      // 5.3 Direct PrimaDeploymentShell running state rendering
      const htmlShellRunning = render(PrimaDeploymentShell, {
        props: {
          dirty: false,
          running: true,
          modelName: 'active-auto-model.gguf',
          reason: 'Active inference run',
          focus: () => {},
          children: () => '',
          nextDeployment: () => '',
          footer: () => '',
        },
      }).body;
      assert.match(htmlShellRunning, /Running/);
      assert.match(htmlShellRunning, /active-auto-model\.gguf/);
      assert.match(htmlShellRunning, /Stop model/);

      // 5.4 Stop action dispatches correctly
      await autoRunning.presenter.stopInference();
      assert.deepEqual(autoRunning.commands, [{ path: '/v2/inference/runs/stop' }]);
    });

    await t.test('6. Cluster auto-planning readiness badge displays online nodes, GPUs, and aggregate VRAM/RAM', () => {
      const { presenter, runtimes, cluster } = createMockPresenter({
        featureFlags: { 'prima-auto-deploy': true },
      });

      const { body } = render(PrimaAutoDeployView, {
        props: { presenter, runtimes, cluster },
      });

      // 2 nodes (master + worker)
      assert.match(body, /2 nodes online/);
      // 2 GPUs (RTX 4090 + RX 7900)
      assert.match(body, /2 GPUs detected/);
      // Aggregate VRAM: 24 GiB + 16 GiB = 40.0 GiB
      assert.match(body, /40\.0 GiB VRAM/);
      // Aggregate RAM: 58 GiB + 28 GiB = 86.0 GiB
      assert.match(body, /86\.0 GiB RAM/);
      assert.match(body, /Cluster Ready/);
    });
  } finally {
    await server.close();
  }
});
