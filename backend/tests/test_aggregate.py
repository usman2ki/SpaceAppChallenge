"""Aggregation metrics and observation-status semantics on a hand-built case."""

import pandas as pd
from conftest import FIXTURES

from ember_atlas.analysis.aggregate import Selection, cell_aggregates, platform_window_series
from ember_atlas.config import method_config
from ember_atlas.geo import equal_area as ea
from ember_atlas.geo.polygons import aoi_from_bbox
from ember_atlas.ingest.covariates import import_coverage
from ember_atlas.ingest.importer import import_bytes
from ember_atlas.ingest.normalize import Declaration
from ember_atlas.store.sqlite_store import SqliteStore

AOI = aoi_from_bbox(-48.0, -11.0, -46.0, -9.5)


def _store(with_coverage: bool):
    st = SqliteStore(":memory:")
    st.migrate()
    import_bytes(
        st,
        (FIXTURES / "SYNTHETIC_tiny_modis_sp.csv").read_bytes(),
        "m.csv",
        Declaration("synthetic", "MODIS_SP", period_start="2019-07-30", period_end="2019-08-05", query_bbox=[-49, -12, -45, -9]),
    )
    if with_coverage:
        cells = list(AOI.cell_overlaps(ea.GridSpec(36)))
        rows = []
        for d in pd.date_range("2019-07-30", "2019-08-05").strftime("%Y-%m-%d"):
            for c in cells:
                looks = 0.0 if d == "2019-07-31" else 2.0  # one fully unobserved day
                rows.append({"platform": "Aqua", "cell_id": c, "date": d, "valid_looks": looks, "nominal_looks": 2.0})
        import_coverage(st, pd.DataFrame(rows), name="t", data_origin="synthetic", grid_size_km=36)
    return st


def _sel(window="day", platforms=("Aqua",)):
    return Selection("synthetic", AOI, "2019-07-28", "2019-08-05", list(platforms), None, 18, window, method_config())


def test_metrics_are_distinct_and_quality_filtered():
    st = _store(False)
    s, meta = platform_window_series(st, _sel())
    d1 = s[s["window_start"] == "2019-08-01"].iloc[0]
    # 2019-08-01 Aqua rows in the fixture: 82, 64, 41 confidence (+1 exact duplicate skipped at import)
    assert d1["n_raw"] == 3 and d1["n_quality"] == 3
    # two of them share an 18 km cell (pixels 0.6 km apart): 2 active cells, not 3
    assert d1["active_cells"] == 2
    d2 = s[s["window_start"] == "2019-08-02"].iloc[0]
    # 2019-08-02: confidence 22 (low) and a type-2 static source -> raw 2, quality 0
    assert d2["n_raw"] == 2 and d2["n_quality"] == 0
    assert d1["frp_sum_mw"] == 24.6 + 12.3 + 8.9


def test_status_without_coverage_never_claims_zero_fire():
    st = _store(False)
    s, meta = platform_window_series(st, _sel())
    st_by_day = dict(zip(s["window_start"], s["status"]))
    assert st_by_day["2019-07-28"] == "not_queried"  # before the declared query period
    assert st_by_day["2019-07-31"] == "none_reported_coverage_unknown"
    assert st_by_day["2019-08-01"] == "detections"
    assert meta["coverage_available"] is False


def test_status_with_coverage_distinguishes_observed_and_unobserved():
    st = _store(True)
    s, meta = platform_window_series(st, _sel())
    st_by_day = dict(zip(s["window_start"], s["status"]))
    assert st_by_day["2019-07-31"] == "unobserved"
    assert st_by_day["2019-07-30"] == "observed_none"
    assert st_by_day["2019-07-28"] == "not_queried"
    assert meta["coverage_available"] is True


def test_platform_without_query_is_not_queried_and_cells_sum_up():
    st = _store(False)
    s, _ = platform_window_series(st, _sel(platforms=("Aqua", "Suomi-NPP")))
    assert set(s[s["platform"] == "Suomi-NPP"]["status"]) == {"not_queried"}
    cells = cell_aggregates(st, _sel(window="week"))
    assert cells["raw_Aqua"].sum() == s[s["platform"] == "Aqua"]["n_raw"].sum()
    assert 0 < cells["aoi_fraction"].max() <= 1
