"""The same behavioural contract for every store implementation.

SQLite runs with and without its in-memory frame cache. PostGIS runs only
when EMBER_TEST_DATABASE_URL points at an empty PostGIS database
(e.g. the Docker Compose db service); it is skipped otherwise.
"""

import gzip
import os

import numpy as np
import pandas as pd
import pytest
from conftest import FIXTURES

from ember_atlas.geo.polygons import aoi_from_bbox, parse_aoi
from ember_atlas.ingest.covariates import import_coverage
from ember_atlas.ingest.importer import import_bytes
from ember_atlas.ingest.normalize import Declaration
from ember_atlas.store.base import DetectionQuery
from ember_atlas.store.sqlite_store import SqliteStore

PG_URL = os.environ.get("EMBER_TEST_DATABASE_URL")


def _make(kind):
    if kind == "sqlite-cached":
        s = SqliteStore(":memory:", memory_cache=True)
    elif kind == "sqlite-sql":
        s = SqliteStore(":memory:", memory_cache=False)
    else:
        from ember_atlas.store.postgis_store import PostgisStore

        s = PostgisStore(PG_URL)
    s.migrate()
    return s


KINDS = ["sqlite-cached", "sqlite-sql"] + (["postgis"] if PG_URL else [])


@pytest.fixture(params=KINDS)
def store(request):
    s = _make(request.param)
    yield s
    s.close()


def _load(store):
    for name, src in (("SYNTHETIC_tiny_modis_sp.csv", "MODIS_SP"), ("SYNTHETIC_tiny_viirs_snpp_sp.csv", "VIIRS_SNPP_SP")):
        rep = import_bytes(store, (FIXTURES / name).read_bytes(), name, Declaration("synthetic", src, period_start="2019-07-30", period_end="2019-08-05"))
        assert rep["status"] == "complete"


def test_query_parity_and_exact_polygon(store):
    _load(store)
    tri = parse_aoi({"type": "Polygon", "coordinates": [[[-48, -11], [-46, -11], [-48, -9.5], [-48, -11]]]})
    df = store.query_detections(DetectionQuery("synthetic", tri, "2019-01-01", "2019-12-31"))
    box = store.query_detections(DetectionQuery("synthetic", aoi_from_bbox(-48, -11, -46, -9.5), "2019-01-01", "2019-12-31"))
    assert len(box) == 15 and 0 < len(df) < len(box)
    assert tri.contains(df["longitude"].to_numpy(float), df["latitude"].to_numpy(float)).all()
    only_aqua = store.query_detections(DetectionQuery("synthetic", aoi_from_bbox(-48, -11, -46, -9.5), "2019-08-01", "2019-08-01", ["Aqua"], ["SP"]))
    assert set(only_aqua["platform"]) == {"Aqua"} and set(only_aqua["acq_date"]) == {"2019-08-01"}
    assert store.query_detections(DetectionQuery("real", aoi_from_bbox(-48, -11, -46, -9.5), "2019-01-01", "2019-12-31")).empty


def test_same_results_with_and_without_cache():
    a, b = _make("sqlite-cached"), _make("sqlite-sql")
    for s in (a, b):
        _load(s)
    q = DetectionQuery("synthetic", aoi_from_bbox(-47.5, -10.5, -46.0, -9.6), "2019-08-01", "2019-08-03", ["Aqua", "Suomi-NPP"])
    cols = ["platform", "acq_datetime_utc", "latitude", "longitude", "confidence_class"]
    da = a.query_detections(q)[cols].sort_values(cols).reset_index(drop=True)
    db = b.query_detections(q)[cols].sort_values(cols).reset_index(drop=True)
    pd.testing.assert_frame_equal(da, db, check_dtype=False)


def test_antimeridian_query(store):
    csv = b"latitude,longitude,acq_date,acq_time,satellite,instrument,confidence,version\n64,179.5,2020-07-01,0100,N,VIIRS,n,2.0\n64,-179.5,2020-07-01,0100,N,VIIRS,n,2.0\n64,0,2020-07-01,0100,N,VIIRS,n,2.0\n"
    import_bytes(store, csv, "am.csv", Declaration("synthetic", "VIIRS_SNPP_SP"))
    aoi = parse_aoi("chukotka-antimeridian" and {"type": "Polygon", "coordinates": [[[172, 63], [-172, 63], [-172, 67], [172, 67], [172, 63]]]})
    df = store.query_detections(DetectionQuery("synthetic", aoi, "2020-01-01", "2020-12-31"))
    assert sorted(df["longitude"].tolist()) == [-179.5, 179.5]


def test_raw_file_and_issues_are_kept(store):
    data = (FIXTURES / "SYNTHETIC_malformed_modis.csv").read_bytes()
    rep = import_bytes(store, data, "m.csv", Declaration("synthetic", "MODIS_SP"))
    assert gzip.decompress(store.get_raw_file(rep["batch_id"])) == data
    issues = store.row_issues(rep["batch_id"])
    assert {i["status"] for i in issues} >= {"rejected", "conflict_in_file"}
    assert store.get_batch(rep["batch_id"])["report"]["rows_rejected"] == rep["rows_rejected"]


def test_coverage_roundtrip(store):
    cov = pd.DataFrame(
        {
            "platform": ["Aqua", "Aqua", "Suomi-NPP"],
            "cell_id": ["E36:10:20", "E36:10:21", "E36:10:20"],
            "date": ["2019-08-01", "2019-08-01", "2019-08-02"],
            "valid_looks": [1.5, 0.0, 2.0],
            "nominal_looks": [2.0, 2.0, 2.0],
        }
    )
    r = import_coverage(store, cov, name="t", data_origin="synthetic", grid_size_km=36)
    got = store.query_coverage(r["coverage_set_id"], ["E36:10:20"], "2019-08-01", "2019-08-31", None)
    assert sorted(got["platform"]) == ["Aqua", "Suomi-NPP"]
    assert np.isclose(got[got["platform"] == "Aqua"]["valid_looks"].iloc[0], 1.5)
    assert store.query_coverage(r["coverage_set_id"], ["E36:10:20"], "2019-08-01", "2019-08-01", ["Suomi-NPP"]).empty


def test_data_version_changes_on_import(store):
    v0 = store.data_version("synthetic")
    _load(store)
    assert store.data_version("synthetic") != v0
    assert store.data_version("real") == "b0-0-c0-0"


def test_cache_roundtrip(store):
    store.cache_put("k", '{"a": 1}')
    store.cache_put("k", '{"a": 2}')
    assert store.cache_get("k") == '{"a": 2}' and store.cache_get("missing") is None
