import numpy as np
import pandas as pd
import pytest

from ember_atlas.analysis.windows import week_index, window_start, window_table
from ember_atlas.config import method_config
from ember_atlas.science.anomalies import analyse, annual_totals, bh_fdr

CFG = method_config()


def test_week_bins_anchor_on_jan_1_and_last_bin_absorbs_year_end():
    d = pd.Series(["2019-01-01", "2019-01-07", "2019-01-08", "2019-12-23", "2019-12-24", "2019-12-31", "2020-12-31"])
    assert week_index(d).tolist() == [0, 0, 1, 50, 51, 51, 51]
    assert window_start(d, "week").tolist()[-2:] == ["2019-12-24", "2020-12-23"]
    wt = window_table("2019-01-01", "2019-12-31", "week")
    assert len(wt) == 52 and wt["n_days"].sum() == 365 and wt.iloc[-1]["n_days"] == 8
    assert window_table("2020-02-27", "2020-03-02", "day")["window_start"].tolist() == ["2020-02-27", "2020-02-28", "2020-02-29", "2020-03-01", "2020-03-02"]


def test_bh_fdr_matches_reference_values():
    p = np.array([0.01, 0.04, 0.03, 0.005])
    # manual BH: sorted 0.005,0.01,0.03,0.04 -> *4/rank = 0.02,0.02,0.04,0.04
    assert np.allclose(bh_fdr(p), [0.02, 0.04, 0.04, 0.02])


def _series(years, weekly, spike=None, observed=True, coverage=False):
    rows = []
    rng = np.random.default_rng(1)
    for y in years:
        for k in range(52):
            ws = (pd.Timestamp(f"{y}-01-01") + pd.Timedelta(days=7 * k)).strftime("%Y-%m-%d")
            v = weekly(k) * float(np.exp(rng.normal(0, 0.2)))
            if spike and (y, k) in spike:
                v *= spike[(y, k)]
            rows.append({"window_start": ws, "value": v, "lo": np.nan, "observed": observed, "coverage_known": coverage, "conservative": False})
    return pd.DataFrame(rows)


def test_detects_clear_spike_and_controls_false_positives():
    df = _series(range(2010, 2022), lambda k: 50 + 400 * np.exp(-((k - 33) ** 2) / 20), spike={(2018, 33): 6.0, (2018, 34): 6.0})
    res = analyse(df, CFG, view=("2018-01-01", "2018-12-31"))
    sig = {w["window_start"]: w["signal"] for w in res["windows"]}
    hits = [k for k, v in sig.items() if v == "unusually_high"]
    # the spike weeks, or other weeks of the same 4-week block (block-level signal)
    assert hits and all("2018-08-06" <= k <= "2018-09-09" for k in hits)
    assert {"2018-08-20", "2018-08-27"} <= set(hits)
    assert res["episodes"] and res["episodes"][0]["kind"] == "unusually_high"
    assert "forecast" in res["notes"][0]


def test_low_activity_needs_coverage():
    df = _series(range(2010, 2022), lambda k: 300.0, spike={(2018, 20): 0.02})
    no_cov = analyse(df, CFG, view=("2018-01-01", "2018-12-31"))
    assert not any(w["signal"] == "unusually_low" for w in no_cov["windows"])
    assert all("p_deficit" not in w or w["p_deficit"] is None for w in no_cov["windows"])
    with_cov = analyse(_series(range(2010, 2022), lambda k: 300.0, spike={(2018, 20): 0.02}, coverage=True), CFG, view=("2018-01-01", "2018-12-31"))
    assert any(w["signal"] == "unusually_low" for w in with_cov["windows"])


def test_insufficient_history_and_unobserved_are_not_zero():
    short = _series(range(2018, 2021), lambda k: 100.0)
    res = analyse(short, CFG)
    assert {w["signal"] for w in res["windows"]} == {"insufficient_history"}
    assert all("median" not in c for c in res["seasonal_curve"])
    unobs = _series(range(2010, 2022), lambda k: 100.0)
    unobs.loc[unobs["window_start"].str.startswith("2015"), "observed"] = False
    r = analyse(unobs, CFG, view=("2015-01-01", "2015-12-31"))
    assert {w["signal"] for w in r["windows"]} == {"not_observed"}
    tot = {a["year"]: a for a in annual_totals(unobs)}
    assert tot[2015]["windows_observed"] == 0 and not tot[2015]["complete"]


def test_more_tests_need_stronger_evidence():
    df = _series(range(2008, 2022), lambda k: 200.0, spike={(2018, 30): 3.0})
    one_year = analyse(df, CFG, view=("2018-01-01", "2018-12-31"))
    all_years = analyse(df, CFG)
    assert all_years["n_tests"] > one_year["n_tests"]
    q1 = [w.get("q_excess") for w in one_year["windows"] if w["window_start"] == "2018-07-30"][0]
    q2 = [w.get("q_excess") for w in all_years["windows"] if w["window_start"] == "2018-07-30"][0]
    assert q2 >= q1 == pytest.approx(q1)
