# Model card: Suomi-NPP → Aqua harmonisation

* Generated: 2026-10-24T23:56:14Z  
* Data origin: **SYNTHETIC** (synthetic demo, not NASA measurements)  
* Method: ember-method-1.0.0 (config fingerprint 19f09ad1134cfb29), app 0.1.0  
* Area: Pilot: eastern Tocantins (Cerrado), Brazil (90876.2 km²), grid 18 km, weekly
* Status: **Synthetic demo: method checks passed on synthetic data** (`synthetic_demo_verified`). Held-out checks passed on SYNTHETIC data with a known injected sensor effect. This shows the machinery works; it is not evidence about real MODIS/VIIRS behaviour.

## Intended use

Express weekly active-fire detection activity of the source platform on the reference platform's scale, for historical calendars, seasonal baselines and cross-sensor continuity. Not for burned-area estimation, fire counting, or forecasting.

## Target

Aqua-equivalent quality-filtered detections per week at nominal reference looks (coverage-adjusted)

## Model

Paired conditional rate-ratio model (seasonal + land cover); seasonal Fourier order 1; land-cover classes ['savanna', 'cropland', 'grassland', 'forest']; dispersion 1.05 (cell-week), 1.15 (week totals); 200 block-bootstrap replicates.

| Term | Estimate | SE (quasi) |
|---|---|---|
| intercept (log ratio) | 1.150 | 0.016 |
| sin 1/yr | -0.247 | 0.015 |
| cos 1/yr | -0.118 | 0.014 |
| land cover: cropland vs savanna | 0.326 | 0.016 |
| land cover: grassland vs savanna | 0.037 | 0.016 |
| land cover: forest vs savanna | -0.066 | 0.026 |

## Training data

Overlap 2013-01-01 to 2021-06-18 (9 years), 46737 paired cell-weeks with detections, 33303 reference and 163110 source quality detections, mode `exposure`.

## Held-out evaluation

Measures: agreement with held-out reference-platform observations (not independent truth).

### temporal blocks

90% predictive-interval coverage: 0.914

| Method | MAE (week) | RMSE (week) | Deviance (week) | Rel. bias | Deviance (cell-week) |
|---|---|---|---|---|---|
| Raw substitution (source detections treated as reference detections) | 294.4 | 632.2 | 351.696 | 3.898 | 1.774 |
| Single global ratio (train totals) | 7.4 | 14.2 | 2.037 | 0.000 | 0.529 |
| Paired conditional rate-ratio model (seasonal + land cover) | 6.6 | 12.7 | 1.512 | -0.002 | 0.533 |
| Negative-binomial regression calibration | 15.5 | 30.7 | 6.232 | -0.005 | 0.505 |

### spatial blocks

90% predictive-interval coverage: 0.915

| Method | MAE (week) | RMSE (week) | Deviance (week) | Rel. bias | Deviance (cell-week) |
|---|---|---|---|---|---|
| Raw substitution (source detections treated as reference detections) | 73.7 | 166.3 | 89.035 | 3.898 | 1.774 |
| Single global ratio (train totals) | 3.4 | 6.5 | 1.603 | 0.004 | 0.530 |
| Paired conditional rate-ratio model (seasonal + land cover) | 3.1 | 5.6 | 1.408 | 0.002 | 0.533 |
| Negative-binomial regression calibration | 4.8 | 8.6 | 2.594 | 0.013 | 0.508 |

## Gates

* PASS: temporal: beats raw_substitution on held-out AOI-week deviance (1.51 vs 352)
* PASS: temporal: beats global_ratio on held-out AOI-week deviance (1.51 vs 2.04)
* PASS: temporal: 90% predictive interval coverage within [0.8, 0.97] (0.914)
* PASS: spatial: beats raw_substitution on held-out AOI-week deviance (1.41 vs 89)
* PASS: spatial: beats global_ratio on held-out AOI-week deviance (1.41 vs 1.6)
* PASS: spatial: 90% predictive interval coverage within [0.8, 0.97] (0.915)

## Diagnostics

* Activity dependence of the ratio: 0.024 ± 0.006 (not flagged)
* Drift of the log ratio per year: -0.003 ± 0.003 (not flagged)
* Zero check: observed zero share of reference cell-weeks 0.861 vs 0.860 expected under a negative binomial (alpha 0.257); no zero inflation beyond overdispersion if these agree

## Warnings

* Some harmonised weeks are converted outside the calibration overlap (temporal transfer); this assumes the sensor ratio is stationary.

## Limitations

* Validation measures agreement with the reference platform, not accuracy against independent fire observations.
* Conversion outside the overlap assumes a stationary sensor ratio.
* Without valid-observation coverage, coverage differences are confounded with sensitivity differences.
* The ratio may depend on fire size; see the activity-dependence diagnostic.
