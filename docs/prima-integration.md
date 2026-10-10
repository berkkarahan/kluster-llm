# Prima.cpp preparation and qualification

Prima is an optional engine **under development**. The current integration supports
isolated source builds, topology previews and local profiling. It is not yet a
drop-in inference replacement. Normal target/local selection and inference launch
are blocked by source-controlled qualification gates; llama.cpp remains the default.

The runtime library can resolve and save the supported Prima source through its
existing endpoints using `engine: "primacpp"`, repository
`https://github.com/berkkarahan/prima.cpp.git`, `ref_type: "commit"`, and ref
`aeb3afdd44b58c93e70e53d47ed4ac7e29452ff8`. The response includes `recipe_digest`.
The fork branch is `codex/kluster-integration`; builds always use the immutable commit.
The recipe records upstream `OpenCPIL/prima.cpp` commit
`288df6a2cc2120387e12d6f40798f30cc525c9ea` as provenance. Native changes are already
committed in the fork, so preparation applies no local source patches.
Build using the existing `/v2/node/runtime-builds` resource. Settings → llama.cpp →
Add version offers Prima under qualification. Build and logs are available; selection
requires completed qualification.

Vulkan GPU computation is a mandatory acceptance requirement on Windows, Linux and
macOS (MoltenVK). Qualification includes selected-device GPU offload, profiling,
placement, distributed generation and measured performance. CPU, Metal or CUDA results
cannot establish Vulkan support; a requested Vulkan backend must never fall back to them.

Identity includes repository, commit, engine and build recipe.
Recipe schema 2 / build recipe 7 pins the fork source. Saved builds from the previous
upstream-plus-patch recipe keep their records but must be resolved and rebuilt against
the new source before use; they cannot satisfy the current recipe identity. ZeroMQ dependency
commits and archive digests are pinned. Builds produce static server, CLI and profiler
artifacts in the isolated runtime library and never replace `vendor/bin`.

## Preparation interfaces

All endpoints retain the runtime library's API-token and same-origin rules. Mutation
requires the local node application. IDs refer to persisted resources, not paths.

| Endpoint | Behavior |
| --- | --- |
| `POST /v2/node/runtimes/{runtime_id}/prima-plan` | Master-only, read-only PlanBody preview from current membership; returns ranks, network edges and qualification blockers. |
| `POST /v2/node/prima-profiles` | Start a local profiling job with runtime_id, model, context, threads and UUID request_id. Requires prepared Prima and no active local inference. |
| `GET /v2/node/prima-profiles/{job_id}` | Profiling state, estimate, provenance or failure. |
| `GET /v2/node/prima-profiles/{job_id}/logs` | Bounded profiler output. |
| `POST /v2/node/prima-profiles/{job_id}/cancel` | Cancel and clean up the owned profiler process. |

Topology uses the existing Kluster master as rank zero and includes only planned
workers. Layer windows encode cross-node work; each rank has its own `ngl`, device
order and tensor proportions. Rank launch arguments carry the planned `-ngl` and
`-ts` values; Vulkan device IDs are constrained with `GGML_VK_VISIBLE_DEVICES`.
Zero-layer coordinator windows are supported. Plans that cannot be represented are reported, not
reordered. Ports are derived from data/signal base ports plus rank; the ring and
worker/master edges must eventually pass network admission before any inference.

On macOS Vulkan source builds, the staged runtime includes a relocated Vulkan loader,
MoltenVK library and ICD manifest. The runtime manifest retains the staged ICD path,
and modified Mach-O files are ad-hoc signed after relocation. Device enumeration checks
this packaged path. It confirms MoltenVK discovery only; it does not qualify model
execution or performance.

Profiling is an explicit operation, bounded to ten minutes and 1 MiB output. Vulkan
profiles use a Vulkan-backend Q4K kernel measurement and expose Vulkan copy/bandwidth
measurements; the profiler refuses CPU fallback for its Vulkan throughput value. These
kernel estimates are not decode benchmarks and cannot satisfy the release performance
gate. Profiles are keyed by runtime/model/hardware/settings/backend, bounded to 256
cache records and expire after one day. Ordinary topology previews run no profiler,
transfer no model and start no runtime processes.

## Remaining qualification

Windows and all accelerator contracts, current server/model/launch/API features,
arbitrary placement, signed rank lifecycle, authenticated remote asset transfer,
remote profiling/planner integration and physical paired benchmarks remain open.
The source-controlled recipe records these gates, including a separate mandatory
`vulkan_gpu_inference` gate, as incomplete. Editing an artifact
manifest cannot turn them into successful qualification.

Track the full accepted implementation in [feature tasks](../specs/017-prima-integration/tasks.md).

## Manifest and cluster preparation foundation

Build recipe 7 requires `execution_manifest_v1`. Native manifest checking is available
through `--execution-manifest FILE --rank N --check-execution-manifest`. Manifest mode
prevents automatic profiling/scheduling and weak-node pruning even with a zero-layer
rank zero. Current execution is restricted to exact single-window layer layouts;
segmented revisits, row/tensor and remote-expert layouts fail explicitly. A configured
rank report is not proof that Vulkan executed its layers.

The master can preview a saved prepared runtime using `kllm plan --runtime-id ID --model MODEL`.
`kllm prepare --runtime-id ID --model MODEL` starts a master-owned asset job;
`kllm profile` also requests selected-worker kernel profiling. Workers need the same
prepared source/recipe and `prima_control_version: 1`; missing preparation reports an
upgrade/build error rather than launching a different runtime.

`POST /v2/cluster/prima-preparations` accepts runtime_id, settings, plan_digest,
request_id and include_profile. Status and bounded summaries are available at
`GET /v2/cluster/prima-preparations/{job_id}` and `/{job_id}/logs`; cancellation is
`POST /v2/cluster/prima-preparations/{job_id}/cancel`. Existing local profile routes remain.
The runtime Settings screen exposes preview, preparation, profiling and participant status.

Asset chunks travel from master to worker over the existing paired signed channel.
Each job declares digests/sizes and binds chunks to its operation and sender. Resumes
revalidate offsets and digest verification precedes publication. Partial assets remain
resumable after interruption; cancelled/expired jobs do not grant execution ownership.

The standalone [Windows assessment guide](prima-windows-assessment.md) describes the
published assessment runner and its limits. Baseline backports, production engine dispatch
and network admission, measured end-to-end profiles and physical distributed execution
remain incomplete. Qualification gates stay false.
