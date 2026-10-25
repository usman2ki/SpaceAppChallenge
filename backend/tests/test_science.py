"""Calibration, leakage prevention, status gates and synthetic recovery."""

import math

import numpy as np
import pandas as pd
import pytest
from conftest import small_spec

from ember_atlas.analysis.aggregate import Selection
from ember_atlas.config import method_config
from ember_atlas.geo.polygons import aoi_from_bbox
from ember_atlas.science import calibration as cal
from ember_atlas.science.evaluation import evaluate, spatial_folds, temporal_folds
from ember_atlas.science.experiments import P_PASS, _NoCoverage, _true_log_ratio
from ember_atlas.science.status import decide, sufficiency

CFG = method_config()
AOI = aoi_from_bbox(-48.0, -11.0, -46.5, -9.5)


@pytest.fixture(scope="module")
def pair(small_world_store):
    sel = Selection("synthetic", AOI, "2013-01-01", "2019-12-31", ["Aqua", "Suomi-NPP"], None, 18, "week", CFG)
    return cal.build_pair_table(small_world_store, sel, "Aqua", "Suomi-NPP")


# ------------------------------------------------------------------ recovery on synthetic data


def test_rate_ratio_recovers_injected_sensor_effect(pair, small_world_store):
    spec = small_world_store._world.spec
    m = cal.SeasonalRatio(pair.mode, CFG).fit(pair.units)
    assert pair.mode == "exposure"
    doys = np.arange(1, 366, dtype=float)
    for lc in m.lc_classes_:
        est = m.log_rho(pd.DataFrame({"doy_mid": doys, "lc": lc}))[0].mean()
        truth = _true_log_ratio(spec, doys, lc).mean()
        # small synthetic world: tolerance ~2.5 standard errors of a class mean
        assert abs(est - truth) < 0.15, (lc, est, truth)
    assert m.order_ >= 1  # the injected seasonal ratio is found by train-only selection


def test_hotspot_only_mode_absorbs_coverage_ratio(small_world_store):
    sel = Selection("synthetic", AOI, "2013-01-01", "2019-12-31", ["Aqua", "Suomi-NPP"], None, 18, "week", CFG)
    p_exp = cal.build_pair_table(small_world_store, sel, "Aqua", "Suomi-NPP")
    p_hot = cal.build_pair_table(_NoCoverage(small_world_store), sel, "Aqua", "Suomi-NPP")
    assert p_hot.mode == "hotspot_only"
    b_exp = cal.SeasonalRatio(p_exp.mode, CFG, order=0, use_lc=False).fit(p_exp.units).beta_[0]
    b_hot = cal.SeasonalRatio(p_hot.mode, CFG, order=0, use_lc=False).fit(p_hot.units).beta_[0]
    u = p_exp.units[p_exp.units["n"] > 0]
    expected_shift = math.log((u["e_s"] * u["n"]).sum() / (u["e_r"] * u["n"]).sum())
    assert b_hot - b_exp == pytest.approx(expected_shift, abs=0.05)


def test_conditional_model_is_exact_for_shared_latent_activity():
    # y_r ~ Pois(lam e_r), y_s ~ Pois(rho lam e_s), lam wildly heterogeneous: rho still recovered
    rng = np.random.default_rng(5)
    n = 20000
    lam = rng.lognormal(0, 1.5, n)
    e_r, e_s = rng.uniform(0.5, 2, n), rng.uniform(0.5, 2, n)
    df = pd.DataFrame(
        {
            "y_r": rng.poisson(lam * e_r),
            "y_s": rng.poisson(2.5 * lam * e_s),
            "e_r": e_r,
            "e_s": e_s,
            "doy_mid": rng.uniform(1, 365, n),
            "year": 2015 + rng.integers(0, 4, n),
            "lc": "unknown",
            "window_start": "x",
        }
    )
    df["n"] = df.y_r + df.y_s
    m = cal.SeasonalRatio("exposure", CFG, order=0).fit(df)
    assert math.exp(m.beta_[0]) == pytest.approx(2.5, rel=0.03)


def test_drift_is_flagged(tmp_path):
    from ember_atlas.store.sqlite_store import SqliteStore
    from ember_atlas.synthetic.seed import seed_demo

    st = SqliteStore(tmp_path / "drift.sqlite")
    st.migrate()
    seed_demo(st, spec=small_spec(ratio_drift_per_year=0.12, seed=7))
    sel = Selection("synthetic", AOI, "2013-01-01", "2019-12-31", ["Aqua", "Suomi-NPP"], None, 18, "week", CFG)
    p = cal.build_pair_table(st, sel, "Aqua", "Suomi-NPP")
    m = cal.SeasonalRatio(p.mode, CFG).fit(p.units)
    d = cal.diagnostics(m, p.units, CFG)
    assert d["drift"]["flag"] and d["drift"]["log_ratio_per_year"] == pytest.approx(0.12, abs=0.04)


# ------------------------------------------------------------------ leakage prevention


def test_temporal_folds_are_blocked_and_buffered(pair):
    u = pair.units
    for f in temporal_folds(u, CFG):
        assert not (f["train"] & f["test"]).any()
        test_years = set(u.loc[f["test"], "year"])
        assert len(test_years) == 1
        ws_train = pd.to_datetime(u.loc[f["train"], "window_start"])
        ws_test = pd.to_datetime(u.loc[f["test"], "window_start"])
        gap = min(abs((ws_train - ws_test.min()).dt.days).min(), abs((ws_train - ws_test.max()).dt.days).min())
        assert gap > 7 * CFG["validation"]["temporal_buffer_weeks"]


def test_spatial_folds_are_blocked_and_buffered(pair):
    u = pair.units
    for f in spatial_folds(u, CFG, seed=1):
        tr, te = u[f["train"]], u[f["test"]]
        assert not set(tr["cell_id"]) & set(te["cell_id"])
        tc = te[["col", "row"]].drop_duplicates().to_numpy()
        for col, row in tr[["col", "row"]].drop_duplicates().to_numpy():
            assert np.min(np.maximum(np.abs(tc[:, 0] - col), np.abs(tc[:, 1] - row))) > CFG["validation"]["spatial_buffer_cells"]


def test_fit_never_sees_test_labels(pair):
    u = pair.units
    f = temporal_folds(u, CFG)[2]
    train = u[f["train"]]
    m1 = cal.SeasonalRatio(pair.mode, CFG).fit(train)
    # scramble everything outside the training rows: fitted model must not change
    u2 = u.copy()
    rng = np.random.default_rng(0)
    u2.loc[~f["train"], "y_r"] = rng.integers(0, 50, (~f["train"]).sum())
    u2.loc[~f["train"], "y_s"] = rng.integers(0, 50, (~f["train"]).sum())
    u2["n"] = u2["y_r"] + u2["y_s"]
    m2 = cal.SeasonalRatio(pair.mode, CFG).fit(u2[f["train"]])
    assert m1.order_ == m2.order_ and np.allclose(m1.beta_, m2.beta_)
    g1 = cal.GlobalRatio(pair.mode, CFG).fit(train).ratio
    g2 = cal.GlobalRatio(pair.mode, CFG).fit(u2[f["train"]]).ratio
    assert g1 == g2


def test_evaluation_reports_baselines_and_gates(pair):
    ev = evaluate(pair, CFG, bootstrap_reps=20)
    for scheme in ("temporal", "spatial"):
        meths = ev["schemes"][scheme]["methods"]
        assert set(meths) == {"raw_substitution", "global_ratio", "seasonal_ratio", "nb_regression"}
        # raw substitution of a ~3x more sensitive sensor must be badly biased
        assert meths["raw_substitution"]["aoi_week"]["relative_bias"] > 1.5
        assert abs(meths["seasonal_ratio"]["aoi_week"]["relative_bias"]) < 0.15
    assert ev["what_is_measured"].startswith("agreement")
    assert {c["check"].split(":")[0] for c in ev["gates"]["checks"]} == {"temporal", "spatial"}


# ------------------------------------------------------------------ status gates


def _suff(ok=True):
    return {"passed": ok, "checks": [{"check": "paired active cell-weeks", "value": 10, "required": 150, "passed": ok}]}


def test_status_never_claims_validation_without_evidence():
    passed = {"gates": {"passed": True, "checks": []}}
    failed = {"gates": {"passed": False, "checks": [{"check": "x", "passed": False}]}}
    assert decide("real", "exposure", _suff(False), passed)["code"] == "insufficient_data"
    assert decide("real", "exposure", _suff(), None)["code"] == "not_evaluated"
    assert decide("real", "exposure", _suff(), failed)["show_harmonized"] is False
    assert decide("synthetic", "exposure", _suff(), passed)["code"] == "synthetic_demo_verified"
    assert decide("real", "hotspot_only", _suff(), passed)["code"] == "research_estimate"
    assert decide("real", "exposure", _suff(), passed)["code"] == "validated_holdout"
    # only real data with coverage exposure can reach the "validated" code
    assert decide("synthetic", "exposure", _suff(), passed)["code"] != "validated_holdout"
    assert "synthetic" in decide("synthetic", "exposure", _suff(), passed)["label"].lower()
    assert "not validated" in decide("real", "hotspot_only", _suff(), passed)["label"].lower()


def test_sufficiency_refuses_sparse_overlap():
    info = {
        "n_paired_active_units": 20,
        "total_ref": 40,
        "total_src": 100,
        "overlap_years": [2019],
        "n_spatial_blocks": 1,
        "reference": "Aqua",
        "source": "Suomi-NPP",
    }
    s = sufficiency(info, CFG)
    assert not s["passed"] and sum(not c["passed"] for c in s["checks"]) == 5


def test_effective_truth_constants():
    assert P_PASS["MODIS"] == pytest.approx(0.8525)
