"""Command-line interface: ``ember <command>`` (or ``python -m ember_atlas.cli``)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from .config import PROJECT_ROOT, get_settings
from .store.factory import open_store


def _store():
    s = open_store(get_settings().database_url)
    s.migrate()
    return s


def _aoi_arg(v: str):
    from .presets import PRESET_BY_ID

    if v in PRESET_BY_ID:
        return v
    p = Path(v)
    if p.exists():
        return json.loads(p.read_text())
    try:
        vals = [float(x) for x in v.split(",")]
        if len(vals) == 4:
            return vals
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("AOI must be a preset id, a GeoJSON file path, or west,south,east,north")


def cmd_migrate(a):
    print(json.dumps({"applied": _store().migrate(), "database": get_settings().database_url.split("@")[-1]}))


def cmd_seed_demo(a):
    from .synthetic.seed import describe_world, seed_demo

    store = _store()
    if store.list_batches("synthetic"):
        print("Synthetic demo data already present; nothing to do (delete the database file to reseed).")
        return
    t = time.time()
    res = seed_demo(store, out_dir=Path(a.out) if a.out else None, with_coverage=not a.no_coverage)
    out = {
        "imports": [{k: r[k] for k in ("file_name", "rows_read", "inserted", "rows_rejected")} for r in res["imports"]],
        "coverage": res["coverage"],
        "landcover": {k: v for k, v in (res["landcover"] or {}).items() if k != "classes"},
        "fixture_sha256": res["fixture_sha256"],
        "world": describe_world(res["world"]),
        "seconds": round(time.time() - t, 1),
        "note": "SYNTHETIC demo data - not NASA measurements",
    }
    print(json.dumps(out, indent=2, default=str))


def cmd_import(a):
    from .ingest.importer import import_bytes
    from .ingest.normalize import Declaration

    decl = Declaration(
        data_origin=a.origin,
        source_id=a.source,
        source_url=a.source_url,
        retrieved_at=a.retrieved_at,
        period_start=a.period_start,
        period_end=a.period_end,
        query_bbox=[float(x) for x in a.query_bbox.split(",")] if a.query_bbox else None,
        notes=a.notes,
    )
    for f in a.files:
        rep = import_bytes(_store(), Path(f).read_bytes(), Path(f).name, decl)
        rep.pop("example_issues", None) if not a.verbose else None
        print(json.dumps(rep, indent=2, default=str))


def cmd_import_coverage(a):
    from .ingest.covariates import import_coverage, import_landcover

    fn = import_coverage if a.kind == "coverage" else import_landcover
    print(
        json.dumps(
            fn(
                _store(),
                Path(a.file).read_bytes(),
                name=a.name or Path(a.file).name,
                data_origin=a.origin,
                grid_size_km=a.grid_km,
                description=a.description or "",
            ),
            default=str,
        )
    )


def cmd_fetch_firms(a):
    from .service import EmberService

    key = os.environ.get("FIRMS_MAP_KEY")
    if not key:
        sys.exit("Set the FIRMS_MAP_KEY environment variable (request a free key from NASA: https://firms.modaps.eosdis.nasa.gov/api/map_key/).")
    s = get_settings()
    svc = EmberService(_store())
    res = svc.fetch_firms(
        {"source": a.source, "aoi": _aoi_arg(a.aoi), "date_start": a.start, "date_end": a.end}, key, cache_dir=s.firms_cache_dir, max_requests=a.max_requests
    )
    for r in res["imports"]:
        r.pop("example_issues", None)
    print(json.dumps(res, indent=2, default=str))


def cmd_evaluate(a):
    from .science.experiments import write_evaluation_artifacts
    from .service import EmberService

    svc = EmberService(_store())
    req = {
        "dataset": a.dataset,
        "aoi": _aoi_arg(a.aoi),
        "date_start": "1900-01-01",
        "date_end": "2100-01-01",
        "grid_km": a.grid_km,
        "reference": a.reference,
        "source": a.source,
        "bootstrap_reps": a.bootstrap,
        "streams": a.streams.split(",") if a.streams else None,
    }
    t = time.time()
    ev = svc.run_evaluation(req)
    h = svc.harmonized(req)
    paths = write_evaluation_artifacts(ev, h, Path(a.out))
    print(
        json.dumps(
            {"status": h["status"], "gates_passed": ev["gates"]["passed"], "artifacts": [str(p) for p in paths], "seconds": round(time.time() - t, 1)}, indent=2
        )
    )


def cmd_recovery(a):
    from .science.experiments import synthetic_recovery

    res = synthetic_recovery(Path(a.out), bootstrap_reps=a.bootstrap, quick=a.quick)
    print(json.dumps(res["summary"], indent=2, default=str))


def cmd_sensitivity(a):
    from .science.experiments import sensitivity

    if a.quick:
        res = sensitivity(Path(a.out), quick=True)
    else:
        store = _store()
        if not store.list_batches(a.dataset):
            sys.exit(f"No {a.dataset} data imported; run `ember seed-demo` or import data first.")
        from .presets import PRESET_BY_ID

        aoi = _aoi_arg(a.aoi)
        aoi = PRESET_BY_ID[aoi]["geometry"] if isinstance(aoi, str) else aoi
        res = sensitivity(Path(a.out), store=store, aoi=aoi, data_origin=a.dataset)
    print(json.dumps(res["summary"], indent=2, default=str))


def cmd_serve(a):
    import uvicorn

    from .api.app import create_app

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host=a.host, port=a.port)


def main(argv=None):
    p = argparse.ArgumentParser(prog="ember", description="Ember Atlas - harmonised MODIS/VIIRS hotspot calendars")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="create/upgrade the database schema").set_defaults(fn=cmd_migrate)

    s = sub.add_parser("seed-demo", help="generate and import the deterministic SYNTHETIC demo world")
    s.add_argument("--out", default=str(PROJECT_ROOT / "data" / "synthetic"), help="also write the generated CSVs here")
    s.add_argument("--no-coverage", action="store_true", help="omit synthetic coverage (hotspot-only demo)")
    s.set_defaults(fn=cmd_seed_demo)

    s = sub.add_parser("import", help="import FIRMS CSV files")
    s.add_argument("files", nargs="+")
    s.add_argument("--origin", choices=["real", "synthetic"], required=True)
    s.add_argument("--source", help="FIRMS source id, e.g. MODIS_SP, VIIRS_SNPP_SP")
    s.add_argument("--source-url")
    s.add_argument("--retrieved-at")
    s.add_argument("--period-start", help="first date that was requested from the provider")
    s.add_argument("--period-end", help="last date that was requested")
    s.add_argument("--query-bbox", help="west,south,east,north of the requested area")
    s.add_argument("--notes")
    s.add_argument("--verbose", action="store_true")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("import-covariate", help="import a coverage or land-cover table (see docs/data_dictionary.md)")
    s.add_argument("kind", choices=["coverage", "landcover"])
    s.add_argument("file")
    s.add_argument("--origin", choices=["real", "synthetic"], required=True)
    s.add_argument("--grid-km", type=int, required=True)
    s.add_argument("--name")
    s.add_argument("--description")
    s.set_defaults(fn=cmd_import_coverage)

    s = sub.add_parser("fetch-firms", help="download from the FIRMS Area API (needs FIRMS_MAP_KEY) and import as real data")
    s.add_argument("--source", required=True)
    s.add_argument("--aoi", required=True)
    s.add_argument("--start", required=True)
    s.add_argument("--end", required=True)
    s.add_argument("--max-requests", type=int, default=200)
    s.set_defaults(fn=cmd_fetch_firms)

    s = sub.add_parser("evaluate", help="blocked hold-out validation + harmonisation; writes evaluation artifacts and a model card")
    s.add_argument("--dataset", choices=["real", "synthetic"], default="synthetic")
    s.add_argument("--aoi", default="pilot-cerrado")
    s.add_argument("--grid-km", type=int, default=18)
    s.add_argument("--reference", default="Aqua")
    s.add_argument("--source", default="Suomi-NPP")
    s.add_argument("--bootstrap", type=int, default=None)
    s.add_argument("--streams", default="SP", help="processing streams, comma-separated (default SP: standard products, as in the web app)")
    s.add_argument("--out", default=str(PROJECT_ROOT / "artifacts"))
    s.set_defaults(fn=cmd_evaluate)

    s = sub.add_parser("recovery", help="synthetic recovery experiment with known injected sensor bias")
    s.add_argument("--out", default=str(PROJECT_ROOT / "artifacts"))
    s.add_argument("--bootstrap", type=int, default=100)
    s.add_argument("--quick", action="store_true")
    s.set_defaults(fn=cmd_recovery)

    s = sub.add_parser("sensitivity", help="sensitivity of calibration to grid size, window, quality filter and coverage assumptions")
    s.add_argument("--out", default=str(PROJECT_ROOT / "artifacts"))
    s.add_argument("--dataset", choices=["real", "synthetic"], default="synthetic")
    s.add_argument("--aoi", default="pilot-cerrado")
    s.add_argument("--quick", action="store_true")
    s.set_defaults(fn=cmd_sensitivity)

    s = sub.add_parser("serve", help="run the API and the built web app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
