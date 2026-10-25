"""Import of observation-coverage and land-cover tables.

FIRMS hotspot files contain only detections; they cannot tell whether an
empty place was observed and fire-free (MODIS C6/C6.1 user guide, Sec. 7.1.1:
fire pixel locations include no information about cloud cover or missing
data). Coverage therefore has to come from a separate source, e.g. counts of
valid ("non-fire land" + "fire") pixels per cell and overpass derived from the
MOD14/MYD14 and VNP14IMG fire-mask layers. Ember Atlas does not yet derive
these from Level-2 granules (see docs/status.md); it imports a documented CSV:

coverage CSV columns
  platform       Terra | Aqua | Suomi-NPP | NOAA-20 | NOAA-21
  cell_id        EASE-Grid 2.0 cell id "E{km}:{col}:{row}" at the set's grid size
  date           YYYY-MM-DD (UTC)
  valid_looks    sum over overpasses that day of the fraction of the cell that was
                 validly observed (cloud-free, in swath, processed)
  nominal_looks  number of overpasses the cell would get with no clouds or gaps

land-cover CSV columns
  cell_id, lc_class
"""

from __future__ import annotations

import io

import pandas as pd

from ..geo import equal_area as ea
from ..ingest.firms_schema import INSTRUMENT_OF_PLATFORM
from ..store.base import Store


class CovariateError(ValueError):
    pass


def _check_cells(ids: pd.Series, grid_km: int) -> None:
    bad = ~ids.astype(str).str.fullmatch(rf"E{grid_km}:\d+:\d+")
    if bad.any():
        raise CovariateError(f"{int(bad.sum())} cell ids are not EASE ids at {grid_km} km, e.g. {ids[bad].iloc[0]!r}")


def import_coverage(store: Store, data: bytes | pd.DataFrame, *, name: str, data_origin: str, grid_size_km: int, description: str = "") -> dict:
    ea.GridSpec(grid_size_km)  # validates size
    df = data if isinstance(data, pd.DataFrame) else pd.read_csv(io.BytesIO(data), dtype={"cell_id": str, "date": str})
    need = {"platform", "cell_id", "date", "valid_looks", "nominal_looks"}
    if missing := need - set(df.columns):
        raise CovariateError(f"coverage file missing columns {sorted(missing)}")
    if bad := set(df["platform"]) - set(INSTRUMENT_OF_PLATFORM):
        raise CovariateError(f"unknown platforms {sorted(bad)}")
    _check_cells(df["cell_id"], grid_size_km)
    if pd.to_datetime(df["date"], format="%Y-%m-%d", errors="coerce").isna().any():
        raise CovariateError("coverage dates must be YYYY-MM-DD")
    if (df["valid_looks"] < 0).any() or (df["valid_looks"] > df["nominal_looks"] + 1e-9).any():
        raise CovariateError("valid_looks must be within [0, nominal_looks]")
    set_id = store.create_coverage_set(
        {"name": name, "data_origin": data_origin, "grid_size_km": grid_size_km, "quantity": "valid_looks", "description": description}
    )
    n = store.insert_coverage(set_id, df)
    return {"coverage_set_id": set_id, "rows": n, "platforms": sorted(df["platform"].unique()), "date_range": [df["date"].min(), df["date"].max()]}


def import_landcover(store: Store, data: bytes | pd.DataFrame, *, name: str, data_origin: str, grid_size_km: int, description: str = "") -> dict:
    df = data if isinstance(data, pd.DataFrame) else pd.read_csv(io.BytesIO(data), dtype=str)
    if {"cell_id", "lc_class"} - set(df.columns):
        raise CovariateError("land-cover file needs columns cell_id, lc_class")
    _check_cells(df["cell_id"], grid_size_km)
    set_id = store.create_coverage_set(
        {"name": name, "data_origin": data_origin, "grid_size_km": grid_size_km, "quantity": "landcover", "description": description}
    )
    n = store.insert_landcover(set_id, df)
    return {"landcover_set_id": set_id, "rows": n, "classes": sorted(df["lc_class"].unique())}
