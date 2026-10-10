import { errors } from './shared/errors';
import { HttpClient } from './shared/http.svelte';
import { LiveInteractor } from './live/interactor.svelte';
import type { LiveProgress } from './live/entities';
import { ClusterInteractor } from './modules/cluster/interactor.svelte';
import type { Status } from './modules/cluster/entities';
import { UsageInteractor } from './modules/usage/interactor.svelte';
import type { UsageLive } from './modules/usage/entities';
import { ModelsInteractor } from './modules/models/interactor.svelte';
import type { DownloadJob } from './modules/models/entities';
import { NodeSettingsInteractor } from './modules/node-settings/interactor.svelte';
import { NetworkInteractor } from './modules/network/interactor.svelte';
import { RunInteractor } from './modules/run/interactor.svelte';
import type { LoadProgress } from './modules/run/entities';
import { IntegrationsInteractor } from './modules/integrations/interactor.svelte';
import { RuntimesInteractor } from './modules/runtimes/interactor.svelte';
import { LogsController } from './modules/logs/controller.svelte';

export type App = {
  http: HttpClient;
  live: LiveInteractor;
  cluster: ClusterInteractor;
  usage: UsageInteractor;
  models: ModelsInteractor;
  settings: NodeSettingsInteractor;
  network: NetworkInteractor;
  runtimes: RuntimesInteractor;
  run: RunInteractor;
  integrations: IntegrationsInteractor;
  logs: LogsController;
  clearErrors: () => void;
  start: () => void;
  stop: () => void;
};

let singleton: App | null = null;

/** One dashboard graph per page load. Later calls return the same instance. */
export function createApp(): App {
  if (singleton) return singleton;

  const http = new HttpClient();
  const live = new LiveInteractor();
  const cluster = new ClusterInteractor(http, live);
  const usage = new UsageInteractor(live);
  const models = new ModelsInteractor(http);
  const settings = new NodeSettingsInteractor(http, live, cluster);
  const network = new NetworkInteractor(http, cluster);
  const run = new RunInteractor(http, live, cluster, models, settings, network);
  const integrations = new IntegrationsInteractor(http, cluster, models, run);
  const runtimes = new RuntimesInteractor(http, cluster, run);
  const logs = new LogsController(http, cluster);

  const clearErrors = () => {
    errors.clear();
    usage.invalidateHistory();
    run.invalidateEstimate();
  };

  cluster.usage = usage;
  cluster.nodeSettings = settings;
  cluster.clearErrors = clearErrors;
  cluster.onStatusReplacing = (current, next) => {
    if (current?.model_name !== next.model_name || current?.active_config?.run_id !== next.active_config?.run_id) {
      integrations.onRunIdentityChanged();
    }
    run.activeRunConfig = next.active_config ?? null;
  };
  cluster.onAccel = (data) => settings.applyAccel(data);
  settings.onConfigSaved = () => run.onNodeConfigSaved();
  settings.onCpuDevice = () => {
    run.nodeShares = null;
    run.scheduleEstimate();
  };
  models.bindRun(run);

  http.refresh = () => live.refresh();
  http.loadModels = () => models.loadModels();
  http.onDownloadAccepted = (id) => models.noteAcceptedDownload(id);

  live.onArea('status', (data) => cluster.applyStatus(data as Status));
  live.onArea('usage', (data) => usage.applyUsage(data as UsageLive));
  live.onArea('load', (data) => run.applyLoad(data as LiveProgress<LoadProgress>));
  live.onArea('download', (data) => models.applyDownloadRecords(data as LiveProgress<DownloadJob>));
  live.refreshDirect = () => cluster.refreshDirect();
  live.onConnectionLost = clearErrors;
  live.onUnavailable = () => {
    cluster.statusSeen = true;
  };

  run.watchLive();
  settings.watchLive();
  run.clearErrors = clearErrors;
  run.onStopChat = () => integrations.stopChat();

  const app: App = {
    http,
    live,
    cluster,
    usage,
    models,
    settings,
    network,
    runtimes,
    run,
    integrations,
    logs,
    clearErrors,
    start() {
      errors.start();
      usage.usageError = '';
      usage.historyError = '';
      models.searchError = '';
      run.estimateError = '';
      for (const source of ['usage-request', 'history', 'search', 'estimate']) errors.observe(source, '', '', false);
      run.fieldErrors = {};
      settings.lastApplyError = null;
      cluster.statusSeen = false;
      live.connected = false;
      live.start();
      void http.loadToken();
      void models.loadHfToken();
      void models.loadModels();
      void settings.loadConfig();
      void settings.loadGpus();
    },
    stop() {
      http.apiToken = '';
      live.stop();
      run.stopTimers();
    }
  };
  singleton = app;
  return app;
}
