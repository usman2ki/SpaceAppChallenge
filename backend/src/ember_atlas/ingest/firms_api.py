"""Optional acquisition from the NASA FIRMS Area API.

Endpoint (verified 2026-10-25 at https://firms.modaps.eosdis.nasa.gov/api/area/):
    /api/area/csv/[MAP_KEY]/[SOURCE]/[west,south,east,north]/[DAY_RANGE]/[DATE]
    DAY_RANGE 1..5; DATE YYYY-MM-DD returns DATE .. DATE + DAY_RANGE - 1.
    MAP_KEY limit: 5000 transactions / 10-minute interval; larger requests may
    count as several transactions.
Data availability: /api/data_availability/csv/[MAP_KEY]/[SENSOR]

Credentials
* The MAP_KEY is free and must be requested by the user from NASA
  (https://firms.modaps.eosdis.nasa.gov/api/map_key/). Ember Atlas never
  requests or stores one on the user's behalf.
* The FIRMS API design puts the key in the URL path, so it is unavoidably
  transmitted to NASA's server (over HTTPS). Ember Atlas keeps it out of logs,
  error messages, cache file names, stored provenance and the database:
  every message passes through ``redact``.
* Responses are cached on disk by (source, bbox, date, day_range) so repeated
  analyses do not spend quota; transient errors back off exponentially.
"""

from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import firms_schema as fs

BASE = "https://firms.modaps.eosdis.nasa.gov"
MAX_DAY_RANGE = 5


class FirmsError(RuntimeError):
    pass


def redact(text: str, key: str | None) -> str:
    if key:
        text = text.replace(key, "***MAP_KEY***")
    return text


def area_url(key: str, source: str, bbox: tuple[float, float, float, float], day_range: int, date: str) -> str:
    if source not in fs.PRODUCTS:
        raise FirmsError(f"unknown FIRMS source {source}")
    if not 1 <= day_range <= MAX_DAY_RANGE:
        raise FirmsError("day_range must be 1..5")
    w, s, e, n = bbox
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise FirmsError("bbox must be west,south,east,north within [-180,180] x [-90,90] (split antimeridian AOIs first)")
    return f"{BASE}/api/area/csv/{key}/{source}/{w:.4f},{s:.4f},{e:.4f},{n:.4f}/{day_range}/{date}"


def _urllib_fetch(url: str, timeout: float = 60.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "EmberAtlas/0.1 (+https://github.com/ember-atlas)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() if hasattr(exc, "read") else b""


@dataclass
class FirmsClient:
    map_key: str
    cache_dir: Path | None = None
    max_requests: int = 200
    fetch: Callable[[str], tuple[int, bytes]] = _urllib_fetch
    sleep: Callable[[float], None] = time.sleep
    max_retries: int = 5
    min_interval_s: float = 0.25
    requests_made: int = 0
    cache_hits: int = 0
    log: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.map_key or not self.map_key.strip():
            raise FirmsError("A FIRMS MAP_KEY is required (set FIRMS_MAP_KEY or enter it for this session only).")

    def _cache_path(self, source, bbox, day_range, date) -> Path | None:
        if not self.cache_dir:
            return None
        h = hashlib.sha1(f"{source}|{bbox}|{day_range}|{date}".encode()).hexdigest()[:16]
        return Path(self.cache_dir) / f"{source}_{date}_{day_range}d_{h}.csv"

    def _get(self, url: str) -> bytes:
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            if self.requests_made >= self.max_requests:
                raise FirmsError(f"request budget of {self.max_requests} reached for this run (protects your MAP_KEY quota)")
            self.requests_made += 1
            try:
                status, body = self.fetch(url)
            except Exception as exc:  # network errors: redact before surfacing
                status, body = -1, redact(str(exc), self.map_key).encode()
            if status == 200:
                text = body[:200].decode("utf-8", errors="replace").lower()
                if "invalid" in text and "key" in text:
                    raise FirmsError("FIRMS rejected the MAP_KEY (response: " + redact(body[:200].decode("utf-8", "replace"), self.map_key) + ")")
                self.sleep(self.min_interval_s)
                return body
            if status in (429, 500, 502, 503, 504, -1) and attempt < self.max_retries:
                self.log.append(f"HTTP {status}; retrying in {delay:.0f}s")
                self.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            raise FirmsError(f"FIRMS request failed with HTTP {status}: " + redact(body[:300].decode("utf-8", "replace"), self.map_key))
        raise FirmsError("FIRMS request failed after retries")

    def area_csv(self, source: str, bbox, date_start: str, date_end: str) -> tuple[bytes, list[str]]:
        """Fetch [date_start, date_end] in <= 5-day chunks and concatenate into
        one CSV (single header). Returns (csv bytes, chunk descriptions)."""
        d0, d1 = pd.Timestamp(date_start), pd.Timestamp(date_end)
        if d1 < d0:
            raise FirmsError("date_end before date_start")
        header: bytes | None = None
        rows: list[bytes] = []
        chunks = []
        cur = d0
        while cur <= d1:
            n = min(MAX_DAY_RANGE, (d1 - cur).days + 1)
            date = cur.strftime("%Y-%m-%d")
            cp = self._cache_path(source, tuple(bbox), n, date)
            if cp is not None and cp.exists():
                body = cp.read_bytes()
                self.cache_hits += 1
            else:
                body = self._get(area_url(self.map_key, source, tuple(bbox), n, date))
                if cp is not None:
                    cp.parent.mkdir(parents=True, exist_ok=True)
                    cp.write_bytes(body)
            lines = body.splitlines(keepends=True)
            if lines:
                if header is None:
                    header = lines[0]
                elif lines[0].strip() != header.strip():
                    raise FirmsError("FIRMS returned different CSV headers across chunks; refusing to merge")
                rows.extend(lines[1:])
            chunks.append(f"{date} +{n}d")
            cur += pd.Timedelta(days=n)
        return (header or b"") + b"".join(rows), chunks

    def data_availability(self, sensor: str = "ALL") -> pd.DataFrame:
        body = self._get(f"{BASE}/api/data_availability/csv/{self.map_key}/{sensor}")
        import io

        return pd.read_csv(io.BytesIO(body))


def public_source_url(source: str, bbox, date_start: str, date_end: str) -> str:
    """Provenance string with the key removed."""
    w, s, e, n = bbox
    return f"{BASE}/api/area/csv/<MAP_KEY>/{source}/{w:.4f},{s:.4f},{e:.4f},{n:.4f}/<=5/[{date_start}..{date_end}]"


def retrieved_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
