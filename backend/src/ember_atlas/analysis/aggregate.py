"""Common-grid / common-window aggregation with explicit observation semantics.

Metrics (never interchangeable, none is burned area or a count of fires):
* n_raw          - all stored detections (fire pixels) of a platform
* n_quality      - detections passing the versioned quality filter
* active_cells   - number of distinct equal-area cells with >= 1 quality detection
* frp_sum_mw     - sum of pixel FRP (MW) of quality detections that report FRP;
                   pixel FRP sums are sensor-resolution dependent and are shown
                   per platform only, never pooled.

Observation status of a (window, platform) cell in the calendar:
* not_queried                     - no import covers this platform/date for the AOI
* detections                      - at least one detection
* observed_none                   - zero detections and valid-observation data
                                    show the AOI was observed (>= threshold)
* partially_observed_none         - zero detections, AOI only partly observed
* unobserved                      - zero detections and no valid observation
* none_reported_coverage_unknown  - zero detections, but no valid-observation
                                    data: absence of fire CANNOT be inferred
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..geo import equal_area as ea
from ..geo.polygons import AOI
from ..ingest.firms_schema import CONFIDENCE_ORDER
from ..store.base import DetectionQuery, Store
from .windows import window_start, window_table

QUERY_COLUMNS = [
    "batch_id",
    "platform",
    "instrument",
    "processing_stream",
    "source_product",
    "version_raw",
    "acq_datetime_utc",
    "acq_date",
    "latitude",
    "longitude",
    "x_ease",
    "y_ease",
    "confidence_class",
    "confidence_raw",
    "daynight",
    "frp_mw",
    "scan_km",
    "track_km",
    "footprint_km2",
    "fire_type",
]


@dataclass
class Selection:
    data_origin: str
    aoi: AOI
    date_start: str
    date_end: str
    platforms: list[str]
    streams: list[str] | None
    grid_km: int
    window: str
    cfg: dict
    aoi_label: str | None = None
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def grid(self) -> ea.GridSpec:
        return ea.GridSpec(self.grid_km)

    def key(self) -> dict:
        return {
            "origin": self.data_origin,
            "aoi": self.aoi.to_geojson(),
            "dates": [self.date_start, self.date_end],
            "platforms": sorted(self.platforms),
            "streams": sorted(self.streams) if self.streams else None,
            "grid_km": self.grid_km,
            "window": self.window,
        }


def quality_mask(df: pd.DataFrame, cfg: dict) -> np.ndarray:
    q = cfg["quality"]
    min_rank = CONFIDENCE_ORDER[q["min_confidence"]]
    rank = df["confidence_class"].map(CONFIDENCE_ORDER)
    ok = (rank >= min_rank).fillna(False).to_numpy()
    ftype = pd.to_numeric(df["fire_type"], errors="coerce")
    ok = ok & ~ftype.isin(q["exclude_fire_types"]).to_numpy()
    ok = ok & df["x_ease"].notna().to_numpy()
    return ok


def load(store: Store, sel: Selection) -> pd.DataFrame:
    if "det" in sel._cache:
        return sel._cache["det"]
    df = store.query_detections(DetectionQuery(sel.data_origin, sel.aoi, sel.date_start, sel.date_end, sel.platforms, sel.streams, QUERY_COLUMNS))
    if len(df):
        df["quality"] = quality_mask(df, sel.cfg)
        ok = df["x_ease"].notna()
        cid = pd.Series([None] * len(df), dtype=object)
        if ok.any():
            cid[ok.to_numpy()] = sel.grid.cell_ids(df.loc[ok, "x_ease"].to_numpy(), df.loc[ok, "y_ease"].to_numpy())
        df["cell_id"] = cid.to_numpy()
        df["window_start"] = window_start(df["acq_date"], sel.window).to_numpy()
    else:
        df = pd.DataFrame(columns=QUERY_COLUMNS + ["quality", "cell_id", "window_start"])
    sel._cache["det"] = df
    return df


# ---------------------------------------------------------------------- availability


def _bbox_covers(query_bbox, aoi: AOI) -> bool:
    if not query_bbox:
        return True
    w, s, e, n = query_bbox
    for aw, as_, ae, an in aoi.bboxes():
        if not (aw >= w - 1e-9 and ae <= e + 1e-9 and as_ >= s - 1e-9 and an <= n + 1e-9):
            return False
    return True


def queried_days(store: Store, sel: Selection, windows: pd.DataFrame) -> pd.DataFrame:
    """Per (window, platform): number of days covered by an import of that
    platform whose query area contains the AOI, and the basis of that claim."""
    av = store.availability(sel.data_origin)
    rows = []
    days = pd.date_range(sel.date_start, sel.date_end, freq="D")
    wstart = window_start(days, sel.window).to_numpy()
    for p in sel.platforms:
        sub = av[av["platform"] == p]
        if sel.streams:
            sub = sub[sub["processing_stream"].isin(sel.streams)]
        mask = np.zeros(len(days), dtype=bool)
        partial = np.zeros(len(days), dtype=bool)
        inferred = np.zeros(len(days), dtype=bool)
        for _, a in sub.iterrows():
            in_range = (days >= pd.Timestamp(a["date_start"])) & (days <= pd.Timestamp(a["date_end"]))
            bbox = json.loads(a["bbox"]) if isinstance(a["bbox"], str) and a["bbox"] else None
            if _bbox_covers(bbox, sel.aoi):
                mask |= in_range
            else:
                partial |= in_range
            if a["basis"] != "declared_query":
                inferred |= in_range
        dfp = pd.DataFrame({"window_start": wstart, "q": mask, "partial": partial & ~mask, "inferred": inferred & mask})
        g = dfp.groupby("window_start").agg(queried_days=("q", "sum"), partial_days=("partial", "sum"), inferred_days=("inferred", "sum"))
        g = g.reset_index()
        g["platform"] = p
        rows.append(g)
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["window_start", "platform"])
    return windows[["window_start"]].merge(out, on="window_start", how="left")


# ---------------------------------------------------------------------- coverage


def coverage_set_for(store: Store, origin: str) -> dict | None:
    sets = [c for c in store.coverage_sets(origin) if c["quantity"] == "valid_looks"]
    return sets[-1] if sets else None


def landcover_set_for(store: Store, origin: str) -> dict | None:
    sets = [c for c in store.coverage_sets(origin) if c["quantity"] == "landcover"]
    return sets[-1] if sets else None


def cell_landcover(store: Store, origin: str, grid: ea.GridSpec, cell_ids: list[str]) -> pd.Series | None:
    """Land-cover class per analysis cell: inherited from a coarser land-cover
    grid, or the majority class of child cells on a finer one."""
    ls = landcover_set_for(store, origin)
    if ls is None or not cell_ids:
        return None
    lgrid = ea.GridSpec(int(ls["grid_size_km"]))
    parts = pd.Series(cell_ids).str.split(":", expand=True)
    col, row = parts[1].astype(int).to_numpy(), parts[2].astype(int).to_numpy()
    if lgrid.factor >= grid.factor:
        pc, pr = grid.parent(col, row, lgrid)
        link = pd.DataFrame({"cell_id": cell_ids, "lc_id": ea.encode_ids(lgrid.size_km, pc, pr)})
    else:
        ratio = grid.factor // lgrid.factor
        link = pd.concat(
            [
                pd.DataFrame({"cell_id": cell_ids, "lc_id": ea.encode_ids(lgrid.size_km, col * ratio + dc, row * ratio + dr)})
                for dc in range(ratio)
                for dr in range(ratio)
            ],
            ignore_index=True,
        )
    lc = store.query_landcover(ls["id"], sorted(set(link["lc_id"]))).rename(columns={"cell_id": "lc_id"})
    m = link.merge(lc, on="lc_id", how="inner")
    if m.empty:
        return None
    maj = m.groupby("cell_id")["lc_class"].agg(lambda s: s.value_counts().index[0])
    return maj.reindex(cell_ids)


def aoi_coverage(store: Store, sel: Selection, windows: pd.DataFrame) -> pd.DataFrame | None:
    """AOI-level valid-observation coverage per (window, platform), or None
    when no coverage data exists (the common case for FIRMS-only imports)."""
    cs = coverage_set_for(store, sel.data_origin)
    if cs is None:
        return None
    cgrid = ea.GridSpec(int(cs["grid_size_km"]))
    weights = sel.aoi.cell_overlaps(cgrid)
    cov = store.query_coverage(cs["id"], list(weights), sel.date_start, sel.date_end, sel.platforms)
    days = pd.date_range(sel.date_start, sel.date_end, freq="D").strftime("%Y-%m-%d")
    total_w = sum(weights.values())
    rows = []
    for p in sel.platforms:
        c = cov[cov["platform"] == p].copy()
        if c.empty:
            per_day = pd.DataFrame({"date": days, "vf": np.nan, "looks": np.nan, "wknown": 0.0})
        else:
            c["w"] = c["cell_id"].map(weights)
            c["vf"] = np.clip(c["valid_looks"] / c["nominal_looks"].replace(0, np.nan), 0, 1).fillna(0) * c["w"]
            c["lk"] = c["valid_looks"] * c["w"]
            g = c.groupby("date").agg(vf=("vf", "sum"), lk=("lk", "sum"), wknown=("w", "sum")).reindex(days)
            per_day = pd.DataFrame(
                {
                    "date": days,
                    "vf": (g["vf"] / g["wknown"]).to_numpy(),
                    "looks": (g["lk"] / g["wknown"]).to_numpy(),
                    "wknown": g["wknown"].fillna(0).to_numpy() / total_w,
                }
            )
        per_day["window_start"] = window_start(per_day["date"], sel.window).to_numpy()
        per_day["known"] = per_day["wknown"] >= 0.999
        agg = per_day.groupby("window_start").agg(
            valid_fraction=("vf", "mean"), valid_looks_mean=("looks", "mean"), valid_looks_sum=("looks", "sum"), known_days=("known", "sum")
        )
        agg = agg.reset_index()
        agg["platform"] = p
        rows.append(agg)
    out = pd.concat(rows, ignore_index=True)
    out = windows[["window_start", "n_days"]].merge(out, on="window_start", how="left")
    out["coverage_known"] = out["known_days"] / out["n_days"] >= sel.cfg["coverage"]["min_known_day_fraction"]
    out["coverage_set_id"] = cs["id"]
    return out.drop(columns=["n_days"])


# ---------------------------------------------------------------------- series


def platform_window_series(store: Store, sel: Selection) -> tuple[pd.DataFrame, dict]:
    df = load(store, sel)
    windows = window_table(sel.date_start, sel.date_end, sel.window)
    base = windows.merge(pd.DataFrame({"platform": sel.platforms}), how="cross")
    if len(df):
        g = df.groupby(["window_start", "platform"])
        raw = g.size().rename("n_raw")
        qdf = df[df["quality"]]
        gq = qdf.groupby(["window_start", "platform"])
        stats = pd.concat(
            [
                raw,
                gq.size().rename("n_quality"),
                gq["cell_id"].nunique().rename("active_cells"),
                gq["frp_mw"].sum(min_count=1).rename("frp_sum_mw"),
                gq["frp_mw"].count().rename("frp_n"),
                qdf[qdf["daynight"] == "D"].groupby(["window_start", "platform"]).size().rename("n_day"),
                qdf[qdf["daynight"] == "N"].groupby(["window_start", "platform"]).size().rename("n_night"),
            ],
            axis=1,
        ).reset_index()
        base = base.merge(stats, on=["window_start", "platform"], how="left")
    for c in ["n_raw", "n_quality", "active_cells", "frp_n", "n_day", "n_night"]:
        if c not in base:
            base[c] = 0
        base[c] = base[c].fillna(0).astype(int)
    if "frp_sum_mw" not in base:
        base["frp_sum_mw"] = np.nan

    q = queried_days(store, sel, windows)
    base = base.merge(q, on=["window_start", "platform"], how="left")
    for c in ["queried_days", "partial_days", "inferred_days"]:
        base[c] = base[c].fillna(0).astype(int)

    cov = aoi_coverage(store, sel, windows)
    if cov is not None:
        base = base.merge(cov, on=["window_start", "platform"], how="left")
        base["coverage_known"] = base["coverage_known"].fillna(False).astype(bool)
    else:
        base["coverage_known"] = False
        base["valid_fraction"] = np.nan
        base["valid_looks_sum"] = np.nan

    thr = sel.cfg["coverage"]["observed_fraction"]
    status = np.full(len(base), "none_reported_coverage_unknown", dtype=object)
    zero = base["n_raw"].to_numpy() == 0
    known = base["coverage_known"].to_numpy()
    vf = base["valid_fraction"].to_numpy(dtype=float)
    status[~zero] = "detections"
    status[zero & known & (vf >= thr)] = "observed_none"
    status[zero & known & (vf > 0) & (vf < thr)] = "partially_observed_none"
    status[zero & known & (vf == 0)] = "unobserved"
    status[base["queried_days"].to_numpy() == 0] = "not_queried"
    # detections can exist outside declared windows (inferred availability edge cases)
    status[(base["queried_days"].to_numpy() == 0) & ~zero] = "detections"
    base["status"] = status
    meta = {
        "coverage_available": cov is not None,
        "coverage_set": coverage_set_for(store, sel.data_origin),
        "n_detections": len(df),
        "n_quality": int(df["quality"].sum()) if len(df) else 0,
        "not_gridded": int(df["cell_id"].isna().sum()) if len(df) else 0,
    }
    return base.sort_values(["window_start", "platform"]).reset_index(drop=True), meta


def cell_aggregates(store: Store, sel: Selection, window_from: str | None = None, window_to: str | None = None) -> pd.DataFrame:
    """Per-cell totals for the map (optionally restricted to a window range)."""
    df = load(store, sel)
    if len(df) and window_from:
        df = df[(df["acq_date"] >= window_from) & (df["acq_date"] <= (window_to or window_from))]
    overlaps = sel.aoi.cell_overlaps(sel.grid)
    cells = pd.DataFrame({"cell_id": list(overlaps), "aoi_fraction": list(overlaps.values())})
    if len(df):
        d = df[df["cell_id"].notna()]
        piv_raw = d.groupby(["cell_id", "platform"]).size().unstack(fill_value=0).add_prefix("raw_")
        dq = d[d["quality"]]
        piv_q = dq.groupby(["cell_id", "platform"]).size().unstack(fill_value=0).add_prefix("q_")
        frp = dq.groupby(["cell_id", "platform"])["frp_mw"].sum(min_count=1).unstack().add_prefix("frp_")
        cells = cells.merge(piv_raw, on="cell_id", how="left").merge(piv_q, on="cell_id", how="left").merge(frp, on="cell_id", how="left")
    for p in sel.platforms:
        for pre in ("raw_", "q_"):
            if pre + p not in cells:
                cells[pre + p] = 0
            cells[pre + p] = cells[pre + p].fillna(0).astype(int)
    return cells


def cell_window_counts(store: Store, sel: Selection, platforms: list[str]) -> pd.DataFrame:
    """Dense (cell x window x platform) quality-filtered detection counts for all
    AOI cells, zeros included. Zeros are 'none reported'; whether they are
    observed zeros depends on coverage (see cell_window_exposure)."""
    df = load(store, sel)
    windows = window_table(sel.date_start, sel.date_end, sel.window)
    overlaps = sel.aoi.cell_overlaps(sel.grid)
    cells = pd.DataFrame({"cell_id": list(overlaps), "aoi_fraction": list(overlaps.values())})
    dense = cells.merge(windows[["window_start", "n_days", "year", "week", "doy_mid"]], how="cross")
    for p in platforms:
        if len(df):
            sub = df[(df["platform"] == p) & df["quality"] & df["cell_id"].notna()]
            cnt = sub.groupby(["cell_id", "window_start"]).size().rename(f"y_{p}")
            dense = dense.merge(cnt.reset_index(), on=["cell_id", "window_start"], how="left")
        else:
            dense[f"y_{p}"] = 0
        dense[f"y_{p}"] = dense[f"y_{p}"].fillna(0).astype(int)
    col_row = dense["cell_id"].str.split(":", expand=True)
    dense["col"] = col_row[1].astype(int)
    dense["row"] = col_row[2].astype(int)
    return dense


def cell_window_exposure(store: Store, sel: Selection, platforms: list[str], dense: pd.DataFrame) -> pd.DataFrame | None:
    """Valid-observation looks per (cell, window, platform), or None if no
    coverage data exists.

    * coverage on a coarser (nesting) grid is inherited by the analysis cells
      inside it - this assumes uniform coverage within the coverage cell and
      is reported as such in the method notes;
    * coverage on a finer grid is averaged over the child cells.
    A window whose coverage record is incomplete (missing days) gets NaN
    exposure: unknown, never zero."""
    cs = coverage_set_for(store, sel.data_origin)
    if cs is None:
        return None
    cgrid = ea.GridSpec(int(cs["grid_size_km"]))
    agrid = sel.grid
    cells = dense[["cell_id", "col", "row"]].drop_duplicates()
    if cgrid.factor >= agrid.factor:
        pc, pr = agrid.parent(cells["col"].to_numpy(), cells["row"].to_numpy(), cgrid)
        link = pd.DataFrame({"cell_id": cells["cell_id"].to_numpy(), "cov_id": ea.encode_ids(cgrid.size_km, pc, pr)})
    else:
        ratio = agrid.factor // cgrid.factor
        parts = []
        for dc in range(ratio):
            for dr in range(ratio):
                parts.append(
                    pd.DataFrame(
                        {
                            "cell_id": cells["cell_id"].to_numpy(),
                            "cov_id": ea.encode_ids(cgrid.size_km, cells["col"].to_numpy() * ratio + dc, cells["row"].to_numpy() * ratio + dr),
                        }
                    )
                )
        link = pd.concat(parts, ignore_index=True)
    cov = store.query_coverage(cs["id"], sorted(set(link["cov_id"])), sel.date_start, sel.date_end, platforms)
    cov = cov.rename(columns={"cell_id": "cov_id"})
    cov["window_start"] = window_start(cov["date"], sel.window).to_numpy()
    per = (
        cov.groupby(["cov_id", "window_start", "platform"])
        .agg(looks=("valid_looks", "sum"), nominal=("nominal_looks", "sum"), days=("date", "nunique"))
        .reset_index()
    )
    per = per.merge(link, on="cov_id")
    per = per.groupby(["cell_id", "window_start", "platform"]).agg(looks=("looks", "mean"), nominal=("nominal", "mean"), days=("days", "min")).reset_index()
    out = dense[["cell_id", "window_start", "n_days"]].copy()
    for p in platforms:
        pp = per[per["platform"] == p][["cell_id", "window_start", "looks", "nominal", "days"]]
        pp = pp.rename(columns={"looks": f"e_{p}", "nominal": f"nom_{p}", "days": f"cdays_{p}"})
        out = out.merge(pp, on=["cell_id", "window_start"], how="left")
        incomplete = out[f"cdays_{p}"].fillna(0) < out["n_days"]
        out.loc[incomplete, [f"e_{p}", f"nom_{p}"]] = np.nan
        out = out.drop(columns=[f"cdays_{p}"])
    return out.drop(columns=["n_days"])
