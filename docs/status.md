# Status (2026-10-25)

Legend: ✅ implemented and executed in the build sandbox · 🟡 implemented, not
executed here (reason given) · ⛔ not done / blocked · 🔬 needs expert review

## Build environment and its limits

Linux sandbox, Python 3.13.17, Node 22.23.4, 2 CPUs, 8 GB RAM, **no access to
PyPI, npm or NASA servers** (egress policy), no Docker daemon, no PostGIS.
Consequences:
* FastAPI could not be installed. The API routes are mounted on FastAPI when it
  is installed, otherwise on Starlette (FastAPI's base). All API tests ran on
  the Starlette path. 🟡 FastAPI path.
* PostGIS store, Docker image and Compose stack were written but not run.
  The PostgreSQL parts of the migration (tables, constraints, immutability
  triggers) were executed on a local PostgreSQL 16 without PostGIS; the
  geometry column, GiST index and `ST_Intersects` queries were not. 🟡
* `docker compose config` validated the Compose file. 🟡 image build.
* No real FIRMS data were downloaded or imported; the FIRMS client is tested
  against a fake transport only. ⛔ real-data evidence.
* Lock files could not be generated (registries blocked). Exact versions used
  are in `backend/requirements.lock.txt` and docs/reproducibility.md.
* The wheel could not be built locally because the system setuptools is broken
  under Python 3.13 and PyPI was unreachable; `pyproject.toml` parses and the
  code runs from `src/`. 🟡 packaging.
* React type definitions (@types/react) were unavailable; TypeScript was
  checked with sandbox-only shims (no real errors after excluding
  shim-induced ones). Run `npm run typecheck` after `npm install`. 🟡

## What was executed

| Command | Result |
|---|---|
| `pytest` (backend, 104 tests incl. 8 headless-Chromium browser smoke tests) | 104 passed in 150 s |
| `node --test` (frontend logic, 7 tests) | 7 passed |
| `node build.mjs` | bundle built (375 kB JS, 10 kB CSS) |
| `ember seed-demo` | 461,894 synthetic detection rows + 1,290,150 coverage rows + 306 land-cover cells imported in ~50 s; CSV SHA-256 `dbf0995a…6662` |
| `ember evaluate --dataset synthetic --aoi pilot-cerrado` | 124 s; status `synthetic_demo_verified`; all 6 gates passed |
| `ember recovery` | 121 s; artifacts/synthetic_recovery.{json,md} |
| `ember sensitivity` | 97 s; artifacts/sensitivity.json |
| `scripts/benchmark.py` | artifacts/performance.json (below) |
| `docker compose config -q` | valid |

### Synthetic evidence (not real-sensor evidence)
* Held-out AOI-week mean Poisson deviance, temporal / spatial blocks: candidate
  1.51 / 1.41; global ratio 2.04 / 1.60; NB regression 6.23 / 2.59; raw
  substitution 352 / 89. 90 % predictive interval coverage 0.914 / 0.915.
* Recovery of the injected ratio: 4 of 6 parameters inside their 90 %
  intervals; savanna annual-mean log ratio 1.142 vs truth 1.175 and seasonal
  amplitude 0.282 vs 0.25 fell just outside → intervals somewhat optimistic,
  small bias.
* After the simulated end of the Aqua record (78 weeks): converted weekly
  totals −1.7 % relative bias vs the known truth; 87 % of 90 % intervals
  contained the truth; naive splicing +237 %.
* Hotspot-only mode (coverage hidden): the ratio absorbs the coverage
  difference as predicted (log ratio ≈ +0.15 higher); transfer bias −1.2 %,
  interval coverage 78 %.
* Anomalies, single-year test families: injected 8× surge (2019): 1 of 6
  episode weeks flagged; injected 0.15× deficit (2016): 6 of 11 weeks flagged,
  plus 4 low weeks elsewhere in 2016, a year that also has a low random
  activity factor in the generator (the experiment does not separate genuine
  latent departures from false discoveries). With all 13 years tested at once
  (~1,700 tests) nothing survives FDR control.
* Sensitivity: the ratio depends strongly on the quality filter (3.16 at
  nominal+, 1.19 high-only) and rises from 3.16 to 3.34 between 18 and 72 km
  grids; ignoring coverage raises it to 3.68 and worsens held-out deviance
  (2.57 vs 1.51).

### Measured performance (SQLite store, 461,894 detections, 2-CPU sandbox)
| Call | First call in a fresh process | Repeated |
|---|---|---|
| Weekly summary, 13 years, 4 platforms | 14.2 s (includes one-off loading of the dataset into memory) | 0.03 s |
| Daily summary, 1 year | 1.9 s | 0.01 s |
| Map cells, one week | 0.26 s | 0.006 s |
| Detections, one week (page of ≤ 3000) | 0.46 s | 0.41 s |
| Harmonised series (fit + 200 bootstrap replicates + diagnostics) | 27.3 s | 0.015 s |
| Anomalies, one year | 0.19 s | 0.004 s |

`ember serve` now starts loading the in-memory cache in the background at
startup, so the first interactive request usually avoids most of the 14 s.

## Requirement checklist

### 1. Ingestion and provenance
* ✅ FIRMS products, endpoints, attributes, confidence classes verified against official pages (docs/sources.md)
* ✅ Archive CSV import (CLI and web), idempotent with import reports; malformed rows, required columns, coordinates, dates/times, duplicates vs repeated observations, conflicts, mixed platforms, stream mismatches
* ✅ Standard vs NRT/RT/URT kept separate; Terra/Aqua and S-NPP/NOAA-20/NOAA-21 kept as separate platforms
* ✅ UTC normalisation with originals preserved; FRP, brightness, scan/track, day/night, type preserved
* ✅ Immutable raw files and row issues (triggers), append-only detections, versioned method config
* ✅ FIRMS Area API client: 5-day chunking, caching, backoff, request budget, key redaction, session-only key in UI, env key on server — tested with a fake transport
* ⛔ Live FIRMS call (blocked); 🟡 confirm response format on first real run

### 2. Common analysis framework
* ✅ Pilot region + presets, polygon drawing, GeoJSON upload, bbox entry; exact polygon filtering; invalid polygons rejected; antimeridian splitting
* ✅ EASE-Grid 2.0 equal-area grid (9/18/36/72 km), day/week windows, defaults justified
* ✅ Raw vs quality counts, active cells, FRP kept separate per platform
* ✅ Coverage modelled separately; unknown never shown as zero; coverage-dependent tests disabled without coverage
* ⛔ Deriving valid-observation coverage from MOD14/MYD14/VNP14IMG granules (needs Earthdata access and expert review) 🔬

### 3. Harmonisation
* ✅ Separate science module, versioned assumptions
* ✅ Baselines (raw substitution/pooling, global ratio, sensor-specific series, active cells) and candidate conditional rate-ratio model with seasonal + land-cover terms and exposure offset; NB regression alternative
* ✅ Block bootstrap intervals, dispersion, activity-dependence, drift, zero checks, refusal when evidence is insufficient, status gates, temporal-transfer flags
* 🔬 Choice of reference platform/pair and the proportional-ratio assumption for real fire regimes

### 4. Validation
* ✅ Blocked temporal and spatial hold-outs with buffers, train-only selection, leakage tests
* ✅ MAE/RMSE/deviance/bias/interval coverage/season and land-cover stability; gates
* ✅ Synthetic recovery experiment, sensitivity analysis, model card, evaluation artifacts
* ⛔ Real held-out evaluation (no real data here) — the app does **not** claim validation
* ⛔ Independent reference evaluation (no suitable documented reference data identified)

### 5. User experience
* ✅ Calendar (daily/weekly, legends, unknown/no-coverage styling, raw vs harmonised), linked map, time series, selected-period details with provenance/coverage/uncertainty
* ✅ Seasonal baselines, year-to-year totals, FDR-controlled anomalies, critical periods, explicit "not a forecast"
* ✅ Raw-vs-harmonised side-by-side with naive splice, ratio curve with bands, diagnostics
* ✅ Data and method panel, import panel, exports with provenance
* ✅ Loading, empty, error, offline, insufficient-data states; synthetic mode conspicuous everywhere; keyboard navigation of the calendar; responsive down to 390 px (browser-tested)

### 6. Demo and performance
* ✅ Deterministic synthetic world + tiny/malformed CSV fixtures, labelled SYNTHETIC
* ✅ Real-data procedure documented (not executed)
* ✅ Spatial indexes (R*Tree / GiST), result caching, pagination, measured performance above
* ✅ Demo script follows the requested narrative on synthetic data

## Known limitations
* No real-data results exist yet; every number above is synthetic.
* Validation measures agreement with Aqua MODIS, not truth.
* Hotspot-only calibrations are confounded by coverage differences.
* Bootstrap intervals hold the seasonal order fixed and were slightly optimistic in recovery.
* Anomaly power is limited by interannual variability; long views need much stronger evidence.
* The in-memory frame cache of the SQLite store suits pilot-scale data (≤ ~5 M rows); use PostGIS beyond that.
* OSM tiles are fetched from the public tile server when online.

## Remaining steps needing NASA access or expert review
1. Download and import a real overlap sample (docs/real_data_procedure.md); run `ember evaluate --dataset real`; report the status honestly, including failure.
2. Build valid-observation coverage from Level-2 fire masks (Earthdata login; VIIRS bow-tie and off-nadir pixel-area handling) 🔬.
3. Optionally add land cover (e.g. MCD12Q1) per cell.
4. Expert review of the reference-pair choice, proportionality assumption, Terra/Aqua drift years, and anomaly thresholds 🔬.
5. Run on a networked machine: `pip install -e "backend[dev,postgis]"`, generate lock files, `docker compose up --build`, and the PostGIS contract tests (`EMBER_TEST_DATABASE_URL`).
6. Original single-file prototype at `C:\Users\musma\AppData\Local\HeyClicky\...\fire-calendar.html` was **not inspected or modified** (this build ran in a cloud workspace without access to that PC).
