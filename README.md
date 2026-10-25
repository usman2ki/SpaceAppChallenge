# Ember Atlas

A web application that turns NASA FIRMS active-fire hotspots from different
satellites into a **burning-activity calendar** for an area you choose, and
that is explicit about when MODIS and VIIRS numbers can and cannot be
compared. Built for the NASA Space Apps challenge *Harmonization of MODIS and
VIIRS Hot Spots*.

* Pick a preset area, draw a polygon, upload GeoJSON or type a bounding box.
* See each sensor's raw series, a calendar of weekly or daily activity, a
  linked map with every detection at its pixel footprint, and per-period
  provenance and observation coverage.
* Express VIIRS activity on the MODIS Aqua scale with a transparent,
  versioned calibration (paired conditional rate-ratio model) with
  block-bootstrap uncertainty, and see what naive splicing would have shown.
* Check the calibration with blocked temporal and spatial hold-outs against
  baselines, and read a status label that never claims more than the evidence.
* Inspect seasonal baselines, year-to-year totals and FDR-controlled unusual
  periods. These are historical signals, **not fire-risk forecasts**.
* Export CSV/GeoJSON with provenance, method version and uncertainty fields.

> **Data status.** The repository ships a deterministic **synthetic** demo
> world with a known sensor effect. It is labelled SYNTHETIC everywhere in the
> app and in every export, and it is **not NASA data**. No real FIRMS data were
> downloaded while building this version (see [docs/status.md](docs/status.md));
> [docs/real_data_procedure.md](docs/real_data_procedure.md) shows how to load a
> real historical overlap sample. Nothing here is "scientifically validated"
> on real data yet.

## Quick start: synthetic demo, no NASA credentials

Requirements: Python 3.11+ (tested with 3.13) and Node.js 20+ (tested with 22).
The SQLite store needs no database server.

### Windows (PowerShell)

```powershell
git clone <your-fork-url> ember-atlas
cd ember-atlas
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".\backend[dev]"
cd frontend; npm install; npm run build; cd ..
ember migrate
ember seed-demo          # ~1 minute: generates and imports the SYNTHETIC world
ember serve              # open http://127.0.0.1:8000
```

If script execution is blocked, run `Set-ExecutionPolicy -Scope Process RemoteSigned` first.
The database is created at `data\ember.sqlite`; to use another file set
`$env:EMBER_DATABASE_URL = "sqlite:///C:\path\to\ember.sqlite"`.

### Linux / macOS

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e "./backend[dev]"
(cd frontend && npm install && npm run build)
ember migrate && ember seed-demo && ember serve
```

### Docker Compose (PostGIS)

```bash
cp .env.example .env            # optional: add your own FIRMS_MAP_KEY
docker compose up --build -d
docker compose run --rm api ember seed-demo
# open http://127.0.0.1:8000
```

## Real data

* **Archive files (recommended for history):** request CSVs from the FIRMS
  archive download page, then
  `ember import fire_archive_*.csv --origin real --source MODIS_SP --period-start 2018-01-01 --period-end 2020-12-31 --query-bbox=-48.5,-11.5,-45.5,-9.0`
  (repeat with `--source VIIRS_SNPP_SP` for the VIIRS file). Or use the
  Import tab in the app.
* **FIRMS Area API (optional, recent or archive):** request your own free
  MAP_KEY from NASA, then
  - PowerShell: `$env:FIRMS_MAP_KEY = "<your key>"; ember fetch-firms --source VIIRS_SNPP_NRT --aoi pilot-cerrado --start 2026-10-20 --end 2026-10-24`
  - bash: `FIRMS_MAP_KEY=<your key> ember fetch-firms --source VIIRS_SNPP_NRT --aoi pilot-cerrado --start 2026-10-20 --end 2026-10-24`

  In the app you can instead type the key into a password field on the Import
  tab; it is kept in page memory for that request only. The FIRMS API puts the
  key in the request URL, so it is necessarily sent to NASA (over HTTPS);
  Ember Atlas never logs or stores it and redacts it from errors.

Full procedure: [docs/real_data_procedure.md](docs/real_data_procedure.md).

## Commands

| Command | What it does |
|---|---|
| `ember migrate` | create/upgrade the schema (SQLite or PostGIS from `EMBER_DATABASE_URL`) |
| `ember seed-demo` | generate and import the deterministic synthetic world (+ synthetic coverage and land cover) |
| `ember import FILE... --origin real\|synthetic [--source ID] [--period-start/--period-end] [--query-bbox]` | idempotent CSV import with a JSON import report |
| `ember import-covariate coverage\|landcover FILE --origin ... --grid-km N` | load valid-observation coverage or land cover (format in docs/data_dictionary.md) |
| `ember fetch-firms --source ID --aoi PRESET\|FILE\|BBOX --start --end` | FIRMS Area API download + import (needs `FIRMS_MAP_KEY`) |
| `ember evaluate [--dataset] [--aoi] [--reference] [--source]` | blocked hold-out validation + harmonisation; writes JSON, model card and CSV to `artifacts/` |
| `ember recovery [--quick]` | synthetic recovery experiment with known injected sensor bias |
| `ember sensitivity [--quick]` | sensitivity to grid size, window, quality filter and coverage assumption |
| `ember serve [--host --port]` | API + web app |

## Tests

```bash
cd backend
pytest                       # unit, integration, API and (if a browser is installed) browser smoke tests
pytest -m "not browser"      # skip browser tests
python -m playwright install chromium   # once, to enable browser smoke tests
cd ../frontend && npm test   # frontend logic tests
```

`EMBER_TEST_DATABASE_URL=postgresql://...` additionally runs the store
contract tests against PostGIS.

## Documentation

* [docs/architecture.md](docs/architecture.md): components and data flow
* [docs/methods.md](docs/methods.md): the science: grid, coverage, calibration, validation, anomalies
* [docs/data_dictionary.md](docs/data_dictionary.md): every stored and exported field
* [docs/model_card.md](docs/model_card.md) and generated cards in `artifacts/`
* [docs/reproducibility.md](docs/reproducibility.md): seeds, versions, what reproduces what
* [docs/real_data_procedure.md](docs/real_data_procedure.md): loading a real overlap sample
* [docs/sources.md](docs/sources.md): NASA documentation checked, with dates and gaps
* [docs/status.md](docs/status.md): honest status checklist, blocked steps
* [docs/pitch_demo_script.md](docs/pitch_demo_script.md): short pitch and demo script

## Licence and data use

Code: MIT. FIRMS data are provided by NASA; follow FIRMS terms and cite the
products you use (see docs/sources.md). OpenStreetMap basemap tiles are loaded
directly from tile.openstreetmap.org when online; heavy use requires your own
tile server under the OSM tile usage policy.
