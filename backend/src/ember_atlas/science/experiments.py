"""Reproducible offline experiments that write to ``artifacts/``.

* synthetic_recovery : can the method recover a KNOWN injected sensor effect,
                       and does conversion after the reference record ends
                       track the known truth? (synthetic evidence only)
* sensitivity        : how calibration results move with grid size, time
                       window, quality filter and the coverage assumption
* write_evaluation_artifacts : JSON report + model card for a validation run

None of these produce evidence about real MODIS/VIIRS behaviour unless run on
real imported data; every artifact records its data origin.
"""

from __future__ import annotations

import json
import math
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .. import __version__
from ..analysis.aggregate import Selection
from ..config import method_config, method_fingerprint
from ..geo.polygons import aoi_from_bbox, parse_aoi
from ..store.sqlite_store import SqliteStore
from ..synthetic.generator import SyntheticSpec
from ..synthetic.seed import describe_world, seed_demo
from . import anomalies as anom
from .calibration import SeasonalRatio, block_bootstrap_betas, build_pair_table
from .evaluation import evaluate
from .harmonize import harmonize

# quality-filter pass probabilities implied by the generator's confidence mix
# (MODIS: 15 % drawn uniform(0,30) then rounded -> 1/60 of those round to 30 = nominal)
P_PASS = {"MODIS": 0.85 + 0.15 / 60.0, "VIIRS": 0.92}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _NoCoverage:
    """Store proxy that hides coverage sets: reproduces a hotspot-only import."""

    def __init__(self, store):
        self._s = store

    def coverage_sets(self, origin):
        return [c for c in self._s.coverage_sets(origin) if c["quantity"] != "valid_looks"]

    def __getattr__(self, name):
        return getattr(self._s, name)


def _inner_aoi(world):
    """Union of latent cells fully inside the generator bbox (a lon/lat
    rectangle), so truth can be summed exactly over whole cells."""
    w, s, e, n = world.spec.bbox
    c = world.cells
    inner = c[(c.west >= w) & (c.east <= e) & (c.south >= s) & (c.north <= n)]
    aoi = aoi_from_bbox(inner.west.min() + 1e-7, inner.south.min() + 1e-7, inner.east.max() - 1e-7, inner.north.max() - 1e-7)
    return aoi, inner.index.to_numpy()


def _true_log_ratio(spec: SyntheticSpec, doy, lc: str, src: str = "Suomi-NPP") -> np.ndarray:
    base = spec.base_log_ratio[src] + math.log(P_PASS["VIIRS"] / P_PASS["MODIS"])
    return base + spec.seasonal_ratio_amp[src] * np.cos(2 * np.pi * (np.asarray(doy, float) - spec.ratio_peak_doy) / 365.25) + spec.lc_ratio_shift[lc]


def _weekly_truth(world, cell_idx, mode: str) -> pd.Series:
    """Known Aqua-equivalent quality-filtered weekly totals over the AOI cells."""
    lam = world.lam[:, cell_idx]
    if mode == "exposure":
        daily = (lam * 2.0).sum(axis=1) * P_PASS["MODIS"]  # nominal 2 looks/day
    else:
        daily = (lam * world.looks["Aqua"][:, cell_idx]).sum(axis=1) * P_PASS["MODIS"]  # counterfactual Aqua coverage
    from ..analysis.windows import window_start

    ws = window_start(pd.Series(world.days.strftime("%Y-%m-%d")), "week").to_numpy()
    return pd.Series(daily).groupby(ws).sum()


def synthetic_recovery(out_dir: Path, bootstrap_reps: int = 100, quick: bool = False, spec: SyntheticSpec | None = None) -> dict:
    t0 = time.time()
    if spec is None:
        spec = SyntheticSpec()
        if quick:
            spec = SyntheticSpec(
                bbox=(-48.0, -11.0, -46.5, -9.5),
                start="2013-01-01",
                end="2019-12-31",
                windows={
                    "Terra": ("2013-01-01", "2019-12-31"),
                    "Aqua": ("2013-01-01", "2018-06-30"),
                    "Suomi-NPP": ("2014-01-01", "2019-12-31"),
                    "NOAA-20": ("2018-04-01", "2019-12-31"),
                },
                excess_episode={"year": 2017, "doy": (213, 250), "factor": 8.0, "quadrant": "S"},
                deficit_episode={"year": 2015, "doy": (200, 270), "factor": 0.15},
                mean_level=0.05,
            )
    cfg = method_config()
    with tempfile.TemporaryDirectory() as td:
        store = SqliteStore(Path(td) / "recovery.sqlite")
        store.migrate()
        seeded = seed_demo(store, spec=spec)
        world = seeded["world"]
        aoi, cell_idx = _inner_aoi(world)
        results = {}
        for mode, st in (("exposure", store), ("hotspot_only", _NoCoverage(store))):
            sel = Selection("synthetic", aoi, spec.start, spec.end, ["Aqua", "Suomi-NPP"], None, 18, "week", cfg)
            pair = build_pair_table(st, sel, "Aqua", "Suomi-NPP")
            model = SeasonalRatio(pair.mode, cfg).fit(pair.units)
            betas = block_bootstrap_betas(model, pair.units, bootstrap_reps, cfg["calibration"]["seed"])
            params = []
            doys = np.arange(1, 366, dtype=float)
            for lc in ["savanna", "grassland", "cropland", "forest"]:
                df = pd.DataFrame({"doy_mid": doys, "lc": lc})
                est = float(model.log_rho(df)[0].mean())
                sims = np.array([model.log_rho(df, b)[0].mean() for b in betas])
                truth = float(_true_log_ratio(spec, doys, lc).mean())
                if mode == "hotspot_only":
                    truth = None  # absorbs the coverage ratio by design; reported, not scored
                lo, hi = (float(np.quantile(sims, 0.05)), float(np.quantile(sims, 0.95)))
                params.append(
                    {
                        "name": f"annual-mean log ratio, {lc}",
                        "truth": truth,
                        "estimate": est,
                        "lo": lo,
                        "hi": hi,
                        "inside": None if truth is None else bool(lo <= truth <= hi),
                    }
                )
            if model.order_ >= 1:
                a1, b1 = model.beta_[1], model.beta_[2]
                amp = float(math.hypot(a1, b1))
                peak = float((math.degrees(math.atan2(a1, b1)) / 360.0 * 365.25) % 365.25)
                amps = [math.hypot(b[1], b[2]) for b in betas]
                peaks = [(math.degrees(math.atan2(b[1], b[2])) / 360.0 * 365.25) % 365.25 for b in betas]
                params.append(
                    {
                        "name": "seasonal amplitude of log ratio",
                        "truth": spec.seasonal_ratio_amp["Suomi-NPP"],
                        "estimate": amp,
                        "lo": float(np.quantile(amps, 0.05)),
                        "hi": float(np.quantile(amps, 0.95)),
                    }
                )
                params.append(
                    {
                        "name": "day of year of peak ratio",
                        "truth": spec.ratio_peak_doy,
                        "estimate": peak,
                        "lo": float(np.quantile(peaks, 0.05)),
                        "hi": float(np.quantile(peaks, 0.95)),
                    }
                )
                for p in params[-2:]:
                    p["inside"] = bool(p["lo"] <= p["truth"] <= p["hi"])
            # conversion after the reference record ends vs known truth
            h = harmonize(st, sel, "Aqua", "Suomi-NPP", evaluation=None, bootstrap_reps=bootstrap_reps)
            s = pd.DataFrame(h["series"]).set_index("window_start")
            truth_w = _weekly_truth(world, cell_idx, mode)
            aqua_end = spec.windows["Aqua"][1]
            post = s[(s.index > aqua_end) & s["src_converted"].notna()]
            post = post[post.index.isin(truth_w.index)]
            tw = truth_w.reindex(post.index)
            est = post["src_converted"].astype(float)
            transfer = {
                "period": [post.index.min(), post.index.max()],
                "weeks": int(len(post)),
                "relative_bias": float((est.sum() - tw.sum()) / tw.sum()),
                "mae": float((est - tw).abs().mean()),
                "interval_coverage_truth": float(((post["src_conv_lo"] <= tw) & (tw <= post["src_conv_hi"])).mean()),
                "naive_splice_relative_bias": float((post["src_raw"].sum() - tw.sum()) / tw.sum()),
                "truth_definition": (
                    "Aqua-equivalent quality-filtered detections at nominal looks (2 per day)"
                    if mode == "exposure"
                    else "expected Aqua quality-filtered detections under Aqua's counterfactual coverage"
                ),
            }
            results[mode] = {"order": model.order_, "params": params, "transfer": transfer, "pair": pair.info, "status": h["status"]["code"]}

        # anomaly detection of the injected episodes (single-year test families)
        anomalies_found = {}
        full_sel = Selection("synthetic", aoi, spec.start, spec.end, ["Aqua", "Suomi-NPP"], None, 18, "week", cfg)
        hh = harmonize(store, full_sel, "Aqua", "Suomi-NPP", evaluation=None, bootstrap_reps=min(50, bootstrap_reps))
        hs = pd.DataFrame(hh["series"])
        adf = pd.DataFrame(
            {
                "window_start": hs["window_start"],
                "value": hs["harmonized"],
                "lo": hs["harm_lo"],
                "hi": hs["harm_hi"],
                "observed": hs["basis"] != "none",
                "conservative": hs["basis"].str.startswith("converted"),
                "coverage_known": True,
            }
        )
        for label, ep in (("excess", spec.excess_episode), ("deficit", spec.deficit_episode)):
            yr = ep["year"]
            a = anom.analyse(adf, cfg, view=(f"{yr}-01-01", f"{yr}-12-31"))
            want = "unusually_high" if label == "excess" else "unusually_low"
            d0 = pd.Timestamp(f"{yr}-01-01") + pd.Timedelta(days=ep["doy"][0] - 1)
            d1 = pd.Timestamp(f"{yr}-01-01") + pd.Timedelta(days=ep["doy"][1] - 1)
            in_ep = [w for w in a["windows"] if d0 - pd.Timedelta(days=6) <= pd.Timestamp(w["window_start"]) <= d1]
            outside = [w for w in a["windows"] if w not in in_ep]
            anomalies_found[label] = {
                "injected": {"year": yr, "doy": ep["doy"], "factor": ep["factor"], "area": ep.get("quadrant", "all")},
                "weeks_in_episode": len(in_ep),
                "weeks_flagged_in_episode": sum(w["signal"] == want for w in in_ep),
                "weeks_flagged_outside_episode": sum(w["signal"] in ("unusually_high", "unusually_low") for w in outside),
                "n_tests": a["n_tests"],
            }

    exp = results["exposure"]
    summary = {
        "seed": spec.seed,
        "quick": quick,
        "aoi": "inner cells of the synthetic bbox",
        "parameters": exp["params"],
        "transfer": exp["transfer"],
        "hotspot_only": {"params": results["hotspot_only"]["params"], "transfer": results["hotspot_only"]["transfer"]},
        "anomaly_detection": anomalies_found,
        "note": "SYNTHETIC recovery experiment: tests the method against a known generative truth. It is not evidence about real sensors.",
    }
    out = {
        "experiment": "synthetic_recovery",
        "generated_at": _now(),
        "app_version": __version__,
        "method_version": cfg["method_version"],
        "method_fingerprint": method_fingerprint(cfg),
        "data_origin": "synthetic",
        "world": describe_world(world),
        "bootstrap_reps": bootstrap_reps,
        "seconds": round(time.time() - t0, 1),
        "summary": summary,
        "details": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "synthetic_recovery.json").write_text(json.dumps(_clean(out), indent=2, default=str))
    (out_dir / "synthetic_recovery.md").write_text(_recovery_md(out))
    return out


def sensitivity(out_dir: Path, quick: bool = False, store=None, aoi=None, data_origin: str = "synthetic", ref: str = "Aqua", src: str = "Suomi-NPP") -> dict:
    """Calibration sensitivity on an existing store (default: a fresh synthetic world)."""
    t0 = time.time()
    tmp = None
    if store is None:
        tmp = tempfile.TemporaryDirectory()
        store = SqliteStore(Path(tmp.name) / "sens.sqlite")
        store.migrate()
        spec = (
            SyntheticSpec()
            if not quick
            else SyntheticSpec(
                bbox=(-48.0, -11.0, -46.5, -9.5),
                start="2013-01-01",
                end="2019-12-31",
                windows={
                    "Terra": ("2013-01-01", "2019-12-31"),
                    "Aqua": ("2013-01-01", "2018-06-30"),
                    "Suomi-NPP": ("2014-01-01", "2019-12-31"),
                    "NOAA-20": ("2018-04-01", "2019-12-31"),
                },
                mean_level=0.05,
            )
        )
        world = seed_demo(store, spec=spec)["world"]
        aoi = aoi or _inner_aoi(world)[0]
    if aoi is None:
        raise ValueError("aoi required")
    if isinstance(aoi, (dict, str, list)):
        aoi = parse_aoi(aoi)
    base = method_config()
    av = store.availability(data_origin)
    start, end = str(av["date_start"].min()), str(av["date_end"].max())
    settings = [("default (18 km, week, nominal+, coverage exposure)", {}, False)]
    for g in (9, 36, 72):
        settings.append((f"grid {g} km", {"grid": g}, False))
    settings.append(("calibration window: day", {"calibration": {"window": "day"}}, False))
    for c in ("low", "high"):
        settings.append((f"min confidence {c}", {"quality": {"min_confidence": c}}, False))
    settings.append(("coverage ignored (hotspot-only assumption)", {}, True))
    rows = []
    for label, ov, hide_cov in settings:
        cfg = method_config({k: v for k, v in ov.items() if k != "grid"})
        grid = ov.get("grid", base["grid"]["default_km"])
        sel = Selection(data_origin, aoi, start, end, [ref, src], None, grid, "week", cfg)
        st = _NoCoverage(store) if hide_cov else store
        try:
            pair = build_pair_table(st, sel, ref, src)
            m = SeasonalRatio(pair.mode, cfg).fit(pair.units)
            df = pd.DataFrame({"doy_mid": np.arange(1, 366, dtype=float), "lc": m.lc_classes_[0] if m.lc_classes_ else "unknown"})
            ev = evaluate(pair, cfg, methods=("global_ratio", "seasonal_ratio"), bootstrap_reps=0, schemes_to_run=("temporal",))
            tm = ev["schemes"]["temporal"]["methods"]
            rows.append(
                {
                    "setting": label,
                    "mode": pair.mode,
                    "paired_units": pair.info["n_paired_active_units"],
                    "mean_ratio": float(np.exp(m.log_rho(df)[0].mean())),
                    "baseline_landcover": m.lc_classes_[0] if m.lc_classes_ else None,
                    "harmonic_order": m.order_,
                    "dispersion_window": m.phi_window_,
                    "temporal_week_deviance": tm["seasonal_ratio"]["aoi_week"]["mean_poisson_deviance"],
                    "temporal_week_deviance_global_ratio": tm["global_ratio"]["aoi_week"]["mean_poisson_deviance"],
                }
            )
        except Exception as exc:  # a setting can legitimately fail (e.g. too sparse); record it
            rows.append({"setting": label, "error": str(exc)})
    out = {
        "experiment": "sensitivity",
        "generated_at": _now(),
        "app_version": __version__,
        "method_version": base["method_version"],
        "seconds": round(time.time() - t0, 1),
        "summary": {
            "data_origin": data_origin,
            "reference": ref,
            "source": src,
            "rows": rows,
            "reading": "Mean ratio = source detections per reference detection for the baseline land-cover class, averaged over the year. Deviance columns: held-out (leave-one-year-out) mean Poisson deviance of AOI-week reference totals; lower is better. Deviances are only comparable within the same grid/quality setting family because the target counts change.",
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sensitivity.json").write_text(json.dumps(_clean(out), indent=2, default=str))
    if tmp:
        tmp.cleanup()
    return out


def write_evaluation_artifacts(ev: dict, h: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    prov = ev.get("provenance", {})
    tag = f"{prov.get('data_origin', 'unknown')}_{ev.get('reference', h['pair']['reference'])}_{ev.get('source', h['pair']['source'])}".replace("-", "")
    p1 = out_dir / f"evaluation_{tag}.json"
    p1.write_text(json.dumps(_clean({"evaluation": ev, "harmonization": {k: v for k, v in h.items() if k != "series"}}), indent=2, default=str))
    p2 = out_dir / f"model_card_{tag}.md"
    p2.write_text(_model_card(ev, h))
    p3 = out_dir / f"harmonized_series_{tag}.csv"
    pd.DataFrame(h["series"]).to_csv(p3, index=False)
    return [p1, p2, p3]


# ---------------------------------------------------------------------- rendering


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if not math.isfinite(f) else f
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def _f(v, d=3):
    if v is None:
        return "–"
    if isinstance(v, (int, np.integer)):
        return str(v)
    return f"{v:.{d}f}"


def _recovery_md(out: dict) -> str:
    s = out["summary"]
    lines = [
        "# Synthetic recovery experiment",
        "",
        f"Generated {out['generated_at']} with Ember Atlas {out['app_version']}, method {out['method_version']}, seed {s['seed']}, "
        f"{out['bootstrap_reps']} block-bootstrap replicates. **Synthetic data only — this checks the method against a known truth; "
        "it says nothing about real MODIS/VIIRS behaviour.**",
        "",
        "Injected effect: S-NPP/Aqua detection ratio log(3) with a seasonal amplitude of 0.25 (peak at day 245) and land-cover shifts "
        "(cropland +0.30, forest −0.10). The calibration targets *quality-filtered* counts, so the effective truth includes the "
        f"filter pass rates (MODIS {P_PASS['MODIS']:.4f}, VIIRS {P_PASS['VIIRS']:.2f}).",
        "",
        "## Parameters (coverage-exposure mode)",
        "",
        "| Quantity | Truth | Estimate | 90% interval | Inside |",
        "|---|---|---|---|---|",
    ]
    for p in s["parameters"]:
        lines.append(f"| {p['name']} | {_f(p['truth'])} | {_f(p['estimate'])} | {_f(p['lo'])}–{_f(p['hi'])} | {'yes' if p['inside'] else 'no'} |")
    t = s["transfer"]
    lines += [
        "",
        "## Conversion after the reference record ends",
        "",
        f"{t['weeks']} weeks ({t['period'][0]} to {t['period'][1]}) where only S-NPP exists. Truth: {t['truth_definition']}.",
        "",
        f"* relative bias of converted weekly totals: {t['relative_bias'] * 100:.1f}%",
        f"* naive splice (raw S-NPP counts) relative bias: {t['naive_splice_relative_bias'] * 100:.1f}%",
        f"* share of weeks whose 90% estimate interval contains the truth: {t['interval_coverage_truth'] * 100:.0f}%",
        "",
        "## Hotspot-only mode (coverage hidden)",
        "",
        "Without valid-observation coverage the ratio absorbs the average coverage difference between the platforms "
        "(VIIRS has no swath gaps here), so the log ratio is expected to sit above the coverage-adjusted truth:",
        "",
    ]
    for p in s["hotspot_only"]["params"]:
        lines.append(f"* {p['name']}: {_f(p['estimate'])} ({_f(p['lo'])}–{_f(p['hi'])})")
    th = s["hotspot_only"]["transfer"]
    lines += [
        "",
        f"Transfer bias against the hotspot-mode truth ({th['truth_definition']}): {th['relative_bias'] * 100:.1f}%, "
        f"interval coverage {th['interval_coverage_truth'] * 100:.0f}%.",
        "",
        "## Injected anomalies (single-year test families)",
        "",
    ]
    for k, v in s["anomaly_detection"].items():
        inj = v["injected"]
        lines.append(
            f"* {k}: factor {inj['factor']} in {inj['year']} (days {inj['doy'][0]}–{inj['doy'][1]}, area {inj['area']}): "
            f"{v['weeks_flagged_in_episode']} of {v['weeks_in_episode']} episode weeks flagged; "
            f"{v['weeks_flagged_outside_episode']} weeks flagged elsewhere in that year; {v['n_tests']} tests."
        )
    lines += [
        "",
        "Detection power depends on how large an anomaly is relative to natural year-to-year variability and on how many other "
        "years form the baseline; with few baseline years (quick mode) even large injected episodes may not survive FDR control.",
    ]
    return "\n".join(lines) + "\n"


def _model_card(ev: dict, h: dict) -> str:
    prov = ev.get("provenance", {})
    st = h["status"]
    m = h.get("model") or {}
    lines = [
        f"# Model card: {h['pair']['source']} → {h['pair']['reference']} harmonisation",
        "",
        f"* Generated: {prov.get('generated_at')}  ",
        f"* Data origin: **{prov.get('data_origin', '?').upper()}** ({'synthetic demo, not NASA measurements' if prov.get('synthetic') else 'imported data'})  ",
        f"* Method: {prov.get('method_version')} (config fingerprint {prov.get('method_fingerprint')}), app {prov.get('app_version')}  ",
        f"* Area: {prov.get('selection', {}).get('aoi_label') or 'custom AOI'} ({prov.get('selection', {}).get('aoi_area_km2')} km²), grid {h['pair']['grid_km']} km, weekly",
        f"* Status: **{st['label']}** (`{st['code']}`). {st['explanation']}",
        "",
        "## Intended use",
        "",
        "Express weekly active-fire detection activity of the source platform on the reference platform's scale, for historical "
        "calendars, seasonal baselines and cross-sensor continuity. Not for burned-area estimation, fire counting, or forecasting.",
        "",
        "## Target",
        "",
        h.get("target", ""),
        "",
        "## Model",
        "",
        f"{m.get('label', 'not fitted')}; seasonal Fourier order {m.get('harmonic_order')}; land-cover classes {m.get('landcover_classes')}; "
        f"dispersion {(_f(m.get('dispersion_cell_week'), 2))} (cell-week), {_f(m.get('dispersion_window'), 2)} (week totals); "
        f"{m.get('bootstrap_reps_effective')} block-bootstrap replicates.",
        "",
        "| Term | Estimate | SE (quasi) |",
        "|---|---|---|",
    ]
    for c in m.get("coefficients", []):
        lines.append(f"| {c['term']} | {_f(c['estimate'])} | {_f(c['se_quasi'])} |")
    p = h["pair"]
    lines += [
        "",
        "## Training data",
        "",
        f"Overlap {p['overlap_start']} to {p['overlap_end']} ({len(p['overlap_years'])} years), {p['n_paired_active_units']} paired cell-weeks "
        f"with detections, {p['total_ref']} reference and {p['total_src']} source quality detections, mode `{p['mode']}`.",
        "",
        "## Held-out evaluation",
        "",
        f"Measures: {ev.get('what_is_measured', '')}.",
        "",
    ]
    for scheme, res in (ev.get("schemes") or {}).items():
        if "methods" not in res:
            continue
        lines += [
            f"### {scheme} blocks",
            "",
            f"90% predictive-interval coverage: {_f(res.get('interval_coverage'))}",
            "",
            "| Method | MAE (week) | RMSE (week) | Deviance (week) | Rel. bias | Deviance (cell-week) |",
            "|---|---|---|---|---|---|",
        ]
        for v in res["methods"].values():
            a = v["aoi_week"]
            lines.append(
                f"| {v['label']} | {_f(a['mae'], 1)} | {_f(a['rmse'], 1)} | {_f(a['mean_poisson_deviance'])} | {_f(a['relative_bias'])} | {_f(v['cell_week']['mean_poisson_deviance'])} |"
            )
        lines.append("")
    lines += ["## Gates", ""]
    for c in ev.get("gates", {}).get("checks", []):
        lines.append(f"* {'PASS' if c['passed'] else 'FAIL'}: {c['check']} ({c['detail']})")
    d = h.get("diagnostics") or {}
    lines += [
        "",
        "## Diagnostics",
        "",
        f"* Activity dependence of the ratio: {_f(d.get('proportionality', {}).get('coef_log_activity'))} ± {_f(d.get('proportionality', {}).get('se'))} "
        f"({'flagged' if d.get('proportionality', {}).get('flag') else 'not flagged'})",
        f"* Drift of the log ratio per year: {_f(d.get('drift', {}).get('log_ratio_per_year'))} ± {_f(d.get('drift', {}).get('se'))} "
        f"({'flagged' if d.get('drift', {}).get('flag') else 'not flagged'})",
        "* Zero check: "
        + (
            d.get("zero_check", {}).get("note")
            or f"observed zero share of reference cell-weeks {_f(d.get('zero_check', {}).get('observed_zero_fraction_ref'))} vs "
            f"{_f(d.get('zero_check', {}).get('nb_expected_zero_fraction_ref'))} expected under a negative binomial (alpha "
            f"{_f(d.get('zero_check', {}).get('nb_alpha'))}); no zero inflation beyond overdispersion if these agree"
        ),
        "",
        "## Warnings",
        "",
    ]
    lines += [f"* {w}" for w in h.get("warnings", [])] or ["* none"]
    lines += [
        "",
        "## Limitations",
        "",
        "* Validation measures agreement with the reference platform, not accuracy against independent fire observations.",
        "* Conversion outside the overlap assumes a stationary sensor ratio.",
        "* Without valid-observation coverage, coverage differences are confounded with sensitivity differences.",
        "* The ratio may depend on fire size; see the activity-dependence diagnostic.",
    ]
    return "\n".join(lines) + "\n"
