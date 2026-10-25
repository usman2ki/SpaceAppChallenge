"""Row-level validation and normalisation of FIRMS hotspot CSVs.

Input rows are kept as raw strings. Every row ends up either *accepted*
(canonical record) or *rejected* with explicit reasons; nothing is dropped
silently. Non-essential fields that are malformed (e.g. a negative FRP) are
set to null and counted as field warnings, while the detection itself is kept.

Essential fields (location, date, time, platform, processing stream,
confidence) must be valid or the row is rejected.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..geo import equal_area as ea
from . import firms_schema as fs

PARSER_VERSION = "firms-csv-1.0.0"


class FileLevelError(ValueError):
    """The file cannot be interpreted safely (e.g. missing required columns)."""


@dataclass
class Declaration:
    """What the importer is told about a file. Optional fields are cross-checked
    against the rows; contradictions are reported, never silently resolved."""

    data_origin: str  # "real" | "synthetic"
    source_id: str | None = None  # FIRMS SOURCE id, e.g. MODIS_SP
    source_url: str | None = None
    retrieved_at: str | None = None
    period_start: str | None = None  # dates actually requested from the provider
    period_end: str | None = None
    query_bbox: list[float] | None = None
    notes: str | None = None
    # optional per-platform query periods for multi-platform products (e.g. MODIS_SP
    # contains Terra and Aqua, whose records can end on different dates)
    platform_periods: dict[str, tuple[str, str]] | None = None

    def __post_init__(self) -> None:
        if self.data_origin not in ("real", "synthetic"):
            raise ValueError("data_origin must be 'real' or 'synthetic'")
        if self.source_id is not None and self.source_id not in fs.PRODUCTS:
            raise ValueError(f"Unknown FIRMS source '{self.source_id}'. Known: {sorted(fs.PRODUCTS)}")


@dataclass
class NormalizeResult:
    accepted: pd.DataFrame
    rejected: list[dict]
    field_warnings: Counter = field(default_factory=Counter)
    file_warnings: list[str] = field(default_factory=list)
    rows_read: int = 0
    header: list[str] = field(default_factory=list)


def read_csv_rows(data: bytes) -> tuple[list[str], list[tuple[int, list[str]]], list[dict]]:
    """Parse CSV bytes keeping raw strings and physical line numbers.
    Returns (header, rows, malformed) where malformed rows have the wrong
    number of fields."""
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise FileLevelError("File is empty.") from None
    header = [h.strip() for h in header]
    if len(header) < 3:
        raise FileLevelError("File does not look like a comma-separated FIRMS export (fewer than 3 columns).")
    rows, malformed = [], []
    for row in reader:
        line = reader.line_num
        if not row or all(not c.strip() for c in row):
            continue
        if len(row) != len(header):
            malformed.append({"line": line, "reasons": [f"expected {len(header)} fields, found {len(row)}"], "raw": dict(enumerate(row))})
            continue
        rows.append((line, row))
    return header, rows, malformed


def _map_columns(header: list[str]) -> dict[str, int]:
    lower = {h.lower(): i for i, h in enumerate(header)}
    mapping = {}
    for canon, aliases in fs.COLUMN_ALIASES.items():
        for a in aliases:
            if a in lower:
                mapping[canon] = lower[a]
                break
    return mapping


def _hash_cols(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    joined = None
    for c in cols:
        part = df[c].astype(object).where(df[c].notna(), "").astype(str)
        joined = part if joined is None else joined + "|" + part
    return joined.map(lambda s: hashlib.sha1(s.encode()).hexdigest())


def normalize(data: bytes, decl: Declaration) -> NormalizeResult:
    header, rows, malformed = read_csv_rows(data)
    colmap = _map_columns(header)
    lower_header = [h.lower() for h in header]
    missing = [c for c in fs.REQUIRED if c not in colmap]
    if missing:
        raise FileLevelError(f"Missing required column(s): {', '.join(missing)}. Found: {', '.join(header)}")

    product = fs.PRODUCTS.get(decl.source_id) if decl.source_id else None
    file_warnings: list[str] = []

    raw = pd.DataFrame([r for _, r in rows], columns=range(len(header)), dtype=str) if rows else pd.DataFrame(columns=range(len(header)), dtype=str)
    lines = np.array([ln for ln, _ in rows], dtype=np.int64)
    n = len(raw)
    reasons: list[list[str]] = [[] for _ in range(n)]
    warn = Counter()

    def col(name: str) -> pd.Series:
        if name in colmap:
            return raw[colmap[name]].fillna("").str.strip()
        return pd.Series([""] * n, dtype=str)

    def reject(mask: np.ndarray, msg: str) -> None:
        for i in np.nonzero(mask)[0]:
            reasons[i].append(msg)

    # ------------------------------------------------------------ instrument
    header_instrument = None
    if "bright_t31" in lower_header:
        header_instrument = "MODIS"
    elif "bright_ti4" in lower_header or "bright_ti5" in lower_header:
        header_instrument = "VIIRS"
    instr = col("instrument").str.upper()
    fallback = (product.instrument if product else None) or header_instrument
    if "instrument" not in colmap and fallback is None:
        raise FileLevelError("Cannot determine the instrument (no 'instrument' column, no declared product, no MODIS/VIIRS-specific columns).")
    instr = instr.where(instr != "", fallback or "")
    bad_instr = ~instr.isin(["MODIS", "VIIRS"]).to_numpy()
    reject(bad_instr, "unknown instrument")
    if product is not None:
        reject((instr != product.instrument).to_numpy() & ~bad_instr, f"instrument does not match declared product {product.source_id}")
    if header_instrument is not None:
        reject((instr != header_instrument).to_numpy() & ~bad_instr, f"instrument contradicts {header_instrument}-specific columns")

    # ------------------------------------------------------------ platform
    sat = col("satellite").str.upper()
    if "satellite" not in colmap:
        if product is not None and len(product.platforms) == 1:
            sat = pd.Series([product.platforms[0].upper()] * n, dtype=str)
            file_warnings.append(f"No 'satellite' column; platform taken from declared product {product.source_id}.")
        else:
            raise FileLevelError("No 'satellite' column and the platform cannot be inferred from the declared product.")
    platform = sat.map(fs.PLATFORM_CODES)
    reject(platform.isna().to_numpy(), "unknown satellite/platform code")
    plat_instr = platform.map(fs.INSTRUMENT_OF_PLATFORM)
    reject((plat_instr.notna() & (plat_instr != instr)).to_numpy(), "satellite code does not belong to the instrument")
    if product is not None:
        reject(
            (platform.notna() & ~platform.isin(product.platforms)).to_numpy(),
            f"mixed platform label: platform not part of declared product {product.source_id}",
        )

    # ------------------------------------------------------------ version / stream
    if "version" in colmap:
        parsed = col("version").map(fs.parse_version)
        bad_v = parsed.isna().to_numpy()
        reject(bad_v, "unrecognised version string")
        collection = parsed.map(lambda t: t[0] if t else None)
        stream = parsed.map(lambda t: t[1] if t else None)
        if product is not None:
            fam = stream.map(lambda s: fs.stream_family(s) if s else None)
            reject(
                (fam.notna() & (fam != product.stream)).to_numpy(),
                f"processing stream mismatch: declared {product.source_id} ({product.stream}) but row version is a different stream",
            )
        version_raw = col("version")
    else:
        if product is None:
            raise FileLevelError(
                "No 'version' column and no declared product: the processing stream (standard vs NRT) cannot be determined. Refusing to guess."
            )
        collection = pd.Series([None] * n, dtype=object)
        stream = pd.Series([product.stream] * n, dtype=object)
        version_raw = pd.Series([""] * n, dtype=str)
        file_warnings.append(f"No 'version' column; processing stream taken from declared product ({product.stream}); collection unknown.")

    # ------------------------------------------------------------ date / time
    d = col("acq_date")
    iso = d.str.extract(r"^(\d{4})[-/](\d{2})[-/](\d{2})$")
    compact = d.str.extract(r"^(\d{4})(\d{2})(\d{2})$")
    ymd = iso.fillna(compact)
    date_str = ymd[0] + "-" + ymd[1] + "-" + ymd[2]
    t = col("acq_time")
    hm_colon = t.str.extract(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")
    digits = t.where(t.str.fullmatch(r"\d{1,4}"), None)
    padded = digits.str.zfill(4)
    hh = hm_colon[0].fillna(padded.str[:2])
    mm = hm_colon[1].fillna(padded.str[2:])
    hh_i = pd.to_numeric(hh, errors="coerce")
    mm_i = pd.to_numeric(mm, errors="coerce")
    bad_time = (hh_i.isna() | mm_i.isna() | (hh_i > 23) | (mm_i > 59)).to_numpy()
    reject(bad_time, "invalid acq_time (expected HHMM or HH:MM, UTC)")
    dt = pd.to_datetime(
        date_str + " " + hh.fillna("00").str.zfill(2) + ":" + mm.fillna("00").str.zfill(2),
        format="%Y-%m-%d %H:%M",
        errors="coerce",
        utc=True,
    )
    bad_date = dt.isna().to_numpy() & ~bad_time
    reject(bad_date, "invalid acq_date (expected YYYY-MM-DD; ambiguous day/month orders are not accepted)")

    # ------------------------------------------------------------ coordinates
    lat = pd.to_numeric(col("latitude"), errors="coerce")
    lon = pd.to_numeric(col("longitude"), errors="coerce")
    reject((lat.isna() | lon.isna()).to_numpy(), "latitude/longitude missing or not numeric")
    reject(((lat.abs() > 90) | (lon.abs() > 180)).fillna(False).to_numpy(), "latitude/longitude out of range")
    reject(((lat == 0) & (lon == 0)).fillna(False).to_numpy(), "null-island coordinate (0, 0) is treated as missing location")

    # ------------------------------------------------------------ confidence
    conf_raw = col("confidence")
    conf_num = pd.to_numeric(conf_raw, errors="coerce")
    conf_cls = pd.Series([None] * n, dtype=object)
    conf_pct = pd.Series([np.nan] * n, dtype=float)
    is_modis = (instr == "MODIS").to_numpy()
    is_viirs = (instr == "VIIRS").to_numpy()
    lo, hi = fs.MODIS_CONFIDENCE_BREAKS
    m_ok = is_modis & conf_num.notna().to_numpy() & (conf_num >= 0).fillna(False).to_numpy() & (conf_num <= 100).fillna(False).to_numpy()
    reject(is_modis & ~m_ok, "MODIS confidence must be a number from 0 to 100")
    cn = conf_num.to_numpy()
    conf_pct[m_ok] = cn[m_ok]
    conf_cls[m_ok] = np.where(cn[m_ok] < lo, "low", np.where(cn[m_ok] < hi, "nominal", "high"))
    v_map = conf_raw.str.lower().map(fs.VIIRS_CONFIDENCE)
    v_ok = is_viirs & v_map.notna().to_numpy()
    reject(is_viirs & ~v_ok, "VIIRS confidence must be low/nominal/high (l/n/h)")
    conf_cls[v_ok] = v_map[v_ok]

    # ------------------------------------------------------------ optional numerics
    def opt_num(name: str, lo_ok: float | None = None, hi_ok: float | None = None) -> pd.Series:
        s = col(name)
        num = pd.to_numeric(s, errors="coerce")
        nonblank = s != ""
        bad = nonblank & num.isna()
        if lo_ok is not None:
            bad |= num < lo_ok
        if hi_ok is not None:
            bad |= num > hi_ok
        bad = bad.fillna(False)
        if bad.any():
            warn[f"{name}: invalid value set to null"] += int(bad.sum())
        if name in colmap and (~nonblank).any():
            warn[f"{name}: missing"] += int((~nonblank).sum())
        return num.where(~bad)

    frp = opt_num("frp", 0.0)
    bright = opt_num("brightness", 150.0, 600.0)
    bright2 = opt_num("brightness2", 150.0, 600.0)
    scan = opt_num("scan", 0.05, 25.0)
    track = opt_num("track", 0.05, 25.0)
    ftype = opt_num("type", 0, 3)
    ftype = ftype.where(ftype.isna() | (ftype == ftype.round()))
    dn = col("daynight").str.upper()
    bad_dn = ~dn.isin(["D", "N", ""])
    if bad_dn.any():
        warn["daynight: invalid value set to null"] += int(bad_dn.sum())
    dn = dn.where(dn.isin(["D", "N"]), None)

    # ------------------------------------------------------------ assemble
    rej_mask = np.array([bool(r) for r in reasons], dtype=bool)
    keep = ~rej_mask
    x, y = ea.forward(lon.fillna(0).to_numpy(), lat.fillna(0).to_numpy())
    outside_grid = (lat.abs() > ea.VALID_LAT).fillna(False).to_numpy()
    if (outside_grid & keep).any():
        warn["latitude poleward of equal-area grid: kept, not gridded"] += int((outside_grid & keep).sum())

    canon = pd.DataFrame(
        {
            "line_number": lines,
            "data_origin": decl.data_origin,
            "instrument": instr.to_numpy(),
            "platform": platform.to_numpy(),
            "processing_stream": stream.to_numpy(),
            "version_raw": version_raw.to_numpy(),
            "collection": collection.to_numpy(),
            "acq_date_raw": d.to_numpy(),
            "acq_time_raw": t.to_numpy(),
            "acq_datetime_utc": dt.dt.strftime("%Y-%m-%dT%H:%M:00Z").to_numpy(),
            "acq_date": dt.dt.strftime("%Y-%m-%d").to_numpy(),
            "latitude": lat.to_numpy(),
            "longitude": lon.to_numpy(),
            "x_ease": np.where(outside_grid, np.nan, x),
            "y_ease": np.where(outside_grid, np.nan, y),
            "confidence_raw": conf_raw.to_numpy(),
            "confidence_class": conf_cls.to_numpy(),
            "confidence_pct": conf_pct.to_numpy(),
            "daynight": dn.to_numpy(),
            "frp_mw": frp.to_numpy(),
            "brightness_k": bright.to_numpy(),
            "brightness2_k": bright2.to_numpy(),
            "scan_km": scan.to_numpy(),
            "track_km": track.to_numpy(),
            "fire_type": ftype.to_numpy(),
        }
    )
    canon["footprint_km2"] = canon["scan_km"] * canon["track_km"]
    canon = canon[keep].reset_index(drop=True)
    canon["source_product"] = [fs.product_for(i, p, s) for i, p, s in zip(canon["instrument"], canon["platform"], canon["processing_stream"])]
    if len(canon):
        canon["record_key"] = _hash_cols(
            canon.assign(lat6=canon["latitude"].round(6), lon6=canon["longitude"].round(6)),
            ["data_origin", "platform", "processing_stream", "acq_datetime_utc", "lat6", "lon6"],
        )
        canon["content_hash"] = _hash_cols(
            canon,
            ["record_key", "version_raw", "confidence_raw", "frp_mw", "brightness_k", "brightness2_k", "scan_km", "track_km", "daynight", "fire_type"],
        )
    else:
        canon["record_key"] = pd.Series(dtype=str)
        canon["content_hash"] = pd.Series(dtype=str)

    rejected = list(malformed)
    for i in np.nonzero(rej_mask)[0]:
        rejected.append({"line": int(lines[i]), "reasons": reasons[i], "raw": dict(zip(header, raw.iloc[i].tolist()))})
    rejected.sort(key=lambda r: r["line"])

    # mixed-platform / mixed-stream file diagnostics (reported, not silently merged)
    if len(canon):
        streams = sorted(canon["processing_stream"].dropna().unique())
        if len({fs.stream_family(s) for s in streams}) > 1:
            file_warnings.append(f"File mixes processing streams {streams}; they are stored separately and never spliced.")
        plats = canon["platform"].value_counts().to_dict()
        if len(plats) > 1:
            file_warnings.append(f"File contains several platforms {plats}; each is analysed as a separate platform.")

    return NormalizeResult(
        accepted=canon,
        rejected=rejected,
        field_warnings=warn,
        file_warnings=file_warnings,
        rows_read=n + len(malformed),
        header=header,
    )
