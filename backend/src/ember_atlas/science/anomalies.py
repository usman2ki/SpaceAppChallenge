"""Seasonal baselines, unusual-activity signals and critical historical periods.

These describe the PAST record. They are monitoring signals, not fire-risk
forecasts: forecasting would require weather, fuel and ignition data and a
separate, prospective validation.

Rules
* Weekly windows only (daily counts are too sparse and too coverage-sensitive).
* Baseline for (year Y, week k) = values of weeks k-h..k+h in OTHER years
  (leave-one-year-out), only from windows where the series was observed.
  Fewer than ``min_baseline_years`` distinct years -> "insufficient history";
  unqueried weeks are never treated as zeros.
* Test: log-scale predictive t-test. Year-to-year variation of burning is
  multiplicative (weather, fuel), so values are compared on log(1 + x):
      t = (log1p(x) - median) / (s * sqrt(1 + 1/n_years)),  df = n_years - 1
  with the median and a robust scale s = max(1.4826 * MAD, IQR / 1.349) of
  baseline log1p values (robust, so another year's anomaly does not widen
  the baseline; the max guards against MAD collapsing in small samples). Degrees of freedom count
  years, not pooled weeks, because neighbouring weeks of one year are
  correlated. (A negative-binomial moment fit was tried first: it puts too
  much probability near zero for large, multiplicatively varying counts and
  could not flag even a 10-fold drop.) For converted (non-reference) windows
  the conservative lower interval bound is tested for excess.
* Deficit ("unusually low") tests require valid-observation coverage for the
  window; without it, a low count may just be clouds or missing passes.
* Multiple comparisons: Benjamini-Hochberg FDR over every test in the view.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from ..analysis.windows import week_index


def _nb_params(vals: np.ndarray) -> tuple[float, float]:
    m = float(np.mean(vals))
    v = float(np.var(vals, ddof=1)) if len(vals) > 1 else m
    if v <= m * 1.0001 or m <= 0:
        return m, np.inf
    return m, m * m / (v - m)


def _sf(x: float, m: float, k: float) -> float:
    """P(X >= x) for continuous x (rounded up to the next integer count)."""
    xi = int(np.ceil(x - 1e-9))
    if xi <= 0:
        return 1.0
    if np.isinf(k):
        return float(stats.poisson.sf(xi - 1, m))
    p = k / (k + m)
    return float(stats.nbinom.sf(xi - 1, k, p))


def _cdf(x: float, m: float, k: float) -> float:
    xi = int(np.floor(x + 1e-9))
    if np.isinf(k):
        return float(stats.poisson.cdf(xi, m))
    return float(stats.nbinom.cdf(xi, k, k / (k + m)))


def bh_fdr(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, float)
    n = len(p)
    if n == 0:
        return p
    order = np.argsort(p)
    ranked = p[order] * n / (np.arange(n) + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.minimum(q, 1.0)
    return out


def analyse(series: pd.DataFrame, cfg: dict, value_col: str = "value", lo_col: str | None = "lo", view: tuple[str, str] | None = None) -> dict:
    """series columns: window_start, value, [lo], observed (bool),
    coverage_known (bool), conservative (bool: test lo instead of value)."""
    a = cfg["anomaly"]
    df = series.copy()
    df["date"] = pd.to_datetime(df["window_start"])
    df["year"] = df["date"].dt.year
    df["week"] = week_index(df["date"])
    obs = df[df["observed"] & df[value_col].notna()]
    h = a["neighbor_weeks"]

    # ------------------------------------------------ seasonal baseline curve (all years)
    curve = []
    for k in range(52):
        wk = [(k + d) % 52 for d in range(-h, h + 1)]
        vals = obs[obs["week"].isin(wk)]
        yrs = vals["year"].nunique()
        rec = {"week": k, "n_years": int(yrs)}
        if yrs >= a["min_baseline_years"]:
            v = vals[value_col].to_numpy(float)
            rec.update({q: float(np.quantile(v, p)) for q, p in (("q10", 0.1), ("q25", 0.25), ("median", 0.5), ("q75", 0.75), ("q90", 0.9))})
        curve.append(rec)

    # ------------------------------------------------ tests at two scales
    view_mask = np.ones(len(df), dtype=bool)
    if view:
        view_mask = ((df["window_start"] >= view[0]) & (df["window_start"] <= view[1])).to_numpy()
    wk_rows = _test_units(df[view_mask], obs, "week", h, 52, value_col, lo_col, a)

    # 4-week blocks (weeks 0-3, 4-7, ... 48-51): persistent episodes accumulate
    # evidence that single weeks cannot show against large natural variability
    df["block"] = df["week"] // 4
    blk = _block_series(df, value_col, lo_col)
    blk_obs = blk[blk["observed"] & blk[value_col].notna()]
    bview = np.ones(len(blk), dtype=bool)
    if view:
        bview = ((blk["window_start"] >= view[0]) & (blk["window_start"] <= view[1])).to_numpy()
    blk_rows = _test_units(blk[bview], blk_obs, "block", 1, 13, value_col, lo_col, a)

    res = pd.DataFrame(wk_rows)
    bres = pd.DataFrame(blk_rows)
    pvals, where = [], []
    for name, frame in (("week", res), ("block", bres)):
        for col in ("p_excess", "p_deficit"):
            if col in frame:
                m = frame[col].notna()
                pvals.extend(frame.loc[m, col].tolist())
                where.extend([(name, i, col) for i in frame.index[m]])
    q = bh_fdr(np.array(pvals)) if pvals else np.array([])
    for (name, i, col), qv in zip(where, q):
        (res if name == "week" else bres).loc[i, "q_" + col[2:]] = qv
    thr = a["fdr_q"]
    for frame in (res, bres):
        if len(frame):
            frame["signal"] = (frame["signal"] if "signal" in frame else pd.Series(np.nan, index=frame.index)).astype(object)
            tested = frame["signal"].isna()
            frame.loc[tested, "signal"] = "typical"
            if "q_excess" in frame:
                frame.loc[tested & (frame["q_excess"] <= thr), "signal"] = "unusually_high"
            if "q_deficit" in frame:
                frame.loc[tested & (frame["q_deficit"] <= thr) & (frame["signal"] == "typical"), "signal"] = "unusually_low"
            if "p_deficit" not in frame or frame["p_deficit"].isna().all():
                frame.loc[tested & (frame["signal"] == "typical"), "low_test"] = "not testable without coverage"
    if len(res):
        res["scale"] = np.where(res["signal"].isin(["unusually_high", "unusually_low"]), "week", None)
        if len(bres):
            hits = bres[bres["signal"].isin(["unusually_high", "unusually_low"])]
            for _, b in hits.iterrows():
                m = (res["year"] == b["year"]) & (res["week"] // 4 == b["block"]) & (res["signal"] == "typical")
                res.loc[m, "signal"] = b["signal"]
                res.loc[m, "scale"] = "4-week"
                res.loc[m, "block_q"] = b.get("q_excess") if b["signal"] == "unusually_high" else b.get("q_deficit")

    n_tests = len(pvals)
    episodes = _episodes(res)
    peak = _peak_season(curve, a["peak_season_fraction"])
    return {
        "windows": res.replace({np.nan: None}).to_dict("records"),
        "seasonal_curve": curve,
        "n_tests": n_tests,
        "fdr_q": thr,
        "episodes": episodes,
        "peak_season": peak,
        "notes": [
            "Signals describe historical anomalies relative to other years; they are not forecasts of fire risk.",
            (
                f"{n_tests} hypothesis tests in this view (single weeks and 4-week blocks, excess and, where coverage is known, deficit); "
                f"Benjamini-Hochberg FDR controlled at q = {thr} across all of them. Viewing a longer period means more tests and needs stronger evidence; "
                "choose the period of interest before looking, not after."
            ),
        ],
    }


def _test_units(units: pd.DataFrame, pool: pd.DataFrame, key: str, h: int, period: int, value_col: str, lo_col: str | None, a: dict) -> list[dict]:
    rows = []
    for _, r in units.iterrows():
        rec = {"window_start": r["window_start"], "year": int(r["year"]), key: int(r[key]), "value": r[value_col]}
        if key == "week":
            rec["week"] = int(r["week"])
        if not r["observed"] or pd.isna(r[value_col]):
            rec["signal"] = "not_observed"
            rows.append(rec)
            continue
        ks = [(r[key] + d) % period for d in range(-h, h + 1)]
        base = pool[pool[key].isin(ks) & (pool["year"] != r["year"])]
        ny = base["year"].nunique()
        rec["n_baseline_years"] = int(ny)
        if ny < a["min_baseline_years"]:
            rec["signal"] = "insufficient_history"
            rows.append(rec)
            continue
        v = base[value_col].to_numpy(float)
        med = float(np.median(v))
        m = float(np.mean(v))
        lv = np.log1p(v)
        # robust location/scale: other years' own anomalies must not widen the baseline
        mu = float(np.median(lv))
        # two robust scale estimates; the larger one guards against the MAD
        # collapsing in small baselines (observed in tests with ~11 values)
        q75, q25 = np.quantile(lv, [0.75, 0.25])
        sd = max(float(1.4826 * np.median(np.abs(lv - mu))), float((q75 - q25) / 1.349), 0.05)
        rec.update(
            {
                "baseline_median": med,
                "baseline_q25": float(np.quantile(v, 0.25)),
                "baseline_q75": float(np.quantile(v, 0.75)),
                "baseline_mean": m,
                "ratio_to_median": float((r[value_col] + 1) / (med + 1)),
            }
        )
        if m < a["min_baseline_mean"] or not sd > 0:
            rec["signal"] = "too_sparse"
            rows.append(rec)
            continue
        scale = sd * np.sqrt(1 + 1 / ny)
        stat = r[lo_col] if (lo_col and r.get("conservative", False) and pd.notna(r.get(lo_col))) else r[value_col]
        rec["test_statistic"] = float(stat)
        rec["t_excess"] = float((np.log1p(max(float(stat), 0.0)) - mu) / scale)
        rec["p_excess"] = float(stats.t.sf(rec["t_excess"], ny - 1))
        if bool(r.get("coverage_known", False)):
            tl = float((np.log1p(max(float(r[value_col]), 0.0)) - mu) / scale)
            rec["p_deficit"] = float(stats.t.cdf(tl, ny - 1))
        rows.append(rec)
    return rows


def _block_series(df: pd.DataFrame, value_col: str, lo_col: str | None) -> pd.DataFrame:
    g = df.groupby(["year", "block"], sort=True)
    out = pd.DataFrame(
        {
            "window_start": g["window_start"].min(),
            value_col: g[value_col].sum(min_count=1),
            "observed": g["observed"].all() & (g["observed"].count() >= 4),
            "coverage_known": g["coverage_known"].all() if "coverage_known" in df else False,
            "conservative": g["conservative"].any() if "conservative" in df else False,
        }
    ).reset_index()
    if lo_col and lo_col in df:
        lo_or_val = df[lo_col].where(df["conservative"].astype(bool) & df[lo_col].notna(), df[value_col]) if "conservative" in df else df[value_col]
        out[lo_col] = df.assign(_l=lo_or_val).groupby(["year", "block"])["_l"].sum(min_count=1).to_numpy()
    return out


def _episodes(res: pd.DataFrame, max_gap_weeks: int = 1) -> list[dict]:
    if res.empty or "signal" not in res:
        return []
    eps = []
    cur = None
    for _, r in res.sort_values("window_start").iterrows():
        hit = r["signal"] in ("unusually_high", "unusually_low")
        d = pd.Timestamp(r["window_start"])
        if hit:
            kind = r["signal"]
            if cur and cur["kind"] == kind and (d - pd.Timestamp(cur["last"])).days <= 7 * (max_gap_weeks + 1) + 2:
                cur["last"] = r["window_start"]
                cur["weeks"].append(r)
            else:
                if cur:
                    eps.append(cur)
                cur = {"kind": kind, "first": r["window_start"], "last": r["window_start"], "weeks": [r]}
    if cur:
        eps.append(cur)
    out = []
    for e in eps:
        w = pd.DataFrame(e["weeks"])
        excess = float((w["value"] - w["baseline_median"]).sum())
        out.append(
            {
                "kind": e["kind"],
                "start": e["first"],
                "last_window": e["last"],
                "n_weeks": len(w),
                "peak_ratio_to_median": float(w["ratio_to_median"].max() if e["kind"] == "unusually_high" else w["ratio_to_median"].min()),
                "excess_vs_median": excess,
                "min_q": float(w[[c for c in ("q_excess", "q_deficit", "block_q") if c in w.columns]].min(axis=1).min()),
                "explanation": (
                    f"{len(w)} week(s) from {e['first']} with activity "
                    + ("above" if e["kind"] == "unusually_high" else "below")
                    + " the same-season baseline of other years (peak ratio to median "
                    + f"{(w['ratio_to_median'].max() if e['kind'] == 'unusually_high' else w['ratio_to_median'].min()):.1f}x), FDR-controlled."
                ),
            }
        )
    return out


def _peak_season(curve: list[dict], frac: float) -> dict | None:
    meds = np.array([c.get("median", np.nan) for c in curve], float)
    if np.all(np.isnan(meds)) or np.nanmax(meds) <= 0:
        return None
    thr = frac * np.nanmax(meds)
    in_peak = meds >= thr
    weeks = [int(i) for i in np.nonzero(in_peak)[0]]
    return {
        "weeks": weeks,
        "threshold": float(thr),
        "peak_week": int(np.nanargmax(meds)),
        "explanation": f"Climatological peak season: weeks where the multi-year median is at least {int(frac * 100)}% of the highest weekly median.",
    }


def annual_totals(series: pd.DataFrame, value_col: str = "value", lo_col: str = "lo", hi_col: str = "hi", z: float = 1.645) -> list[dict]:
    """Year totals with an approximate interval (independent normal errors per
    window; documented approximation) and completeness."""
    df = series.copy()
    df["year"] = pd.to_datetime(df["window_start"]).dt.year
    out = []
    for yr, g in df.groupby("year"):
        obs = g[g["observed"] & g[value_col].notna()]
        tot = float(obs[value_col].sum())
        rec = {"year": int(yr), "total": tot, "windows_observed": len(obs), "windows_total": len(g), "complete": bool(len(obs) == len(g))}
        if lo_col in g and hi_col in g and obs[lo_col].notna().all():
            sd = ((obs[hi_col] - obs[lo_col]) / (2 * z)).to_numpy(float)
            s = float(np.sqrt(np.sum(sd**2)))
            rec.update({"lo": max(0.0, tot - z * s), "hi": tot + z * s})
        out.append(rec)
    return out
