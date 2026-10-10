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
5. Extend signed commands and native process supervision for rank start/readiness,
   leases, monitoring, stop and bounded recovery. Use run manifests with immutable
   model and runtime identities; admit ring and master-control connectivity.
6. Run explicit profiling jobs; cache measurements by runtime/model/hardware/settings.
   Generate feasible placement candidates in Kluster and choose with complete profiles
   and network measurements. Ordinary previews remain read-only.
7. Show effective engine/placement, preparation and failures in existing dashboard
   modules. Run parity, physical platform/backend and paired benchmark qualification.

Defaults: optional engine; modernize pinned Prima upstream; all existing platforms
and APIs required for release; balanced latency/throughput gate; complete model files
on selected workers initially; source-managed qualification with no environment bypass.
