"""HTTP API.

The app is built with FastAPI (the production/Docker server, with OpenAPI docs
at /docs). FastAPI is a thin layer over Starlette; the build sandbox used to
develop this project could not install FastAPI, so when it is missing the
same routes are mounted on a plain Starlette app (no /docs) and a warning is
logged. Both paths use the identical endpoint functions below, and the API
tests run against whichever framework is installed.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.staticfiles import StaticFiles

from .. import __version__
from ..config import get_settings
from ..service import EmberService, ServiceError
from ..store.factory import open_store

log = logging.getLogger("ember_atlas.api")
MAX_BODY = 210 * 1024 * 1024


@dataclass
class Route:
    method: str
    path: str
    handler: Callable[..., Any]
    summary: str
    body: str = "json"  # json | none | upload


def _routes(svc: EmberService, settings) -> list[Route]:
    def firms_fetch(body: dict, request: Request):
        # session key from the UI header wins over the server env key; neither is logged
        key = request.headers.get("x-firms-map-key") or settings.firms_map_key
        return svc.fetch_firms(body, key, cache_dir=settings.firms_cache_dir, max_requests=settings.firms_max_requests)

    return [
        Route("GET", "/api/health", lambda: {"ok": True, "version": __version__, "store": svc.store.kind}, "Liveness check", "none"),
        Route("GET", "/api/meta", lambda: svc.meta(), "Datasets, presets, products, method config", "none"),
        Route("POST", "/api/aoi/validate", lambda b: svc.validate_aoi(b.get("aoi")), "Validate / normalise an AOI polygon"),
        Route("POST", "/api/summary", svc.summary, "Per-platform window series (raw, quality, active cells, FRP, coverage status)"),
        Route("POST", "/api/map/cells", svc.map_cells, "Equal-area cell aggregates as GeoJSON"),
        Route("POST", "/api/map/detections", svc.detections, "Paginated detections as GeoJSON"),
        Route("POST", "/api/window", svc.window_detail, "Selected day/week details with provenance"),
        Route("POST", "/api/harmonized", svc.harmonized, "Harmonised series, calibration model, diagnostics, status"),
        Route("POST", "/api/evaluation", lambda b: svc.get_evaluation(b) or {"available": False}, "Cached validation report (if run)"),
        Route("POST", "/api/evaluation/run", svc.start_evaluation, "Start blocked hold-out validation (background job)"),
        Route("GET", "/api/jobs/{job_id}", lambda job_id: svc.job(job_id), "Job status", "none"),
        Route("POST", "/api/anomalies", svc.anomalies, "Seasonal baseline, FDR-controlled anomaly signals, critical periods"),
        Route("POST", "/api/export", svc.export, "CSV/GeoJSON export with provenance"),
        Route("POST", "/api/import", None, "Upload a FIRMS CSV (multipart)", "upload"),
        Route("GET", "/api/batches", lambda: svc.batches(), "Import batches", "none"),
        Route("GET", "/api/batches/{batch_id}", lambda batch_id: svc.batch(int(batch_id)), "Import batch report and row issues", "none"),
        Route("POST", "/api/firms/fetch", firms_fetch, "Fetch from the FIRMS Area API (needs a MAP_KEY)"),
        Route("GET", "/api/artifacts", lambda: _artifacts(settings.artifacts_dir), "Offline experiment artifacts (synthetic recovery, sensitivity)", "none"),
    ]


def _artifacts(folder: Path) -> dict:
    out = {}
    for name in ("synthetic_recovery", "sensitivity"):
        p = Path(folder) / f"{name}.json"
        if p.exists():
            try:
                out[name] = json.loads(p.read_text())
            except json.JSONDecodeError:
                out[name] = None
    return out


def _make_endpoint(route: Route, svc: EmberService) -> Callable[[Request], Awaitable[Response]]:
    async def endpoint(request: Request) -> Response:
        try:
            if route.body == "upload":
                form = await request.form(max_part_size=MAX_BODY)
                up = form.get("file")
                if up is None or not hasattr(up, "read"):
                    raise ServiceError("multipart field 'file' is required")
                data = await up.read()
                decl = {k: form.get(k) for k in ("dataset", "source_id", "source_url", "retrieved_at", "period_start", "period_end", "notes")}
                result = await run_in_threadpool(svc.import_csv, data, up.filename or "upload.csv", decl)
            elif route.body == "none":
                result = await run_in_threadpool(route.handler, **request.path_params)
            else:
                raw = await request.body()
                if len(raw) > 5 * 1024 * 1024:
                    raise ServiceError("request body too large", 413)
                try:
                    body = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    raise ServiceError("request body must be JSON") from None
                if not isinstance(body, dict):
                    raise ServiceError("request body must be a JSON object")
                if route.path == "/api/firms/fetch":
                    result = await run_in_threadpool(route.handler, body, request)
                else:
                    result = await run_in_threadpool(route.handler, body)
            if route.path == "/api/export":
                data, filename, media = result
                return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
            return JSONResponse(result)
        except ServiceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)
        except Exception:  # never leak internals (or credentials) to clients
            log.exception("unhandled error on %s", route.path)
            return JSONResponse({"error": "Internal error. See server log for details."}, status_code=500)

    endpoint.__name__ = "ep_" + route.path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    return endpoint


def create_app(svc: EmberService | None = None, frontend_dist: Path | None = None, force_starlette: bool = False):
    settings = get_settings()
    if svc is None:
        store = open_store(settings.database_url)
        store.migrate()
        svc = EmberService(store)
        if hasattr(store, "warm") and os.environ.get("EMBER_WARM_CACHE", "1") == "1":
            import threading

            threading.Thread(target=store.warm, name="ember-warm-cache", daemon=True).start()
    use_fastapi = False
    if not force_starlette:
        try:
            from fastapi import FastAPI

            use_fastapi = True
        except ImportError:
            log.warning("FastAPI is not installed; serving the same routes with Starlette (no /docs). Install requirements for production.")
    if use_fastapi:
        from fastapi import FastAPI

        app = FastAPI(title="Ember Atlas API", version=__version__, description="Harmonised MODIS/VIIRS active-fire calendars")
    else:
        from starlette.applications import Starlette

        app = Starlette()
    app.state.service = svc
    app.state.framework = "fastapi" if use_fastapi else "starlette"
    for r in _routes(svc, settings):
        ep = _make_endpoint(r, svc)
        if use_fastapi:
            app.add_api_route(r.path, ep, methods=[r.method], summary=r.summary, response_class=Response)
        else:
            app.add_route(r.path, ep, methods=[r.method])

    dist = frontend_dist if frontend_dist is not None else settings.frontend_dist
    if dist and Path(dist).exists() and (Path(dist) / "index.html").exists():
        index = Path(dist) / "index.html"
        app.mount("/assets", StaticFiles(directory=str(Path(dist) / "assets")), name="assets") if (Path(dist) / "assets").exists() else None

        async def spa(request: Request) -> Response:
            return FileResponse(index, headers={"Cache-Control": "no-store"})

        app.add_route("/", spa, methods=["GET"])
    return app


def main() -> None:  # pragma: no cover - CLI wrapper
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host=os.environ.get("EMBER_HOST", "127.0.0.1"), port=int(os.environ.get("EMBER_PORT", "8000")))
