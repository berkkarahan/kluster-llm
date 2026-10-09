from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kllm.agent.routes.dashboard import mount_dashboard


def test_mount_dashboard_serves_spa_routes_and_static_files(tmp_path):
    (tmp_path / "_app").mkdir()
    (tmp_path / "_app" / "app.js").write_text("globalThis.ready = true")
    (tmp_path / "index.html").write_text("dashboard index")
    (tmp_path / "asset.txt").write_text("asset content")
    app = FastAPI()
    mount_dashboard(app, tmp_path)

    client = TestClient(app)
    assert client.get("/settings").text == "dashboard index"
    assert client.get("/asset.txt").text == "asset content"
    assert client.get("/_app/app.js").text == "globalThis.ready = true"
    assert client.get("/settings").headers["cache-control"] == (
        "no-store, no-cache, must-revalidate"
    )
    assert client.post("/settings").status_code == 404
    assert client.get("/v2/missing").status_code == 404


def test_mount_dashboard_without_build_does_not_add_routes():
    app = FastAPI()
    mount_dashboard(app, None)

    assert len(app.routes) == 4


def test_mount_dashboard_without_index_returns_build_hint(tmp_path):
    app = FastAPI()
    mount_dashboard(app, tmp_path)

    response = TestClient(app).get("/settings")
    assert response.status_code == 404
    assert response.json() == {"detail": "dashboard not built — run: cd dashboard && npm run build"}
