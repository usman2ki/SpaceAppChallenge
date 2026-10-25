import gzip
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from ember_atlas.geo.polygons import aoi_from_bbox
from ember_atlas.ingest import firms_schema as fs
from ember_atlas.ingest.importer import import_bytes
from ember_atlas.ingest.normalize import Declaration, normalize
from ember_atlas.store.base import DetectionQuery
from ember_atlas.store.sqlite_store import SqliteStore

FIX = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture()
def store():
    s = SqliteStore(":memory:")
    s.migrate()
    yield s
    s.close()


def read(name):
    return (FIX / name).read_bytes()


# ------------------------------------------------------------------ schema & quality mappings


def test_modis_confidence_classes_follow_user_guide_thresholds():
    csv = b"latitude,longitude,acq_date,acq_time,satellite,instrument,confidence,version\n" + b"".join(
        f"-10,-47,2019-01-01,1200,A,MODIS,{c},6.1\n".replace("-10,", f"-10.{i},").encode() for i, c in enumerate([0, 29, 29.9, 30, 79, 79.9, 80, 100])
    )
    res = normalize(csv, Declaration("synthetic", "MODIS_SP"))
    assert res.accepted["confidence_class"].tolist() == ["low", "low", "low", "nominal", "nominal", "nominal", "high", "high"]


def test_viirs_confidence_letters_and_words():
    rows = "\n".join(f"-10.{i},-47,2019-01-01,1200,N,VIIRS,{c},2.0" for i, c in enumerate(["l", "n", "h", "low", "Nominal", "HIGH"]))
    csv = ("latitude,longitude,acq_date,acq_time,satellite,instrument,confidence,version\n" + rows).encode()
    res = normalize(csv, Declaration("synthetic", "VIIRS_SNPP_SP"))
    assert res.accepted["confidence_class"].tolist() == ["low", "nominal", "high", "low", "nominal", "high"]


@pytest.mark.parametrize(
    "v,expected",
    [
        ("6.1", ("6.1", "SP")),
        ("6.1NRT", ("6.1", "NRT")),
        ("2.0URT", ("2.0", "URT")),
        ("2.0RT", ("2.0", "RT")),
        ("2", ("2", "SP")),
        ("1.0_NRT", ("1.0", "NRT")),
        ("abc", None),
    ],
)
def test_version_parsing(v, expected):
    assert fs.parse_version(v) == expected


def test_platform_codes_are_documented_ones_only():
    assert fs.PLATFORM_CODES["N20"] == "NOAA-20" and fs.PLATFORM_CODES["T"] == "Terra"
    assert "1" not in fs.PLATFORM_CODES  # undocumented codes are rejected, not guessed


# ------------------------------------------------------------------ UTC handling


def test_times_are_utc_and_originals_preserved():
    res = normalize(read("SYNTHETIC_tiny_modis_sp.csv"), Declaration("synthetic", "MODIS_SP"))
    df = res.accepted
    row = df[df["acq_time_raw"] == "130"].iloc[0]
    assert row["acq_datetime_utc"] == "2019-08-01T01:30:00Z"
    assert row["acq_date"] == "2019-08-01"
    assert row["acq_date_raw"] == "2019-08-01"
    row2 = df[df["acq_time_raw"] == "0105"].iloc[0]
    assert row2["acq_datetime_utc"] == "2019-08-03T01:05:00Z"


def test_hh_mm_time_format_and_compact_date():
    csv = b"latitude,longitude,acq_date,acq_time,satellite,instrument,confidence,version\n-10,-47,20190801,13:05,A,MODIS,50,6.1\n"
    res = normalize(csv, Declaration("synthetic", "MODIS_SP"))
    assert res.accepted.iloc[0]["acq_datetime_utc"] == "2019-08-01T13:05:00Z"


# ------------------------------------------------------------------ validation


def test_malformed_rows_rejected_with_specific_reasons():
    res = normalize(read("SYNTHETIC_malformed_modis.csv"), Declaration("synthetic", "MODIS_SP"))
    reasons = {r["line"]: " | ".join(r["reasons"]) for r in res.rejected}
    assert "out of range" in reasons[3]
    assert "invalid acq_date" in reasons[4]
    assert "invalid acq_time" in reasons[5]
    assert "unknown satellite" in reasons[6]
    assert "0 to 100" in reasons[7]
    assert "expected 15 fields" in reasons[8]
    assert "processing stream mismatch" in reasons[9]
    assert "does not belong to the instrument" in reasons[10]
    assert "null-island" in reasons[12]
    # negative FRP and invalid day/night are field warnings, the detections are kept
    assert res.field_warnings["frp: invalid value set to null"] == 1
    assert res.field_warnings["daynight: invalid value set to null"] == 1
    kept_lines = set(res.accepted["line_number"])
    assert {2, 11, 13, 14} <= kept_lines


def test_missing_required_column_is_a_file_level_error(store):
    rep = import_bytes(store, b"latitude,longitude,acq_date,satellite\n1,2,2019-01-01,A\n", "x.csv", Declaration("synthetic"))
    assert rep["status"] == "rejected_file" and "acq_time" in rep["message"]
    # the rejected file is still stored as raw evidence
    assert gzip.decompress(store.get_raw_file(rep["batch_id"])).startswith(b"latitude")


def test_missing_version_without_declaration_refuses_to_guess_stream(store):
    csv = b"latitude,longitude,acq_date,acq_time,satellite,instrument,confidence\n-10,-47,2019-01-01,1200,A,MODIS,50\n"
    rep = import_bytes(store, csv, "x.csv", Declaration("synthetic"))
    assert rep["status"] == "rejected_file" and "Refusing to guess" in rep["message"]


def test_mixed_platform_label_in_declared_product():
    csv = b"latitude,longitude,acq_date,acq_time,satellite,instrument,confidence,version\n-10,-47,2019-01-01,1200,N20,VIIRS,n,2.0\n-10.1,-47,2019-01-01,1200,N,VIIRS,n,2.0\n"
    res = normalize(csv, Declaration("synthetic", "VIIRS_SNPP_SP"))
    assert len(res.accepted) == 1
    assert "mixed platform label" in res.rejected[0]["reasons"][0]


# ------------------------------------------------------------------ dedup & idempotency


def test_import_is_idempotent_and_reports_duplicates(store):
    data = read("SYNTHETIC_tiny_modis_sp.csv")
    rep = import_bytes(store, data, "SYNTHETIC_tiny_modis_sp.csv", Declaration("synthetic", "MODIS_SP"))
    assert rep["status"] == "complete"
    assert rep["rows_read"] == 8 and rep["duplicates_in_file"] == 1 and rep["inserted"] == 7
    rep2 = import_bytes(store, data, "again.csv", Declaration("synthetic", "MODIS_SP"))
    assert rep2["status"] == "already_imported" and rep2["inserted"] == 0
    # an overlapping, differently-named file: rows already stored are duplicates, not new evidence
    overlap = data + b"-9.6000,-46.4000,312.6,1.5,1.2,2019-08-03,0105,A,MODIS,55,6.1,292.2,10.4,N,0\n"
    rep3 = import_bytes(store, overlap, "overlap.csv", Declaration("synthetic", "MODIS_SP"))
    assert rep3["inserted"] == 1 and rep3["duplicates_existing"] == 7


def test_conflicting_reobservation_is_flagged_not_overwritten(store):
    import_bytes(store, read("SYNTHETIC_malformed_modis.csv"), "m.csv", Declaration("synthetic", "MODIS_SP"))
    issues = store.row_issues(1, limit=100)
    conflicts = [i for i in issues if i["status"] == "conflict_in_file"]
    assert len(conflicts) == 1 and conflicts[0]["line"] == 14


def test_repeated_observations_are_not_duplicates(store):
    # same pixel location, different overpass time / platform / stream => all kept
    base = "latitude,longitude,acq_date,acq_time,satellite,instrument,confidence,version\n"
    rows = [
        "-10,-47,2019-01-01,1200,A,MODIS,50,6.1",
        "-10,-47,2019-01-01,1340,A,MODIS,50,6.1",  # later overpass (orbit overlap)
        "-10,-47,2019-01-01,1200,T,MODIS,50,6.1",  # other platform
    ]
    rep = import_bytes(store, (base + "\n".join(rows)).encode(), "a.csv", Declaration("synthetic"))
    assert rep["inserted"] == 3
    nrt = base + "-10,-47,2019-01-01,1200,A,MODIS,50,6.1NRT"
    rep2 = import_bytes(store, nrt.encode(), "b.csv", Declaration("synthetic"))
    assert rep2["inserted"] == 1  # NRT version of the same observation is stored separately


def test_raw_evidence_is_immutable(store):
    import_bytes(store, read("SYNTHETIC_tiny_modis_sp.csv"), "t.csv", Declaration("synthetic", "MODIS_SP"))
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM raw_files")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE detections SET frp_mw = 0")


def test_availability_declared_vs_inferred(store):
    import_bytes(
        store,
        read("SYNTHETIC_tiny_modis_sp.csv"),
        "t.csv",
        Declaration("synthetic", "MODIS_SP", period_start="2019-07-25", period_end="2019-08-10"),
    )
    rep = import_bytes(store, read("SYNTHETIC_tiny_viirs_snpp_sp.csv"), "v.csv", Declaration("synthetic", "VIIRS_SNPP_SP"))
    av = store.availability("synthetic")
    modis = av[av["platform"] == "Aqua"].iloc[0]
    assert modis["basis"] == "declared_query" and modis["date_start"] == "2019-07-25"
    viirs = av[av["platform"] == "Suomi-NPP"].iloc[0]
    assert viirs["basis"] == "inferred_from_detections" and viirs["date_start"] == "2019-08-01"
    assert any("inferred" in w for w in rep["file_warnings"])


def test_query_uses_exact_polygon_and_keeps_origins_separate(store):
    import_bytes(store, read("SYNTHETIC_tiny_modis_sp.csv"), "t.csv", Declaration("synthetic", "MODIS_SP"))
    import_bytes(store, read("SYNTHETIC_tiny_modis_sp.csv"), "t.csv", Declaration("real", "MODIS_SP"))
    aoi = aoi_from_bbox(-48, -11, -46, -9.5)
    q = DetectionQuery("synthetic", aoi, "2019-01-01", "2019-12-31")
    assert len(store.query_detections(q)) == 7
    tri = {"type": "Polygon", "coordinates": [[[-48, -11], [-46, -11], [-48, -9.5], [-48, -11]]]}
    from ember_atlas.geo.polygons import parse_aoi

    df = store.query_detections(DetectionQuery("synthetic", parse_aoi(tri), "2019-01-01", "2019-12-31"))
    inside = parse_aoi(tri).contains(df["longitude"].to_numpy(), df["latitude"].to_numpy())
    assert inside.all() and 0 < len(df) < 7
    assert set(store.query_detections(DetectionQuery("real", aoi, "2019-01-01", "2019-12-31"))["data_origin"]) == {"real"}
    assert np.all(store.query_detections(DetectionQuery("synthetic", aoi, "2019-08-02", "2019-08-02"))["acq_date"] == "2019-08-02")
