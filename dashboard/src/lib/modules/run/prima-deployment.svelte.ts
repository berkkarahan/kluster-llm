import type { RunInteractor } from './interactor.svelte';
import type { RuntimesInteractor } from '../runtimes/interactor.svelte';
import { primaPlacementError } from '../runtimes/presenter';
import { runtimeRunReason } from './runtime-readiness';
import { HttpResponseError } from '../../shared/http.svelte';

export type DeploymentSnapshot = {
  epoch: number;
  revision: number;
  key: string;
  runtimeId: string;
  settings: Record<string, unknown>;
  plan: Record<string, any>;
};

export interface StageTask {
  id?: string;
  stage_name: 'materialize' | 'asset_sync' | 'node_profile' | 'link_profile' | 'plan_qualification';
  stage_order: number;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'cancelled' | 'interrupted';
  attempt: number;
  progress_fraction: number;
  progress_label: string;
  bytes_done: number;
  bytes_total: number;
  error_message: string | null;
  started_at?: number | null;
  finished_at?: number | null;
}

export interface LinkMatrixEntry {
  source: string;
  target: string;
  latency_ms: number;
  bandwidth_bytes_s: number;
}

export interface PrepareProfileCoverage {
  run_id: string;
  coverage_status: string;
  node_profiles: Record<string, any>;
  link_matrix: LinkMatrixEntry[];
  highs_scheduler?: any;
  qualification?: any;
}

export interface PrepareProfileRun {
  run_id: string;
  job_id: string;
  request_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
  phase: string;
  current_stage: string;
  include_profile: boolean;
  runtime_id: string;
  plan_digest: string;
  manifest_digest?: string | null;
  manifest?: any;
  created_at: number;
  started_at?: number | null;
  updated_at?: number | null;
  finished_at?: number | null;
  expires_at?: number | null;
  error?: string | null;
  stages: StageTask[];
  nodes: Record<string, {
    role: string;
    status: string;
    backend: string;
    phase?: string;
    progress?: { fraction?: number; label?: string; detail?: string; rate_bps?: number; eta_seconds?: number };
    [key: string]: any;
  }>;
  progress?: {
    phase: string;
    stage: string;
    fraction: number;
    pct: number;
    label: string;
    bytes_done: number;
    bytes_total: number;
    step_index: number;
    step_total: number;
    rate_bps?: number;
    eta_seconds?: number;
    rate_kind?: string;
    updated_at?: number;
  };
  qualification?: any;
}

const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value));
function freeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    Object.freeze(value);
    for (const v of Object.values(value)) freeze(v);
  }
  return value;
}

/** Runtime-bound operations over RunInteractor's one editable draft, never a second settings store. */
export class PrimaDeployment {
  runtimeId = $state('');
  preview = $state.raw<DeploymentSnapshot | null>(null);
  review = $state.raw<DeploymentSnapshot | null>(null);
  revision = $state(0);
  inspecting = $state(false);
  preparing = $state(false);
  pendingAction = $state<'prepare' | 'review' | null>(null);
  planningError = $state('');
  launchState = $state<'idle' | 'sending' | 'accepted' | 'unknown'>('idle');
  error = $state('');
  notice = $state('');

  // Authoritative SQLite Prepare & Profile state
  activeRun = $state.raw<PrepareProfileRun | null>(null);
  coverage = $state.raw<PrepareProfileCoverage | null>(null);
  lastPlan = $state.raw<Record<string, any> | null>(null);
  retryingStage = $state<string | null>(null);
  diagnosticsLogs = $state.raw<any[] | null>(null);

  private epoch = $state(0);
  private key = '';
  private timer: ReturnType<typeof setTimeout> | null = null;
  private read: AbortController | null = null;
  private pending: { key: string; promise: Promise<unknown> } | null = null;
  private preparationRequest = $state.raw<{ snapshot: DeploymentSnapshot; profile: boolean; requestId: string; unknown: boolean } | null>(null);
  private refreshedJobs = new Set<string>();
  private participantIds = $state.raw<string[]>([]);
  private pollTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(readonly run: RunInteractor, readonly runtimes: RuntimesInteractor) {
    run.primaEstimate = (delay, force) => this.schedule(delay, force);
  }

  settings() {
    return copy({ ...this.run.networkPlanPayload(), settings_source: 'defaults', remember: false, advanced_open: false });
  }

  /** Only current lifecycle identity, never historical terminal loads or progress telemetry. */
  private get lifecycleIdentity() {
    const load = this.run.loadProgress;
    const loading = load?.phase === 'starting' || load?.phase === 'loading';
    return [
      !!this.run.status?.inference_up,
      this.run.status?.process_pid ?? null,
      loading ? load.operation_id || load.started_at || true : null,
      this.run.busy === 'run' || this.run.busy === '/v2/inference/runs',
    ];
  }

  get lifecycleReason() {
    return this.lifecycleIdentity.some(Boolean)
      ? 'Use Stop model explicitly, wait for inference and loading to settle, then choose Prepare or Review. Prima never replaces or stops an existing run automatically.'
      : '';
  }

  /** Exclude volatile capacity and heartbeat counters; retain participant identities and readiness. */
  semanticKey() {
    const status = this.run.status;
    const selected = this.participantIds;
    const members = status?.members ?? {};
    const inventory = Object.keys(members).sort().map(id => {
      const m = members[id] as unknown as Record<string, unknown>;
      const devices = Array.isArray(m.devices) ? m.devices.map((d: any) => [d.index, d.name, d.uuid, d.backend, d.device_identity, d.driver_identity]) : [];
      const prepared = selected.includes(id) ? status?.runtimes?.nodes?.find(n => n.node_id === id) : undefined;
      return [
        id,
        m.host,
        devices,
        m.accel_backend,
        selected.includes(id) ? m.online : undefined,
        prepared?.phase,
        prepared?.prepared?.runtime_id,
        prepared?.channel?.connected,
      ];
    });
    let settings: unknown;
    try {
      settings = this.settings();
    } catch {
      settings = { invalid: this.run.extraArgsText };
    }
    return JSON.stringify([
      this.runtimeId,
      this.run.selectionGeneration,
      settings,
      this.run.localDraftErrors,
      this.run.stopParseError,
      this.run.live.connected,
      this.run.live.deliveryHealth,
      this.run.live.areaHealth?.status?.availability,
      status?.role,
      this.lifecycleIdentity,
      status?.runtimes?.target,
      status?.runtimes?.target_state,
      inventory,
      status?.managed_networks?.filter(n => n.network_id === this.run.selectedNetworkId).map(n => [n.network_id, n.observed_state]),
    ]);
  }

  changed() {
    const key = this.semanticKey();
    if (key === this.key) return false;
    this.key = key;
    ++this.epoch;
    ++this.revision;
    if (this.planningError && this.error === this.planningError) this.error = '';
    this.planningError = '';
    if (this.review) this.notice = this.lifecycleReason || 'Review revoked: draft, runtime, connection, lifecycle or inventory changed.';
    this.review = null;
    this.read?.abort();
    this.read = null;
    this.pending = null;
    this.inspecting = false;
    this.run.estimating = false;
    this.run.planResult = null;
    // NOTE: Decoupled: do NOT clear this.lastPlan, this.coverage, or this.activeRun on draft edits!
    return true;
  }

  async setRuntime(id: string) {
    if (id === this.runtimeId && this.run.settingsScope === 'primacpp' && this.run.settingsRuntimeId === id) return;
    this.runtimeId = id;
    this.changed();
    await this.run.setSettingsScope('primacpp', id);
    this.changed();
  }

  get fresh() {
    return !!this.preview && this.preview.epoch === this.epoch && this.preview.key === this.semanticKey();
  }

  get latestPlan(): Record<string, any> | null {
    return this.preview?.plan ?? this.lastPlan ?? null;
  }

  get draftModified(): boolean {
    return !this.fresh && !!this.preview && this.preview.epoch < this.epoch;
  }

  get failedStage(): StageTask | null {
    return this.activeRun?.stages?.find(s => s.status === 'failed' || s.status === 'interrupted') ?? null;
  }

  get canRetryStage(): boolean {
    return !!this.failedStage && this.activeRun?.status !== 'running' && !this.retryingStage;
  }

  get profileCoverageStatus(): string {
    if (this.coverage?.coverage_status) return this.coverage.coverage_status;
    const plan = this.latestPlan;
    return plan?.profiling?.status ?? 'Unmeasured';
  }

  get qualificationStatus(): string {
    const plan = this.latestPlan;
    if (!plan) return 'Unknown';
    if (plan.inference_supported === true && !plan.qualification_errors?.length) return 'Qualified';
    return plan.qualification_errors?.join('; ') || 'Not confirmed';
  }

  get architectureSupportStatus(): string {
    const plan = this.latestPlan;
    if (!plan) return 'Pending calculation';
    if (plan.model_support?.supported === false) return 'Unsupported architecture';
    if (plan.model_support?.warning) return 'Support unverified';
    if (plan.model_support?.supported === true) return 'Architecture supported';
    return 'Pending calculation';
  }

  get placementError() {
    return this.fresh ? primaPlacementError(this.preview?.plan ?? null) : '';
  }

  get inspectReason() {
    if (this.run.status?.role !== 'master') return 'Only the master can plan or deploy.';
    if (!this.run.live.connected) return 'Connection lost. Reconnect before planning or deploying.';
    if (
      ['unavailable', 'denied', 'loading'].includes(this.run.live.areaHealth?.status?.availability ?? '') ||
      ['loading', 'unavailable', 'reconnecting', 'paused'].includes(this.run.live.deliveryHealth ?? '')
    ) {
      return 'Current cluster status is unavailable. Wait for status reconciliation before planning or deploying.';
    }
    if (!this.runtimeId || !this.run.selectedModel) return 'Select a model and an installed Prima runtime.';
    if (this.run.settingsScope !== 'primacpp' || this.run.settingsRuntimeId !== this.runtimeId) {
      return 'Load the Prima model settings for this preview runtime first.';
    }
    if (this.run.settingsLoading) return 'Loading model configuration…';
    if (this.run.settingsLoadError) return this.run.settingsLoadError;
    if (Object.keys(this.run.localDraftErrors).length || this.run.stopParseError) return 'Fix invalid draft inputs first.';
    return '';
  }

  private get actionPrerequisiteReason() {
    if (this.inspectReason) return this.inspectReason;
    if (this.lifecycleReason) return this.lifecycleReason;
    if (this.launchState === 'unknown') return 'Run outcome unknown. Reconcile status; do not resend the deployment.';
    if (this.launchState === 'sending') return 'Submitting reviewed deployment…';
    if (this.preparationRequest?.unknown) return 'Reconcile the uncertain preparation request before continuing.';
    if (this.runtimes.preparation.active || this.preparing) return 'Preparation is running or awaiting local settlement.';
    return '';
  }

  get prepareActionReason() {
    if (this.pendingAction) return 'Finishing the requested deployment action…';
    return this.actionPrerequisiteReason || (this.fresh ? this.placementError : '');
  }

  get reviewActionReason() {
    if (this.pendingAction) return 'Finishing the requested deployment action…';
    return this.fresh ? this.reviewReason : this.actionPrerequisiteReason;
  }

  get prepareReason() {
    return this.actionPrerequisiteReason || (!this.fresh ? 'A current deployment plan is not available.' : this.placementError);
  }

  get reviewReason() {
    if (this.actionPrerequisiteReason) return this.actionPrerequisiteReason;
    if (!this.fresh) return 'A current deployment plan is not available.';
    const plan = this.preview!.plan;
    if (plan.deployment_review_version !== 1 || !/^[0-9a-f]{64}$/.test(plan.plan_digest ?? '')) {
      return 'This server cannot guard reviewed deployments. Upgrade the control API; unguarded launch is disabled.';
    }
    const target = this.run.status?.runtimes?.target?.version;
    if (target?.runtime_id !== this.runtimeId || target?.engine !== 'primacpp') {
      return 'Selected runtime differs from the cluster target. Set the target in Settings, then choose Prepare or Review.';
    }
    if (this.placementError) return this.placementError;
    if (plan.inference_supported !== true || plan.qualification_errors?.length) {
      return (plan.qualification_errors ?? []).join('; ') || 'Inference qualification is not confirmed.';
    }
    if (plan.fit_status === 'overflow') return String(plan.hint ?? 'The deployment exceeds available memory.');
    if ((plan.prima_topology?.ranks?.length ?? 0) > 1 && (!this.jobMatches || this.runtimes.preparation.job?.phase !== 'prepared')) {
      return 'Prepare the exact current distributed plan before reviewing. A previous placement is not current evidence.';
    }
    return runtimeRunReason(this.run.status?.runtimes, this.run.status?.node_id ?? '', plan) ?? '';
  }

  schedule(delay = 300, force = false) {
    this.changed();
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      if (force || !this.fresh) void this.inspect(force);
    }, delay);
  }

  inspect(force = false): Promise<unknown> {
    this.changed();
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (this.inspectReason || !this.run.validateRunSettings()) return Promise.resolve(null);
    const key = this.key;
    if (this.pending?.key === key) return this.pending.promise;
    if (this.fresh && !force) return Promise.resolve(this.preview?.plan);
    if (force) {
      ++this.epoch;
      this.review = null;
      this.run.planResult = null;
    }
    const epoch = this.epoch, runtimeId = this.runtimeId, settings = this.settings();
    this.read?.abort();
    const controller = new AbortController();
    this.read = controller;
    this.inspecting = true;
    this.run.estimating = true;
    this.error = '';
    this.planningError = '';
    const promise = this.runtimes.primaPlan(runtimeId, settings, controller.signal).then(plan => {
      if (epoch !== this.epoch || key !== this.semanticKey() || controller.signal.aborted) return null;
      if (plan.runtime_id !== runtimeId) {
        throw new Error('Preview runtime identity is missing or mismatched. Retry planning for the selected runtime; this response cannot authorize deployment.');
      }
      this.participantIds = (plan.prima_topology?.ranks ?? []).map((rank: any) => String(rank.node_id));
      this.lastPlan = copy(plan);
      this.preview = freeze({ epoch, revision: this.revision, key, runtimeId, settings, plan: copy(plan) });
      // Topology can add participant readiness to the semantic key; bind the accepted snapshot to it.
      this.key = this.semanticKey();
      this.preview = freeze({ ...this.preview, key: this.key });
      this.run.planResult = copy(plan);
      return plan;
    }).catch(error => {
      if (epoch === this.epoch && !controller.signal.aborted) {
        this.planningError = String(error);
        this.error = this.planningError;
        // Do NOT null out this.lastPlan on transient errors like 409 unavailable nodes
      }
      return null;
    }).finally(() => {
      if (epoch === this.epoch) {
        this.inspecting = false;
        this.run.estimating = false;
        this.pending = null;
      }
    });
    this.pending = { key, promise };
    return promise;
  }

  /** One explicit action can await the shared read; a changed draft never inherits that intent. */
  private async withCurrentPlan<T>(action: 'prepare' | 'review', complete: () => T | Promise<T>): Promise<T | null> {
    this.changed();
    if (this.pendingAction) return null;
    const reason = action === 'prepare' ? this.prepareActionReason : this.reviewActionReason;
    if (reason || !this.run.validateRunSettings(true)) {
      this.error = reason || 'Fix validation errors.';
      return null;
    }
    this.pendingAction = action;
    const epoch = this.epoch;
    this.notice = '';
    try {
      await this.inspect();
      this.changed();
      if (epoch !== this.epoch) {
        this.notice = 'Deployment changed while calculating. Choose Prepare or Review again for the current draft.';
        return null;
      }
      if (!this.fresh) return null;
      const blocked = action === 'prepare' ? this.prepareReason : this.reviewReason;
      if (blocked || !this.run.validateRunSettings(true)) {
        this.error = blocked || 'Fix validation errors.';
        return null;
      }
      return await complete();
    } finally {
      this.pendingAction = null;
    }
  }

  async openReview() {
    return this.withCurrentPlan('review', () => {
      this.review = freeze(copy(this.preview!));
      this.error = '';
      return this.review;
    });
  }

  async launch() {
    const approved = this.review;
    if (!approved || this.reviewReason) return;
    this.launchState = 'sending';
    this.error = '';
    try {
      await this.run.http.loadToken();
      this.changed();
      // The coordinator's own sending state is not a competing run. Recheck live lifecycle after auth.
      if (this.lifecycleReason) {
        this.review = null;
        this.launchState = 'idle';
        this.error = this.lifecycleReason;
        return;
      }
      if (this.review !== approved || approved.epoch !== this.epoch || approved.key !== this.semanticKey() || !this.run.validateRunSettings(true)) {
        this.launchState = 'idle';
        this.error = 'Review changed before submission. Review the updated deployment again.';
        return;
      }
      // No form reads beyond this boundary. Unknown responses never trigger replay.
      const payload = { ...copy(approved.settings), review: { runtime_id: approved.runtimeId, plan_digest: approved.plan.plan_digest } };
      await this.run.http.command('/v2/inference/runs', payload);
      this.launchState = 'accepted';
      this.review = null;
    } catch (error) {
      this.review = null;
      const rejected = error instanceof HttpResponseError && error.code !== 'PRIMA_RUN_REFUSED'
        && ([401, 403, 422].includes(error.status) || (error.status === 409 && error.code === 'DEPLOYMENT_REVIEW_CHANGED'));
      if (rejected) {
        // These responses precede dispatch. A fresh guarded review is required, never an automatic retry.
        this.launchState = 'idle';
        this.preview = null;
        this.run.planResult = null;
        ++this.epoch;
        this.error = `Deployment rejected: ${String(error)}. Review the updated deployment again before submitting.`;
      } else {
        this.launchState = 'unknown';
        this.error = `Run outcome unknown: ${String(error)}. Refresh status before any further action; no retry was sent.`;
      }
    }
  }

  async reconcile() {
    try {
      await this.run.http.refresh();
      this.notice = 'Status refreshed. Check the active configuration and activity log. An ambiguous request is not automatically safe to retry.';
    } catch (error) {
      this.error = String(error);
    }
  }

  async prepare(profile: boolean, retry = false) {
    if (this.pendingAction || this.preparing) return;
    if (retry) return this.submitPreparation(profile, true);
    return this.withCurrentPlan('prepare', () => this.submitPreparation(profile, false));
  }

  private async submitPreparation(profile: boolean, retry: boolean) {
    this.changed();
    if (retry) {
      if (!this.preparationRequest?.unknown || this.preparing || this.run.status?.role !== 'master' || !this.run.live.connected || this.lifecycleReason) return;
    } else if (this.prepareReason || !this.run.validateRunSettings(true)) {
      this.error = this.prepareReason || 'Fix validation errors.';
      return;
    }
    if (!retry) {
      if (this.preparationRequest?.unknown) {
        this.error = 'Previous preparation outcome is unknown. Retry that same request or reconcile its status first.';
        return;
      }
      this.preparationRequest = { snapshot: freeze(copy(this.preview!)), profile, requestId: crypto.randomUUID(), unknown: false };
    }
    const request = this.preparationRequest;
    if (!request || (!retry && request.snapshot.key !== this.semanticKey())) {
      this.error = 'Preparation request belongs to an older draft. Reconcile it before preparing a changed draft.';
      return;
    }
    this.preparing = true;
    this.error = '';
    try {
      await this.run.http.loadToken();
      this.changed();
      if ((!retry && (request.snapshot.epoch !== this.epoch || request.snapshot.key !== this.semanticKey())) || this.lifecycleReason || this.run.status?.role !== 'master' || !this.run.live.connected) {
        this.error = this.lifecycleReason || 'Draft or runtime changed before preparation submission. Choose Prepare again for the current draft.';
        return;
      }
      const job = await this.runtimes.preparePrima(
        request.snapshot.runtimeId,
        request.snapshot.settings,
        request.snapshot.plan.plan_digest,
        request.profile,
        request.requestId,
      );
      this.preparationRequest = { ...request, unknown: false };
      if (job && (job.run_id || job.job_id)) {
        this.activeRun = job as any;
        this.startPollingRun(job.run_id || job.job_id);
      }
      this.runtimes.preparation.observe(job);
    } catch (error) {
      this.preparationRequest = { ...request, unknown: true };
      this.error = `Preparation outcome unknown: ${String(error)}. Retry only the same request.`;
    } finally {
      this.preparing = false;
    }
  }

  get uncertainPreparation() {
    return !!this.preparationRequest?.unknown;
  }

  get jobMatches() {
    const job = this.activeRun || this.runtimes.preparation.job;
    return (
      this.fresh &&
      !!job &&
      job.runtime_id === this.runtimeId &&
      job.plan_digest === this.preview?.plan.plan_digest &&
      (!this.preparationRequest || this.preparationRequest.snapshot.revision === this.preview?.revision)
    );
  }

  onPrepared(job: Record<string, any>) {
    if (job.phase !== 'prepared' || job.local_work_pending !== false || this.refreshedJobs.has(job.job_id)) return;
    this.refreshedJobs.add(job.job_id);
    this.activeRun = job as any;
    void this.fetchCoverage(job.job_id);
    if (!this.jobMatches) return;
    this.preview = null;
    this.review = null;
    this.notice = 'Preparation completed. Updating the plan; if placement changed, explicitly prepare the new plan.';
    this.schedule(0);
  }

  // ---------------------------------------------------------------------------
  // SQLite Prepare & Profile Persistence & Independent Stage Retry
  // ---------------------------------------------------------------------------

  private get requestFn(): (<T = any>(path: string, method?: string, body?: unknown, signal?: AbortSignal) => Promise<T>) | null {
    if (typeof this.run.http?.networkRequest === 'function') {
      return this.run.http.networkRequest.bind(this.run.http);
    }
    if (typeof (this.runtimes as any).http?.networkRequest === 'function') {
      return (this.runtimes as any).http.networkRequest.bind((this.runtimes as any).http);
    }
    return null;
  }

  async fetchRun(runId?: string): Promise<PrepareProfileRun | null> {
    const id = runId || this.activeRun?.run_id || this.runtimes.preparation.job?.job_id;
    if (!id) return null;
    try {
      let data: any = null;
      if (typeof this.runtimes.primaJob === 'function') {
        data = await this.runtimes.primaJob(id);
      } else if (this.requestFn) {
        data = await this.requestFn<PrepareProfileRun>(
          `/v2/cluster/prima-preparations/${encodeURIComponent(id)}`,
          'GET',
        );
      }
      if (data && (data.run_id || data.job_id)) {
        this.activeRun = data;
        this.runtimes.preparation.observe(data);
        if (data.status === 'completed' && !this.coverage) {
          void this.fetchCoverage(id);
        }
      }
      return data;
    } catch {
      return null;
    }
  }

  async fetchCoverage(runId?: string): Promise<PrepareProfileCoverage | null> {
    const id = runId || this.activeRun?.run_id || this.runtimes.preparation.job?.job_id;
    if (!id || !this.requestFn) return null;
    try {
      const data = await this.requestFn<PrepareProfileCoverage>(
        `/v2/cluster/prima-preparations/${encodeURIComponent(id)}/coverage`,
        'GET',
      );
      if (data) {
        this.coverage = data;
      }
      return data;
    } catch {
      return null;
    }
  }

  async fetchLogs(runId?: string, stage?: string, node?: string, level?: string): Promise<any[]> {
    const id = runId || this.activeRun?.run_id || this.runtimes.preparation.job?.job_id;
    if (!id || !this.requestFn) return [];
    try {
      const params = new URLSearchParams();
      if (stage) params.set('stage', stage);
      if (node) params.set('node', node);
      if (level) params.set('level', level);
      params.set('limit', '300');
      const data = await this.requestFn<{ run_id: string; logs: any[]; text: string }>(
        `/v2/cluster/prima-preparations/${encodeURIComponent(id)}/logs?${params.toString()}`,
        'GET',
      );
      this.diagnosticsLogs = data?.logs ?? [];
      return this.diagnosticsLogs ?? [];
    } catch {
      return [];
    }
  }

  async retryStage(stageName?: string, nodeId?: string): Promise<unknown> {
    const runId = this.activeRun?.run_id || this.runtimes.preparation.job?.job_id;
    if (!runId) {
      this.error = 'No preparation run available to retry.';
      return null;
    }
    const targetStage = stageName || this.failedStage?.stage_name;
    this.retryingStage = targetStage || 'stage';
    this.error = '';
    try {
      if (!this.requestFn) {
        throw new Error('HTTP network transport unavailable');
      }
      const url = targetStage
        ? `/v2/cluster/prima-preparations/${encodeURIComponent(runId)}/retry?stage=${encodeURIComponent(targetStage)}`
        : `/v2/cluster/prima-preparations/${encodeURIComponent(runId)}/retry`;
      const result = await this.requestFn<Record<string, any>>(url, 'POST', {
        stage_name: targetStage ?? null,
        node_id: nodeId ?? null,
      });
      // Optimistically update stages locally
      if (this.activeRun && targetStage) {
        const updatedStages = (this.activeRun.stages || []).map(s => {
          if (s.stage_name === targetStage) {
            return {
              ...s,
              status: 'running' as const,
              error_message: null,
              progress_fraction: 0.05,
              progress_label: 'Retrying stage...',
            };
          }
          return s;
        });
        this.activeRun = {
          ...this.activeRun,
          status: 'running',
          phase: 'preparing',
          current_stage: targetStage,
          stages: updatedStages,
        };
      }
      this.startPollingRun(runId);
      return result;
    } catch (err) {
      this.error = `Stage retry failed: ${String(err)}`;
      return null;
    } finally {
      this.retryingStage = null;
    }
  }

  async cancelRun(runId?: string): Promise<unknown> {
    const id = runId || this.activeRun?.run_id || this.runtimes.preparation.job?.job_id;
    if (!id) return null;
    try {
      if (typeof this.runtimes.cancelPrima === 'function') {
        const res = await this.runtimes.cancelPrima(id);
        await this.fetchRun(id);
        return res;
      }
      if (this.requestFn) {
        const res = await this.requestFn(
          `/v2/cluster/prima-preparations/${encodeURIComponent(id)}/cancel`,
          'POST',
        );
        await this.fetchRun(id);
        return res;
      }
      return null;
    } catch (err) {
      this.error = `Failed to cancel run: ${String(err)}`;
      return null;
    }
  }

  private pollingRunId: string | null = null;

  startPollingRun(runId: string) {
    this.stopPollingRun();
    this.pollingRunId = runId;
    const tick = async () => {
      if (this.pollingRunId !== runId) return;
      const run = await this.fetchRun(runId);
      if (this.pollingRunId !== runId) return;
      const isActive = run?.status === 'running' || run?.status === 'queued';
      if (isActive) {
        this.pollTimer = setTimeout(tick, 1000);
      } else {
        this.pollingRunId = null;
        if (run?.status === 'completed') {
          await this.fetchCoverage(runId);
        }
      }
    };
    void tick();
  }

  stopPollingRun() {
    this.pollingRunId = null;
    if (this.pollTimer) {
      clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
  }

  stop() {
    this.stopPollingRun();
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.read?.abort();
    this.read = null;
    this.pending = null;
    ++this.epoch;
    this.review = null;
    this.inspecting = false;
    this.run.estimating = false;
  }
}
