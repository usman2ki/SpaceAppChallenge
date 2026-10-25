"""Area-of-interest (AOI) polygons: parsing, validation, antimeridian handling,
exact point-in-polygon filtering, equal-area area, and grid-cell overlap.

Design notes
------------
* GeoJSON (RFC 7946) edges are straight lines in lon/lat. We follow that
  convention for membership tests, and densify edges before projecting to the
  equal-area plane for area calculation.
* An AOI is represented as a list of polygons, each a list of rings
  (shell first, then holes), every ring an (N, 2) float array of lon/lat with
  the closing vertex removed.
* Rings that cross the antimeridian are unwrapped (consecutive longitude jumps
  > 180 degrees) and split into an eastern and a western part, both expressed
  in [-180, 180]. This is done explicitly; we never silently produce a polygon
  that wraps the "long way" round the globe.
* Invalid polygons (self-intersections, too few vertices, zero area, holes
  outside the shell, non-finite or out-of-range coordinates) are rejected with
  a specific message. We do not auto-repair: a repaired polygon may not be the
  area the user meant.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import numpy as np

from . import equal_area as ea

MAX_AOI_AREA_KM2 = 5_000_000.0  # guard against accidental continent-scale queries


class AOIError(ValueError):
    """Raised for invalid AOI input; message is safe to show to users."""


@dataclass
class AOI:
    polygons: list[list[np.ndarray]]
    crosses_antimeridian: bool = False
    warnings: list[str] = field(default_factory=list)
    name: str | None = None

    # ------------------------------------------------------------------ queries
    def bboxes(self) -> list[tuple[float, float, float, float]]:
        """One (west, south, east, north) bbox per polygon part, never wrapping."""
        out = []
        for rings in self.polygons:
            shell = rings[0]
            out.append(
                (
                    float(shell[:, 0].min()),
                    float(shell[:, 1].min()),
                    float(shell[:, 0].max()),
                    float(shell[:, 1].max()),
                )
            )
        return out

    def contains(self, lon, lat) -> np.ndarray:
        lon = np.asarray(lon, dtype=float)
        lat = np.asarray(lat, dtype=float)
        inside = np.zeros(lon.shape, dtype=bool)
        for rings, (w, s, e, n) in zip(self.polygons, self.bboxes()):
            cand = (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n) & ~inside
            if not cand.any():
                continue
            idx = np.nonzero(cand)[0]
            res = _even_odd(lon[idx], lat[idx], rings)
            inside[idx[res]] = True
        return inside

    def area_km2(self) -> float:
        total = 0.0
        for rings in self.polygons:
            a = abs(_ring_area_m2(rings[0]))
            for hole in rings[1:]:
                a -= abs(_ring_area_m2(hole))
            total += a
        return total / 1e6

    def to_geojson(self) -> dict:
        polys = []
        for rings in self.polygons:
            polys.append([_close(r).tolist() for r in rings])
        return {"type": "MultiPolygon", "coordinates": polys}

    def cell_overlaps(self, grid: ea.GridSpec, samples: int = 6) -> dict[str, float]:
        """Return {cell_id: fraction of the cell area inside the AOI} for all
        cells that intersect the AOI. Fractions are estimated by a regular
        samples x samples lattice of points in the cell (equal-area spacing),
        plus an exact edge-crossing test so that thin intersections are not
        missed. Documented approximation: fraction resolution is 1/samples^2."""
        out: dict[str, float] = {}
        offs = (np.arange(samples) + 0.5) / samples
        for rings, (w, s, e, n) in zip(self.polygons, self.bboxes()):
            x0, y0 = ea.forward(np.array([w, e]), np.array([s, n]))
            c0, r1 = grid.col_row(x0[0], y0[1])
            c1, r0 = grid.col_row(x0[1], y0[0])
            cols = np.arange(int(c0), int(c1) + 1)
            rows = np.arange(int(r1), int(r0) + 1)
            if cols.size * rows.size > 200_000:
                raise AOIError("AOI is too large for the selected grid size; choose a coarser grid.")
            cc, rr = np.meshgrid(cols, rows, indexing="ij")
            cc, rr = cc.ravel(), rr.ravel()
            # lattice points: (ncells, samples*samples)
            ox, oy = np.meshgrid(offs, offs, indexing="ij")
            px = ea.X_MIN + (cc[:, None] + ox.ravel()[None, :]) * grid.cell_m
            py = ea.Y_MAX - (rr[:, None] + oy.ravel()[None, :]) * grid.cell_m
            lon, lat = ea.inverse(px.ravel(), py.ravel())
            inside = _even_odd(lon, lat, rings).reshape(px.shape)
            frac = inside.mean(axis=1)
            # cells crossed by the boundary but missed by the lattice (thin slivers)
            edge_cells = set()
            for ring in rings:
                d = _densify(ring, max_step_deg=max(0.002, grid.cell_m / 4 / 111_000))
                ex, ey = ea.forward(d[:, 0], d[:, 1])
                ec, er = grid.col_row(ex, ey)
                edge_cells.update(zip(ec.tolist(), er.tolist()))
            for i in range(len(cc)):
                f = float(frac[i])
                if f == 0.0 and (int(cc[i]), int(rr[i])) in edge_cells:
                    f = 1.0 / (samples * samples) / 2.0  # non-zero, below lattice resolution
                if f > 0:
                    cid = str(ea.encode_ids(grid.size_km, cc[i], rr[i]))
                    out[cid] = min(1.0, out.get(cid, 0.0) + f)
        return out


# ---------------------------------------------------------------------- parsing


def parse_aoi(obj, name: str | None = None) -> AOI:
    """Parse GeoJSON (Polygon / MultiPolygon / Feature / FeatureCollection with
    polygonal geometries) or a bbox list [west, south, east, north]."""
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except json.JSONDecodeError as exc:
            raise AOIError(f"AOI is not valid JSON: {exc.msg}") from None
    if isinstance(obj, (list, tuple)) and len(obj) == 4 and all(isinstance(v, (int, float)) for v in obj):
        return aoi_from_bbox(*[float(v) for v in obj], name=name)
    if not isinstance(obj, dict) or "type" not in obj:
        raise AOIError("AOI must be a GeoJSON object or a [west, south, east, north] bbox.")

    geoms: list[dict] = []
    t = obj["type"]
    if t == "FeatureCollection":
        for f in obj.get("features", []):
            if f and f.get("geometry"):
                geoms.append(f["geometry"])
        name = name or _feature_name(obj.get("features", [{}])[0] if obj.get("features") else {})
    elif t == "Feature":
        if not obj.get("geometry"):
            raise AOIError("Feature has no geometry.")
        geoms.append(obj["geometry"])
        name = name or _feature_name(obj)
    else:
        geoms.append(obj)

    polygons: list[list[list]] = []
    for g in geoms:
        gt = g.get("type")
        if gt == "Polygon":
            polygons.append(g.get("coordinates") or [])
        elif gt == "MultiPolygon":
            polygons.extend(g.get("coordinates") or [])
        elif gt == "GeometryCollection":
            raise AOIError("GeometryCollection AOIs are not supported; use Polygon or MultiPolygon.")
        else:
            raise AOIError(f"Unsupported AOI geometry type '{gt}'. Use Polygon or MultiPolygon.")
    if not polygons:
        raise AOIError("AOI contains no polygons.")
    return build_aoi(polygons, name=name)


def _feature_name(f: dict) -> str | None:
    props = (f or {}).get("properties") or {}
    for k in ("name", "NAME", "title", "id"):
        if props.get(k):
            return str(props[k])
    return None


def aoi_from_bbox(w: float, s: float, e: float, n: float, name: str | None = None) -> AOI:
    if not all(math.isfinite(v) for v in (w, s, e, n)):
        raise AOIError("Bounding box contains non-finite values.")
    if s >= n:
        raise AOIError("Bounding box south must be less than north.")
    if w == e:
        raise AOIError("Bounding box has zero width.")
    if w > e:  # explicit antimeridian-crossing bbox, e.g. [170, -20, -170, -10]
        e = e + 360.0
    ring = [[w, s], [e, s], [e, n], [w, n], [w, s]]
    return build_aoi([[ring]], name=name)


def build_aoi(polygons: list[list[list]], name: str | None = None) -> AOI:
    warnings: list[str] = []
    parts: list[list[np.ndarray]] = []
    crosses = False
    for pi, rings in enumerate(polygons):
        if not rings:
            raise AOIError(f"Polygon {pi + 1} has no rings.")
        arrs = []
        for ri, ring in enumerate(rings):
            arr = _ring_array(ring, pi, ri, warnings)
            arrs.append(arr)
        # unwrap shell and holes consistently relative to the shell's first vertex
        unwrapped, crossed = _unwrap_rings(arrs)
        crosses |= crossed
        for ri, r in enumerate(unwrapped):
            _check_simple(r, pi, ri)
            if abs(_planar_area(r)) < 1e-12:
                raise AOIError(f"Polygon {pi + 1} ring {ri + 1} has zero area.")
        shell = unwrapped[0]
        for hi, hole in enumerate(unwrapped[1:]):
            if not _even_odd(hole[:, 0], hole[:, 1], [shell]).all():
                raise AOIError(f"Polygon {pi + 1}: hole {hi + 1} is not inside its outer ring.")
        if np.any(np.abs(shell[:, 1]) > ea.VALID_LAT):
            raise AOIError(f"Polygon {pi + 1} extends poleward of {ea.VALID_LAT} degrees, outside the equal-area analysis grid.")
        parts.extend(_split_at_antimeridian(unwrapped))
    if crosses:
        warnings.append("AOI crosses the antimeridian; it was split into eastern and western parts.")
    aoi = AOI(polygons=parts, crosses_antimeridian=crosses, warnings=warnings, name=name)
    if not aoi.polygons:
        raise AOIError("AOI is empty after processing.")
    area = aoi.area_km2()
    if area <= 0:
        raise AOIError("AOI has zero area.")
    if area > MAX_AOI_AREA_KM2:
        raise AOIError(f"AOI area {area:,.0f} km^2 exceeds the {MAX_AOI_AREA_KM2:,.0f} km^2 limit for interactive analysis.")
    return aoi


def _ring_array(ring, pi: int, ri: int, warnings: list[str]) -> np.ndarray:
    try:
        arr = np.asarray(ring, dtype=float)
    except (TypeError, ValueError):
        raise AOIError(f"Polygon {pi + 1} ring {ri + 1} has non-numeric coordinates.") from None
    if arr.ndim != 2 or arr.shape[1] < 2:
        raise AOIError(f"Polygon {pi + 1} ring {ri + 1} must be a list of [lon, lat] positions.")
    arr = arr[:, :2]
    if not np.isfinite(arr).all():
        raise AOIError(f"Polygon {pi + 1} ring {ri + 1} contains non-finite coordinates.")
    if np.any(np.abs(arr[:, 1]) > 90):
        raise AOIError(f"Polygon {pi + 1} ring {ri + 1} has latitude outside [-90, 90].")
    if np.any(np.abs(arr[:, 0]) > 540):
        raise AOIError(f"Polygon {pi + 1} ring {ri + 1} has longitude outside [-540, 540].")
    if len(arr) >= 2 and np.allclose(arr[0], arr[-1]):
        arr = arr[:-1]
    elif len(arr) >= 3:
        warnings.append(f"Polygon {pi + 1} ring {ri + 1} was not closed; it was closed automatically.")
    # drop consecutive duplicate vertices
    keep = np.ones(len(arr), dtype=bool)
    keep[1:] = np.any(np.abs(np.diff(arr, axis=0)) > 0, axis=1)
    arr = arr[keep]
    if len(arr) < 3:
        raise AOIError(f"Polygon {pi + 1} ring {ri + 1} needs at least 3 distinct vertices.")
    return arr


def _unwrap_rings(rings: list[np.ndarray]) -> tuple[list[np.ndarray], bool]:
    crossed = False
    out = []
    ref = rings[0][0, 0]
    for r in rings:
        lon = r[:, 0].copy()
        d = np.diff(lon)
        jumps = np.abs(d) > 180.0
        if jumps.any():
            crossed = True
        # sequential unwrap so consecutive vertices never differ by > 180
        lon[1:] = lon[0] + np.cumsum(d - 360.0 * np.round(d / 360.0))
        # keep holes near the shell
        shift = 360.0 * np.round((lon.mean() - ref) / 360.0) if out else 0.0
        lon -= shift
        out.append(np.column_stack([lon, r[:, 1]]))
    # also count explicit >180 coordinates (already unwrapped input) as crossing
    allx = np.concatenate([r[:, 0] for r in out])
    if allx.max() > 180.0 or allx.min() < -180.0:
        crossed = True
    if allx.max() - allx.min() >= 360.0:
        raise AOIError("AOI spans 360 degrees of longitude or more; split it into smaller areas.")
    return out, crossed


def _split_at_antimeridian(rings: list[np.ndarray]) -> list[list[np.ndarray]]:
    allx = np.concatenate([r[:, 0] for r in rings])
    lo, hi = allx.min(), allx.max()
    windows = []
    for k in range(int(math.floor((lo + 180) / 360)), int(math.floor((hi + 180) / 360)) + 1):
        windows.append(k)
    parts = []
    for k in windows:
        xmin, xmax = -180.0 + 360.0 * k, 180.0 + 360.0 * k
        clipped = []
        for r in rings:
            c = _clip_x(r, xmin, xmax)
            if c is not None:
                c = c.copy()
                c[:, 0] -= 360.0 * k
                clipped.append(c)
            elif r is rings[0]:
                clipped = []
                break
        if clipped and abs(_planar_area(clipped[0])) > 1e-12:
            parts.append(clipped)
    return parts


def _clip_x(ring: np.ndarray, xmin: float, xmax: float):
    """Sutherland-Hodgman clip of a ring to xmin <= x <= xmax."""
    pts = ring
    for bound, keep_ge in ((xmin, True), (xmax, False)):
        if len(pts) == 0:
            return None
        out = []
        n = len(pts)
        for i in range(n):
            cur, prev = pts[i], pts[i - 1]
            cin = cur[0] >= bound if keep_ge else cur[0] <= bound
            pin = prev[0] >= bound if keep_ge else prev[0] <= bound
            if cin:
                if not pin:
                    out.append(_x_intersect(prev, cur, bound))
                out.append(cur)
            elif pin:
                out.append(_x_intersect(prev, cur, bound))
        pts = np.array(out) if out else np.empty((0, 2))
    if len(pts) < 3:
        return None
    return pts


def _x_intersect(p, q, x):
    t = (x - p[0]) / (q[0] - p[0])
    return np.array([x, p[1] + t * (q[1] - p[1])])


def _check_simple(ring: np.ndarray, pi: int, ri: int) -> None:
    """Reject self-intersecting rings (O(n^2) segment test, fine for AOIs)."""
    n = len(ring)
    if n > 5000:
        raise AOIError(f"Polygon {pi + 1} ring {ri + 1} has more than 5000 vertices; simplify it first.")
    p = ring
    q = np.roll(ring, -1, axis=0)
    for i in range(n):
        # segments j that are not adjacent to i
        js = np.arange(i + 2, n)
        if i == 0:
            js = js[js != n - 1]
        if js.size == 0:
            continue
        if _segments_intersect(p[i], q[i], p[js], q[js]).any():
            raise AOIError(
                f"Polygon {pi + 1} ring {ri + 1} is self-intersecting (edge {i + 1} crosses another edge). Redraw the boundary without crossing lines."
            )


def _segments_intersect(a, b, c, d) -> np.ndarray:
    def orient(p, q, r):
        return (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])

    a = np.broadcast_to(a, c.shape)
    b = np.broadcast_to(b, c.shape)
    o1, o2 = orient(a, b, c), orient(a, b, d)
    o3, o4 = orient(c, d, a), orient(c, d, b)
    proper = (o1 * o2 < 0) & (o3 * o4 < 0)

    def on_seg(p, q, r, o):
        return (
            (np.abs(o) < 1e-12)
            & (np.minimum(p[..., 0], q[..., 0]) <= r[..., 0] + 1e-12)
            & (r[..., 0] <= np.maximum(p[..., 0], q[..., 0]) + 1e-12)
            & (np.minimum(p[..., 1], q[..., 1]) <= r[..., 1] + 1e-12)
            & (r[..., 1] <= np.maximum(p[..., 1], q[..., 1]) + 1e-12)
        )

    touching = on_seg(a, b, c, o1) | on_seg(a, b, d, o2) | on_seg(c, d, a, o3) | on_seg(c, d, b, o4)
    return proper | touching


def _even_odd(x: np.ndarray, y: np.ndarray, rings: list[np.ndarray]) -> np.ndarray:
    """Vectorised even-odd point-in-polygon over shell + holes."""
    inside = np.zeros(x.shape, dtype=bool)
    for ring in rings:
        xi, yi = ring[:, 0], ring[:, 1]
        xj, yj = np.roll(xi, 1), np.roll(yi, 1)
        for k in range(len(xi)):
            cond = (yi[k] > y) != (yj[k] > y)
            if not cond.any():
                continue
            with np.errstate(divide="ignore", invalid="ignore"):
                xcross = (xj[k] - xi[k]) * (y - yi[k]) / (yj[k] - yi[k]) + xi[k]
            inside ^= cond & (x < xcross)
    return inside


def _planar_area(ring: np.ndarray) -> float:
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _densify(ring: np.ndarray, max_step_deg: float = 0.05) -> np.ndarray:
    out = []
    n = len(ring)
    for i in range(n):
        p, q = ring[i], ring[(i + 1) % n]
        steps = max(1, int(math.ceil(np.max(np.abs(q - p)) / max_step_deg)))
        t = np.arange(steps)[:, None] / steps
        out.append(p + t * (q - p))
    return np.vstack(out)


def _ring_area_m2(ring: np.ndarray) -> float:
    d = _densify(ring)
    x, y = ea.forward(d[:, 0], d[:, 1])
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _close(r: np.ndarray) -> np.ndarray:
    return np.vstack([r, r[:1]])
