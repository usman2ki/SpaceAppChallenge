"""Cross-sensor calibration models on the platform overlap.

Target and reference scale
--------------------------
The harmonised quantity is *reference-platform-equivalent active-fire
detections* (default reference: Aqua MODIS) aggregated over equal-area cells
and weeks. The reference platform is a measurement scale, not ground truth.

Candidate C1 - paired conditional rate-ratio model (``SeasonalRatio``)
---------------------------------------------------------------------
For a cell-week i observed by the reference (r) and source (s) platform

    y_r,i ~ Poisson(lambda_i e_r,i),   y_s,i ~ Poisson(rho_i lambda_i e_s,i)

with unknown latent activity lambda_i shared by both sensors and valid-look
exposures e. Conditioning on n_i = y_r,i + y_s,i removes lambda_i exactly:

    y_s,i | n_i ~ Binomial(n_i, pi_i),   logit pi_i = log rho_i + log(e_s,i / e_r,i)

    log rho_i = beta_0 + sum_k (a_k sin + b_k cos)(2 pi k doy_i / 365.25) + gamma_lc(i)

So the model needs no assumption about how much fire there is, cell-weeks
with n_i = 0 carry no information (and are correctly ignored), and
overdispersion from a shared multiplicative latent process cancels.
Remaining extra-binomial variation is absorbed by a quasi-likelihood
dispersion. Conversion: y_r-equivalent = y_s / rho * (e_r / e_s).

Identifiability limits: rho is only identified *relative* to the reference;
anything that affects both platforms equally (fire activity, shared clouds)
cancels; anything that differs between them and covaries with season or land
cover (overpass time, swath gaps, sensitivity to small fires) is absorbed into
rho(doy, lc). In hotspot-only mode e_s/e_r is unknown and set to 1, so rho
also absorbs average coverage differences (confounded, documented).

Candidate C2 - negative-binomial regression calibration (``NBRegression``)
-------------------------------------------------------------------------
    log E[y_r] = b0 + b1 log(1 + y_s') + seasonal + lc (+ log e_r)
A prediction-optimal regression of the reference count on the source count.
It shrinks extremes towards the seasonal mean (regression dilution), which is
a known weakness for anomaly detection; evaluated, not preferred a priori.

Baselines: raw substitution (y_r_hat = y_s) and a single global ratio.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..analysis.aggregate import (
    Selection,
    cell_landcover,
    cell_window_counts,
    cell_window_exposure,
    queried_days,
)
from ..analysis.windows import window_table
from .glm import GLMResult, fit_glm, harmonics

# ======================================================================== data


@dataclass
class PairTable:
    ref: str
    src: str
    mode: str  # "exposure" | "hotspot_only"
    units: pd.DataFrame  # all overlap cell-weeks used for evaluation
    info: dict = field(default_factory=dict)
    all_windows: pd.DataFrame | None = None  # cell-weeks for the full selection (for conversion)


def build_pair_table(store, sel: Selection, ref: str, src: str) -> PairTable:
    cfg = sel.cfg
    csel = Selection(sel.data_origin, sel.aoi, sel.date_start, sel.date_end, [ref, src], sel.streams, sel.grid_km, cfg["calibration"]["window"], cfg)
    dense = cell_window_counts(store, csel, [ref, src])
    dense = dense.rename(columns={f"y_{ref}": "y_r", f"y_{src}": "y_s"})
    windows = window_table(csel.date_start, csel.date_end, csel.window)
    q = queried_days(store, csel, windows)
    full = {}
    for p in (ref, src):
        qp = q[q["platform"] == p].merge(windows[["window_start", "n_days"]], on="window_start")
        full[p] = set(qp.loc[qp["queried_days"] >= qp["n_days"], "window_start"])
    dense["queried_r"] = dense["window_start"].isin(full[ref])
    dense["queried_s"] = dense["window_start"].isin(full[src])

    exp = cell_window_exposure(store, csel, [ref, src], dense)
    mode = "hotspot_only"
    if exp is not None:
        exp = exp.rename(columns={f"e_{ref}": "e_r", f"e_{src}": "e_s", f"nom_{ref}": "nom_r", f"nom_{src}": "nom_s"})
        dense = dense.merge(exp, on=["cell_id", "window_start"], how="left")
        mode = "exposure"
    else:
        for c in ("e_r", "e_s", "nom_r", "nom_s"):
            dense[c] = np.nan

    lc = cell_landcover(store, sel.data_origin, csel.grid, sorted(dense["cell_id"].unique()))
    dense["lc"] = dense["cell_id"].map(lc) if lc is not None else None
    dense["lc"] = dense["lc"].fillna("unknown") if lc is not None else "unknown"

    bc = cfg["calibration"]["bootstrap_block_cells"]
    bw = cfg["calibration"]["bootstrap_block_weeks"]
    dense["sblock"] = (dense["col"] // bc).astype(str) + "_" + (dense["row"] // bc).astype(str)
    dense["tblock"] = dense["year"].astype(str) + "_" + (dense["week"] // bw).astype(str)

    overlap = dense[dense["queried_r"] & dense["queried_s"]].copy()
    dropped_unknown = 0
    if mode == "exposure":
        known = overlap["e_r"].notna() & overlap["e_s"].notna()
        dropped_unknown = int((~known).sum())
        overlap = overlap[known]
        unobs = (overlap["e_r"] <= 0) | (overlap["e_s"] <= 0)
        overlap = overlap[~unobs]
    overlap["n"] = overlap["y_r"] + overlap["y_s"]
    info = {
        "reference": ref,
        "source": src,
        "mode": mode,
        "calibration_window": csel.window,
        "grid_km": sel.grid_km,
        "overlap_start": overlap["window_start"].min() if len(overlap) else None,
        "overlap_end": overlap["window_start"].max() if len(overlap) else None,
        "overlap_years": sorted(overlap["year"].unique().tolist()) if len(overlap) else [],
        "n_units": len(overlap),
        "n_paired_active_units": int((overlap["n"] > 0).sum()) if len(overlap) else 0,
        "total_ref": int(overlap["y_r"].sum()) if len(overlap) else 0,
        "total_src": int(overlap["y_s"].sum()) if len(overlap) else 0,
        "n_spatial_blocks": int(overlap["sblock"].nunique()) if len(overlap) else 0,
        "units_dropped_unknown_coverage": dropped_unknown,
        "landcover": sorted(overlap["lc"].unique().tolist()) if len(overlap) else [],
    }
    return PairTable(ref, src, mode, overlap.reset_index(drop=True), info, dense)


# ======================================================================== models


class Method:
    name = "method"
    label = "method"
    kind = "baseline"

    def __init__(self, mode: str, cfg: dict):
        self.mode = mode
        self.cfg = cfg

    def fit(self, train: pd.DataFrame) -> Method:
        return self

    def predict_mean(self, df: pd.DataFrame) -> np.ndarray:
        raise NotImplementedError

    def _er_es(self, df: pd.DataFrame) -> np.ndarray:
        if self.mode == "exposure":
            return (df["e_r"] / df["e_s"]).to_numpy(float)
        return np.ones(len(df))


class RawSubstitution(Method):
    name = "raw_substitution"
    label = "Raw substitution (source detections treated as reference detections)"

    def predict_mean(self, df):
        return df["y_s"].to_numpy(float)


class GlobalRatio(Method):
    name = "global_ratio"
    label = "Single global ratio (train totals)"

    def fit(self, train):
        adj = (train["y_s"] * self._er_es(train)).sum()
        self.ratio = float(train["y_r"].sum() / adj) if adj > 0 else np.nan
        return self

    def predict_mean(self, df):
        return df["y_s"].to_numpy(float) * self._er_es(df) * self.ratio


def _lc_design(df: pd.DataFrame, classes: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """Dummy columns for land-cover classes (first class is the baseline).
    Returns (matrix, unseen_mask)."""
    lc = df["lc"].to_numpy()
    if len(classes) <= 1:
        return np.empty((len(df), 0)), np.zeros(len(df), dtype=bool)
    cols = [(lc == c).astype(float) for c in classes[1:]]
    unseen = ~np.isin(lc, classes)
    return np.column_stack(cols), unseen


def _year_blocks(years: np.ndarray, k: int = 4) -> list[np.ndarray]:
    uy = np.unique(years)
    if len(uy) < 2:
        return []
    groups = np.array_split(uy, min(k, len(uy)))
    return [np.isin(years, g) for g in groups]


class SeasonalRatio(Method):
    name = "seasonal_ratio"
    label = "Paired conditional rate-ratio model (seasonal + land cover)"
    kind = "candidate"

    def __init__(self, mode, cfg, order: int | None = None, use_lc: bool = True, min_lc_units: int = 30):
        super().__init__(mode, cfg)
        self.order = order
        self.use_lc = use_lc
        self.min_lc_units = min_lc_units
        self.selection_scores: dict[int, float] = {}

    # -- design
    def _X(self, df):
        h = harmonics(df["doy_mid"].to_numpy(float), self.order_)
        lcX, unseen = _lc_design(df, self.lc_classes_)
        X = np.column_stack([np.ones(len(df)), h, lcX])
        return X, unseen

    def _offset(self, df):
        if self.mode == "exposure":
            return np.log(df["e_s"].to_numpy(float) / df["e_r"].to_numpy(float))
        return np.zeros(len(df))

    def _fit_order(self, act, order):
        self.order_ = order
        X, _ = self._X(act)
        return fit_glm(X, act["y_s"].to_numpy(float), "binomial", trials=act["n"].to_numpy(float), offset=self._offset(act))

    def fit(self, train):
        act = train[train["n"] > 0]
        if len(act) == 0:
            raise ValueError("no paired detections to calibrate on")
        counts = act["lc"].value_counts()
        classes = [c for c in counts.index if c != "unknown" and counts[c] >= self.min_lc_units] if self.use_lc else []
        self.lc_classes_ = sorted(classes, key=lambda c: -counts[c])  # largest class = baseline
        orders = self.cfg["calibration"]["harmonic_orders"] if self.order is None else [self.order]
        if len(orders) > 1:
            # train-only model selection: leave-year-block-out binomial deviance
            folds = _year_blocks(act["year"].to_numpy())
            for o in orders:
                dev = 0.0
                for test_mask in folds:
                    tr, te = act[~test_mask], act[test_mask]
                    if len(tr) == 0 or len(te) == 0:
                        continue
                    r = self._fit_order(tr, o)
                    X, _ = self._X(te)
                    p = 1 / (1 + np.exp(-(X @ r.beta + self._offset(te))))
                    y, n = te["y_s"].to_numpy(float), te["n"].to_numpy(float)
                    p = np.clip(p, 1e-9, 1 - 1e-9)
                    dev += -2 * np.sum(y * np.log(p) + (n - y) * np.log(1 - p))
                self.selection_scores[o] = dev
            best = min(self.selection_scores.values())
            # held-out deviance already penalises over-fitting; ties (within 2
            # deviance units, i.e. no real out-of-sample gain) go to the simpler model
            chosen = min(o for o, d in self.selection_scores.items() if d <= best + 2.0)
        else:
            chosen = orders[0]
        self.result_: GLMResult = self._fit_order(act, chosen)
        self.beta_ = self.result_.beta
        self.phi_ = self.result_.dispersion
        self.phi_window_ = self._window_dispersion(act)
        self.train_doy_range_ = (float(train["doy_mid"].min()), float(train["doy_mid"].max()))
        self.train_years_ = sorted(train["year"].unique().tolist())
        return self

    def _window_dispersion(self, act):
        X, _ = self._X(act)
        p = 1 / (1 + np.exp(-(X @ self.beta_ + self._offset(act))))
        g = pd.DataFrame(
            {"w": act["window_start"].to_numpy(), "y": act["y_s"].to_numpy(float), "n": act["n"].to_numpy(float), "np": act["n"].to_numpy(float) * p}
        )
        a = g.groupby("w").sum()
        pbar = (a["np"] / a["n"]).clip(1e-9, 1 - 1e-9)
        chi2 = ((a["y"] - a["np"]) ** 2 / (a["n"] * pbar * (1 - pbar))).sum()
        dof = max(1, len(a) - len(self.beta_))
        return float(max(1.0, chi2 / dof))

    # -- outputs
    def log_rho(self, df, beta=None):
        X, unseen = self._X(df)
        return X @ (self.beta_ if beta is None else beta), unseen

    def predict_mean(self, df, beta=None):
        lr, _ = self.log_rho(df, beta)
        return df["y_s"].to_numpy(float) * np.exp(-lr) * self._er_es(df)

    def ratio_curve(self, betas: list[np.ndarray] | None = None, interval: float = 0.9) -> list[dict]:
        doys = np.arange(1, 366, 7, dtype=float)
        out = []
        classes = self.lc_classes_ or ["all"]
        for c in classes:
            df = pd.DataFrame({"doy_mid": doys, "lc": c})
            lr, _ = self.log_rho(df)
            rec = {"lc_class": c, "doy": doys.tolist(), "ratio": np.exp(lr).tolist()}
            if betas:
                sims = np.array([np.exp(self.log_rho(df, b)[0]) for b in betas])
                a = (1 - interval) / 2
                rec["lo"] = np.quantile(sims, a, axis=0).tolist()
                rec["hi"] = np.quantile(sims, 1 - a, axis=0).tolist()
            out.append(rec)
        return out

    def coefficient_table(self) -> list[dict]:
        names = ["intercept (log ratio)"]
        for k in range(1, self.order_ + 1):
            names += [f"sin {k}/yr", f"cos {k}/yr"]
        names += [f"land cover: {c} vs {self.lc_classes_[0]}" for c in self.lc_classes_[1:]]
        se = self.result_.se(quasi=True)
        return [{"term": n, "estimate": float(b), "se_quasi": float(s)} for n, b, s in zip(names, self.beta_, se)]


class NBRegression(Method):
    name = "nb_regression"
    label = "Negative-binomial regression calibration"
    kind = "candidate"

    def __init__(self, mode, cfg, order: int = 2, use_lc: bool = True):
        super().__init__(mode, cfg)
        self.order = order
        self.use_lc = use_lc

    def _X(self, df):
        ys = df["y_s"].to_numpy(float) * self._er_es(df)
        h = harmonics(df["doy_mid"].to_numpy(float), self.order)
        lcX, unseen = _lc_design(df, self.lc_classes_)
        return np.column_stack([np.ones(len(df)), np.log1p(ys), h, lcX]), unseen

    def _offset(self, df):
        if self.mode == "exposure":
            return np.log(df["e_r"].to_numpy(float))
        return np.zeros(len(df))

    def fit(self, train):
        counts = train["lc"].value_counts()
        classes = [c for c in counts.index if c != "unknown" and counts[c] >= 30] if self.use_lc else []
        self.lc_classes_ = sorted(classes, key=lambda c: -counts[c])
        X, _ = self._X(train)
        self.result_ = fit_glm(X, train["y_r"].to_numpy(float), "negbin", offset=self._offset(train))
        return self

    def predict_mean(self, df):
        X, _ = self._X(df)
        return np.exp(np.clip(X @ self.result_.beta + self._offset(df), -30, 30))


METHODS = {m.name: m for m in (RawSubstitution, GlobalRatio, SeasonalRatio, NBRegression)}


# ======================================================================== uncertainty


def block_bootstrap_betas(model: SeasonalRatio, train: pd.DataFrame, B: int, seed: int) -> list[np.ndarray]:
    """Refit the selected rate-ratio model on block-resampled training data.
    Blocks = (spatial block of cells) x (4-week time block), so spatially and
    temporally correlated detections are resampled together. The harmonic
    order is held at the value chosen on the full training data (documented
    simplification: selection uncertainty is not propagated)."""
    rng = np.random.default_rng(seed)
    act = train[train["n"] > 0]
    block = (act["sblock"] + "|" + act["tblock"]).to_numpy()
    ub, inv = np.unique(block, return_inverse=True)
    idx_by_block = [np.nonzero(inv == i)[0] for i in range(len(ub))]
    betas = []
    for _ in range(B):
        pick = rng.integers(0, len(ub), len(ub))
        idx = np.concatenate([idx_by_block[i] for i in pick])
        sub = act.iloc[idx]
        X, _ = model._X(sub)
        try:
            r = fit_glm(X, sub["y_s"].to_numpy(float), "binomial", trials=sub["n"].to_numpy(float), offset=model._offset(sub), max_iter=50)
            betas.append(r.beta)
        except (np.linalg.LinAlgError, ValueError):
            continue
    return betas


def aggregate_intervals(
    model: SeasonalRatio,
    betas: list[np.ndarray],
    units: pd.DataFrame,
    by: str = "window_start",
    interval: float = 0.9,
    seed: int = 0,
    n_sims: int = 400,
) -> pd.DataFrame:
    """Converted reference-equivalent totals per group with two intervals:

    * est_lo/est_hi  - uncertainty of the reference-equivalent *expected*
                       count (calibration parameters + source sampling noise)
    * pred_lo/pred_hi - predictive interval for what the reference platform
                        would actually have recorded (adds reference noise);
                        this is the interval checked for coverage on holdouts.
    Overdispersion uses the window-level quasi-binomial dispersion estimated
    on training data."""
    rng = np.random.default_rng(seed)
    phi = model.phi_window_
    grp = units[by].to_numpy()
    ug, gi = np.unique(grp, return_inverse=True)
    ys = units["y_s"].to_numpy(float)
    Ys = np.bincount(gi, weights=ys, minlength=len(ug))
    point = np.bincount(gi, weights=model.predict_mean(units), minlength=len(ug))
    bs = betas or [model.beta_]
    sims_est = np.empty((n_sims, len(ug)))
    sims_pred = np.empty((n_sims, len(ug)))
    for k in range(n_sims):
        b = bs[k % len(bs)]
        conv = np.bincount(gi, weights=model.predict_mean(units, b), minlength=len(ug))
        # multiplicative source-count noise: Gamma with CV = sqrt(phi / (Ys + 0.5))
        shape = (Ys + 0.5) / phi
        noise = rng.gamma(shape, 1.0 / shape)
        est = conv * noise
        sims_est[k] = est
        if phi > 1.0 + 1e-9:
            lam = rng.gamma(np.maximum(est, 1e-9) / (phi - 1.0), phi - 1.0)
        else:
            lam = est
        sims_pred[k] = rng.poisson(lam)
    a = (1 - interval) / 2
    return pd.DataFrame(
        {
            by: ug,
            "src_total": Ys,
            "estimate": point,
            "est_lo": np.quantile(sims_est, a, axis=0),
            "est_hi": np.quantile(sims_est, 1 - a, axis=0),
            "pred_lo": np.quantile(sims_pred, a, axis=0),
            "pred_hi": np.quantile(sims_pred, 1 - a, axis=0),
        }
    )


# ======================================================================== diagnostics


def diagnostics(model: SeasonalRatio, train: pd.DataFrame, cfg: dict) -> dict:
    act = train[train["n"] > 0].copy()
    X, _ = model._X(act)
    off = model._offset(act)
    y, n = act["y_s"].to_numpy(float), act["n"].to_numpy(float)
    out: dict = {
        "dispersion_cell_week": model.phi_,
        "dispersion_window": model.phi_window_,
        "harmonic_order": model.order_,
        "order_selection_deviance": {str(k): v for k, v in model.selection_scores.items()},
    }

    # proportionality: does the ratio depend on activity level (n)?
    Xp = np.column_stack([X, np.log(n) - np.log(n).mean()])
    rp = fit_glm(Xp, y, "binomial", trials=n, offset=off)
    b, se = float(rp.beta[-1]), float(rp.se()[-1])
    out["proportionality"] = {
        "coef_log_activity": b,
        "se": se,
        "flag": bool(abs(b) > 2 * se and abs(b) > 0.05),
        "meaning": "Change in log(source/reference ratio) per unit log(paired detections). Non-zero means the ratio depends on fire size/activity, so a constant-ratio conversion is biased at extremes.",
    }
    # drift: linear trend of the log-ratio across overlap years
    yrs = act["year"].to_numpy(float) + act["doy_mid"].to_numpy(float) / 365.25
    Xd = np.column_stack([X, yrs - yrs.mean()])
    rd = fit_glm(Xd, y, "binomial", trials=n, offset=off)
    b, se = float(rd.beta[-1]), float(rd.se()[-1])
    out["drift"] = {
        "log_ratio_per_year": b,
        "se": se,
        "flag": bool(abs(b) > 2 * se and abs(b) > cfg["calibration"]["trend_alarm_per_year"]),
        "meaning": "Trend of the log ratio across the overlap. A significant trend means transferring the calibration outside the overlap period is unsafe.",
    }
    # per-year ratio for display
    per_year = []
    for yr, g in act.groupby("year"):
        if g["n"].sum() < 20:
            continue
        Xg, _ = model._X(g)
        rg = fit_glm(
            np.ones((len(g), 1)), g["y_s"].to_numpy(float), "binomial", trials=g["n"].to_numpy(float), offset=model._offset(g) + Xg[:, 1:] @ model.beta_[1:]
        )
        per_year.append({"year": int(yr), "log_ratio_intercept": float(rg.beta[0]), "se": float(rg.se()[0]), "paired_detections": int(g["n"].sum())})
    out["per_year_intercept"] = per_year
    # zero / overdispersion check of the reference counts (exposure mode only)
    if model.mode == "exposure":
        nb = NBRegression(model.mode, cfg).fit(train)
        mu = nb.predict_mean(train)
        a = nb.result_.alpha
        p0 = (1 + a * mu) ** (-1 / a)
        out["zero_check"] = {
            "observed_zero_fraction_ref": float((train["y_r"] == 0).mean()),
            "nb_expected_zero_fraction_ref": float(p0.mean()),
            "nb_alpha": float(a),
            "meaning": "If observed zeros greatly exceed the NB-expected share, there is zero inflation beyond overdispersion.",
        }
    else:
        out["zero_check"] = {"note": "Not assessable: without valid-observation coverage, zero cell-weeks cannot be distinguished from unobserved ones."}
    return out
