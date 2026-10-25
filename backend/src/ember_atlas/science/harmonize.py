"""Harmonised burning-activity series for a selection.

Output quantity (per calibration window = week):
* hotspot-only mode : reference-equivalent quality detections
                      H_src = sum_c y_s,c / rho_c          (coverage assumed comparable)
* exposure mode     : coverage-adjusted reference-equivalent detections at the
                      reference platform's nominal looks
                      H_src = (sum_c y_s,c / rho_c) * (sum_c nom_c / sum_c e_s,c)
                      H_ref = Y_r * (sum_c nom_c / sum_c e_r,c)
Harmonised value policy: the reference platform's own observations where it
was queried for the whole window, otherwise the converted source platform.
Windows outside the calibration overlap are flagged as temporal transfer.
Individual detections are never converted; only cell-week and coarser
aggregates have a harmonised value (information loss is explicit).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..analysis.aggregate import Selection
from .calibration import SeasonalRatio, aggregate_intervals, block_bootstrap_betas, build_pair_table, diagnostics
from .status import decide, sufficiency


def _ref_interval(Y: np.ndarray, phi: float, interval: float, rng) -> tuple[np.ndarray, np.ndarray]:
    a = (1 - interval) / 2
    shape = (Y + 0.5) / phi
    sims = rng.gamma(shape[None, :], (1.0 / shape)[None, :] * (Y + 0.5)[None, :], size=(2000, len(Y)))
    return np.quantile(sims, a, axis=0), np.quantile(sims, 1 - a, axis=0)


def harmonize(store, sel: Selection, ref: str, src: str, evaluation: dict | None = None, bootstrap_reps: int | None = None) -> dict:
    cfg = sel.cfg
    interval = cfg["calibration"]["interval"]
    seed = cfg["calibration"]["seed"]
    pair = build_pair_table(store, sel, ref, src)
    suff = sufficiency(pair.info, cfg)
    status = decide(sel.data_origin, pair.mode, suff, evaluation)
    dense = pair.all_windows
    out: dict = {"pair": pair.info, "sufficiency": suff, "status": status, "mode": pair.mode}

    # raw per-window totals (always returned)
    g = dense.groupby("window_start")
    series = pd.DataFrame(
        {
            "ref_raw": g["y_r"].sum(),
            "src_raw": g["y_s"].sum(),
            "ref_queried": g["queried_r"].first(),
            "src_queried": g["queried_s"].first(),
        }
    )
    series["raw_pooled"] = series["ref_raw"] + series["src_raw"]
    if not suff["passed"]:
        out["series"] = series.reset_index().to_dict("records")
        out["model"] = None
        return out

    model = SeasonalRatio(pair.mode, cfg).fit(pair.units)
    B = bootstrap_reps if bootstrap_reps is not None else cfg["calibration"]["bootstrap_replicates"]
    betas = block_bootstrap_betas(model, pair.units, B, seed)
    rng = np.random.default_rng(seed + 7)

    src_units = dense[dense["queried_s"]].copy()
    if pair.mode == "exposure":
        src_units = src_units[src_units["e_s"].notna() & (src_units["e_s"] > 0)]
        # conversion to nominal reference looks happens at window level (below)
        src_units["e_r"] = src_units["e_s"]
    unseen = model.log_rho(src_units)[1]
    iv = aggregate_intervals(model, betas, src_units, "window_start", interval, seed + 11).set_index("window_start")
    if pair.mode == "exposure":
        w = src_units.groupby("window_start")
        F = (w["nom_s"].sum() / w["e_s"].sum()).reindex(iv.index)
        for c in ("estimate", "est_lo", "est_hi", "pred_lo", "pred_hi"):
            iv[c] = iv[c] * F
    series = series.join(iv[["estimate", "est_lo", "est_hi"]].rename(columns={"estimate": "src_converted", "est_lo": "src_conv_lo", "est_hi": "src_conv_hi"}))

    # reference observed (coverage-adjusted in exposure mode), with sampling interval
    ref_units = dense[dense["queried_r"]]
    if pair.mode == "exposure":
        ref_units = ref_units[ref_units["e_r"].notna() & (ref_units["e_r"] > 0)]
        rw = ref_units.groupby("window_start")
        Fr = rw["nom_r"].sum() / rw["e_r"].sum()
        Yr = rw["y_r"].sum()
        ref_adj = Yr * Fr
    else:
        Yr = ref_units.groupby("window_start")["y_r"].sum()
        Fr = pd.Series(1.0, index=Yr.index)
        ref_adj = Yr.astype(float)
    lo, hi = _ref_interval(Yr.to_numpy(float), model.phi_window_, interval, rng)
    ref_df = pd.DataFrame({"ref_value": ref_adj, "ref_lo": lo * Fr.to_numpy(), "ref_hi": hi * Fr.to_numpy()})
    series = series.join(ref_df)

    ov0, ov1 = pair.info["overlap_start"], pair.info["overlap_end"]
    idx = series.index.to_series()
    series["in_overlap"] = (idx >= ov0) & (idx <= ov1)
    use_ref = series["ref_queried"].fillna(False).astype(bool) & series["ref_value"].notna()
    use_src = ~use_ref & series["src_converted"].notna()
    series["harmonized"] = np.where(use_ref, series["ref_value"], np.where(use_src, series["src_converted"], np.nan))
    series["harm_lo"] = np.where(use_ref, series["ref_lo"], np.where(use_src, series["src_conv_lo"], np.nan))
    series["harm_hi"] = np.where(use_ref, series["ref_hi"], np.where(use_src, series["src_conv_hi"], np.nan))
    series["basis"] = np.where(use_ref, "reference_observed", np.where(use_src, f"converted_from_{src}", "none"))
    series["transfer_outside_overlap"] = use_src & ~series["in_overlap"]

    diag = diagnostics(model, pair.units, cfg)
    warnings = []
    if diag["drift"]["flag"]:
        warnings.append("Significant drift of the sensor ratio across the overlap: transfer outside the overlap is unreliable.")
    if diag["proportionality"]["flag"]:
        warnings.append("The sensor ratio depends on activity level: constant-ratio conversion may be biased for very large or very small events.")
    if unseen.any():
        warnings.append(f"{int(unseen.sum())} cell-weeks have a land-cover class not seen in calibration; the baseline class ratio was used.")
    if series["transfer_outside_overlap"].any():
        warnings.append("Some harmonised weeks are converted outside the calibration overlap (temporal transfer); this assumes the sensor ratio is stationary.")
    if pair.mode == "hotspot_only":
        warnings.append(
            "No valid-observation coverage: coverage differences between platforms are absorbed into the ratio, and zero weeks cannot be distinguished from unobserved weeks."
        )

    out["series"] = series.reset_index().replace({np.nan: None}).to_dict("records")
    out["model"] = {
        "name": model.name,
        "label": model.label,
        "harmonic_order": model.order_,
        "landcover_classes": model.lc_classes_,
        "coefficients": model.coefficient_table(),
        "ratio_curve": model.ratio_curve(betas, interval),
        "dispersion_cell_week": model.phi_,
        "dispersion_window": model.phi_window_,
        "bootstrap_reps_effective": len(betas),
        "interval": interval,
    }
    out["diagnostics"] = diag
    out["warnings"] = warnings
    out["target"] = f"{ref}-equivalent quality-filtered detections per week" + (
        " at nominal reference looks (coverage-adjusted)" if pair.mode == "exposure" else " (coverage assumed comparable between platforms)"
    )
    return out
