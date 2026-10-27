"""Architecture boundary tests: enforce Clean Architecture one-way dependency rules."""

import ast
from pathlib import Path
from unittest.mock import MagicMock
from fastapi import APIRouter

from kllm.modules.settings.endpoints import create_settings_router
from kllm.modules.updates.endpoints import create_updates_router
from kllm.modules.usage.endpoints import create_usage_router
from kllm.modules.live.endpoints import create_live_router
from kllm.modules.logs.endpoints import create_logs_router
from kllm.modules.cluster.endpoints import create_cluster_router
from kllm.modules.models.endpoints import create_models_router
from kllm.modules.inference.endpoints import create_inference_router


def test_modules_do_not_import_agent_runtime():
    """Verify that no file in src/kllm/modules imports from kllm.agent."""
    repo_root = Path(__file__).resolve().parent.parent
    modules_dir = repo_root / "src" / "kllm" / "modules"
    assert modules_dir.is_dir()

    violations = []
    for py_file in modules_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except Exception as exc:
            violations.append(f"Failed to parse {py_file}: {exc}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "kllm.agent" or alias.name.startswith("kllm.agent."):
                        violations.append(f"{py_file}:{node.lineno} imports '{alias.name}'")
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.module == "kllm.agent" or node.module.startswith("kllm.agent.")):
                    violations.append(f"{py_file}:{node.lineno} imports from '{node.module}'")

    assert not violations, "Architecture Constitution boundary violations found:\n" + "\n".join(violations)


def test_router_construction_in_isolation():
    """Verify that all router factories construct cleanly in isolation with mock dependencies."""
    mock_settings = MagicMock()
    settings_router = create_settings_router(mock_settings)
    assert isinstance(settings_router, APIRouter)
    assert len(settings_router.routes) > 0

    mock_updates = MagicMock()
    updates_router = create_updates_router(mock_updates)
    assert isinstance(updates_router, APIRouter)
    assert len(updates_router.routes) > 0

    mock_usage = MagicMock()
    usage_router = create_usage_router(live_handlers=mock_usage, history_handlers=mock_usage, ingest_handlers=mock_usage)
    assert isinstance(usage_router, APIRouter)
    assert len(usage_router.routes) > 0

    mock_feed = MagicMock()
    mock_ws = MagicMock()
    live_router = create_live_router(live_feed=mock_feed, websocket_handler=mock_ws)
    assert isinstance(live_router, APIRouter)
    assert len(live_router.routes) > 0

    mock_logs = MagicMock()
    logs_router = create_logs_router(handlers=mock_logs, proxy=MagicMock())
    assert isinstance(logs_router, APIRouter)
    assert len(logs_router.routes) > 0

    cluster_router = create_cluster_router(
        status_service=MagicMock(),
        start_handler=MagicMock(),
        lifecycle_handlers=MagicMock(),
        join_fn=MagicMock(),
        membership_handlers=MagicMock(),
    )
    assert isinstance(cluster_router, APIRouter)
    assert len(cluster_router.routes) > 0

    mock_models = MagicMock()
    models_router = create_models_router(handlers=mock_models)
    assert isinstance(models_router, APIRouter)
    assert len(models_router.routes) > 0

    inference_router = create_inference_router(
        run_settings_handlers=MagicMock(),
        engine_settings_handlers=MagicMock(),
        inference_handlers=MagicMock(),
        template_validation_service=MagicMock(),
        managed_network_service=MagicMock(),
    )
    assert isinstance(inference_router, APIRouter)
    assert len(inference_router.routes) > 0
