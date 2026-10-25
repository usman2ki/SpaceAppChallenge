# Reproducibility

## Versions recorded in every output
* `app_version` (package), `method_version` and `method_fingerprint` (SHA-256 of
  the effective method config), `data_version` (import and coverage batches),
  and, in cache keys, a fingerprint of the analysis source code.
* Imports keep the original file bytes and SHA-256, parser version, declared
  source, retrieval date and queried period.

## Deterministic synthetic data
* `SyntheticSpec(seed=20261025)` generates the same world given the same
  NumPy and pandas versions (NumPy does not promise identical random streams
  for every distribution across versions; this was run with NumPy 2.5.3). `tests/test_firms_and_determinism.py`
  checks same-seed identity and different-seed difference.
* `ember seed-demo` prints the SHA-256 of the generated CSVs. In the build
  sandbox it was
  `dbf0995aa5444ba64884d4348a63c583e9db2d66560df5d72e8239d926b6fa69`
  (13-year world, 461,894 detection rows including the synthetic static heat source; see
  docs/status.md for the exact run). A different hash on another machine means
  a different NumPy/pandas version or generator change, not necessarily an
  error.
* Bootstrap and fold assignment use `calibration.seed` from the method config.

## Environment used for the recorded runs
Python 3.13.17 on Linux (x86_64, 2 CPUs, 8 GB RAM); NumPy 2.5.3, pandas 3.0.5,
SciPy 1.18.1, Starlette 1.7.0, uvicorn 0.54.0, pytest 9.1.1, httpx 0.28.1,
scikit-learn 1.9.1, matplotlib 3.11.2, Playwright 1.63.0 with Chromium 153;
Node 22.23.4, esbuild 0.28.2, React 19.2.8, d3-geo 3.1.1, TypeScript 6.0.3.
`backend/requirements.lock.txt` lists these pins. FastAPI and psycopg were not
installable there, so no lock file covering them exists yet: run
`uv pip compile backend/pyproject.toml --extra dev --extra postgis -o backend/requirements.lock.txt`
on a networked machine and commit the result. The frontend `package.json` pins
the runtime libraries; `package-lock.json` likewise needs one networked
`npm install`.

## Re-running the evidence
```bash
ember seed-demo
ember evaluate --dataset synthetic --aoi pilot-cerrado     # artifacts/evaluation_*.json, model_card_*.md, harmonized_series_*.csv
ember recovery                                             # artifacts/synthetic_recovery.{json,md}
ember sensitivity                                          # artifacts/sensitivity.json
python scripts/benchmark.py                                # artifacts/performance.json
```
Numbers in docs/status.md come from these files as generated in the build
sandbox; rerunning them is the way to check them.
