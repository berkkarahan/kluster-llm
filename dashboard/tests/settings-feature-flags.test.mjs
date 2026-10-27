import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

test('Settings Feature Flags admin view, on-the-go toggling, live sync, and failure recovery', async (t) => {
  process.chdir(fileURLToPath(new URL('..', import.meta.url)));
  const server = await createServer({
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    appType: 'custom'
  });

  try {
    const { render } = await server.ssrLoadModule('svelte/server');
    const { default: FeatureFlags } = await server.ssrLoadModule(
      '/src/lib/modules/node-settings/view/FeatureFlags.svelte'
    );
    const { default: SettingsPage } = await server.ssrLoadModule(
      '/src/lib/modules/node-settings/view/SettingsPage.svelte'
    );
    const { default: PrimaRunPage } = await server.ssrLoadModule(
      '/src/lib/modules/run/view/PrimaRunPage.svelte'
    );
    const { NodeSettingsInteractor } = await server.ssrLoadModule(
      '/src/lib/modules/node-settings/interactor.svelte.ts'
    );

    function createFixture(initialFlags = { 'private-network-setup': false, 'prima-auto-deploy': false }) {
      let refreshCount = 0;
      const cluster = {
        status: {
          role: 'master',
          node_id: 'master1',
          engine: 'primacpp',
          feature_flags: { ...initialFlags },
          members: {
            master1: {
              role: 'master',
              host: '192.168.1.10',
              online: true,
              devices: [{ name: 'NVIDIA RTX 4090', index: 0, backend: 'vulkan' }],
              vram_bytes: 24 * 1024 * 1024 * 1024,
              total_ram_bytes: 64 * 1024 * 1024 * 1024,
              available_ram_bytes: 58 * 1024 * 1024 * 1024
            }
          }
        },
        viewState: () => 'live'
      };
      const live = {
        connected: true,
        refresh: async () => {
          refreshCount += 1;
        },
        onStatusFingerprint: () => {}
      };
      const http = { busy: '' };
      const settings = new NodeSettingsInteractor(http, live, cluster);
      return { settings, cluster, live, getRefreshCount: () => refreshCount };
    }

    await t.test('US1: Renders Settings Feature flags section and toggles flags on the go across gated views', async () => {
      const { settings, cluster, getRefreshCount } = createFixture();

      // Render SettingsPage and confirm "Feature flags" nav item and panel exist
      const pageRendered = render(SettingsPage, {
        props: {
          settings,
          cluster,
          runtimes: { status: null },
          run: { activeRunConfig: null, planResult: null }
        }
      });
      assert.match(pageRendered.body, /Feature flags/);
      assert.match(pageRendered.body, /data-testid="feature-flags-panel"/);
      assert.match(pageRendered.body, /Guided private network setup/);
      assert.match(pageRendered.body, /Prima one-click auto-deploy/);
      assert.match(pageRendered.body, /data-testid="flag-toggle-prima-auto-deploy"/);

      // Initially prima-auto-deploy is disabled: PrimaRunPage renders full configuration shell
      const runMock = {
        status: cluster.status,
        models: [{ id: 'test-model.gguf' }],
        selectedModel: 'test-model.gguf',
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
        busy: '',
        unrepresentedSettings: {},
        templatePresets: [],
        chatTemplate: 'default',
        chatTemplateText: '',
        modelTemplateText: '',
        templateEmbedded: false,
        parameterDescriptor: (field) => ({ id: field, available: true }),
        parameterValue: () => null,
        setParameter: () => {},
        selectedModelDefaults: () => null,
        planUnsupportedReason: () => null,
        defaultSettingsChanges: () => [],
        useDefaultSettings: () => {},
        revertSavedSettings: () => {},
        selectModel: () => {},
        stopInference: async () => {},
        scheduleEstimate: () => {},
        saveModelSettings: () => {},
        planRunBlockedReason: () => null,
        noteRawDraftEdit: () => {},
        validateRunSettings: () => true,
        loadProgress: null,
        http: { command: async () => ({ ok: true }), loadToken: async () => {} },
        live: { connected: true }
      };

      const beforeToggleRun = render(PrimaRunPage, {
        props: { presenter: runMock, cluster }
      });
      assert.doesNotMatch(beforeToggleRun.body, /One-Click Auto Deploy/);

      // Stub fetch for PUT /v2/node/flags and flip prima-auto-deploy on the go
      const originalFetch = globalThis.fetch;
      const requests = [];
      globalThis.fetch = async (url, init) => {
        requests.push({ url, init });
        return new Response(
          JSON.stringify({
            flags: {
              'private-network-setup': false,
              'prima-auto-deploy': true
            }
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      };

      try {
        const result = await settings.setFeatureFlag('prima-auto-deploy', true);
        assert.equal(result, true);
        assert.equal(requests.length, 1);
        assert.equal(requests[0].url, '/v2/node/flags');
        assert.equal(requests[0].init.method, 'PUT');
        assert.deepEqual(JSON.parse(requests[0].init.body), {
          name: 'prima-auto-deploy',
          enabled: true
        });
        assert.equal(cluster.status.feature_flags['prima-auto-deploy'], true);
        assert.equal(getRefreshCount(), 1);

        // FeatureFlags panel now renders enabled state
        const flagsRendered = render(FeatureFlags, { props: { presenter: settings } });
        assert.match(flagsRendered.body, /aria-checked="true"/);

        // PrimaRunPage immediately switches to PrimaAutoDeployView without reload
        runMock.status = cluster.status;
        const afterToggleRun = render(PrimaRunPage, {
          props: { presenter: runMock, cluster }
        });
        assert.match(afterToggleRun.body, /One-Click Auto Deploy/);
      } finally {
        globalThis.fetch = originalFetch;
      }
    });

    await t.test('US2: Synchronizes with incoming live status and GET /v2/node/flags', async () => {
      const { settings, cluster } = createFixture();
      assert.equal(settings.isFeatureFlagEnabled('private-network-setup'), false);

      // External CLI toggle arrives via status fingerprint
      settings.onStatusFingerprint({
        ...cluster.status,
        feature_flags: {
          'private-network-setup': true,
          'prima-auto-deploy': false
        }
      });
      assert.equal(settings.isFeatureFlagEnabled('private-network-setup'), true);

      const rendered = render(FeatureFlags, { props: { presenter: settings } });
      assert.match(rendered.body, /aria-checked="true"[^>]*data-testid="flag-toggle-private-network-setup"/);
    });

    await t.test('US3: Prevents duplicate in-flight toggles and preserves state with error notice on failure', async () => {
      const { settings } = createFixture({
        'private-network-setup': false,
        'prima-auto-deploy': false
      });

      const originalFetch = globalThis.fetch;
      let fetchCalls = 0;
      let resolveFetch;
      globalThis.fetch = () => {
        fetchCalls += 1;
        return new Promise((resolve) => {
          resolveFetch = resolve;
        });
      };

      try {
        // Start first toggle; rapid second click while pending is ignored
        const firstPromise = settings.setFeatureFlag('prima-auto-deploy', true);
        assert.equal(settings.pendingFlags['prima-auto-deploy'], true);

        const pendingRendered = render(FeatureFlags, { props: { presenter: settings } });
        assert.match(pendingRendered.body, /Saving…/);
        assert.match(pendingRendered.body, /disabled/);

        const duplicateResult = await settings.setFeatureFlag('prima-auto-deploy', false);
        assert.equal(duplicateResult, false);
        assert.equal(fetchCalls, 1);

        // Simulate server failure (400/500)
        resolveFetch(
          new Response(JSON.stringify({ detail: 'storage writer timed out' }), {
            status: 500,
            headers: { 'Content-Type': 'application/json' }
          })
        );
        const finalState = await firstPromise;
        assert.equal(finalState, false);
        assert.equal(settings.isFeatureFlagEnabled('prima-auto-deploy'), false);
        assert.equal(settings.pendingFlags['prima-auto-deploy'], undefined);
        assert.equal(settings.flagError, 'storage writer timed out');

        const errorRendered = render(FeatureFlags, { props: { presenter: settings } });
        assert.match(errorRendered.body, /data-testid="feature-flags-error"/);
        assert.match(errorRendered.body, /storage writer timed out/);
      } finally {
        globalThis.fetch = originalFetch;
      }
    });
  } finally {
    await server.close();
  }
});
