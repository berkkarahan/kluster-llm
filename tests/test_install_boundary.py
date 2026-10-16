"""scripts/install.py is the only place that builds or installs missing packages."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "kllm"

INSTALLER_ENTRYPOINTS = {
    "ensure_uv",
    "ensure_venv",
    "ensure_rust",
    "ensure_node",
    "ensure_git",
    "ensure_cmake",
    "ensure_vulkan_macos",
    "ensure_windows_vulkan",
    "ensure_prima_deps",
}
# Imported only by `scripts/install.py` managed modes (spawned as a subprocess).
INSTALLER_OWNED = {
    "modules/runtimes/interactors/build.py",
    "modules/primacpp/interactors/build.py",
}
# Modules allowed to launch `scripts/install.py` as a separate process.
INSTALLER_LAUNCHERS = {
    "modules/runtimes/interactors/registry.py",
    "modules/runtimes/router_cli.py",
    "modules/updates/interactors/update_worker.py",
    "modules/primacpp/interactors/build.py",
}


def _sources():
    for path in SRC.rglob("*.py"):
        yield path.relative_to(SRC).as_posix(), path.read_text(encoding="utf-8")


def test_runtime_never_calls_installer_entrypoints():
    offenders = []
    for name, text in _sources():
        if name in INSTALLER_OWNED:
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Attribute) and node.attr in INSTALLER_ENTRYPOINTS:
                offenders.append(f"{name}:{node.lineno} {node.attr}")
            elif isinstance(node, ast.Name) and node.id in INSTALLER_ENTRYPOINTS:
                offenders.append(f"{name}:{node.lineno} {node.id}")
    assert not offenders, offenders


def test_runtime_does_not_load_install_script_in_process():
    offenders = [
        name
        for name, text in _sources()
        if "kllm_install_script" in text or "llama_pack" in text
    ]
    assert not offenders, offenders


def test_only_known_modules_reference_the_installer_script():
    offenders = [
        name
        for name, text in _sources()
        if "install.py" in text
        and name not in INSTALLER_LAUNCHERS
        and name not in {"shared/native.py"}
        and not _only_in_text(text)
    ]
    assert not offenders, offenders


def _only_in_text(text: str) -> bool:
    """True when every `install.py` mention is inside a docstring or comment/message."""
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for arg in ast.walk(node):
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and arg.value.endswith("install.py")
                ):
                    return False
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            right = node.right
            if isinstance(right, ast.Constant) and right.value == "install.py":
                return False
    return True
