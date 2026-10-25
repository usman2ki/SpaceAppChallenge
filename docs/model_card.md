# Model card (template and current state)

`ember evaluate` writes a filled-in model card for a specific area, data
origin and platform pair to `artifacts/model_card_<origin>_<ref>_<src>.md`.
The only card produced so far is for **synthetic** data
(`artifacts/model_card_synthetic_Aqua_SuomiNPP.md`); it documents that the
method works on a world with a known answer, not how real sensors behave.

## Model
Paired conditional rate-ratio model (binomial GLM with logit link on the share
of paired detections from the source sensor; seasonal Fourier terms; land-cover
effects; log valid-look ratio offset when coverage exists). Details:
docs/methods.md section 5.

## Intended use
Historical burning-activity calendars that span the MODIS and VIIRS eras,
seasonal baselines and comparison of years, at cell-week and coarser
aggregation, for scientists, land managers and emergency analysts exploring
the past record.

## Out of scope
Burned area, number of fires, fire size, emissions, operational alerts, fire
risk forecasting, conversion of individual detections, regions or periods
outside the calibration domain without re-fitting.

## Training data
The platform overlap within the selected area (full stored record, not just
the dates on screen). Requirements: ≥ 150 paired active cell-weeks, ≥ 500
quality detections per platform, ≥ 3 overlap years, ≥ 4 spatial blocks.

## Evaluation
Blocked temporal (leave-one-year-out, 2-week buffer) and spatial (4 × 4-cell
blocks, 1-cell buffer) hold-outs; metrics against the reference platform's
held-out observations; gates in docs/methods.md §5.4. No independent ground
truth.

## Current evidence (synthetic only, from artifacts/ in the build sandbox)
| | Temporal blocks | Spatial blocks |
|---|---|---|
| Candidate AOI-week mean Poisson deviance | 1.51 | 1.41 |
| Global ratio baseline | 2.04 | 1.60 |
| Raw substitution baseline | 352 | 89 |
| NB regression alternative | 6.23 | 2.59 |
| 90 % predictive interval coverage | 0.914 | 0.915 |

Synthetic recovery: see artifacts/synthetic_recovery.md (transfer after the
simulated end of the Aqua record had a relative bias of about −2 % against
the known truth, versus +237 % for naive splicing; some parameter intervals
were slightly too narrow).

## Known limitations and risks
* Agreement with Aqua MODIS is not accuracy. Systematic errors shared by both
  sensors are invisible to this validation.
* Without coverage data the ratio absorbs coverage differences (confounding).
* The ratio can depend on fire size (diagnostic reported) and on the quality
  filter (refitted per filter).
* Transfer outside the overlap assumes stationarity; Terra/Aqua orbit drift
  late in their missions may break it.
* Bootstrap intervals hold the seasonal order fixed and may be optimistic.

## Ethical considerations
Fire maps can affect land-use and enforcement decisions. The app shows
uncertainty and coverage gaps by default, refuses unsupported calibrations,
and labels synthetic and unvalidated results conspicuously so they are not
mistaken for evidence.
