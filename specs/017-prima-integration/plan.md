# Implementation plan

1. Merge remote main without losing local work; follow its VIPER architecture.
2. Add engine identity and reproducible source/patch/dependency provenance to the
   existing runtime library and isolated installer. Preserve llama.cpp wire defaults.
3. Modernize Prima's source to the baseline API/platform/model/launch contract.
   Gate ordinary selection until qualification is complete.
   Prioritize Vulkan GPU execution on Windows/Linux/macOS, including MoltenVK,
   backend-specific profiling and selected-device/offload verification. Treat
   Vulkan as an independent mandatory gate before considering Prima deployable.
4. Derive ranks and network edges from Kluster membership and exact planner output.
   Stage verified model assets through authenticated, resumable transfers.
   Issue source grants per asset, renew them only after exact next-range acknowledgement,
   and bind worker approval renewal to fresh signed operation progress. Keep completed
   workers enrolled until master preparation finishes; expire stalled/cancelled work.
   Worker cache verification/chunk I/O must not block the command event loop.
5. Extend signed commands and native process supervision for rank start/readiness,
   leases, monitoring, stop and bounded recovery. Use run manifests with immutable
   model and runtime identities; admit ring and master-control connectivity.
   Derive exact inbound native ports/peers from approved edges. Use the privileged
   helper's run-owned rule ledger, compose all-node admission before startup and
   retain partial intents until rank stop plus port cleanup acknowledgements.
   T055 supplies the signed Windows helper/contract foundation; T057 composes the
   coordinator barrier, participant intent/cleanup and node-local admission leases.
   T056 retains remaining platform/admin paths, helper service/absence recovery
   and protected IPC/physical OS acceptance. T060/T061 supply local review and its
   dashboard controls without claiming physical acceptance.
   Provide local token-authenticated loopback listener review/resolve with a digest
   binding durable ownership and current rule snapshots. Require owned rank stop;
   exact restoration must never adopt changed rules. An explicit keep-current
   decision preserves OS rules and relinquishes only local listener ownership.
   Show pending local Prima rules in the master and worker network panel, render
   exact peer/port ownership and snapshot details, require acknowledgement before
   keeping rules, and invalidate stale/closed/failed reviews. Verify narrow/wide
   browser interactions without claiming physical OS or cluster acceptance.
   Bound local helper queue/connection/framed I/O with one monotonic deadline.
   On Windows retry thread-handle I/O cancellation until settlement, including
   server dispatch-to-flush gaps; flush replies before named-pipe disconnect.
   Keep timeout distinct from administrator authorization and retain owned intents
   until cleanup acknowledgement. Test isolated real pipes separately from the
   protected service, ACLs and physical firewall acceptance.
   Add a direct backend for already elevated Windows manual adapters using an owned
   private-stdio native policy child. Reuse signed scope/listener checks and durable
   rule ownership while retaining manual address/DHCP boundaries. Correlate replies,
   preserve partial framing across timeout, and fence backend/grant changes plus
   owned child shutdown. Reconcile unchanged signed participant sets before reuse.
   Treat isolated pipe/native-policy coverage and elevated physical acceptance separately.
6. Run explicit profiling jobs; cache measurements by runtime/model/hardware/settings.
   Generate feasible placement candidates in Kluster and choose with complete profiles
   and network measurements. Ordinary previews remain read-only.
7. Show effective engine/placement, preparation and failures in existing dashboard
   modules. Run parity, physical platform/backend and paired benchmark qualification.

Defaults: optional engine; modernize pinned Prima upstream; all existing platforms
and APIs required for release; balanced latency/throughput gate; complete model files
on selected workers initially; source-managed qualification with no environment bypass.


### Performance validation deferred by user - 2026-10-12

The user explicitly requests no further performance-gate validation here and will
check performance on a live Kluster cluster of three computers. Local paired
acceptance and further performance-gate diagnostic experiments are stopped.
Implementation and correctness checks, including functional profiler contracts,
continue. Existing benchmark failures remain historical evidence, not passes.
Preserve the no more than 5% regression and 10% representative distributed
improvement criteria until the user's live measurements can evaluate them. Do not
relax thresholds, retokenize usage, infer a pass or enable an unverified release scope.

Physical distributed cluster gates and validations will be tested later on the
user's three-computer setup. Performance and distributed qualification remain
unpassed. Their deferral is explicit and is not an implementation blocker for
finishing remaining API/model/assets/settings/placement/planner work.
The bounded timing diagnostic already performed is not a performance gate result:
its summary is corrected to insufficient_decode_samples because mixed token-count
prefill graphs include a final single-token projection. No new timing experiment
will extend that sample collection here. The optional diagnostic-mode correctness
repair and its benchmark-rejection checks can be finished independently.
