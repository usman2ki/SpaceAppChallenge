"""Storage interface shared by the SQLite (local / no-Docker) and PostGIS stores.

Contract (both implementations, enforced by tests/test_store_contract.py):
* raw uploaded files and per-row rejection/duplicate records are immutable:
  there is no update or delete operation for them;
* detections are append-only and keyed by ``record_key`` (idempotent imports);
* spatial queries apply the *exact* AOI polygon, not only its bounding box;
* synthetic and real data are never mixed in one query (``data_origin``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

from ..geo.polygons import AOI

DETECTION_COLUMNS = [
    "record_key",
    "content_hash",
    "batch_id",
    "line_number",
    "data_origin",
    "instrument",
    "platform",
    "source_product",
    "processing_stream",
    "version_raw",
    "collection",
    "acq_date_raw",
    "acq_time_raw",
    "acq_datetime_utc",
    "acq_date",
    "latitude",
    "longitude",
    "x_ease",
    "y_ease",
    "confidence_raw",
    "confidence_class",
    "confidence_pct",
    "daynight",
    "frp_mw",
    "brightness_k",
    "brightness2_k",
    "scan_km",
    "track_km",
    "footprint_km2",
    "fire_type",
]


@dataclass
class DetectionQuery:
    data_origin: str
    aoi: AOI
    date_start: str
    date_end: str
    platforms: list[str] | None = None
    streams: list[str] | None = None  # SP / NRT / RT / URT
    columns: list[str] | None = None


class Store(ABC):
    kind: str = "abstract"

    # -------------------------------------------------------------- lifecycle
    @abstractmethod
    def migrate(self) -> list[str]: ...

    @abstractmethod
    def close(self) -> None: ...

    # -------------------------------------------------------------- imports
    @abstractmethod
    def find_batch_by_sha(self, sha256: str, data_origin: str) -> dict | None: ...

    @abstractmethod
    def create_batch(self, meta: dict, raw_gz: bytes) -> int: ...

    @abstractmethod
    def finish_batch(self, batch_id: int, report: dict, status: str = "complete") -> None: ...

    @abstractmethod
    def record_row_issues(self, batch_id: int, issues: list[dict]) -> None: ...

    @abstractmethod
    def existing_keys(self, keys: list[str]) -> dict[str, str]:
        """record_key -> content_hash for keys already stored."""

    @abstractmethod
    def insert_detections(self, df: pd.DataFrame) -> int: ...

    @abstractmethod
    def add_availability(self, rows: list[dict]) -> None:
        """Declared query windows: (data_origin, platform, processing_stream,
        date_start, date_end, bbox, batch_id, basis). A platform/date inside a
        declared window was *queried*; zero detections there means 'none
        reported', which is still not proof of absence."""

    @abstractmethod
    def list_batches(self, data_origin: str | None = None) -> list[dict]: ...

    @abstractmethod
    def get_batch(self, batch_id: int) -> dict | None: ...

    @abstractmethod
    def get_raw_file(self, batch_id: int) -> bytes | None: ...

    @abstractmethod
    def row_issues(self, batch_id: int, limit: int = 100) -> list[dict]: ...

    # -------------------------------------------------------------- queries
    @abstractmethod
    def query_detections(self, q: DetectionQuery) -> pd.DataFrame: ...

    @abstractmethod
    def availability(self, data_origin: str) -> pd.DataFrame: ...

    @abstractmethod
    def dataset_summary(self, data_origin: str) -> dict: ...

    # -------------------------------------------------------------- coverage / covariates
    @abstractmethod
    def create_coverage_set(self, meta: dict) -> int: ...

    @abstractmethod
    def insert_coverage(self, set_id: int, df: pd.DataFrame) -> int: ...

    @abstractmethod
    def coverage_sets(self, data_origin: str) -> list[dict]: ...

    @abstractmethod
    def query_coverage(self, set_id: int, cell_ids: list[str], date_start: str, date_end: str, platforms: list[str] | None) -> pd.DataFrame: ...

    @abstractmethod
    def insert_landcover(self, set_id: int, df: pd.DataFrame) -> int: ...

    @abstractmethod
    def query_landcover(self, set_id: int, cell_ids: list[str]) -> pd.DataFrame: ...

    # -------------------------------------------------------------- cache
    @abstractmethod
    def cache_get(self, key: str) -> str | None: ...

    @abstractmethod
    def cache_put(self, key: str, payload: str) -> None: ...

    @abstractmethod
    def data_version(self, data_origin: str) -> str:
        """Changes whenever data for this origin changes (cache invalidation)."""
