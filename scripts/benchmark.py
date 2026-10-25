"""Measure API latency on the current database and write artifacts/performance.json.

Usage (from the repository root, after `ember seed-demo`):
    python scripts/benchmark.py
Timings are wall-clock on the machine where it runs; cold = first call in a
fresh process (no in-memory cache), warm = repeated identical call.
"""

import json
import os
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from starlette.testclient import TestClient  # noqa: E402

from ember_atlas.api.app import create_app  # noqa: E402
from ember_atlas.config import get_settings  # noqa: E402
from ember_atlas.service import EmberService  # noqa: E402
from ember_atlas.store.factory import open_store  # noqa: E402

REQ = {
    "dataset": "synthetic",
    "aoi": "pilot-cerrado",
    "date_start": "2010-01-01",
    "date_end": "2022-12-31",
    "grid_km": 18,
    "window": "week",
    "platforms": ["Terra", "Aqua", "Suomi-NPP", "NOAA-20"],
    "streams": ["SP"],
    "min_confidence": "nominal",
    "reference": "Aqua",
    "source": "Suomi-NPP",
}


def timed(client, method, path, body=None):
    t = time.perf_counter()
    r = client.request(method, path, json=body)
    dt = time.perf_counter() - t
    r.raise_for_status()
    return round(dt, 3)


def main():
    store = open_store(os.environ.get("EMBER_DATABASE_URL", get_settings().database_url))
    store.migrate()
    # disable the persistent result cache for the cold measurements
    svc = EmberService(store)
    svc._cached = lambda key: svc._mem.get(key)  # type: ignore[method-assign]
    svc._put = lambda key, value: svc._mem.__setitem__(key, value) or value  # type: ignore[method-assign]
    client = TestClient(create_app(svc, frontend_dist=None))
    calls = [
        ("summary weekly 13 years, 4 platforms", "POST", "/api/summary", REQ),
        ("summary daily 1 year", "POST", "/api/summary", {**REQ, "window": "day", "date_start": "2019-01-01", "date_end": "2019-12-31"}),
        ("map cells for one week", "POST", "/api/map/cells", {**REQ, "window_from": "2019-08-13", "window_to": "2019-08-19"}),
        ("detections for one week (page 1)", "POST", "/api/map/detections", {**REQ, "window_from": "2019-08-13", "window_to": "2019-08-19", "page_size": 3000}),
        ("harmonised series (calibration + 200 bootstrap)", "POST", "/api/harmonized", REQ),
        ("anomalies one year", "POST", "/api/anomalies", {**REQ, "date_start": "2019-01-01", "date_end": "2019-12-31", "view": ["2019-01-01", "2019-12-31"]}),
    ]
    rows = []
    for label, m, p, body in calls:
        cold = timed(client, m, p, body)
        warm = timed(client, m, p, body)
        rows.append({"call": label, "cold_s": cold, "warm_s": warm})
        print(f"{label:55s} cold {cold:7.2f}s  warm {warm:6.3f}s")
    n = sum(s["detections"] for s in store.dataset_summary("synthetic")["series"])
    out = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "store": store.kind,
        "detections_in_dataset": n,
        "machine": {"python": sys.version.split()[0], "platform": platform.platform(), "cpus": os.cpu_count()},
        "rows": rows,
        "note": "Cold = first request in a fresh process, including loading the dataset into the in-memory frame cache on the first call.",
    }
    (ROOT / "artifacts").mkdir(exist_ok=True)
    (ROOT / "artifacts" / "performance.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
