"""Integration checks for the versioned resource control API."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from kllm.agent import app, legacy_v1_router
from kllm.agent.routes.v1 import ROUTES as V1_ROUTES


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "kllm-home"))
    with TestClient(app) as test_client:
        yield test_client


def test_health_advertises_control_protocol_version(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    from kllm.config import CONTROL_PROTOCOL_VERSION

    assert response.json()["control_protocol"] == CONTROL_PROTOCOL_VERSION


def test_v2_validation_errors_use_the_structured_error_contract(client) -> None:
    response = client.post("/v2/models/downloads", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert isinstance(response.json()["error"]["details"], list)


def test_resource_routes_are_registered_with_expected_methods() -> None:
    paths = app.openapi()["paths"]
    assert set(paths["/v2/node"]) == {"get"}
    assert set(paths["/v2/cluster"]) == {"get", "post", "delete"}
    assert set(paths["/v2/cluster/membership"]) == {"post", "delete"}
    assert set(paths["/v2/inference/plans"]) == {"post"}
    assert set(paths["/v2/inference/runs"]) == {"post"}
    assert set(paths["/v2/models/downloads"]) == {"post"}
    assert set(paths["/v2/inference/connection"]) == {"get"}


def test_legacy_v1_routes_are_bound_outside_the_agent_module() -> None:
    paths = app.openapi()["paths"]
    websocket_paths = {route.path for route in legacy_v1_router.routes if hasattr(route, "path")}
    for path, method, _handler_name in V1_ROUTES:
        if method == "WEBSOCKET":
            assert path in websocket_paths
        else:
            assert method.lower() in paths[path]


def test_worker_join_rejects_a_master_with_an_old_protocol(monkeypatch) -> None:
    from kllm.agent import join_flow

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"control_protocol": 1}

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, _url: str) -> Response:
            return Response()

        def post(self, *_args, **_kwargs) -> Response:
            raise AssertionError("a protocol mismatch must stop before registration")

    monkeypatch.setattr(join_flow.httpx, "Client", Client)
    with pytest.raises(HTTPException) as error:
        join_flow.post_register("http://master", {}, "key", "master", 18765, lambda: None)
    assert error.value.status_code == 426
    assert "upgrade the master" in str(error.value.detail)
