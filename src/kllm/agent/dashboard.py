"""Static dashboard asset and SPA fallback routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def mount_dashboard(app: FastAPI, dashboard_dir: Path | None) -> None:
    """Mount dashboard assets and its client-side route fallback, if built."""
    if dashboard_dir is None:
        return

    assets = dashboard_dir / "_app"
    if assets.is_dir():
        app.mount("/_app", StaticFiles(directory=str(assets)), name="dashboard-assets")

    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def spa_fallback(full_path: str, request: Request) -> FileResponse:
        if full_path.startswith(("v1/", "v2/")) or full_path == "health":
            raise HTTPException(404, "not found")
        if request.method not in ("GET", "HEAD"):
            raise HTTPException(404, "not found")

        candidate = dashboard_dir / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        index = dashboard_dir / "index.html"
        if index.is_file():
            return FileResponse(
                index,
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                    "Pragma": "no-cache",
                },
            )
        raise HTTPException(404, "dashboard not built — run: cd dashboard && npm run build")
