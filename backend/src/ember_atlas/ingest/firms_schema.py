"""NASA FIRMS product registry and attribute semantics.

Everything here was checked against official NASA documentation on
2026-10-25 (see docs/sources.md for the full list and what each source
did / did not state). Where documentation was silent we say so instead of
guessing, e.g. FIRMS does not state units for scan/track.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

RETRIEVED = "2026-10-25"

DOCS = {
    "firms_area_api": "https://firms.modaps.eosdis.nasa.gov/api/area/",
    "firms_data_availability": "https://firms.modaps.eosdis.nasa.gov/api/data_availability/",
    "firms_attributes": "https://earthdata.nasa.gov/data/tools/firms/active-fire-data-attributes-modis-viirs",
    "firms_archive_download": "https://firms.modaps.eosdis.nasa.gov/download/",
    "modis_c61_user_guide": "https://earthdata.nasa.gov/s3fs-public/2023-09/MODIS_C6_C6.1_Fire_User_Guide_1.0.pdf",
    "viirs_snpp_nrt_product": "https://modaps.modaps.eosdis.nasa.gov/services/about/products/viirs-land-c2-nrt/vnp14imgtdl_nrt.html",
    "viirs_noaa21_nrt_product": "https://www.earthdata.nasa.gov/learn/find-data/near-real-time/firms/vj214imgtdlnrt",
}


@dataclass(frozen=True)
class Product:
    source_id: str  # FIRMS API "SOURCE" identifier
    instrument: str  # MODIS | VIIRS
    platforms: tuple[str, ...]
    stream: str  # SP (standard / science quality) | NRT (incl. RT/URT rows)
    nominal_pixel_m: int
    archive_start: str | None  # earliest date listed on the FIRMS archive download page
    description: str


PRODUCTS: dict[str, Product] = {
    p.source_id: p
    for p in [
        Product("MODIS_SP", "MODIS", ("Terra", "Aqua"), "SP", 1000, "2000-11-01", "MODIS Collection 6.1 standard processing (MCD14ML)"),
        Product("MODIS_NRT", "MODIS", ("Terra", "Aqua"), "NRT", 1000, None, "MODIS Collection 6.1 near real-time (incl. RT/URT)"),
        Product("VIIRS_SNPP_SP", "VIIRS", ("Suomi-NPP",), "SP", 375, "2012-01-20", "VIIRS S-NPP 375 m standard processing"),
        Product("VIIRS_SNPP_NRT", "VIIRS", ("Suomi-NPP",), "NRT", 375, None, "VIIRS S-NPP 375 m near real-time"),
        Product("VIIRS_NOAA20_SP", "VIIRS", ("NOAA-20",), "SP", 375, "2018-04-01", "VIIRS NOAA-20 375 m standard processing"),
        Product("VIIRS_NOAA20_NRT", "VIIRS", ("NOAA-20",), "NRT", 375, None, "VIIRS NOAA-20 375 m near real-time"),
        Product("VIIRS_NOAA21_SP", "VIIRS", ("NOAA-21",), "SP", 375, "2023-03-01", "VIIRS NOAA-21 375 m standard processing"),
        Product("VIIRS_NOAA21_NRT", "VIIRS", ("NOAA-21",), "NRT", 375, None, "VIIRS NOAA-21 375 m near real-time"),
    ]
}

INSTRUMENT_OF_PLATFORM = {
    "Terra": "MODIS",
    "Aqua": "MODIS",
    "Suomi-NPP": "VIIRS",
    "NOAA-20": "VIIRS",
    "NOAA-21": "VIIRS",
}

# Codes documented on the FIRMS attribute page: MODIS A/T; VIIRS N, N20, N21
# (NOAA-20 was "JPSS-1" and NOAA-21 "JPSS-2" before launch). Full names are
# accepted too. Anything else is rejected rather than guessed.
PLATFORM_CODES = {
    "T": "Terra",
    "TERRA": "Terra",
    "A": "Aqua",
    "AQUA": "Aqua",
    "N": "Suomi-NPP",
    "NPP": "Suomi-NPP",
    "SNPP": "Suomi-NPP",
    "S-NPP": "Suomi-NPP",
    "SUOMI-NPP": "Suomi-NPP",
    "SUOMI NPP": "Suomi-NPP",
    "N20": "NOAA-20",
    "NOAA-20": "NOAA-20",
    "NOAA20": "NOAA-20",
    "JPSS-1": "NOAA-20",
    "N21": "NOAA-21",
    "NOAA-21": "NOAA-21",
    "NOAA21": "NOAA-21",
    "JPSS-2": "NOAA-21",
}

# Approximate local equator-crossing times; used only for documentation and
# for the synthetic generator. Terra and Aqua drifted in their extended
# missions, so real overpass times must be read from acq_time, not assumed.
NOMINAL_LOCAL_OVERPASS = {
    "Terra": ("10:30", "22:30"),
    "Aqua": ("13:30", "01:30"),
    "Suomi-NPP": ("13:30", "01:30"),
    "NOAA-20": ("13:30", "01:30"),  # ~50 min (half an orbit) from S-NPP in the same orbital plane
    "NOAA-21": ("13:30", "01:30"),
}

# Column aliases: FIRMS CSV/API (lower-case), archive shapefile attribute
# tables (upper-case) and the "web services" names listed on the attribute page.
COLUMN_ALIASES = {
    "latitude": ["latitude", "lat"],
    "longitude": ["longitude", "lon", "long"],
    "brightness": ["brightness", "bright_ti4"],
    "brightness2": ["bright_t31", "bright_ti5", "brightness_2"],
    "scan": ["scan"],
    "track": ["track"],
    "acq_date": ["acq_date"],
    "acq_time": ["acq_time"],
    "satellite": ["satellite"],
    "instrument": ["instrument"],
    "confidence": ["confidence"],
    "version": ["version"],
    "frp": ["frp"],
    "daynight": ["daynight"],
    "type": ["type"],
}
REQUIRED = ["latitude", "longitude", "acq_date", "acq_time", "confidence"]

# MODIS C6/C6.1 user guide, Sec. 8.4.3 / Table 11:
#   low: 0 <= C < 30 ; nominal: 30 <= C < 80 ; high: 80 <= C <= 100
MODIS_CONFIDENCE_BREAKS = (30.0, 80.0)

VIIRS_CONFIDENCE = {"l": "low", "low": "low", "n": "nominal", "nominal": "nominal", "h": "high", "high": "high"}

CONFIDENCE_ORDER = {"low": 0, "nominal": 1, "high": 2}

# MCD14ML / VIIRS standard "type": 0 presumed vegetation fire, 1 active volcano,
# 2 other static land source, 3 offshore. Only present in standard products.
FIRE_TYPES = {0: "presumed vegetation fire", 1: "active volcano", 2: "other static land source", 3: "offshore"}

_VERSION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[_ ]?(URT|RT|NRT)?\s*$", re.IGNORECASE)


def parse_version(v: str) -> tuple[str, str] | None:
    """'6.1NRT' -> ('6.1', 'NRT'); '2.0URT' -> ('2.0', 'URT'); '6.1' -> ('6.1', 'SP').

    FIRMS documents the version attribute as collection number plus a
    processing-source suffix (URT / RT / NRT); no suffix means standard
    processing. Returns None when the string does not follow that pattern.
    """
    m = _VERSION_RE.match(str(v))
    if not m:
        return None
    return m.group(1), (m.group(2) or "SP").upper()


def stream_family(stream: str) -> str:
    """URT and RT rows are provisional near-real-time data; FIRMS replaces them
    with NRT and later standard (SP) data. They are kept distinct in storage
    but belong to the NRT family for product matching."""
    return "SP" if stream == "SP" else "NRT"


def product_for(instrument: str, platform: str, stream: str) -> str | None:
    fam = stream_family(stream)
    for p in PRODUCTS.values():
        if p.instrument == instrument and platform in p.platforms and p.stream == fam:
            return p.source_id
    return None
