# Loading a small real historical overlap sample

This procedure was written from the official FIRMS documentation checked on
2026-10-25 (docs/sources.md). It has **not been executed end-to-end**: the
build environment could not reach NASA servers. Follow it on your own machine
and record what you actually downloaded.

## What to download

* **Area:** the pilot box, eastern Tocantins (Cerrado), Brazil:
  west −48.5, south −11.5, east −45.5, north −9.0. Frequent dry-season
  burning gives enough paired detections to calibrate; the low latitude also
  exposes MODIS swath gaps.
* **Period:** 2018-01-01 to 2020-12-31 (three full years; the calibration
  needs at least three overlap years). Use standard (science-quality)
  products only; do not mix in near-real-time files.
* **Products:** MODIS Collection 6.1 standard (Terra and Aqua) and VIIRS
  S-NPP 375 m standard. Optionally VIIRS NOAA-20 375 m (from 2018-04-01).
* **Size:** not known in advance; the FIRMS page does not state it. Do not
  commit the downloaded files to the repository.

## Option A: FIRMS archive download (no API key)

1. Open <https://firms.modaps.eosdis.nasa.gov/download/> and log in with the
   e-mailed one-time code.
2. Create a request for the area and period above for each product, CSV
   format. You receive an e-mail when the files are ready.
3. Note the request date and the file names, then import each file with its
   declared product and the period and box you requested:

PowerShell:

```powershell
ember import "C:\path\to\<modis file>.csv" --origin real --source MODIS_SP `
  --period-start 2018-01-01 --period-end 2020-12-31 --query-bbox=-48.5,-11.5,-45.5,-9.0 `
  --source-url "FIRMS archive download, request <id>" --retrieved-at 2026-10-25
ember import "C:\path\to\<viirs s-npp file>.csv" --origin real --source VIIRS_SNPP_SP `
  --period-start 2018-01-01 --period-end 2020-12-31 --query-bbox=-48.5,-11.5,-45.5,-9.0 `
  --source-url "FIRMS archive download, request <id>" --retrieved-at 2026-10-25
```

bash: same commands with `\` line continuations.

Read the import report: rows rejected (and why), duplicates, conflicts,
platforms (MODIS files contain both Terra and Aqua), processing streams
(should be `SP` only) and versions.

## Option B: FIRMS Area API (needs your own free MAP_KEY)

```powershell
$env:FIRMS_MAP_KEY = "<your key>"
ember fetch-firms --source MODIS_SP --aoi pilot-cerrado --start 2018-01-01 --end 2020-12-31
ember fetch-firms --source VIIRS_SNPP_SP --aoi pilot-cerrado --start 2018-01-01 --end 2020-12-31
Remove-Item Env:FIRMS_MAP_KEY
```

The client splits the period into 5-day requests (about 220 per source for
three years), caches every response in `data/firms_cache/` so reruns cost no
quota, waits between requests, backs off on HTTP 429/5xx, and stops at
`--max-requests` (default 200; raise it for long periods). FIRMS allows 5000
transactions per 10 minutes per key and says larger requests may count as
several. Check product date availability first if unsure:
`https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/<KEY>/ALL`.

## Then

```bash
ember evaluate --dataset real --aoi pilot-cerrado      # ~2 minutes; writes artifacts/
ember serve                                            # switch the app to "Imported real data"
```

Expected status with FIRMS files alone: at best **research estimate
(coverage-unadjusted, not validated)**, because hotspot files carry no
valid-observation coverage. If the calibration does not beat the baselines,
the app shows `failed_validation` and raw series only — report that result
rather than tuning until it passes.

## Getting to coverage-adjusted results (not implemented here)

1. Download MOD14/MYD14 (MODIS) and VNP14IMG (VIIRS S-NPP) Level-2 fire
   products for the same area and dates (LAADS DAAC; needs an Earthdata login).
2. From each granule's fire mask, count per EASE-Grid cell the fraction of
   pixels in classes "non-fire land" (5) and "fire" (7–9) — valid
   observations — versus cloud (4), not processed (0–2) and unknown (6)
   (MODIS user guide Table 3; check the VIIRS user guide for its classes).
3. Sum per platform, cell and UTC day into the coverage CSV format in
   docs/data_dictionary.md and load it with
   `ember import-covariate coverage file.csv --origin real --grid-km 36`.
4. Optionally load land cover per cell (e.g. majority MCD12Q1 class) with
   `ember import-covariate landcover`.

Steps 1–2 need expert review (pixel-area weighting off nadir, bow-tie
deletion in VIIRS, water masking) before results can be called validated.
