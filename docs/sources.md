# Official sources checked

All pages were read on **2026-10-25** (UTC+3) while building Ember Atlas. The
"Used for" column says what each source determined in the code; the "Not
stated" column records gaps, so nobody mistakes an assumption for a documented
fact.

| Source | Used for | Not stated / caveats |
|---|---|---|
| [FIRMS API – area](https://firms.modaps.eosdis.nasa.gov/api/area/) | Endpoint pattern `/api/area/csv/[MAP_KEY]/[SOURCE]/[west,south,east,north]/[DAY_RANGE]/[DATE]`; `DAY_RANGE` 1–5; `DATE` returns `DATE … DATE+DAY_RANGE-1`; bbox order west,south,east,north; source ids `MODIS_NRT, MODIS_SP, VIIRS_NOAA20_NRT, VIIRS_NOAA20_SP, VIIRS_NOAA21_NRT, VIIRS_NOAA21_SP, VIIRS_SNPP_NRT, VIIRS_SNPP_SP` (+ `LANDSAT_NRT`, not used); MAP_KEY limit 5000 transactions / 10-minute interval, larger requests may count as several | How multi-day requests are counted against the quota |
| [FIRMS API – data_availability](https://firms.modaps.eosdis.nasa.gov/api/data_availability/) | `/api/data_availability/csv/[MAP_KEY]/[SENSOR]`; sensors incl. `ALL` | — |
| [FIRMS active fire attributes (MODIS & VIIRS)](https://earthdata.nasa.gov/data/tools/firms/active-fire-data-attributes-modis-viirs) | Attribute names; `acq_time` is UTC; satellite codes MODIS `A`/`T`, VIIRS `N`, `N20`, `N21`; MODIS confidence 0–100 %; VIIRS confidence low/nominal/high; version = collection + processing suffix (URT/RT/NRT, none = standard); FRP in MW; brightness in K; `type` codes; scan/track reflect actual pixel size | Units of scan/track (values are consistent with km; recorded as "km, inferred"); `acq_time` string format; the page lists VIIRS versions as Collection 1 although current products are Collection 2 |
| [MODIS C6/C6.1 Active Fire User Guide 1.0](https://earthdata.nasa.gov/s3fs-public/2023-09/MODIS_C6_C6.1_Fire_User_Guide_1.0.pdf) | Confidence classes low 0≤C<30, nominal 30≤C<80, high 80≤C≤100 (Sec. 8.4.3, Table 11); MCD14ML `type` 0–3 (Sec. 5.5, Table 10); fire-mask classes for deriving valid observations: 0–2 not processed, 3 water, 4 cloud, 5 non-fire land, 6 unknown, 7–9 fire (Sec. 5.1.1, Table 3); fire pixel lists carry no cloud/missing-data information (Sec. 7.1.1); burned area cannot generally be estimated from active-fire pixels (Sec. 8.3.9) | Exact overpass times; edge-of-scan pixel size as a single number (formulas only) |
| [VIIRS S-NPP 375 m NRT product page (VNP14IMGTDL_NRT)](https://modaps.modaps.eosdis.nasa.gov/services/about/products/viirs-land-c2-nrt/vnp14imgtdl_nrt.html) | ~375 m pixels at nadir; better response to small fires than ≥1 km products; detection-only (frequent saturation); version suffix examples; product DOI 10.5067/FIRMS/VIIRS/VNP14IMGT_NRT.002; algorithm citation Schroeder et al. (2014) RSE 143:85–96 | Swath width; overpass time |
| [VIIRS NOAA-21 NRT product page (VJ214IMGTDL_NRT)](https://www.earthdata.nasa.gov/learn/find-data/near-real-time/firms/vj214imgtdlnrt) | Same attribute semantics for NOAA-21; `type` only in standard (non-NRT) data | — |
| [FIRMS archive download](https://firms.modaps.eosdis.nasa.gov/download/) | Archive start dates: MODIS C6.1 2000-11-01; VIIRS S-NPP 2012-01-20; NOAA-20 2018-04-01; NOAA-21 2023-03-01; NRT is replaced by standard data with ~5-month lag; formats SHP/CSV/JSON; email one-time-code login | File size limits; how archive and NRT files are distinguished inside a download (Ember Atlas uses the `version` column and a declared source) |

## Not verified (blocked) in this build

The build sandbox could not reach `firms.modaps.eosdis.nasa.gov` from code
(egress policy), so **no real FIRMS data were downloaded and no live API call
was executed**. The client is tested against a fake transport. The first real
run must check the response format against the documentation above.

## Statements that depend on general knowledge, flagged for expert review

* Nominal local equator-crossing times (Terra ~10:30/22:30, Aqua and S-NPP
  ~13:30/01:30, NOAA-20 about half an orbit from S-NPP). Terra and Aqua
  drifted during their extended missions, so the code never assumes overpass
  times; they appear only in documentation and in the synthetic generator.
* MODIS swath ~2,330 km leaves daily gaps at low latitudes; VIIRS swath
  ~3,000 km does not. Used only to motivate the synthetic coverage model.
