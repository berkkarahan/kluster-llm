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
    ROOT,
    recipe,
    recipe_digest,
    qualification_errors,
)
from kllm.modules.runtimes.entities import SourceRequest, runtime_id
from kllm.modules.runtimes.interactors.build import _materialize_source, _vulkan_build_environment
from kllm.modules.runtimes.interactors.registry import atomic_json, file_digest
from kllm.modules.runtimes.interactors.source import git_environment


def parse_device_report(install, stdout: str, stderr: str = "") -> dict:
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
    if not isinstance(capabilities, dict):
        raise ValueError("Prima staged server reported an invalid execution contract")
    features = capabilities.get("features")
    devices = capabilities.get("devices")
    reported_backends = capabilities.get("backends")
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
    shutil.copy2(loader_source, loader_target)
    shutil.copy2(driver_source, driver_target)
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


def managed_prima_build(
    install, repository: str, commit: str, destination: Path, backend: str, expected_recipe: str
) -> dict:
    source = SourceRequest(repository=repository, ref=commit, ref_type="commit", engine="primacpp")
    definition = recipe()
    digest = recipe_digest()
    if expected_recipe != digest:
        raise ValueError("Prima build recipe changed; resolve the source again before building")
    if (
        source.repository != definition["source_repository"]
        or commit != definition["source_commit"]
    ):
        raise ValueError(
            "This Prima recipe requires the pinned source repository and commit"
        )
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
    print("KLLM_RUNTIME_PHASE=downloading", flush=True)
    _materialize_source(vendor, source.repository, commit)
    for patch in definition["patches"]:
        install._run(
            ["git", "apply", "--check", str(ROOT / "patches" / patch)],
            cwd=vendor,
            env=git_environment(),
        )
        install._run(
            ["git", "apply", str(ROOT / "patches" / patch)], cwd=vendor, env=git_environment()
        )
    build = vendor / "build"
    flags = [
        *install._cmake_backend_flags(resolved),
        "-DBUILD_SHARED_LIBS=OFF",
        "-DLLAMA_BUILD_TESTS=OFF",
        "-DGGML_BLAS=OFF",
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
    ]
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
    capabilities = parse_device_report(install, probe_result.stdout, probe_result.stderr)
    actual = capabilities["backends"]
    if "vulkan" in resolved and "vulkan_profile" not in capabilities["features"]:
        raise ValueError("Prima Vulkan build does not expose its Vulkan profiler")
    if resolved != "cpu" and not install.backends_satisfied(expected, actual):
        raise ValueError("Prima staged server cannot satisfy this node's backend contract")
    if resolved != "cpu" and not capabilities["devices"]:
        raise ValueError("Prima staged server reports no usable GPU devices")
    identity = runtime_id(source.repository, commit, "primacpp", digest)
    manifest = {
        "schema": 1,
        "engine": "primacpp",
        "transport": "prima-ring-tcp",
        "runtime_id": identity,
        "repository": source.repository,
        "commit": commit,
        "upstream_repository": definition["upstream_repository"],
        "upstream_commit": definition["upstream_commit"],
        "recipe_digest": digest,
        "dependencies": definition["dependencies"],
        "build_id": destination.name,
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
            if p.is_file()
        },
        "size_bytes": sum(p.stat().st_size for p in output.rglob("*") if p.is_file()),
        "qualification_errors": qualification_errors({"engine": "primacpp"}),
    }
    atomic_json(destination / "manifest.json", manifest)
    return manifest
