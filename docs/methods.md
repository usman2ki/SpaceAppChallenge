# Methods

This document states exactly what Ember Atlas computes, why, and where the
evidence ends. Configuration values quoted here come from
`backend/src/ember_atlas/resources/method_v1.toml` (method version
`ember-method-1.0.0`); every output carries that version and a fingerprint of
the effective configuration.

## 1. What is being measured

FIRMS active-fire products list **fire pixels**: satellite pixels in which a
fire was detected at overpass time. A pixel is not a fire, and pixel counts
are not burned area (MODIS C6/C6.1 user guide, Sec. 8.3.9). The app keeps
three activity metrics separate and never converts one into another:

| Metric | Definition | Sensitive to |
|---|---|---|
| Detection count | quality-filtered fire pixels | pixel size (375 m vs 1 km), fire size distribution, sensitivity, coverage |
| Active-cell count | equal-area cells with ≥ 1 quality detection | much less to pixel size; still to sensitivity and coverage |
| FRP sum (MW) | sum of pixel fire radiative power | pixel size, saturation; compared within a sensor only |

Quality filter (configurable): confidence ≥ nominal (MODIS: ≥ 30 %, per the
user guide's class thresholds; VIIRS: nominal/high) and, where standard
products provide `type`, exclusion of volcano (1), static land source (2) and
offshore (3) detections. Raw counts are always kept beside filtered counts.

## 2. Ingestion, provenance and duplicates

* Every uploaded file is stored gzip-compressed and immutable; every row is
  accepted or rejected with reasons; database triggers forbid changing raw
  evidence and detections.
* Times are UTC (`acq_time` is UTC per FIRMS); the original `acq_date` and
  `acq_time` strings are kept beside the normalised timestamp. Three-digit
  times (`130`) are zero-padded to `0130`.
* Processing streams are parsed from `version` (`6.1` standard, `6.1NRT`,
  `2.0RT`, `2.0URT`, …). A declared product that disagrees with a row (an
  NRT row in a standard file, a NOAA-20 row in an S-NPP file) rejects the row;
  standard and NRT series are never merged.
* `record_key` = origin + platform + stream + UTC minute + lat/lon (1e-6°).
  Same key and content → duplicate (skipped, reported); same key, different
  content → conflict (first kept, conflict recorded); same place at another
  time, platform or stream → a separate, legitimate observation.
* Each import records the **queried period** (declared, or inferred from the
  first and last detection with a warning). This is what lets the app
  distinguish "no data imported" from "imported, no detections reported".

## 3. Common grid and windows

* **Grid:** EASE-Grid 2.0 global (EPSG:6933), the Lambert cylindrical
  equal-area projection on WGS84, implemented in NumPy and checked against the
  published grid extent. Cells nest on the official 9 km (M09) grid: 9, 18, 36,
  72 km. **Default 18 km** — about four times the largest MODIS pixel dimension
  near the scan edge (~4.8 km along-scan), so footprint and geolocation
  differences rarely move a fire seen by both sensors into different cells,
  while landscapes stay resolved. Finer than 9 km would imply more precision
  than 1 km MODIS pixels support. Sensitivity results in
  `artifacts/sensitivity.json`.
* **AOI:** exact polygon intersection (even–odd point-in-polygon on GeoJSON's
  lon/lat edges; PostGIS `ST_Intersects` in the database store). Invalid
  polygons are rejected with the reason; antimeridian-crossing rings are
  unwrapped and split into eastern and western parts.
* **Windows:** UTC days, or 7-day weeks anchored on 1 January (the last week
  absorbs the final 1–2 days), so week k has the same seasonal position every
  year. Window length is reported.

## 4. Observation coverage

A hotspot file cannot show that an empty place was observed (MODIS user guide,
Sec. 7.1.1). Ember Atlas therefore models coverage separately:

* **With a coverage table** (valid looks per cell, day and platform; see the
  data dictionary): zero-detection windows are classified as observed,
  partly observed or not observed; calibration uses valid looks as exposure;
  low-activity anomaly tests become possible.
* **Without one** (any plain FIRMS import): zero windows are shown as "no
  detections reported, coverage unknown" (hatched, never as a zero colour),
  low-activity tests are disabled, and calibration is limited to a research
  estimate (section 5.4).

Valid looks can be derived from the MOD14/MYD14 and VNP14IMG fire-mask layers
(classes "non-fire land" and "fire" are valid observations; "cloud", "not
processed", "unknown" are not; MODIS user guide Table 3). **Ember Atlas does
not yet derive them from Level-2 granules**; it imports a documented table.
The synthetic demo includes synthetic coverage to exercise this path.

## 5. Harmonisation

### 5.1 Target and reference scale

Target: **reference-platform-equivalent quality-filtered detections per
cell-week**, aggregated to the AOI. Default reference: Aqua MODIS; default
source: S-NPP VIIRS, because both cross the equator near 13:30 local time, so
their comparison isolates sensor differences from the diurnal fire cycle
(Terra's ~10:30 overpass would fold the diurnal cycle into the ratio). The
reference is a measurement scale, **not ground truth**. Allowed pairs:
Aqua←S-NPP, Aqua←NOAA-20, Aqua←Terra, Terra←S-NPP. Two VIIRS platforms are
never treated as independent confirmation of each other.

### 5.2 Candidate: paired conditional rate-ratio model

For a cell-week *i* seen by both platforms,
`y_r ~ Poisson(λ_i e_r,i)` and `y_s ~ Poisson(ρ_i λ_i e_s,i)` with unknown
shared activity λ_i and valid-look exposures e. Conditioning on
`n_i = y_r + y_s` removes λ_i exactly:

    y_s | n ~ Binomial(n, π),  logit π = log ρ + log(e_s / e_r)
    log ρ = β0 + Σ_k [a_k sin(2πk·doy/365.25) + b_k cos(2πk·doy/365.25)] + γ_landcover

* No assumption about the amount of fire is needed; cell-weeks with no
  detections from either sensor carry no information and are correctly
  ignored; overdispersion from a shared multiplicative activity process
  cancels. Remaining extra-binomial variation is absorbed by a
  quasi-likelihood dispersion (Pearson χ²/df).
* Seasonal order k ∈ {0, 1, 2} is chosen by leave-year-block-out binomial
  deviance **on training data only**; ties (< 2 deviance units) go to the
  simpler model. Land-cover classes need ≥ 30 active cell-weeks.
* Conversion: `ŷ_r = y_s / ρ̂ · (e_r / e_s)`; at window level in exposure
  mode, the harmonised value is coverage-adjusted to the reference's nominal
  looks: `(Σ y_s/ρ̂) · Σ nom / Σ e_s`. Where the reference platform was itself
  observed, its own (coverage-adjusted) count is used, so harmonised values
  are converted only where needed.
* Fitted by IRLS (`science/glm.py`), checked in tests against scikit-learn
  and closed-form maximum-likelihood solutions.

**Identifiability limits.** ρ is identified only relative to the reference.
Anything affecting both sensors equally (fire activity, clouds shared by
near-simultaneous overpasses) cancels; anything that differs between them and
covaries with season or land cover (overpass time, swath gaps, small-fire
sensitivity) is absorbed into ρ(doy, land cover). The ratio is specific to
the quality filter: in the synthetic sensitivity run it changed from 3.16
(nominal+) to 1.19 (high only), so it is always refitted for the active
filter.

### 5.3 Alternatives and baselines (all evaluated, never hidden)

* **Raw substitution**: VIIRS detections counted as if they were MODIS
  detections (the naive splice).
* **Global ratio**: one training-period ratio of totals.
* **Negative-binomial regression calibration**:
  `log E[y_r] = b0 + b1 log(1 + y_s) + seasonal + land cover (+ log e_r)`.
  Prediction-optimal for individual cell-weeks but it shrinks extremes towards
  the seasonal mean (regression dilution), which is harmful for anomaly
  detection. On the synthetic data it had the best cell-week deviance and the
  worst AOI-week deviance of the calibrated methods — kept as an honest
  negative result.

### 5.4 Status gates (`science/status.py`)

| Code | When | Harmonised series shown? |
|---|---|---|
| `insufficient_data` | < 150 paired active cell-weeks, < 500 detections per platform, < 3 overlap years or < 4 spatial blocks | no |
| `not_evaluated` | fitted, hold-out validation not run yet | yes, labelled unvalidated |
| `failed_validation` | candidate did not beat raw substitution and global ratio on held-out AOI-week deviance in both schemes, or 90 % interval coverage outside [0.80, 0.97] | no (diagnostic view only) |
| `synthetic_demo_verified` | gates passed on synthetic data | yes, labelled synthetic |
| `research_estimate` | gates passed on real data without coverage exposure | yes, labelled not validated |
| `validated_holdout` | gates passed on real data with coverage exposure | yes; means agreement with the reference sensor, not ground truth |

### 5.5 Uncertainty

Block bootstrap: spatial blocks of 4 × 4 cells × 4-week time blocks are
resampled with replacement and the model is refitted (200 replicates by
default; the seasonal order is held at its selected value, so selection
uncertainty is not propagated). Window-level intervals combine bootstrap
parameter draws with overdispersed (Gamma) noise of the source counts; the
*predictive* interval adds reference-count noise and is the one checked for
coverage on hold-outs. Reference-observed windows get a sampling interval
from the same dispersion.

### 5.6 Transfer outside the overlap

Converted values outside the overlap years assume a stationary ratio. The fit
reports a linear trend of the log ratio across the overlap (flagged when
|trend| > 0.03/yr and > 2 SE) and per-year ratios. Terra and Aqua orbits
drifted late in their missions, which can shift overpass times; check the
per-year ratios before relying on late overlap years. Pre-overlap years need
no conversion when the reference platform (Aqua, from 2002) covers them;
Terra-only years before Aqua require the Aqua←Terra pair, whose ratio
includes the diurnal-cycle difference.

### 5.7 Diagnostics reported with every fit

Dispersion (cell-week and week totals), dependence of the ratio on activity
level (log n term; non-zero means constant-ratio conversion is biased for very
large or small events), drift, per-year ratios, zero check (observed zero
share vs negative-binomial expectation, exposure mode only), land-cover
classes not seen in calibration.

## 6. Validation

* **Blocked hold-outs only.** Temporal: leave one overlap year out, with a
  2-week buffer removed from training on both sides. Spatial: blocks of 4 × 4
  cells assigned to 4 folds, with a 1-cell (Chebyshev) buffer removed from
  training. Never random row splits.
* **Train-only preprocessing.** Every data-dependent step (global ratio,
  seasonal order, dispersion, bootstrap) happens inside `fit(train)`. A test
  scrambles all non-training labels and asserts identical fitted parameters.
* **Metrics** on held-out reference observations: AOI-week MAE, RMSE, mean
  Poisson deviance, relative bias, median |log ratio|; cell-week deviance;
  90 % predictive-interval coverage and width; stability by season and land
  cover where samples allow.
* **What this measures:** agreement with the reference platform on unseen
  blocks. No independent fire reference data (e.g. field records or
  higher-resolution detections) are used.
* **Synthetic recovery** (`ember recovery`) is separate from any real-data
  evidence: a world with a known injected ratio (log 3, seasonal amplitude
  0.25 peaking at day 245, land-cover shifts), synthetic clouds shared by the
  13:30 overpasses, MODIS swath gaps, a Terra outage and an end of the Aqua
  record. See `artifacts/synthetic_recovery.md` for the latest numbers,
  including where it falls short (two of six parameters just outside their
  90 % intervals in the full run, small negative bias of ~0.03 in the log
  ratio).
* **Sensitivity** (`ember sensitivity`): grid 9/18/36/72 km, calibration
  window day/week, confidence low/nominal/high, coverage used vs ignored.

## 7. Seasonal baselines and anomalies

* Weekly windows. Baseline for (year Y, week k) = weeks k±1 of all other years
  with observations (leave-one-year-out); fewer than 5 other years →
  "insufficient history". Unqueried weeks are never zeros.
* Test: log-scale predictive t-test,
  `t = (log1p(x) − median) / (s·√(1 + 1/n_years))`, df = n_years − 1, with a
  robust scale `s = max(1.4826·MAD, IQR/1.349)` of the baseline log values.
  Year-to-year variation of burning is multiplicative; a negative-binomial
  moment fit was tried first and put so much probability near zero that a
  ten-fold drop could not be flagged.
* Tests run for single weeks and for 4-week blocks (persistent episodes
  accumulate evidence). Converted (non-reference) weeks test their lower
  interval bound for excess. Low-activity tests need coverage.
* Multiple comparisons: Benjamini–Hochberg FDR at q = 0.05 over **all** tests
  in the view (both scales, both directions). Looking at 13 years means ~1,700
  tests and needs far stronger evidence than one year (~130 tests); choose the
  period before looking. In the synthetic world the injected 8× August surge
  and 0.15× drought-like deficit are flagged in single-year views but not when
  all 13 years are tested at once — an honest power limitation.
* Critical periods: the climatological peak season (weeks with median ≥ 50 %
  of the peak median) and runs of flagged weeks (episodes) with their excess
  and smallest q-value.
* **Not forecasts.** Forecasting fire risk would need weather, fuel and
  ignition data and prospective validation.
