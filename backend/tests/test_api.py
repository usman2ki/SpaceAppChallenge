"""HTTP API behaviour (runs against FastAPI when installed, otherwise the
identical Starlette routing used in the build sandbox)."""

import io
import time

import numpy as np
import pandas as pd
import pytest
from conftest import FIXTURES
from starlette.testclient import TestClient

from ember_atlas.api.app import create_app
from ember_atlas.service import EmberService

PILOT = {"type": "Polygon", "coordinates": [[[-48.0, -11.0], [-46.5, -11.0], [-46.5, -9.5], [-48.0, -9.5], [-48.0, -11.0]]]}
REQ = {
    "dataset": "synthetic",
    "aoi": PILOT,
    "date_start": "2017-01-01",
    "date_end": "2017-12-31",
    "grid_km": 18,
    "window": "week",
    "platforms": ["Terra", "Aqua", "Suomi-NPP", "NOAA-20"],
    "bootstrap_reps": 30,
}


@pytest.fixture(scope="module")
def client(small_world_store):
    svc = EmberService(small_world_store)
    app = create_app(svc, frontend_dist=None)
    with TestClient(app) as c:
        c.svc = svc
        yield c


def test_health_and_meta(client):
    assert client.get("/api/health").json()["ok"] is True
    meta = client.get("/api/meta").json()
    syn = meta["datasets"]["synthetic"]
    assert syn["coverage_available"] is True
    assert {s["platform"] for s in syn["series"]} == {"Terra", "Aqua", "Suomi-NPP", "NOAA-20"}
    assert meta["method"]["method_version"].startswith("ember-method-")
    assert any(p["id"] == "chukotka-antimeridian" for p in meta["presets"])


def test_aoi_validation(client):
    ok = client.post("/api/aoi/validate", json={"aoi": "chukotka-antimeridian"}).json()
    assert ok["crosses_antimeridian"] and ok["parts"] == 2
    bad = client.post("/api/aoi/validate", json={"aoi": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}})
    assert bad.status_code == 400 and "self-intersecting" in bad.json()["error"]


def test_summary_filters_and_semantics(client, small_world_store):
    r = client.post("/api/summary", json=REQ)
    assert r.status_code == 200
    body = r.json()
    s = pd.DataFrame(body["series"])
    assert set(s["platform"]) == set(REQ["platforms"])
    # Aqua stops at the end of 2018 in this synthetic world but 2017 is fully covered
    assert (s[s["platform"] == "Aqua"]["status"] != "not_queried").all()
    # totals agree with a direct store query (exact polygon, date and platform filter)
    from ember_atlas.geo.polygons import parse_aoi
    from ember_atlas.store.base import DetectionQuery

    d = small_world_store.query_detections(DetectionQuery("synthetic", parse_aoi(PILOT), "2017-01-01", "2017-12-31", ["Aqua"]))
    assert s[s["platform"] == "Aqua"]["n_raw"].sum() == len(d)
    assert body["provenance"]["synthetic"] is True
    # platform filter
    r2 = client.post("/api/summary", json={**REQ, "platforms": ["Aqua"]}).json()
    assert {x["platform"] for x in r2["series"]} == {"Aqua"}
    # a platform with no data in the period is "not_queried", never zero activity
    r3 = client.post("/api/summary", json={**REQ, "date_start": "2013-01-01", "date_end": "2013-03-31", "platforms": ["NOAA-20"]}).json()
    assert {x["status"] for x in r3["series"]} == {"not_queried"}


def test_missing_coverage_stays_unknown(client):
    # day windows: zeros exist; with coverage they are classified, never "zero fire" by default
    body = client.post("/api/summary", json={**REQ, "window": "day", "date_start": "2017-02-01", "date_end": "2017-02-28"}).json()
    statuses = {x["status"] for x in body["series"]}
    assert statuses <= {"detections", "observed_none", "partially_observed_none", "unobserved", "not_queried", "none_reported_coverage_unknown"}


def test_map_cells_and_detections(client):
    cells = client.post("/api/map/cells", json={**REQ, "window_from": "2017-08-01", "window_to": "2017-08-31"}).json()
    assert cells["type"] == "FeatureCollection" and len(cells["features"]) > 10
    tot = sum(f["properties"]["raw_Suomi-NPP"] for f in cells["features"])
    det = client.post(
        "/api/map/detections", json={**REQ, "platforms": ["Suomi-NPP"], "window_from": "2017-08-01", "window_to": "2017-08-31", "page_size": 100}
    ).json()
    assert det["total"] == tot
    assert len(det["features"]) == 100 and det["pages"] == int(np.ceil(tot / 100))
    page2 = client.post(
        "/api/map/detections", json={**REQ, "platforms": ["Suomi-NPP"], "window_from": "2017-08-01", "window_to": "2017-08-31", "page_size": 100, "page": 2}
    ).json()
    assert page2["features"][0] != det["features"][0]


def test_window_detail_has_provenance(client):
    body = client.post("/api/window", json={**REQ, "window_start": "2017-08-27"}).json()
    assert body["provenance_rows"] and all("file_name" in p for p in body["provenance_rows"])
    assert body["static_or_offshore_excluded"] >= 0


def test_harmonization_status_flow(client):
    h = client.post("/api/harmonized", json=REQ).json()
    assert h["status"]["code"] in ("not_evaluated", "insufficient_data")
    assert "validated" not in h["status"]["code"] or h["status"]["code"] == "synthetic_demo_verified"
    if h["status"]["code"] == "insufficient_data":
        pytest.skip("small world too sparse")
    job = client.post("/api/evaluation/run", json=REQ).json()
    job_id = job["job"]
    for _ in range(900):
        if job.get("status") == "done":
            break
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] != "error", job
        time.sleep(0.2)
    ev = client.post("/api/evaluation", json=REQ).json()
    assert "schemes" in ev and "gates" in ev
    h2 = client.post("/api/harmonized", json=REQ).json()
    # synthetic data can never be labelled real-world validated
    assert h2["status"]["code"] in ("synthetic_demo_verified", "failed_validation")
    assert h2["evaluation_summary"] is not None
    s = pd.DataFrame(h2["series"])
    assert {"reference_observed", "converted_from_Suomi-NPP"} <= set(s["basis"])
    conv = s[s["basis"] == "converted_from_Suomi-NPP"]
    assert (conv["harm_lo"] <= conv["harmonized"]).all() and (conv["harmonized"] <= conv["harm_hi"]).all()


def test_anomalies(client):
    a = client.post("/api/anomalies", json={**REQ, "date_start": "2013-01-01", "date_end": "2019-12-31", "view": ["2017-01-01", "2017-12-31"]}).json()
    assert a["n_tests"] > 0 and a["fdr_q"] == 0.05
    sig = {w["signal"] for w in a["windows"]}
    assert sig <= {"typical", "unusually_high", "unusually_low", "insufficient_history", "not_observed", "too_sparse"}
    assert "forecast" in " ".join(a["notes"]).lower()


def test_exports_carry_provenance(client):
    r = client.post("/api/export", json={**REQ, "format": "csv", "content": "series"})
    assert r.status_code == 200
    assert "SYNTHETIC_" in r.headers["content-disposition"]
    text = r.text
    assert text.startswith("# Ember Atlas export. SYNTHETIC DEMO DATA")
    df = pd.read_csv(io.StringIO(text), comment="#")
    assert {"method_version", "data_origin", "status", "n_raw"} <= set(df.columns)
    assert set(df["data_origin"]) == {"SYNTHETIC"}
    g = client.post("/api/export", json={**REQ, "format": "geojson", "content": "cells"}).json()
    assert g["type"] == "FeatureCollection" and g["properties"]["data_origin"] == "SYNTHETIC"
    bad = client.post("/api/export", json={**REQ, "format": "geojson", "content": "series"})
    assert bad.status_code == 400


def test_upload_import_and_idempotency(client):
    data = (FIXTURES / "SYNTHETIC_tiny_viirs_snpp_sp.csv").read_bytes()
    files = {"file": ("SYNTHETIC_tiny_viirs_snpp_sp.csv", data, "text/csv")}
    r = client.post("/api/import", files=files, data={"dataset": "real", "source_id": "VIIRS_SNPP_SP"})
    rep = r.json()
    assert rep["status"] == "complete" and rep["inserted"] == 8
    again = client.post("/api/import", files=files, data={"dataset": "real", "source_id": "VIIRS_SNPP_SP"}).json()
    assert again["status"] == "already_imported"
    b = client.get(f"/api/batches/{rep['batch_id']}").json()
    assert b["report"]["inserted"] == 8
    bad = client.post("/api/import", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")}, data={"dataset": "real"}).json()
    assert bad["status"] == "rejected_file"


def test_errors_are_explicit(client):
    assert client.post("/api/summary", json={**REQ, "dataset": "fake"}).status_code == 400
    assert client.post("/api/summary", json={**REQ, "grid_km": 25}).status_code == 400
    assert client.post("/api/summary", json={**REQ, "date_end": "2016-01-01"}).status_code == 400
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.post("/api/summary", content=b"not json", headers={"content-type": "application/json"}).status_code == 400
    r = client.post("/api/firms/fetch", json={**REQ, "source": "MODIS_SP"})
    assert r.status_code == 401 and "MAP_KEY" in r.json()["error"]
