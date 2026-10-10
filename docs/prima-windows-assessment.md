# Prima Windows assessment handoff

Prima's manifest/preparation foundation and standalone assessment runner are available.
**The complete compatibility/lifecycle/planner plan is not finished.** Normal inference
selection remains blocked. Native API/model backports, arbitrary segmented/row/tensor/
MoE execution, production network admission/dispatch, and measured end-to-end planner
profiling remain open. Compilation and the controlled lifecycle suite cannot close
those items or any physical Vulkan/performance gate.

## Revisions and setup

Use `feat/prima-integration` in `berkkarahan/kluster-llm` and
`codex/kluster-integration` in `berkkarahan/prima.cpp`. The committed Prima recipe is
always the authoritative source SHA and build digest. The fork currently pins
`aeb3afdd44b58c93e70e53d47ed4ac7e29452ff8`, descended from
`288df6a2cc2120387e12d6f40798f30cc525c9ea`. Baseline llama.cpp is `v0.5.0`,
`7fe450e19305b828c199d602c23a8337aaa1f03b`.

Build recipe 7 digest for this handoff:
`084e6674c89d9e454e004df6e6f2a8b926cb285b1dca3b1679afbffd90c485bb`.
Native CPU compilation and contract checks passed on all three CI platforms in
[run 38085012210](https://github.com/berkkarahan/prima.cpp/actions/runs/38085012210).
The [controlled macOS receipt](../specs/017-prima-integration/assessment-macos-controlled.json)
records the separate lifecycle result and explicitly unavailable checks.

From the Windows Kluster checkout, use the normal installer from
[agent-install.md](agent-install.md). It provisions the baseline and Vulkan toolchain;
no separate installer flow is required. Physical device/compiler checks must be
recorded on Windows even when a CI compilation passed.

```powershell
git fetch origin
git switch feat/prima-integration
git pull --ff-only
python scripts/install.py

$primaSpec = Get-Content src/kllm/modules/primacpp/recipe.json | ConvertFrom-Json
$primaRecipeDigest = uv run python -c "from kllm.modules.primacpp.interactors.recipe import recipe_digest; print(recipe_digest())"
$primaRuntimeDir = Join-Path $env:TEMP ("kllm-prima-" + [guid]::NewGuid().ToString())
uv run python scripts/install.py --llama-only --engine primacpp --from-source --backend vulkan --source-repo $primaSpec.source_repository --revision $primaSpec.source_commit --recipe-digest $primaRecipeDigest --runtime-dir $primaRuntimeDir
```

The managed build does not replace `vendor/bin` or start inference. Requested Vulkan
failure is explicit. Preserve build output and `manifest.json`; source identity,
artifact hashes and the selected device report belong in the assessment receipt.

## Controlled checks first

```powershell
uv run python tests/prima_assessment.py --runtime-dir $primaRuntimeDir --suite lifecycle --output prima-lifecycle-windows.json
```

This runs real native manifest validation and controlled Python child-process ownership,
readiness rejection, cancellation/cleanup, resumable-transfer integrity, and lease-expiry
checks. It does not exercise multi-machine model execution or prove GPU offload.

## Supplied dense and MoE models

Provide existing complete GGUF files and every shard/companion. No model is downloaded
by the assessment runner. Substitute actual model paths in these examples:

```powershell
uv run python tests/prima_assessment.py --runtime-dir $primaRuntimeDir --baseline-server vendor/bin/llama-server.exe --dense-model "D:\models\dense.gguf" --moe-model "D:\models\moe.gguf" --backend vulkan --ngl 1 --suite api --output prima-api-windows.json
uv run python tests/prima_assessment.py --runtime-dir $primaRuntimeDir --baseline-server vendor/bin/llama-server.exe --dense-model "D:\models\dense.gguf" --moe-model "D:\models\moe.gguf" --backend vulkan --ngl 1 --suite vulkan --output prima-vulkan-windows.json
```

The runner verifies the pinned Prima artifacts, records model/asset hashes, and starts
isolated loopback servers on unused ports. It captures chat, streaming, structured-output
and tool-request responses against both runtimes, plus observed timing and logs. These
are diagnostic observations, not the complete API or paired performance acceptance suite.
The supplied baseline must report commit `7fe450e`; its executable digest and reported
version are recorded. This is a binary identity check, not a source-build attestation.
HTTP mismatches and error responses are failures, including matching error responses.
GPU execution is explicitly recorded as unverified until selected-device/layer execution
proof exists and produces a separate `unavailable` GPU check. Missing models or
distributed execution also produce `unavailable`, never a pass.
Unavailable cases return exit code 2; failures return 1. A runner exit code of zero does not imply qualification; inspect the receipt's statuses
and `qualification: open` field.

`--suite distributed` currently records that physical distributed model execution is
unavailable. It must not be represented as an executed multi-node test.

All child processes created by the runner are terminated in its cleanup path. It never
selects Prima in an existing agent, alters cluster identities, changes adapters, or starts
worker RPC. Run against isolated supplied models rather than a serving model's mutable
files. Keep receipts/build logs for the next implementation and hardware assessment.

Windows profiler portability uses explicit PSAPI dependencies and the supported
[pagefile enumeration interface](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-enumpagefilesw).
Build CI is separate from physical Vulkan model execution.
