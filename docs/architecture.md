# Architecture

```
            ┌────────────── browser (React + d3-geo, esbuild bundle) ──────────────┐
            │ calendar · map · selected-period panel · tabs (series, raw vs         │
            │ harmonised, seasons & anomalies, validation, data & method, import)   │
            └───────────────┬──────────────────────────────────────────────────────┘
                            │ JSON over HTTP (same origin; built app served by the API)
┌───────────────────────────▼───────────────────────────────────────────────────────┐
│ api/app.py      route table → FastAPI (production) or Starlette (fallback)         │
│ service.py      selection parsing, provenance, result cache, compute-once locks,   │
│                 background validation jobs, exports, imports, FIRMS fetch          │
├──────────────┬──────────────────┬────────────────────────┬────────────────────────┤
│ ingest/      │ geo/             │ analysis/              │ science/               │
│ firms_schema │ equal_area       │ windows (day/week)     │ glm (IRLS)             │
│ normalize    │  (EASE-Grid 2.0) │ aggregate (series,     │ calibration (models,   │
│ importer     │ polygons (AOI,   │  coverage status,      │  bootstrap, diagnostics│
│ covariates   │  antimeridian,   │  cell-window tables)   │ harmonize · status     │
│ firms_api    │  point-in-poly)  │                        │ evaluation · anomalies │
│              │                  │                        │ experiments            │
├──────────────┴──────────────────┴────────────────────────┴────────────────────────┤
│ store/base.py   one contract (tests/test_store_contract.py)                        │
│   sqlite_store.py   local / no-Docker; R*Tree prefilter + exact polygon in NumPy;   │
│                     in-memory frame cache for pilot-scale data                     │
│   postgis_store.py  Docker; GiST index + ST_Intersects; COPY inserts               │
└───────────────────────────────────────────────────────────────────────────────────┘
synthetic/ generator + seed: deterministic SYNTHETIC world loaded through the normal importer
cli.py: ember migrate | seed-demo | import | import-covariate | fetch-firms | evaluate | recovery | sensitivity | serve
```

## Design decisions

* **Science separated from ingestion and UI.** `science/` only sees tidy
  tables (cell × week × platform counts, exposures, land cover). Its
  assumptions are in a versioned TOML file; every response carries the method
  version, a config fingerprint, a data version and a code fingerprint, and the
  result cache is keyed on all of them.
* **Two stores, one contract.** PostGIS is the production store (Docker
  Compose). SQLite + an exact NumPy polygon test is the no-Docker path for
  Windows laptops and for the test-suite. Both must pass the same contract
  tests; the PostGIS run needs `EMBER_TEST_DATABASE_URL`.
* **FastAPI with a Starlette fallback.** Routes are declared once and mounted
  on FastAPI when installed (OpenAPI docs at `/docs`). The build sandbox could
  not install FastAPI, so the identical endpoints were exercised on Starlette
  (FastAPI's own base); see docs/status.md.
* **Map without a tile dependency.** The map is d3-geo (Mercator, zoom/pan,
  polygon drawing) over optional OpenStreetMap raster tiles. Offline, the
  analysis layers still render on a plain background with a graticule.
* **Calibration on the full record.** Calibration, harmonisation and anomaly
  baselines always use the whole stored record of the AOI for the platform
  pair, whatever dates are on screen, so a short view cannot starve the fit.
* **Expensive work once.** Concurrent requests for the same result share one
  computation; validation runs as a background job; results are cached in the
  database and invalidated by any data, config or code change.

## Request flow (calendar)

1. `POST /api/summary` → `Selection` (dataset, AOI, dates, platforms, streams,
   grid, window, quality) → `store.query_detections` (exact polygon) →
   quality mask, cell ids, window starts → per-platform window metrics →
   queried-day accounting from import availability → coverage status → JSON.
2. `POST /api/harmonized` → pair table over the full record → sufficiency →
   fit + bootstrap + diagnostics → harmonised series + status (using the cached
   validation report, if any).
3. `POST /api/evaluation/run` → background job: blocked folds → all methods →
   metrics, interval coverage, gates → cached; the next harmonisation request
   picks it up and the status label updates.
4. `POST /api/anomalies` → harmonised or single-platform weekly series →
   baselines and FDR-controlled tests over the requested view.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | /api/health | liveness |
| GET | /api/meta | datasets, availability, coverage sets, presets, products, method config, documentation links |
| POST | /api/aoi/validate | validate/normalise an AOI (area, antimeridian split, cell counts) |
| POST | /api/summary | per-platform window series with raw/quality counts, active cells, FRP, coverage status |
| POST | /api/map/cells | equal-area cell aggregates (GeoJSON) for the whole period or a window |
| POST | /api/map/detections | paginated detections (GeoJSON) for a window |
| POST | /api/window | selected-period details with per-platform metrics and provenance rows |
| POST | /api/harmonized | harmonised series, model, ratio curve with bootstrap bands, diagnostics, status |
| POST | /api/evaluation | cached validation report |
| POST | /api/evaluation/run, GET /api/jobs/{id} | run blocked validation in the background |
| POST | /api/anomalies | seasonal curve, annual totals, FDR-controlled signals, critical periods |
| POST | /api/export | CSV / GeoJSON with provenance header and fields |
| POST | /api/import | multipart CSV upload → import report |
| GET | /api/batches, /api/batches/{id} | import batches, reports and row issues |
| POST | /api/firms/fetch | FIRMS Area API fetch (key from header `X-FIRMS-Map-Key` or server env) |
| GET | /api/artifacts | synthetic recovery and sensitivity artifacts for the Validation tab |
