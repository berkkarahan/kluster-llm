"""Build the pinned Prima source in an installer-owned, isolated directory."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from kllm.modules.primacpp.interactors.recipe import (
    recipe,
    recipe_digest,
    qualification_errors,
)
from kllm.modules.runtimes.entities import SourceRequest, runtime_id
from kllm.modules.runtimes.interactors.build import _materialize_source, _vulkan_build_environment
from kllm.modules.runtimes.interactors.registry import atomic_json, file_digest
from kllm.modules.runtimes.interactors.source import accept_prima_source, git_environment


def _remove_tree(install, path: Path, what: str) -> None:
    """Remove an installer-owned tree via the installer's lock-aware remover."""
    remover = getattr(install, "_remove_vendor_tree", None)
    if remover is not None:
        remover(path, what)
    else:
        shutil.rmtree(path, ignore_errors=True)


def prima_cmake_flags(install, resolved: str) -> list[str]:
    if hasattr(install, "_cmake_prima_flags"):
        flags = list(install._cmake_prima_flags(resolved))
    else:
        flags = [
            *install._cmake_backend_flags(resolved),
            "-DUSE_HIGHS=ON",
            "-DBUILD_SHARED_LIBS=OFF",
            "-DLLAMA_BUILD_TESTS=OFF",
            "-DGGML_BLAS=OFF",
            "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
        ]
    if "-DUSE_HIGHS=ON" not in flags:
        raise ValueError("Prima builds must enable HiGHS via -DUSE_HIGHS=ON")
    return flags


def parse_device_report(
    install, stdout: str, stderr: str = "", *, require_highs: bool = False
) -> dict:
    capability_lines = [
        line.removeprefix("KLLM_PRIMA_CAPABILITIES=")
        for line in stdout.splitlines()
        if line.startswith("KLLM_PRIMA_CAPABILITIES=")
    ]
    if len(capability_lines) != 1:
        raise ValueError("Prima staged server returned an invalid capability report")
    try:
        capabilities = json.loads(capability_lines[0])
    except json.JSONDecodeError as error:
        raise ValueError("Prima staged server returned malformed capability JSON") from error
    required_features = {"layer_window", "tensor_split", "ngl", "ring_tcp", "execution_manifest_v1"}
    if require_highs:
        required_features.add("highs")
    if not isinstance(capabilities, dict):
        raise ValueError("Prima staged server reported an invalid execution contract")
    features = capabilities.get("features")
    devices = capabilities.get("devices")
    reported_backends = capabilities.get("backends")
    if isinstance(features, list) and any(not isinstance(feature, str) or not feature for feature in features):
        raise ValueError("Prima capability report contains an invalid feature entry")
    if (
        capabilities.get("schema_version") != 1
        or capabilities.get("ring_protocol_version") != 1
        or not isinstance(features, list)
        or not required_features.issubset(features)
        or not isinstance(devices, list)
        or not isinstance(reported_backends, list)
    ):
        raise ValueError("Prima staged server reported an incompatible execution contract")
    actual = install.parse_list_devices_backends(stdout + stderr)
    reported = {str(value).lower() for value in reported_backends}
    if reported != set(actual):
        raise ValueError("Prima device list and capability report disagree")
    if any(
        not isinstance(device, dict)
        or str(device.get("backend", "")).lower() not in reported
        or not isinstance(device.get("name"), str)
        or not isinstance(device.get("description"), str)
        for device in devices
    ):
        raise ValueError("Prima capability report contains an invalid device entry")
    capabilities["backends"] = sorted(reported)
    # A pinned build may not advertise its model architectures (older pins). When it
    # does, the list must be well formed; support decisions are made from it later.
    reported_architectures = capabilities.get("architectures")
    if "architectures" in capabilities and (
        not isinstance(reported_architectures, list)
        or any(not isinstance(name, str) or not name for name in reported_architectures)
    ):
        raise ValueError("Prima capability report contains an invalid model architecture list")
    if isinstance(reported_architectures, list):
        capabilities["architectures"] = sorted({name for name in reported_architectures})
    if "vulkan_device_identity_v1" in features:
        from kllm.modules.primacpp.interactors.devices import select_vulkan_devices

        vulkan = [d for d in devices if d["backend"].lower() == "vulkan"]
        select_vulkan_devices(vulkan, [d["name"] for d in vulkan])
    return capabilities


def package_macos_vulkan(binaries: dict, output: Path, environment: dict[str, str] | None) -> dict[str, str] | None:
    """Relocate Vulkan loader/ICD assets so a staged macOS build is self-contained."""
    if platform.system() != "Darwin" or not environment:
        return environment
    icd_value = environment.get("VK_ICD_FILENAMES")
    if not icd_value:
        raise ValueError("macOS Vulkan build did not provide a Vulkan ICD manifest")
    icd_source = Path(icd_value.split(os.pathsep)[0]).resolve()
    try:
        icd = json.loads(icd_source.read_text(encoding="utf-8"))
        driver_value = icd["ICD"]["library_path"]
    except (OSError, KeyError, json.JSONDecodeError, TypeError) as error:
        raise ValueError("macOS Vulkan ICD manifest is invalid") from error
    driver_source = Path(driver_value)
    if not driver_source.is_absolute():
        driver_source = (icd_source.parent / driver_source).resolve()
    if not driver_source.is_file():
        raise ValueError("macOS Vulkan ICD driver library is missing")

    server = Path(binaries["llama_server"])
    linked = subprocess.run(["otool", "-L", str(server)], capture_output=True, text=True, timeout=30)
    if linked.returncode:
        raise ValueError("Could not inspect the macOS Prima Vulkan loader dependency")
    loader_lines = [line.strip().split(" ", 1)[0] for line in linked.stdout.splitlines()[1:]]
    loader_source = next(
        (Path(value) for value in loader_lines if Path(value).name.startswith("libvulkan") and value.endswith(".dylib")),
        None,
    )
    if loader_source is None or not loader_source.is_file():
        raise ValueError("macOS Prima Vulkan build does not link a relocatable Vulkan loader")

    library_dir = output / "lib"
    icd_dir = output / "vulkan" / "icd.d"
    library_dir.mkdir(parents=True, exist_ok=True)
    icd_dir.mkdir(parents=True, exist_ok=True)
    loader_target = library_dir / "libvulkan.1.dylib"
    driver_target = library_dir / "libMoltenVK.dylib"
    for target in (loader_target, driver_target):
        if target.exists() or target.is_symlink():
            target.unlink()
    shutil.copy2(loader_source, loader_target)
    shutil.copy2(driver_source, driver_target)
    loader_target.chmod(0o755)
    driver_target.chmod(0o755)
    subprocess.run(
        ["install_name_tool", "-id", "@rpath/libvulkan.1.dylib", str(loader_target)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    for binary in binaries.values():
        subprocess.run(
            ["install_name_tool", "-change", str(loader_source), "@rpath/libvulkan.1.dylib", binary],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        subprocess.run(
            ["install_name_tool", "-add_rpath", "@executable_path/lib", binary],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    relocated_icd = {
        **icd,
        "ICD": {**icd["ICD"], "library_path": "../../lib/libMoltenVK.dylib"},
    }
    icd_target = icd_dir / "MoltenVK_icd.json"
    icd_target.write_text(json.dumps(relocated_icd, sort_keys=True), encoding="utf-8")
    for binary in [loader_target, driver_target, *(Path(path) for path in binaries.values())]:
        subprocess.run(
            ["codesign", "--force", "--sign", "-", str(binary)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    return {**environment, "VK_ICD_FILENAMES": str(icd_target)}


def _compile_and_verify_prima(
    install,
    *,
    vendor: Path,
    output: Path,
    manifest_dir: Path,
    build_id: str,
    repository: str,
    commit: str,
    resolved: str,
    expected: str,
    definition: dict,
    digest: str,
) -> dict:
    build = vendor / "build"
    flags = prima_cmake_flags(install, resolved)
    environment = _vulkan_build_environment(install, resolved)
    print("KLLM_RUNTIME_PHASE=building", flush=True)
    install._run(["cmake", "-S", str(vendor), "-B", str(build), *flags], env=environment)
    install._run(
        [
            "cmake",
            "--build",
            str(build),
            "--config",
            "Release",
            "--target",
            "llama-server",
            "llama-cli",
            "profile-tool",
            "-j",
            "4",
        ],
        env=environment,
    )
    print("KLLM_RUNTIME_PHASE=verifying", flush=True)
    output.mkdir(parents=True, exist_ok=True)
    reset = getattr(install, "_reset_staged_binaries", None)
    predicate = getattr(install, "_owned_prima_artifact", None)
    if reset is not None and predicate is not None:
        # Clear installer-owned Prima artifacts so a backend or recipe change
        # cannot leave a stale llama-server / llama-cli / profile-tool behind.
        reset(output, predicate, what="vendor/prima")
    binaries = {}
    extension = ".exe" if platform.system() == "Windows" else ""
    for name, key in (
        ("llama-server", "llama_server"),
        ("llama-cli", "llama_cli"),
        ("profile-tool", "profile_tool"),
    ):
        candidates = [
            build / "bin" / (name + extension),
            build / "bin/Release" / (name + extension),
        ]
        binary = next((p for p in candidates if p.is_file()), None)
        if binary is None:
            raise ValueError(f"Prima did not build {name}")
        staged = output / binary.name
        shutil.copy2(binary, staged)
        binaries[key] = str(staged.resolve())
    environment = package_macos_vulkan(binaries, output, environment)
    # Probe the staged executable, not a different binary in the source tree.
    help_result = subprocess.run(
        [binaries["llama_server"], "--help"], capture_output=True, text=True, timeout=60
    )
    help_text = help_result.stdout + help_result.stderr
    if help_result.returncode or any(
        flag not in help_text for flag in ("--world", "--rank", "--layer-window", "--list-devices")
    ):
        raise ValueError("Prima staged server does not expose distributed launch controls")
    probe_result = subprocess.run(
        [binaries["llama_server"], "--list-devices"],
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
    )
    if probe_result.returncode:
        raise ValueError("Prima staged server could not enumerate its compute devices")
    capabilities = parse_device_report(
        install, probe_result.stdout, probe_result.stderr, require_highs=True
    )
    actual = capabilities["backends"]
    if "vulkan" in resolved and "vulkan_profile" not in capabilities["features"]:
        raise ValueError("Prima Vulkan build does not expose its Vulkan profiler")
    if resolved != "cpu" and not install.backends_satisfied(expected, actual):
        raise ValueError("Prima staged server cannot satisfy this node's backend contract")
    if resolved != "cpu" and not capabilities["devices"]:
        raise ValueError("Prima staged server reports no usable GPU devices")
    identity = runtime_id(repository, commit, "primacpp", digest)
    manifest = {
        "schema": 1,
        "engine": "primacpp",
        "transport": "prima-ring-tcp",
        "runtime_id": identity,
        "repository": repository,
        "commit": commit,
        "upstream_repository": definition["upstream_repository"],
        "upstream_commit": definition["upstream_commit"],
        "recipe_digest": digest,
        "dependencies": definition["dependencies"],
        "build_id": build_id,
        "backend": expected,
        "capabilities": capabilities,
        "runtime_env": {
            key: value
            for key, value in (environment or {}).items()
            if key in {"VK_ICD_FILENAMES", "VK_DRIVER_FILES"}
        },
        "mode": "source",
        "platform": platform.system(),
        "architecture": platform.machine(),
        "installed_at": time.time(),
        "artifact_dir": str(output.resolve()),
        **binaries,
        "artifacts": {
            p.relative_to(output).as_posix(): file_digest(p)
            for p in output.rglob("*")
            if p.is_file() and p.name not in {"manifest.json", "prima-release.txt"}
        },
        "size_bytes": sum(
            p.stat().st_size
            for p in output.rglob("*")
            if p.is_file() and p.name not in {"manifest.json", "prima-release.txt"}
        ),
        "qualification_errors": qualification_errors({"engine": "primacpp"}),
    }
    atomic_json(manifest_dir / "manifest.json", manifest)
    return manifest


def managed_prima_build(
    install, repository: str, commit: str, destination: Path, backend: str, expected_recipe: str
) -> dict:
    source = SourceRequest(repository=repository, ref=commit, ref_type="commit", engine="primacpp")
    definition = recipe()
    digest = recipe_digest()
    if expected_recipe != digest:
        raise ValueError("Prima build recipe changed; resolve the source again before building")
    accept_prima_source(source, commit, definition)
    destination.mkdir(parents=True, exist_ok=True)
    vendor, output = destination / "source", destination / "bin"
    if vendor.exists():
        raise ValueError(
            "Prima source destination already exists; retry using a new preparation job"
        )
    resolved = install.detect_backend(backend)
    expected = install.stamp_backend_for_install(resolved)
    if not install.ensure_cmake():
        raise ValueError("CMake is required; follow docs/agent-install.md")
    install.ensure_git()
    if hasattr(install, "ensure_prima_deps"):
        install.ensure_prima_deps(resolved)
    print("KLLM_RUNTIME_PHASE=downloading", flush=True)
    _materialize_source(vendor, source.repository, commit)
    return _compile_and_verify_prima(
        install,
        vendor=vendor,
        output=output,
        manifest_dir=destination,
        build_id=destination.name,
        repository=source.repository,
        commit=commit,
        resolved=resolved,
        expected=expected,
        definition=definition,
        digest=digest,
    )


def bootstrap_prima_build(install, vendor: Path, output: Path, backend: str) -> dict:
    definition = recipe()
    digest = recipe_digest()
    repository = definition["source_repository"]
    commit = definition["source_commit"]
    resolved = install.detect_backend(backend)
    expected = install.stamp_backend_for_install(resolved)
    if not install.ensure_cmake():
        raise ValueError("CMake is required; follow docs/agent-install.md")
    install.ensure_git()
    if hasattr(install, "ensure_prima_deps"):
        install.ensure_prima_deps(resolved)
    print("KLLM_RUNTIME_PHASE=downloading", flush=True)
    if (vendor / ".git").is_dir():
        env = git_environment()
        head = subprocess.run(
            ["git", "-C", str(vendor), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            env=env,
        )
        if head.returncode != 0 or head.stdout.strip() != commit:
            _remove_tree(install, vendor, "vendor/prima.cpp")
            _materialize_source(vendor, repository, commit)
        else:
            install._run(["git", "-C", str(vendor), "reset", "--hard", commit], env=env)
            install._run(
                ["git", "-C", str(vendor), "clean", "-fd", "-e", "build"],
                env=env,
            )
    else:
        if vendor.exists():
            _remove_tree(install, vendor, "vendor/prima.cpp")
        _materialize_source(vendor, repository, commit)
    return _compile_and_verify_prima(
        install,
        vendor=vendor,
        output=output,
        manifest_dir=output,
        build_id=output.name,
        repository=repository,
        commit=commit,
        resolved=resolved,
        expected=expected,
        definition=definition,
        digest=digest,
    )
