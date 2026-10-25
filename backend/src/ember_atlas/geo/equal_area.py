"""EASE-Grid 2.0 global equal-area projection (EPSG:6933) and grid indexing.

The projection is the ellipsoidal Lambert cylindrical equal-area projection on
WGS84 with a standard parallel of 30 degrees (Brodzik et al., 2012, ISPRS Int.
J. Geo-Inf. 1(1):32-45; Snyder, 1987, USGS Prof. Paper 1395, pp. 81-85).

Implemented here in NumPy (instead of pyproj) so that the analysis core has no
compiled GIS dependency. ``tests/test_geo.py`` checks it against the published
EASE-Grid 2.0 extent constants and round-trips.

Grid family
-----------
Analysis cells are squares in projected metres aligned to the EASE-Grid 2.0
global origin. The base cell is the official M09 cell (9008.055210146 m,
3856 x 1624 cells). Supported analysis sizes are 1, 2, 4 and 8 base cells
(~9, 18, 36 and 72 km), so every size nests exactly in the next. 9 km and
36 km coincide with the official EASE2_G09km and EASE2_G36km grids.
Because the projection is equal-area, every cell has exactly the same
ground area (size_m ** 2), which keeps active-cell counts comparable across
latitudes inside an AOI.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# WGS84
A = 6378137.0
F = 1.0 / 298.257223563
E2 = F * (2.0 - F)
E = math.sqrt(E2)
PHI1 = math.radians(30.0)

K0 = math.cos(PHI1) / math.sqrt(1.0 - E2 * math.sin(PHI1) ** 2)

# Official EASE-Grid 2.0 M09 constants (NSIDC).
M09_CELL_M = 9008.055210146
M09_COLS = 3856
M09_ROWS = 1624
X_MIN = -M09_COLS * M09_CELL_M / 2.0  # -17367530.44516...
Y_MAX = M09_ROWS * M09_CELL_M / 2.0  # 7314540.83...
# The global grid's y extent corresponds to ~85.044 degrees (checked in tests).
# Points poleward of this cannot be gridded and are reported, never dropped silently.
VALID_LAT = 85.044

SUPPORTED_FACTORS = {9: 1, 18: 2, 36: 4, 72: 8}


def _q(sinphi: np.ndarray) -> np.ndarray:
    esin = E * sinphi
    return (1.0 - E2) * (sinphi / (1.0 - esin**2) - (1.0 / (2.0 * E)) * np.log((1.0 - esin) / (1.0 + esin)))


QP = float(_q(np.array(1.0)))


def forward(lon_deg, lat_deg):
    """Longitude/latitude (degrees, WGS84) -> EASE-Grid 2.0 x/y metres."""
    lon = np.asarray(lon_deg, dtype=float)
    lat = np.asarray(lat_deg, dtype=float)
    lam = np.radians(((lon + 180.0) % 360.0) - 180.0)
    # keep +180 exactly at +180 rather than wrapping to -180
    lam = np.where(np.isclose(lon, 180.0), math.pi, lam)
    x = A * K0 * lam
    y = A * _q(np.sin(np.radians(lat))) / (2.0 * K0)
    return x, y


def inverse(x, y):
    """EASE-Grid 2.0 x/y metres -> longitude/latitude degrees."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    beta = np.arcsin(np.clip(2.0 * y * K0 / (A * QP), -1.0, 1.0))
    e4 = E2 * E2
    e6 = e4 * E2
    phi = (
        beta
        + (E2 / 3.0 + 31.0 * e4 / 180.0 + 517.0 * e6 / 5040.0) * np.sin(2 * beta)
        + (23.0 * e4 / 360.0 + 251.0 * e6 / 3780.0) * np.sin(4 * beta)
        + (761.0 * e6 / 45360.0) * np.sin(6 * beta)
    )
    lon = np.degrees(x / (A * K0))
    return lon, np.degrees(phi)


@dataclass(frozen=True)
class GridSpec:
    """An analysis grid of square equal-area cells."""

    size_km: int

    def __post_init__(self) -> None:
        if self.size_km not in SUPPORTED_FACTORS:
            raise ValueError(f"Unsupported grid size {self.size_km} km; choose one of {sorted(SUPPORTED_FACTORS)}")

    @property
    def factor(self) -> int:
        return SUPPORTED_FACTORS[self.size_km]

    @property
    def cell_m(self) -> float:
        return M09_CELL_M * self.factor

    @property
    def cell_area_km2(self) -> float:
        return (self.cell_m / 1000.0) ** 2

    @property
    def n_cols(self) -> int:
        return M09_COLS // self.factor

    @property
    def n_rows(self) -> int:
        return M09_ROWS // self.factor

    def col_row(self, x, y):
        col = np.floor((np.asarray(x) - X_MIN) / self.cell_m).astype(np.int64)
        row = np.floor((Y_MAX - np.asarray(y)) / self.cell_m).astype(np.int64)
        col = np.clip(col, 0, self.n_cols - 1)
        row = np.clip(row, 0, self.n_rows - 1)
        return col, row

    def cell_ids(self, x, y) -> np.ndarray:
        col, row = self.col_row(x, y)
        return encode_ids(self.size_km, col, row)

    def cell_bounds_xy(self, col: int, row: int) -> tuple[float, float, float, float]:
        x0 = X_MIN + col * self.cell_m
        y1 = Y_MAX - row * self.cell_m
        return x0, y1 - self.cell_m, x0 + self.cell_m, y1

    def cell_bounds_lonlat(self, col: int, row: int) -> tuple[float, float, float, float]:
        """(west, south, east, north). Cylindrical projection => cells are lon/lat rectangles."""
        x0, y0, x1, y1 = self.cell_bounds_xy(col, row)
        (w, e), (s, n) = inverse(np.array([x0, x1]), np.array([y0, y1]))
        return float(w), float(min(s, n)), float(e), float(max(s, n))

    def parent(self, col, row, coarser: GridSpec):
        ratio = coarser.factor // self.factor
        if coarser.factor % self.factor:
            raise ValueError("grids do not nest")
        return np.asarray(col) // ratio, np.asarray(row) // ratio


def encode_ids(size_km: int, col, row) -> np.ndarray:
    col = np.asarray(col, dtype=np.int64)
    row = np.asarray(row, dtype=np.int64)
    return np.char.add(np.char.add(f"E{size_km}:", col.astype(str)), np.char.add(":", row.astype(str)))


def decode_id(cell_id: str) -> tuple[int, int, int]:
    size, col, row = cell_id[1:].split(":")
    return int(size), int(col), int(row)
