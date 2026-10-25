"""Deterministic synthetic hotspot world with KNOWN sensor effects.

THIS IS NOT NASA DATA. It imitates the *format* of FIRMS exports so the full
pipeline can be exercised and the harmonisation method can be checked against
a known truth. Everything produced here is labelled ``data_origin=synthetic``.

Generative model (all parameters in SyntheticSpec, seeded):

  latent activity  lambda[c, d]  = Aqua-MODIS-equivalent detections per valid look
                                   in 18 km cell c on day d
                   = base[c] * season(doy; land cover) * year[y] * episode[c, d]
                     * exp(regional AR(1) weather + cell noise)
  valid looks      e[p, tile, d] = sum over the day and night overpass of
                                   in_swath * (1 - cloud_fraction)
                   (clouds shared by near-simultaneous Aqua / S-NPP / NOAA-20
                   overpasses; MODIS has swath gaps at this latitude, VIIRS not)
  detections       y[p, c, d] ~ NegBin(mean = rho[p](doy, lc) * lambda * e, size k)

The injected sensor effect rho is the thing harmonisation has to recover:
  log rho_SNPP = log(3.0) + 0.25 cos(2 pi (doy - 245)/365.25) + lc_shift
  NOAA-20 = S-NPP x 0.95, Terra = 0.65 x (morning overpass sees less activity).
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..geo import equal_area as ea

PLATFORM_CODE = {"Terra": "T", "Aqua": "A", "Suomi-NPP": "N", "NOAA-20": "N20"}
PRODUCT_OF = {"Terra": "MODIS_SP", "Aqua": "MODIS_SP", "Suomi-NPP": "VIIRS_SNPP_SP", "NOAA-20": "VIIRS_NOAA20_SP"}
LOCAL_OVERPASS_H = {"Terra": (10.5, 22.5), "Aqua": (13.5, 1.5), "Suomi-NPP": (13.5, 1.5), "NOAA-20": (12.67, 0.67)}
LC_CLASSES = ["savanna", "grassland", "cropland", "forest"]


@dataclass
class SyntheticSpec:
    seed: int = 20261025
    bbox: tuple = (-48.5, -11.5, -45.5, -9.0)
    start: str = "2010-01-01"
    end: str = "2022-12-31"
    latent_km: int = 18
    coverage_km: int = 36
    # published data windows (Aqua "ends" mid-2021 in this synthetic world to
    # exercise the cross-platform transition; this is NOT a real mission date)
    windows: dict = field(
        default_factory=lambda: {
            "Terra": ("2010-01-01", "2022-12-31"),
            "Aqua": ("2010-01-01", "2021-06-30"),
            "Suomi-NPP": ("2013-01-01", "2022-12-31"),
            "NOAA-20": ("2018-04-01", "2022-12-31"),
        }
    )
    base_log_ratio: dict = field(default_factory=lambda: {"Aqua": 0.0, "Terra": math.log(0.65), "Suomi-NPP": math.log(3.0), "NOAA-20": math.log(3.0 * 0.95)})
    seasonal_ratio_amp: dict = field(default_factory=lambda: {"Aqua": 0.0, "Terra": 0.10, "Suomi-NPP": 0.25, "NOAA-20": 0.25})
    lc_ratio_shift: dict = field(default_factory=lambda: {"savanna": 0.0, "grassland": 0.0, "cropland": 0.30, "forest": -0.10})
    ratio_peak_doy: float = 245.0
    ratio_drift_per_year: float = 0.0  # set non-zero to test drift detection
    nb_size: float = 4.0
    mean_level: float = 0.035  # mean lambda (detections / valid look / 18 km cell / day)
    season_peak_doy: float = 245.0
    season_amp: float = 2.0
    lc_season_shift: dict = field(default_factory=lambda: {"cropland": -40.0})
    lc_level: dict = field(default_factory=lambda: {"savanna": 1.3, "grassland": 1.0, "cropland": 0.8, "forest": 0.35})
    year_sd: float = 0.3
    # injected anomalies (synthetic, labelled in docs and UI)
    excess_episode: dict = field(default_factory=lambda: {"year": 2019, "doy": (213, 250), "factor": 8.0, "quadrant": "S"})
    deficit_episode: dict = field(default_factory=lambda: {"year": 2016, "doy": (200, 270), "factor": 0.15})
    terra_outage: tuple = ("2015-03-01", "2015-03-20")
    modis_gap_prob: float = 0.15
    static_source: tuple = (-10.70, -47.70)


@dataclass
class SyntheticWorld:
    spec: SyntheticSpec
    days: pd.DatetimeIndex
    cells: pd.DataFrame  # latent 18 km cells: cell_id, col, row, west, south, east, north, lc, tile
    lam: np.ndarray  # (n_days, n_cells)
    looks: dict  # platform -> (n_days, n_cells) valid looks (counterfactual for all days)
    rho: dict  # platform -> (n_days, n_cells) true ratio vs Aqua
    counts: dict  # platform -> (n_days, n_cells) detection counts (published windows only)
    detections: pd.DataFrame  # FIRMS-like rows incl. static source
    coverage: pd.DataFrame  # published coverage at coverage_km tiles

    def truth_expected(self, platform: str = "Aqua") -> np.ndarray:
        """Expected counts E[y] for a platform under its (counterfactual) coverage."""
        return self.rho[platform] * self.lam * self.looks[platform]

    def truth_cell_ids(self, grid_km: int) -> np.ndarray:
        g = ea.GridSpec(grid_km)
        x, y = ea.forward(((self.cells.west + self.cells.east) / 2).to_numpy(), ((self.cells.south + self.cells.north) / 2).to_numpy())
        return g.cell_ids(x, y)


def _smooth_field(rng, shape, n_bumps=8, scale=3.0):
    rows, cols = shape
    yy, xx = np.mgrid[0:rows, 0:cols]
    f = np.zeros(shape)
    for _ in range(n_bumps):
        cy, cx = rng.uniform(0, rows), rng.uniform(0, cols)
        s = rng.uniform(1.5, scale * 2)
        f += rng.normal() * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * s * s))
    return (f - f.mean()) / (f.std() + 1e-9)


def _ar1(rng, n, phi, sd, size=None):
    shape = (n,) if size is None else (n, size)
    z = rng.normal(0, sd * math.sqrt(1 - phi * phi), shape)
    out = np.empty(shape)
    out[0] = rng.normal(0, sd, shape[1:] if size else None)
    for i in range(1, n):
        out[i] = phi * out[i - 1] + z[i]
    return out


def generate(spec: SyntheticSpec | None = None) -> SyntheticWorld:
    spec = spec or SyntheticSpec()
    rng = np.random.default_rng(spec.seed)
    days = pd.date_range(spec.start, spec.end, freq="D")
    nd = len(days)
    doy = days.dayofyear.to_numpy().astype(float)
    years = days.year.to_numpy()

    # ------------------------------------------------------------- latent cells
    g = ea.GridSpec(spec.latent_km)
    w, s, e, n = spec.bbox
    x0, y0 = ea.forward(np.array([w, e]), np.array([s, n]))
    c0, r1 = g.col_row(x0[0], y0[1])
    c1, r0 = g.col_row(x0[1], y0[0])
    cols = np.arange(int(c0), int(c1) + 1)
    rws = np.arange(int(r1), int(r0) + 1)
    cc, rr = np.meshgrid(cols, rws, indexing="xy")
    cc, rr = cc.ravel(), rr.ravel()
    bounds = np.array([g.cell_bounds_lonlat(int(a), int(b)) for a, b in zip(cc, rr)])
    nc = len(cc)
    shape = (len(rws), len(cols))
    lc_field = _smooth_field(rng, shape, 10).ravel()
    lc_field2 = _smooth_field(rng, shape, 10).ravel()
    lc = np.where(lc_field > 0.9, "forest", np.where(lc_field2 > 0.6, "cropland", np.where(lc_field < -0.4, "grassland", "savanna")))
    base_field = np.exp(0.6 * _smooth_field(rng, shape, 12).ravel())
    level = np.array([spec.lc_level[c] for c in lc]) * base_field
    tg = ea.GridSpec(spec.coverage_km)
    tc, tr = g.parent(cc, rr, tg)
    tile = ea.encode_ids(spec.coverage_km, tc, tr)
    cells = pd.DataFrame(
        {
            "cell_id": ea.encode_ids(spec.latent_km, cc, rr),
            "col": cc,
            "row": rr,
            "west": bounds[:, 0],
            "south": bounds[:, 1],
            "east": bounds[:, 2],
            "north": bounds[:, 3],
            "lc": lc,
            "tile": tile,
        }
    )

    # ------------------------------------------------------------- latent activity
    peak = np.full(nc, spec.season_peak_doy) + np.array([spec.lc_season_shift.get(c, 0.0) for c in lc])
    season = np.exp(spec.season_amp * np.cos(2 * np.pi * (doy[:, None] - peak[None, :]) / 365.25))
    season /= season.mean()
    uyears = np.unique(years)
    yfac = dict(zip(uyears, np.exp(rng.normal(0, spec.year_sd, len(uyears)))))
    year_f = np.array([yfac[y] for y in years])
    weather = _ar1(rng, nd, 0.92, 0.45)
    cell_noise = _ar1(rng, nd, 0.6, 0.5, size=nc)
    lam = level[None, :] * season * year_f[:, None] * np.exp(weather[:, None] + cell_noise)

    ex = spec.excess_episode
    lat_mid = (cells.south + cells.north).to_numpy() / 2
    lon_mid = (cells.west + cells.east).to_numpy() / 2
    south = lat_mid < (s + n) / 2
    quad = south & (lon_mid > (w + e) / 2) if ex.get("quadrant") == "SE" else south  # "SE" quadrant or "S" half
    m_ex = (years == ex["year"]) & (doy >= ex["doy"][0]) & (doy <= ex["doy"][1])
    lam[np.ix_(m_ex, quad)] *= ex["factor"]
    de = spec.deficit_episode
    m_de = (years == de["year"]) & (doy >= de["doy"][0]) & (doy <= de["doy"][1])
    lam[m_de, :] *= de["factor"]
    lam *= spec.mean_level / lam.mean()

    # ------------------------------------------------------------- coverage
    tiles = np.unique(tile)
    tidx = np.searchsorted(tiles, tile)
    nt = len(tiles)
    wet = 0.5 + 0.5 * np.cos(2 * np.pi * (doy - 30) / 365.25)  # cloudy Dec-Mar
    base_logit = -2.2 + 3.2 * wet
    cloud_reg = _ar1(rng, nd, 0.8, 0.8)
    cloud_tile = rng.normal(0, 0.7, (nd, nt))

    def cloud(noise_sd, corr_shift=None):
        z = base_logit[:, None] + cloud_reg[:, None] + cloud_tile + rng.normal(0, noise_sd, (nd, nt))
        if corr_shift is not None:
            z = z + corr_shift
        return 1 / (1 + np.exp(-z))

    cloud_pm_day = cloud(0.15)  # shared by the ~13:30 overpasses
    cloud_pm_night = cloud(0.15)
    terra_shift = rng.normal(0, 0.9, (nd, nt))
    cloud_am_day = cloud(0.15, terra_shift - 0.4)  # mornings slightly clearer, partly independent
    cloud_am_night = cloud(0.15, terra_shift)

    def jitter(c):
        return np.clip(c + rng.normal(0, 0.05, c.shape), 0, 1)

    def looks_for(platform):
        modis = platform in ("Terra", "Aqua")
        if platform == "Terra":
            cd, cn = jitter(cloud_am_day), jitter(cloud_am_night)
        else:
            cd, cn = jitter(cloud_pm_day), jitter(cloud_pm_night)
        in_d = np.ones((nd, nt))
        in_n = np.ones((nd, nt))
        if modis:
            in_d = (rng.uniform(size=(nd, 1)) > spec.modis_gap_prob) * np.ones((1, nt))  # gaps span the region
            in_n = (rng.uniform(size=(nd, 1)) > spec.modis_gap_prob) * np.ones((1, nt))
        if platform == "Terra":
            out = (days >= spec.terra_outage[0]) & (days <= spec.terra_outage[1])
            in_d[out] = 0
            in_n[out] = 0
        day_looks = in_d * (1 - cd)
        night_looks = in_n * (1 - cn)
        return day_looks, night_looks

    looks, day_share = {}, {}
    for p in ["Terra", "Aqua", "Suomi-NPP", "NOAA-20"]:
        dl, nl = looks_for(p)
        looks[p] = (dl + nl)[:, tidx]
        # fires peak in the afternoon: day looks carry more activity than night looks
        wd, wn = 0.8 * dl, 0.2 * nl
        day_share[p] = (wd / np.maximum(wd + wn, 1e-9))[:, tidx]
        looks[p + "_daynight"] = (dl[:, tidx], nl[:, tidx])

    # ------------------------------------------------------------- sensor ratios & counts
    lc_shift = np.array([spec.lc_ratio_shift[c] for c in lc])
    rho, counts = {}, {}
    yrs_from_2010 = (days.year.to_numpy() - 2010) + (doy - 1) / 365.25
    for p in ["Terra", "Aqua", "Suomi-NPP", "NOAA-20"]:
        lr = spec.base_log_ratio[p] + spec.seasonal_ratio_amp[p] * np.cos(2 * np.pi * (doy - spec.ratio_peak_doy) / 365.25)
        lr = lr[:, None] + (lc_shift[None, :] if p in ("Suomi-NPP", "NOAA-20") else 0.0)
        if p in ("Suomi-NPP", "NOAA-20"):
            lr = lr + spec.ratio_drift_per_year * yrs_from_2010[:, None]
        rho[p] = np.exp(lr)
        mu = rho[p] * lam * looks[p]
        gam = rng.gamma(spec.nb_size, 1.0 / spec.nb_size, mu.shape)
        y = rng.poisson(mu * gam)
        ws, we = spec.windows[p]
        pub = (days >= ws) & (days <= we)
        y[~pub] = 0
        counts[p] = y

    detections = _materialise(rng, spec, days, cells, counts, day_share)
    coverage = _coverage_frame(spec, days, tiles, {p: looks[p + "_daynight"] for p in counts}, tidx)
    return SyntheticWorld(spec, days, cells, lam, looks, rho, counts, detections, coverage)


def _materialise(rng, spec, days, cells, counts, day_share) -> pd.DataFrame:
    frames = []
    nd, nc = next(iter(counts.values())).shape
    # one fire centre per cell-day shared by all platforms (co-located evidence)
    cx = rng.uniform(0.15, 0.85, (nd, nc))
    cy = rng.uniform(0.15, 0.85, (nd, nc))
    west, east = cells.west.to_numpy(), cells.east.to_numpy()
    south, north = cells.south.to_numpy(), cells.north.to_numpy()
    for p, y in counts.items():
        di, ci = np.nonzero(y)
        reps = y[di, ci]
        di = np.repeat(di, reps)
        ci = np.repeat(ci, reps)
        m = len(di)
        if m == 0:
            continue
        modis = p in ("Terra", "Aqua")
        spread_deg = 0.03 if modis else 0.02
        lon = west[ci] + cx[di, ci] * (east[ci] - west[ci]) + rng.normal(0, spread_deg, m)
        lat = south[ci] + cy[di, ci] * (north[ci] - south[ci]) + rng.normal(0, spread_deg, m)
        lon = np.clip(lon, west[ci] + 1e-4, east[ci] - 1e-4)
        lat = np.clip(lat, south[ci] + 1e-4, north[ci] - 1e-4)
        is_day = rng.uniform(size=m) < day_share[p][di, ci]
        frames.append(_attributes(rng, p, days[di], lon, lat, is_day, modis, np.zeros(m, dtype=int)))

    # static industrial heat source (type 2, standard products only)
    slat, slon = spec.static_source
    for p in counts:
        ws, we = spec.windows[p]
        dsel = days[(days >= ws) & (days <= we)]
        hit = rng.uniform(size=len(dsel)) < 0.5
        dd = dsel[hit]
        m = len(dd)
        modis = p in ("Terra", "Aqua")
        frames.append(
            _attributes(rng, p, dd, slon + rng.normal(0, 0.003, m), slat + rng.normal(0, 0.003, m), rng.uniform(size=m) < 0.5, modis, np.full(m, 2), high=True)
        )
    out = pd.concat(frames, ignore_index=True)
    # keep each platform's records inside its published window in UTC dates
    # (local-time night overpasses can fall on the next UTC day)
    keep = np.ones(len(out), dtype=bool)
    for p, (ws, we) in spec.windows.items():
        m = (out["_platform"] == p).to_numpy()
        keep &= ~m | ((out["acq_date"] >= ws) & (out["acq_date"] <= we)).to_numpy()
    out = out[keep]
    return out.sort_values(["acq_date", "acq_time", "satellite", "latitude"]).reset_index(drop=True)


def _attributes(rng, p, local_days, lon, lat, is_day, modis, ftype, high=False) -> pd.DataFrame:
    m = len(lon)
    hd, hn = LOCAL_OVERPASS_H[p]
    local_h = np.where(is_day, hd, hn) + rng.normal(0, 0.35, m)
    # local solar time -> UTC (lon in degrees east; 15 degrees per hour)
    utc = pd.DatetimeIndex(local_days) + pd.to_timedelta(local_h - lon / 15.0, unit="h")
    utc = utc.floor("min")
    u = rng.uniform(size=m)
    if modis:
        conf = np.where(u < 0.15, rng.uniform(0, 30, m), np.where(u < 0.75, rng.uniform(30, 80, m), rng.uniform(80, 100, m))).round().astype(int).astype(str)
        if high:
            conf = rng.integers(85, 101, m).astype(str)
        frp = np.round(np.exp(rng.normal(math.log(15), 1.0, m)), 1)
        b1 = np.round(rng.uniform(300, 360, m), 1)
        b2 = np.round(rng.uniform(285, 305, m), 1)
        sa = rng.uniform(0, 1, m) ** 2
        scan = np.round(1.0 + 3.8 * sa, 1)
        track = np.round(1.0 + 0.9 * sa, 1)
        version = "6.1"
    else:
        conf = np.where(u < 0.08, "l", np.where(u < 0.90, "n", "h"))
        if high:
            conf = np.full(m, "h")
        frp = np.round(np.exp(rng.normal(math.log(4), 1.0, m)), 2)
        b1 = np.round(rng.uniform(300, 367, m), 2)
        b2 = np.round(rng.uniform(280, 310, m), 2)
        sa = rng.uniform(0, 1, m) ** 2
        scan = np.round(0.37 + 0.43 * sa, 2)
        track = np.round(0.36 + 0.42 * sa, 2)
        version = "2.0"
    return pd.DataFrame(
        {
            "latitude": np.round(lat, 4),
            "longitude": np.round(lon, 4),
            "brightness": b1,
            "scan": scan,
            "track": track,
            "acq_date": utc.strftime("%Y-%m-%d"),
            "acq_time": utc.strftime("%H%M"),
            "satellite": PLATFORM_CODE[p],
            "instrument": "MODIS" if modis else "VIIRS",
            "confidence": conf,
            "version": version,
            "brightness2": b2,
            "frp": frp,
            "daynight": np.where(is_day, "D", "N"),
            "type": ftype,
            "_platform": p,
        }
    )


def _coverage_frame(spec, days, tiles, daynight, tidx) -> pd.DataFrame:
    rows = []
    first_cell_of_tile = {t: np.nonzero(tidx == i)[0][0] for i, t in enumerate(tiles)}
    cols = np.array([first_cell_of_tile[t] for t in tiles])
    for p, (dl, nl) in daynight.items():
        ws, we = spec.windows[p]
        pub = (days >= ws) & (days <= we)
        v = (dl + nl)[:, cols][pub]
        d = days[pub].strftime("%Y-%m-%d")
        rows.append(
            pd.DataFrame(
                {
                    "platform": p,
                    "cell_id": np.tile(tiles, len(d)),
                    "date": np.repeat(d, len(tiles)),
                    "valid_looks": np.round(v.ravel(), 3),
                    "nominal_looks": 2.0,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def firms_csv_by_product(world: SyntheticWorld) -> dict[str, bytes]:
    """Split synthetic detections into FIRMS-format CSVs, one per FIRMS product."""
    out = {}
    det = world.detections
    for product, plats in [("MODIS_SP", ["Terra", "Aqua"]), ("VIIRS_SNPP_SP", ["Suomi-NPP"]), ("VIIRS_NOAA20_SP", ["NOAA-20"])]:
        sub = det[det["_platform"].isin(plats)].drop(columns=["_platform"]).copy()
        if product == "MODIS_SP":
            sub = sub.rename(columns={"brightness2": "bright_t31"})
            cols = [
                "latitude",
                "longitude",
                "brightness",
                "scan",
                "track",
                "acq_date",
                "acq_time",
                "satellite",
                "instrument",
                "confidence",
                "version",
                "bright_t31",
                "frp",
                "daynight",
                "type",
            ]
        else:
            sub = sub.rename(columns={"brightness": "bright_ti4", "brightness2": "bright_ti5"})
            cols = [
                "latitude",
                "longitude",
                "bright_ti4",
                "scan",
                "track",
                "acq_date",
                "acq_time",
                "satellite",
                "instrument",
                "confidence",
                "version",
                "bright_ti5",
                "frp",
                "daynight",
                "type",
            ]
        buf = io.StringIO()
        sub[cols].to_csv(buf, index=False)
        out[product] = buf.getvalue().encode()
    return out
