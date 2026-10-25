# Synthetic recovery experiment

Generated 2026-10-24T23:58:41Z with Ember Atlas 0.1.0, method ember-method-1.0.0, seed 20261025, 100 block-bootstrap replicates. **Synthetic data only — this checks the method against a known truth; it says nothing about real MODIS/VIIRS behaviour.**

Injected effect: S-NPP/Aqua detection ratio log(3) with a seasonal amplitude of 0.25 (peak at day 245) and land-cover shifts (cropland +0.30, forest −0.10). The calibration targets *quality-filtered* counts, so the effective truth includes the filter pass rates (MODIS 0.8525, VIIRS 0.92).

## Parameters (coverage-exposure mode)

| Quantity | Truth | Estimate | 90% interval | Inside |
|---|---|---|---|---|
| annual-mean log ratio, savanna | 1.175 | 1.142 | 1.119–1.170 | no |
| annual-mean log ratio, grassland | 1.175 | 1.174 | 1.141–1.208 | yes |
| annual-mean log ratio, cropland | 1.475 | 1.466 | 1.437–1.497 | yes |
| annual-mean log ratio, forest | 1.075 | 1.083 | 1.041–1.135 | yes |
| seasonal amplitude of log ratio | 0.250 | 0.282 | 0.251–0.309 | no |
| day of year of peak ratio | 245.000 | 247.919 | 244.216–251.774 | yes |

## Conversion after the reference record ends

78 weeks (2021-07-02 to 2022-12-24) where only S-NPP exists. Truth: Aqua-equivalent quality-filtered detections at nominal looks (2 per day).

* relative bias of converted weekly totals: -1.7%
* naive splice (raw S-NPP counts) relative bias: 237.4%
* share of weeks whose 90% estimate interval contains the truth: 87%

## Hotspot-only mode (coverage hidden)

Without valid-observation coverage the ratio absorbs the average coverage difference between the platforms (VIIRS has no swath gaps here), so the log ratio is expected to sit above the coverage-adjusted truth:

* annual-mean log ratio, savanna: 1.293 (1.269–1.324)
* annual-mean log ratio, grassland: 1.328 (1.294–1.361)
* annual-mean log ratio, cropland: 1.618 (1.591–1.650)
* annual-mean log ratio, forest: 1.238 (1.201–1.291)
* seasonal amplitude of log ratio: 0.268 (0.235–0.295)
* day of year of peak ratio: 242.804 (238.229–246.746)

Transfer bias against the hotspot-mode truth (expected Aqua quality-filtered detections under Aqua's counterfactual coverage): -1.2%, interval coverage 78%.

## Injected anomalies (single-year test families)

* excess: factor 8.0 in 2019 (days 213–250, area S): 1 of 6 episode weeks flagged; 0 weeks flagged elsewhere in that year; 130 tests.
* deficit: factor 0.15 in 2016 (days 200–270, area all): 6 of 11 episode weeks flagged; 4 weeks flagged elsewhere in that year; 130 tests.

Detection power depends on how large an anomaly is relative to natural year-to-year variability and on how many other years form the baseline; with few baseline years (quick mode) even large injected episodes may not survive FDR control.
