import hashlib

import pytest
from conftest import small_spec

from ember_atlas.ingest.firms_api import FirmsClient, FirmsError, area_url, redact
from ember_atlas.service import EmberService, ServiceError
from ember_atlas.store.sqlite_store import SqliteStore
from ember_atlas.synthetic.generator import firms_csv_by_product, generate

KEY = "abcdef0123456789abcdef0123456789"
HEADER = b"latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"


class FakeFirms:
    def __init__(self, responses=None):
        self.urls = []
        self.responses = list(responses or [])

    def __call__(self, url):
        self.urls.append(url)
        if self.responses:
            return self.responses.pop(0)
        date = url.rstrip("/").split("/")[-1]
        return 200, HEADER + f"-10.1,-47.2,330.1,0.39,0.36,{date},1318,N,VIIRS,n,2.0NRT,298.0,5.2,D\n".encode()


def test_area_url_matches_documented_pattern():
    u = area_url(KEY, "VIIRS_SNPP_NRT", (-48.5, -11.5, -45.5, -9.0), 5, "2026-10-01")
    assert u == f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{KEY}/VIIRS_SNPP_NRT/-48.5000,-11.5000,-45.5000,-9.0000/5/2026-10-01"
    with pytest.raises(FirmsError):
        area_url(KEY, "VIIRS_SNPP_NRT", (170, 0, -170, 10), 1, "2026-10-01")  # antimeridian must be split first
    with pytest.raises(FirmsError):
        area_url(KEY, "BOGUS", (0, 0, 1, 1), 1, "2026-10-01")


def test_chunks_of_five_days_cache_and_merge(tmp_path):
    fake = FakeFirms()
    c = FirmsClient(KEY, cache_dir=tmp_path, fetch=fake, sleep=lambda s: None)
    data, chunks = c.area_csv("VIIRS_SNPP_NRT", (-48, -11, -46, -9), "2026-10-01", "2026-10-12")
    assert chunks == ["2026-10-01 +5d", "2026-10-06 +5d", "2026-10-11 +2d"]
    assert data.count(b"latitude") == 1 and data.count(b"\n") == 4
    assert not any(KEY in p.name for p in tmp_path.iterdir())  # key never in cache file names
    c2 = FirmsClient(KEY, cache_dir=tmp_path, fetch=FakeFirms(), sleep=lambda s: None)
    c2.area_csv("VIIRS_SNPP_NRT", (-48, -11, -46, -9), "2026-10-01", "2026-10-12")
    assert c2.cache_hits == 3 and c2.requests_made == 0


def test_backoff_on_rate_limit_and_budget():
    sleeps = []
    fake = FakeFirms([(429, b"Too many requests"), (503, b"busy")])
    c = FirmsClient(KEY, fetch=fake, sleep=sleeps.append)
    c.area_csv("VIIRS_SNPP_NRT", (-48, -11, -46, -9), "2026-10-01", "2026-10-01")
    assert sleeps[:2] == [2.0, 4.0] and c.requests_made == 3
    c3 = FirmsClient(KEY, fetch=FakeFirms(), sleep=lambda s: None, max_requests=2)
    with pytest.raises(FirmsError, match="budget"):
        c3.area_csv("VIIRS_SNPP_NRT", (-48, -11, -46, -9), "2026-10-01", "2026-10-30")


def test_key_is_redacted_from_errors():
    fake = FakeFirms([(400, f"Invalid request for key {KEY}".encode())])
    c = FirmsClient(KEY, fetch=fake, sleep=lambda s: None, max_retries=0)
    with pytest.raises(FirmsError) as e:
        c.area_csv("VIIRS_SNPP_NRT", (-48, -11, -46, -9), "2026-10-01", "2026-10-01")
    assert KEY not in str(e.value) and "***MAP_KEY***" in str(e.value)
    assert redact(f"x {KEY} y", KEY) == "x ***MAP_KEY*** y"
    c2 = FirmsClient(KEY, fetch=FakeFirms([(200, b"Invalid MAP_KEY.")]), sleep=lambda s: None)
    with pytest.raises(FirmsError, match="rejected the MAP_KEY"):
        c2.area_csv("VIIRS_SNPP_NRT", (-48, -11, -46, -9), "2026-10-01", "2026-10-01")
    with pytest.raises(FirmsError):
        FirmsClient("  ", fetch=FakeFirms())


def test_service_fetch_imports_real_data_without_storing_key(tmp_path):
    st = SqliteStore(":memory:")
    st.migrate()
    svc = EmberService(st)
    res = svc.fetch_firms(
        {"source": "VIIRS_SNPP_NRT", "aoi": [-48, -11, -46, -9], "date_start": "2026-10-01", "date_end": "2026-10-03"},
        KEY,
        cache_dir=tmp_path,
        fetch=FakeFirms(),
    )
    rep = res["imports"][0]
    assert rep["status"] == "complete" and rep["data_origin"] == "real" and rep["processing_streams"] == {"NRT": 1}
    b = st.get_batch(rep["batch_id"])
    assert "<MAP_KEY>" in b["source_url"] and KEY not in str(b)
    assert KEY not in st.conn.execute("SELECT group_concat(report_json) FROM import_batches").fetchone()[0]
    with pytest.raises(ServiceError) as e:
        svc.fetch_firms({"source": "VIIRS_SNPP_NRT", "aoi": [-48, -11, -46, -9], "date_start": "2026-10-01", "date_end": "2026-10-01"}, None)
    assert e.value.status == 401


def test_synthetic_world_is_deterministic():
    a = firms_csv_by_product(generate(small_spec()))
    b = firms_csv_by_product(generate(small_spec()))
    c = firms_csv_by_product(generate(small_spec(seed=99)))
    h = lambda d: hashlib.sha256(b"".join(d[k] for k in sorted(d))).hexdigest()  # noqa: E731
    assert h(a) == h(b) != h(c)
    w = generate(small_spec())
    assert (w.detections["acq_date"] <= "2019-12-31").all()
    aqua = w.detections[w.detections["_platform"] == "Aqua"]
    assert aqua["acq_date"].max() <= "2018-12-31"
