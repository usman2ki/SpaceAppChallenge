"""Blocked out-of-sample validation of harmonisation methods.

Leakage rules (tested in tests/test_validation.py):
* folds are blocked in time (leave-one-year-out) or space (blocks of
  ``spatial_block_cells`` x ``spatial_block_cells`` analysis cells), never random rows;
* a buffer of ``temporal_buffer_weeks`` / ``spatial_buffer_cells`` around the
  test block is removed from training, because hotspot counts are strongly
  autocorrelated;
* every data-dependent step (global ratio, harmonic-order selection,
  dispersion, bootstrap) runs inside ``fit(train)`` only.

What is evaluated: agreement with the *reference platform's own held-out
observations* (cross-sensor agreement), not accuracy against independent
truth. No independent fire reference data are used.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .calibration import METHODS, PairTable, SeasonalRatio, aggregate_intervals, block_bootstrap_betas
from .glm import poisson_deviance_terms

DEV_FLOOR = 0.05  # predicted means are floored for deviance (a prediction of 0 for an observed fire is otherwise infinite)
SEASONS = {"DJF": (12, 1, 2), "MAM": (3, 4, 5), "JJA": (6, 7, 8), "SON": (9, 10, 11)}


# ------------------------------------------------------------------ folds


def temporal_folds(units: pd.DataFrame, cfg: dict) -> list[dict]:
    buf = pd.Timedelta(weeks=cfg["validation"]["temporal_buffer_weeks"])
    ws = pd.to_datetime(units["window_start"])
    folds = []
    for yr in sorted(units["year"].unique()):
        test = (units["year"] == yr).to_numpy()
        t0, t1 = ws[test].min(), ws[test].max()
        near = ((ws >= t0 - buf) & (ws <= t1 + buf)).to_numpy()
        train = ~near
        folds.append({"label": f"year {yr}", "train": train, "test": test, "buffer": int((near & ~test).sum())})
    return folds


def spatial_folds(units: pd.DataFrame, cfg: dict, seed: int) -> list[dict]:
    b = cfg["validation"]["spatial_block_cells"]
    k = cfg["validation"]["n_spatial_folds"]
    bufc = cfg["validation"]["spatial_buffer_cells"]
    blk = (units["col"] // b).astype(str) + "_" + (units["row"] // b).astype(str)
    ub = np.array(sorted(blk.unique()))
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(ub))
    assign = {ub[i]: j % k for j, i in enumerate(order)}
    fold_of = blk.map(assign).to_numpy()
    cells = units[["cell_id", "col", "row"]].drop_duplicates()
    folds = []
    for f in range(k):
        test = fold_of == f
        if not test.any():
            continue
        tc = cells[cells["cell_id"].isin(set(units.loc[test, "cell_id"]))]
        # Chebyshev distance buffer around every test cell
        near_cells = set()
        cols, rows = tc["col"].to_numpy(), tc["row"].to_numpy()
        for _, c in cells.iterrows():
            if np.any((np.abs(cols - c["col"]) <= bufc) & (np.abs(rows - c["row"]) <= bufc)):
                near_cells.add(c["cell_id"])
        near = units["cell_id"].isin(near_cells).to_numpy()
        folds.append({"label": f"spatial fold {f + 1}/{k}", "train": ~near & ~test, "test": test, "buffer": int((near & ~test).sum())})
    return folds


# ------------------------------------------------------------------ metrics


def _metrics(obs: np.ndarray, pred: np.ndarray) -> dict:
    obs = np.asarray(obs, float)
    pred = np.asarray(pred, float)
    if len(obs) == 0:
        return {}
    err = pred - obs
    return {
        "n": len(obs),
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err**2))),
        "mean_poisson_deviance": float(np.mean(poisson_deviance_terms(obs, np.maximum(pred, DEV_FLOOR)))),
        "bias": float(np.mean(err)),
        "relative_bias": float(err.sum() / max(obs.sum(), 1e-9)),
        "median_abs_log_ratio": float(np.median(np.abs(np.log((pred + 1) / (obs + 1))))),
    }


def _season(window_start: pd.Series) -> np.ndarray:
    m = pd.to_datetime(window_start).dt.month.to_numpy()
    out = np.empty(len(m), dtype=object)
    for name, months in SEASONS.items():
        out[np.isin(m, months)] = name
    return out


# ------------------------------------------------------------------ evaluation


def evaluate(
    pair: PairTable,
    cfg: dict,
    methods: tuple[str, ...] = ("raw_substitution", "global_ratio", "seasonal_ratio", "nb_regression"),
    bootstrap_reps: int | None = None,
    seed: int | None = None,
    schemes_to_run: tuple[str, ...] = ("temporal", "spatial"),
) -> dict:
    seed = cfg["calibration"]["seed"] if seed is None else seed
    B = bootstrap_reps if bootstrap_reps is not None else max(50, cfg["calibration"]["bootstrap_replicates"] // 2)
    # B == 0 skips interval estimation (used by the sensitivity sweep)
    interval = cfg["calibration"]["interval"]
    units = pair.units
    schemes = {}
    if "temporal" in schemes_to_run:
        schemes["temporal"] = temporal_folds(units, cfg)
    if "spatial" in schemes_to_run:
        schemes["spatial"] = spatial_folds(units, cfg, seed)
    report: dict = {
        "reference": pair.ref,
        "source": pair.src,
        "mode": pair.mode,
        "interval": interval,
        "bootstrap_reps": B,
        "what_is_measured": "agreement with held-out reference-platform observations (not independent truth)",
        "schemes": {},
    }
    for scheme, folds in schemes.items():
        fold_rows, cell_rows, win_rows = [], [], []
        for fi, f in enumerate(folds):
            train, test = units[f["train"]], units[f["test"]]
            if (train["n"] > 0).sum() < 30 or len(test) == 0:
                fold_rows.append({"fold": f["label"], "skipped": "too few paired detections in training data"})
                continue
            fold_info = {
                "fold": f["label"],
                "train_units": len(train),
                "test_units": len(test),
                "buffer_units_removed": f["buffer"],
                "train_active": int((train["n"] > 0).sum()),
                "test_ref_total": int(test["y_r"].sum()),
            }
            preds = {}
            for name in methods:
                m = METHODS[name](pair.mode, cfg).fit(train)
                preds[name] = m.predict_mean(test)
                if isinstance(m, SeasonalRatio):
                    fold_info["harmonic_order"] = m.order_
                if isinstance(m, SeasonalRatio) and B > 0:
                    betas = block_bootstrap_betas(m, train, B, seed + fi)
                    iv = aggregate_intervals(m, betas, test, "window_start", interval, seed + 1000 + fi)
                    obs_w = test.groupby("window_start")["y_r"].sum().reindex(iv["window_start"]).to_numpy()
                    inside = (obs_w >= iv["pred_lo"].to_numpy()) & (obs_w <= iv["pred_hi"].to_numpy())
                    fold_info["interval_coverage"] = float(inside.mean())
                    fold_info["interval_mean_width"] = float((iv["pred_hi"] - iv["pred_lo"]).mean())
                    win_rows.append(
                        pd.DataFrame(
                            {"fold": f["label"], "window_start": iv["window_start"], "inside": inside, "width": (iv["pred_hi"] - iv["pred_lo"]).to_numpy()}
                        )
                    )
            for name, p in preds.items():
                cell_rows.append(
                    pd.DataFrame(
                        {
                            "method": name,
                            "obs": test["y_r"].to_numpy(float),
                            "pred": p,
                            "fold": f["label"],
                            "season": _season(test["window_start"]),
                            "lc": test["lc"].to_numpy(),
                            "window_start": test["window_start"].to_numpy(),
                        }
                    )
                )
            fold_rows.append(fold_info)
        if not cell_rows:
            report["schemes"][scheme] = {"folds": fold_rows, "error": "no usable folds"}
            continue
        cells = pd.concat(cell_rows, ignore_index=True)
        # AOI-week totals (sum over test cells) per fold and method
        wins = cells.groupby(["method", "fold", "window_start"], as_index=False)[["obs", "pred"]].sum()
        summary = {}
        for name in methods:
            c = cells[cells["method"] == name]
            w = wins[wins["method"] == name]
            per_fold = {fl: _metrics(g["obs"], g["pred"]) for fl, g in w.groupby("fold")}
            summary[name] = {
                "label": METHODS[name].label,
                "kind": METHODS[name].kind,
                "cell_week": _metrics(c["obs"], c["pred"]),
                "aoi_week": _metrics(w["obs"], w["pred"]),
                "aoi_week_per_fold": per_fold,
                "by_season": {s: _metrics(g["obs"], g["pred"]) for s, g in w.assign(season=_season(w["window_start"])).groupby("season") if len(g) >= 20},
                "by_landcover": {lc: _metrics(g["obs"], g["pred"]) for lc, g in c.groupby("lc") if len(g) >= 200 and lc != "unknown"},
            }
        cov = pd.concat(win_rows, ignore_index=True) if win_rows else None
        report["schemes"][scheme] = {
            "folds": fold_rows,
            "methods": summary,
            "interval_coverage": float(cov["inside"].mean()) if cov is not None else None,
            "interval_mean_width": float(cov["width"].mean()) if cov is not None else None,
            "n_test_windows": int(wins["window_start"].nunique()),
        }
    report["gates"] = gate(report, cfg)
    return report


def gate(report: dict, cfg: dict, candidate: str = "seasonal_ratio") -> dict:
    v = cfg["validation"]
    checks = []
    for scheme, res in report["schemes"].items():
        meths = res.get("methods")
        if not meths or candidate not in meths:
            checks.append({"check": f"{scheme}: evaluation ran", "passed": False, "detail": res.get("error", "missing")})
            continue
        cand = meths[candidate]["aoi_week"]["mean_poisson_deviance"]
        for b in v["must_beat"]:
            if b not in meths:
                continue
            base = meths[b]["aoi_week"]["mean_poisson_deviance"]
            checks.append({"check": f"{scheme}: beats {b} on held-out AOI-week deviance", "passed": bool(cand < base), "detail": f"{cand:.3g} vs {base:.3g}"})
        cov = res.get("interval_coverage")
        ok = cov is not None and v["interval_coverage_min"] <= cov <= v["interval_coverage_max"]
        checks.append(
            {
                "check": f"{scheme}: {int(report['interval'] * 100)}% predictive interval coverage within "
                f"[{v['interval_coverage_min']}, {v['interval_coverage_max']}]",
                "passed": bool(ok),
                "detail": "n/a" if cov is None else f"{cov:.3f}",
            }
        )
    return {"checks": checks, "passed": bool(checks) and all(c["passed"] for c in checks)}
