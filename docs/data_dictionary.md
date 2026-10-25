# Data dictionary

## Stored tables (SQLite and PostGIS share names and meaning)

### import_batches
| Field | Meaning |
|---|---|
| id | batch number |
| file_name, file_sha256 | uploaded file and its SHA-256 (idempotency key together with data_origin) |
| data_origin | `real` or `synthetic`; never mixed in one analysis |
| declared_source | FIRMS source id declared at import (e.g. `MODIS_SP`) |
| source_url, retrieved_at | where and when the file was obtained (API URLs are stored with `<MAP_KEY>` in place of the key) |
| period_start, period_end, query_bbox | what was requested from the provider; defines "queried" days |
| parser_version | normaliser version (`firms-csv-1.0.0`) |
| status | `in_progress`, `complete`, `rejected`, `failed` |
| report_json | the import report (below) |

### raw_files (immutable)
`batch_id`, `gz_bytes`: the exact uploaded bytes, gzip-compressed.

### row_issues (immutable)
`batch_id`, `line_number`, `status` (`rejected`, `duplicate_in_file`, `conflict_in_file`, `duplicate_existing`, `conflict_existing`), `reasons` (list), `raw_json` (row content for rejected/conflicting rows).

### detections (append-only)
| Field | Unit / values | Source |
|---|---|---|
| record_key | SHA-1 of origin, platform, stream, UTC minute, lat/lon (1e-6°) | derived |
| content_hash | SHA-1 of record_key and attribute values | derived |
| batch_id, line_number | provenance (physical line in the uploaded file) | derived |
| data_origin | real / synthetic | declared |
| instrument | MODIS / VIIRS | `instrument` column, product, or MODIS/VIIRS-specific columns |
| platform | Terra, Aqua, Suomi-NPP, NOAA-20, NOAA-21 | `satellite` code (A, T, N, N20, N21 or names) |
| source_product | FIRMS source id, e.g. VIIRS_SNPP_SP | derived from instrument, platform, stream |
| processing_stream | SP (standard), NRT, RT, URT | `version` suffix |
| version_raw, collection | e.g. `6.1NRT`, collection `6.1` | `version` |
| acq_date_raw, acq_time_raw | original strings | FIRMS |
| acq_datetime_utc | ISO-8601 UTC minute | normalised |
| acq_date | UTC date | normalised |
| latitude, longitude | degrees WGS84, pixel centre | FIRMS |
| x_ease, y_ease | metres, EASE-Grid 2.0 (null poleward of 85.044°) | derived |
| confidence_raw | original value | FIRMS |
| confidence_class | low / nominal / high (MODIS 0–29 / 30–79 / 80–100 %) | derived |
| confidence_pct | MODIS percentage | FIRMS |
| daynight | D / N | FIRMS |
| frp_mw | MW, pixel fire radiative power | FIRMS |
| brightness_k, brightness2_k | K (MODIS ch21/22 and ch31; VIIRS I4 and I5) | FIRMS |
| scan_km, track_km | pixel size along scan / track; FIRMS does not state the unit, values are consistent with km | FIRMS |
| footprint_km2 | scan × track (approximate pixel area) | derived |
| fire_type | 0 vegetation, 1 volcano, 2 static land source, 3 offshore (standard products only) | FIRMS |

### availability
Declared or inferred queried windows: `platform`, `processing_stream`, `date_start`, `date_end`, `bbox`, `batch_id`, `basis` (`declared_query` or `inferred_from_detections`).

### coverage_sets, coverage, landcover
* `coverage_sets`: `name`, `data_origin`, `grid_size_km`, `quantity` (`valid_looks` or `landcover`), `description`.
* `coverage`: `platform`, `cell_id` (EASE id at the set's grid), `date` (UTC), `valid_looks` (sum over the day's overpasses of the validly observed fraction of the cell), `nominal_looks` (overpasses with no cloud or gaps).
* `landcover`: `cell_id`, `lc_class`.

Import formats (CSV): coverage `platform,cell_id,date,valid_looks,nominal_looks`; land cover `cell_id,lc_class`. Cell ids are `E{km}:{col}:{row}` on the EASE-Grid 2.0 family nested on the official 9 km grid (origin x = −17 367 530.45 m, y = 7 314 540.83 m; cell edge 9 008.055 m × km/9).

## Import report (JSON)
`status`, `batch_id`, `rows_read`, `rows_valid`, `rows_rejected`, `rejection_reasons` (counts), `duplicates_in_file`, `conflicts_in_file`, `duplicates_existing`, `conflicts_existing`, `inserted`, `field_warnings` (e.g. negative FRP set to null), `file_warnings`, `platforms`, `processing_streams`, `versions`, `date_range`, `availability`, `example_issues` (first 25), `seconds`.

## Calendar series (`/api/summary`, export `series`)
| Field | Meaning |
|---|---|
| window_start, window_end, n_days, full_days, complete | window (UTC) |
| platform | platform of this row |
| n_raw | all detections |
| n_quality | detections passing the quality filter |
| active_cells | distinct grid cells with ≥ 1 quality detection |
| frp_sum_mw, frp_n | FRP sum of quality detections reporting FRP; how many reported it |
| n_day, n_night | quality detections by day/night flag |
| queried_days, partial_days, inferred_days | days covered by an import whose query area contains the AOI; days whose query area only partly covers it; days whose availability is inferred |
| coverage_known, valid_fraction, valid_looks_sum | AOI-weighted valid-observation share and looks, if coverage exists |
| status | detections, observed_none, partially_observed_none, unobserved, none_reported_coverage_unknown, not_queried |

## Harmonised series (`/api/harmonized`, export `harmonized`)
| Field | Meaning |
|---|---|
| ref_raw, src_raw | quality detections of reference and source platform in the AOI week |
| ref_queried, src_queried | platform fully queried in that week |
| raw_pooled | ref_raw + src_raw (baseline only; not comparable) |
| src_converted, src_conv_lo, src_conv_hi | source converted to the reference scale, with 90 % estimate interval |
| ref_value, ref_lo, ref_hi | reference observed (coverage-adjusted in exposure mode) with sampling interval |
| in_overlap | week inside the calibration overlap |
| harmonized, harm_lo, harm_hi | the harmonised value and its 90 % interval |
| basis | reference_observed, converted_from_<platform>, none |
| transfer_outside_overlap | converted outside the overlap (stationarity assumed) |

## Anomaly windows (`/api/anomalies`, export `anomalies`)
`window_start`, `year`, `week`, `value`, `n_baseline_years`, `baseline_median`, `baseline_q25`, `baseline_q75`, `baseline_mean`, `ratio_to_median`, `test_statistic`, `t_excess`, `p_excess`, `q_excess`, `p_deficit`, `q_deficit`, `signal` (typical, unusually_high, unusually_low, insufficient_history, not_observed, too_sparse), `scale` (week or 4-week), `block_q`, `low_test`.

## Every export
CSV files start with two comment lines (`# Ember Atlas export…` with a SYNTHETIC warning when applicable, and `# {provenance JSON}`), and every row carries `data_origin`, `method_version`, `method_fingerprint`, `data_version`, `generated_at` (harmonised exports also `calibration_status`, `target`). GeoJSON exports carry the same in top-level `properties`. Synthetic exports are named `SYNTHETIC_ember_…`.
