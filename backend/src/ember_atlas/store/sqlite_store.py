"""SQLite store: the local, no-Docker backend (also used by the test-suite).

Spatial indexing uses SQLite's built-in R*Tree module for the bounding-box
prefilter; the exact AOI polygon test is then applied in NumPy
(ember_atlas.geo.polygons). Raw tables are protected against UPDATE/DELETE
by triggers, mirroring the PostGIS schema.
"""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .base import DETECTION_COLUMNS, DetectionQuery, Store

SCHEMA_VERSION = 1

DDL = [
    """CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS import_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_name TEXT NOT NULL,
        file_sha256 TEXT NOT NULL,
        data_origin TEXT NOT NULL CHECK (data_origin IN ('real','synthetic')),
        declared_source TEXT,
        source_url TEXT,
        retrieved_at TEXT,
        period_start TEXT,
        period_end TEXT,
        query_bbox TEXT,
        parser_version TEXT NOT NULL,
        imported_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'in_progress',
        report_json TEXT,
        notes TEXT,
        UNIQUE (file_sha256, data_origin)
    )""",
    """CREATE TABLE IF NOT EXISTS raw_files (
        batch_id INTEGER PRIMARY KEY REFERENCES import_batches(id),
        gz_bytes BLOB NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS row_issues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id INTEGER NOT NULL REFERENCES import_batches(id),
        line_number INTEGER,
        status TEXT NOT NULL,
        reasons TEXT NOT NULL,
        raw_json TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS detections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        record_key TEXT NOT NULL UNIQUE,
        content_hash TEXT NOT NULL,
        batch_id INTEGER NOT NULL REFERENCES import_batches(id),
        line_number INTEGER,
        data_origin TEXT NOT NULL,
        instrument TEXT NOT NULL,
        platform TEXT NOT NULL,
        source_product TEXT,
        processing_stream TEXT NOT NULL,
        version_raw TEXT,
        collection TEXT,
        acq_date_raw TEXT,
        acq_time_raw TEXT,
        acq_datetime_utc TEXT NOT NULL,
        acq_date TEXT NOT NULL,
        latitude REAL NOT NULL,
        longitude REAL NOT NULL,
        x_ease REAL,
        y_ease REAL,
        confidence_raw TEXT,
        confidence_class TEXT,
        confidence_pct REAL,
        daynight TEXT,
        frp_mw REAL,
        brightness_k REAL,
        brightness2_k REAL,
        scan_km REAL,
        track_km REAL,
        footprint_km2 REAL,
        fire_type INTEGER
    )""",
    "CREATE INDEX IF NOT EXISTS ix_det_origin_date ON detections (data_origin, acq_date)",
    "CREATE INDEX IF NOT EXISTS ix_det_platform_date ON detections (platform, acq_date)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS detections_rtree USING rtree(id, minx, maxx, miny, maxy)",
    """CREATE TABLE IF NOT EXISTS availability (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_origin TEXT NOT NULL,
        platform TEXT NOT NULL,
        processing_stream TEXT NOT NULL,
        date_start TEXT NOT NULL,
        date_end TEXT NOT NULL,
        bbox TEXT,
        batch_id INTEGER REFERENCES import_batches(id),
        basis TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS coverage_sets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        data_origin TEXT NOT NULL,
        grid_size_km INTEGER NOT NULL,
        quantity TEXT NOT NULL,
        description TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS coverage (
        set_id INTEGER NOT NULL REFERENCES coverage_sets(id),
        platform TEXT NOT NULL,
        cell_id TEXT NOT NULL,
        date TEXT NOT NULL,
        valid_looks REAL NOT NULL,
        nominal_looks REAL NOT NULL,
        PRIMARY KEY (set_id, platform, cell_id, date)
    ) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS landcover (
        set_id INTEGER NOT NULL REFERENCES coverage_sets(id),
        cell_id TEXT NOT NULL,
        lc_class TEXT NOT NULL,
        PRIMARY KEY (set_id, cell_id)
    ) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS analysis_cache (key TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)""",
    # Immutability of raw evidence
    """CREATE TRIGGER IF NOT EXISTS raw_files_no_update BEFORE UPDATE ON raw_files
       BEGIN SELECT RAISE(ABORT, 'raw_files is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS raw_files_no_delete BEFORE DELETE ON raw_files
       BEGIN SELECT RAISE(ABORT, 'raw_files is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS row_issues_no_update BEFORE UPDATE ON row_issues
       BEGIN SELECT RAISE(ABORT, 'row_issues is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS detections_no_update BEFORE UPDATE ON detections
       BEGIN SELECT RAISE(ABORT, 'detections are append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS detections_no_delete BEFORE DELETE ON detections
       BEGIN SELECT RAISE(ABORT, 'detections are append-only'); END""",
]


def _locked(fn):
    """Serialise all use of the shared sqlite3 connection across API threads."""

    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        with self._lock:
            return fn(self, *a, **kw)

    return wrapper


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SqliteStore(Store):
    kind = "sqlite"

    # Detections/coverage of one origin are cached in memory as DataFrames after
    # the first query (invalidated whenever data_version changes). SQLite row
    # fetching dominates query time; for pilot-scale datasets (<= a few million
    # rows) filtering an in-memory frame is ~10x faster. PostGIS does not need this.
    MEMORY_CACHE_MAX_ROWS = 5_000_000

    def __init__(self, path: str | Path, memory_cache: bool = True):
        self.path = str(path)
        self.memory_cache = memory_cache
        self._frames: dict = {}
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL" if self.path != ":memory:" else "PRAGMA journal_mode=MEMORY")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA synchronous=NORMAL")

    # ---------------------------------------------------------------- lifecycle
    @_locked
    def migrate(self) -> list[str]:
        with self._lock, self.conn:
            for stmt in DDL:
                self.conn.execute(stmt)
            cur = self.conn.execute("SELECT version FROM schema_migrations WHERE version=?", (SCHEMA_VERSION,))
            if cur.fetchone() is None:
                self.conn.execute("INSERT INTO schema_migrations VALUES (?, ?)", (SCHEMA_VERSION, _now()))
                return [f"sqlite schema v{SCHEMA_VERSION}"]
        return []

    @_locked
    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- imports
    @_locked
    def find_batch_by_sha(self, sha256, data_origin):
        r = self.conn.execute("SELECT * FROM import_batches WHERE file_sha256=? AND data_origin=? AND status='complete'", (sha256, data_origin)).fetchone()
        return self._batch_dict(r) if r else None

    @_locked
    def create_batch(self, meta: dict, raw_gz: bytes) -> int:
        with self._lock, self.conn:
            # a previous attempt that crashed mid-import leaves an in_progress batch;
            # it is marked failed (kept for audit) so the retry can proceed
            self.conn.execute(
                "UPDATE import_batches SET status='failed' WHERE file_sha256=? AND data_origin=? AND status='in_progress'",
                (meta["file_sha256"], meta["data_origin"]),
            )
            prev = self.conn.execute(
                "SELECT id FROM import_batches WHERE file_sha256=? AND data_origin=?", (meta["file_sha256"], meta["data_origin"])
            ).fetchone()
            if prev is not None:
                # UNIQUE(sha, origin): reuse the failed row's slot by renaming its sha
                self.conn.execute("UPDATE import_batches SET file_sha256 = file_sha256 || ':failed:' || id WHERE id=?", (prev["id"],))
            cur = self.conn.execute(
                """INSERT INTO import_batches (file_name, file_sha256, data_origin, declared_source, source_url,
                   retrieved_at, period_start, period_end, query_bbox, parser_version, imported_at, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    meta["file_name"],
                    meta["file_sha256"],
                    meta["data_origin"],
                    meta.get("declared_source"),
                    meta.get("source_url"),
                    meta.get("retrieved_at"),
                    meta.get("period_start"),
                    meta.get("period_end"),
                    json.dumps(meta.get("query_bbox")) if meta.get("query_bbox") else None,
                    meta["parser_version"],
                    _now(),
                    meta.get("notes"),
                ),
            )
            bid = int(cur.lastrowid)
            self.conn.execute("INSERT INTO raw_files VALUES (?, ?)", (bid, raw_gz))
            return bid

    @_locked
    def finish_batch(self, batch_id, report, status="complete"):
        with self._lock, self.conn:
            self.conn.execute("UPDATE import_batches SET status=?, report_json=? WHERE id=?", (status, json.dumps(report), batch_id))

    @_locked
    def record_row_issues(self, batch_id, issues):
        if not issues:
            return
        with self._lock, self.conn:
            self.conn.executemany(
                "INSERT INTO row_issues (batch_id, line_number, status, reasons, raw_json) VALUES (?,?,?,?,?)",
                [(batch_id, i.get("line"), i["status"], json.dumps(i["reasons"]), json.dumps(i.get("raw"), default=str)) for i in issues],
            )

    @_locked
    def existing_keys(self, keys):
        out: dict[str, str] = {}
        for i in range(0, len(keys), 900):
            chunk = keys[i : i + 900]
            q = "SELECT record_key, content_hash FROM detections WHERE record_key IN (%s)" % ",".join("?" * len(chunk))
            out.update({r[0]: r[1] for r in self.conn.execute(q, chunk)})
        return out

    @_locked
    def insert_detections(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        cols = DETECTION_COLUMNS
        rows = df[cols].astype(object).where(df[cols].notna(), None).values.tolist()
        with self._lock, self.conn:
            start = self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM detections").fetchone()[0]
            self.conn.executemany(f"INSERT INTO detections ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", rows)
            ids = np.arange(start + 1, start + 1 + len(rows))
            lon = df["longitude"].to_numpy(float)
            lat = df["latitude"].to_numpy(float)
            self.conn.executemany(
                "INSERT INTO detections_rtree VALUES (?,?,?,?,?)",
                list(zip(ids.tolist(), lon.tolist(), lon.tolist(), lat.tolist(), lat.tolist())),
            )
        return len(rows)

    @_locked
    def add_availability(self, rows):
        if not rows:
            return
        with self._lock, self.conn:
            self.conn.executemany(
                """INSERT INTO availability (data_origin, platform, processing_stream, date_start, date_end, bbox, batch_id, basis)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [
                    (
                        r["data_origin"],
                        r["platform"],
                        r["processing_stream"],
                        r["date_start"],
                        r["date_end"],
                        json.dumps(r.get("bbox")) if r.get("bbox") else None,
                        r.get("batch_id"),
                        r["basis"],
                    )
                    for r in rows
                ],
            )

    @_locked
    def _batch_dict(self, r) -> dict:
        d = dict(r)
        if d.get("report_json"):
            d["report"] = json.loads(d.pop("report_json"))
        else:
            d.pop("report_json", None)
        if d.get("query_bbox"):
            d["query_bbox"] = json.loads(d["query_bbox"])
        return d

    @_locked
    def list_batches(self, data_origin=None):
        if data_origin:
            rows = self.conn.execute("SELECT * FROM import_batches WHERE data_origin=? ORDER BY id", (data_origin,))
        else:
            rows = self.conn.execute("SELECT * FROM import_batches ORDER BY id")
        return [self._batch_dict(r) for r in rows]

    @_locked
    def get_batch(self, batch_id):
        r = self.conn.execute("SELECT * FROM import_batches WHERE id=?", (batch_id,)).fetchone()
        return self._batch_dict(r) if r else None

    @_locked
    def get_raw_file(self, batch_id):
        r = self.conn.execute("SELECT gz_bytes FROM raw_files WHERE batch_id=?", (batch_id,)).fetchone()
        return bytes(r[0]) if r else None

    @_locked
    def row_issues(self, batch_id, limit=100):
        rows = self.conn.execute(
            "SELECT line_number, status, reasons, raw_json FROM row_issues WHERE batch_id=? ORDER BY line_number LIMIT ?",
            (batch_id, limit),
        )
        return [{"line": r[0], "status": r[1], "reasons": json.loads(r[2]), "raw": json.loads(r[3]) if r[3] else None} for r in rows]

    # ---------------------------------------------------------------- queries
    @_locked
    def _cached_frame(self, origin: str) -> pd.DataFrame | None:
        if not self.memory_cache:
            return None
        ver = self.data_version(origin)
        hit = self._frames.get(("det", origin))
        if hit and hit[0] == ver:
            return hit[1]
        n = self.conn.execute("SELECT COUNT(*) FROM detections WHERE data_origin=?", (origin,)).fetchone()[0]
        if n > self.MEMORY_CACHE_MAX_ROWS:
            return None
        # all columns except bulky raw strings that analysis never needs from the cache
        skip = {"record_key", "content_hash", "acq_date_raw", "acq_time_raw", "confidence_raw"}
        cols = [c for c in DETECTION_COLUMNS if c not in skip]
        df = pd.read_sql_query(f"SELECT {','.join(cols)} FROM detections WHERE data_origin=?", self.conn, params=[origin])
        for c in ("platform", "instrument", "processing_stream", "source_product", "version_raw", "confidence_class", "daynight", "data_origin", "collection"):
            df[c] = df[c].astype("category")
        with self._lock:
            self._frames[("det", origin)] = (ver, df)
        return df

    def warm(self) -> None:
        """Load the in-memory frames in the background at server start, so the
        first interactive request does not pay the one-off SQLite fetch."""
        for origin in ("synthetic", "real"):
            if self.list_batches(origin):
                self._cached_frame(origin)
                for cs in self.coverage_sets(origin):
                    if cs["quantity"] == "valid_looks":
                        self.query_coverage(cs["id"], ["E0:0:0"], "1900-01-01", "1900-01-01", None)

    @_locked
    def query_detections(self, q: DetectionQuery) -> pd.DataFrame:
        cols = q.columns or DETECTION_COLUMNS
        frame = self._cached_frame(q.data_origin)
        if frame is not None:
            m = (frame["acq_date"] >= q.date_start).to_numpy() & (frame["acq_date"] <= q.date_end).to_numpy()
            if q.platforms:
                m &= frame["platform"].isin(q.platforms).to_numpy()
            if q.streams:
                m &= frame["processing_stream"].isin(q.streams).to_numpy()
            lon = frame["longitude"].to_numpy()
            lat = frame["latitude"].to_numpy()
            bb = np.zeros(len(frame), dtype=bool)
            for w, s_, e, n in q.aoi.bboxes():
                bb |= (lon >= w) & (lon <= e) & (lat >= s_) & (lat <= n)
            m &= bb
            idx = np.nonzero(m)[0]
            if len(idx):
                idx = idx[q.aoi.contains(lon[idx], lat[idx])]
            missing = [c for c in cols if c not in frame.columns]
            if missing:  # raw-string columns are not cached: fetch them from SQLite for the selected rows only
                return self._query_detections_sql(q)
            out = frame.iloc[idx][cols].reset_index(drop=True)
            for c in out.columns:
                if isinstance(out[c].dtype, pd.CategoricalDtype):
                    out[c] = out[c].astype(object)
            return out
        return self._query_detections_sql(q)

    def _query_detections_sql(self, q: DetectionQuery) -> pd.DataFrame:
        cols = q.columns or DETECTION_COLUMNS
        sel = "d.id AS _id," + ",".join(f"d.{c}" for c in cols)
        frames = []
        for w, s, e, n in q.aoi.bboxes():
            sql = (
                f"SELECT {sel} FROM detections_rtree r JOIN detections d ON d.id = r.id "
                # overlap test: the R*Tree stores 32-bit bounds rounded outwards,
                # so containment tests could miss points on the bbox edge
                "WHERE r.maxx >= ? AND r.minx <= ? AND r.maxy >= ? AND r.miny <= ? "
                "AND d.data_origin = ? AND d.acq_date >= ? AND d.acq_date <= ?"
            )
            params: list = [w, e, s, n, q.data_origin, q.date_start, q.date_end]
            if q.platforms:
                sql += " AND d.platform IN (%s)" % ",".join("?" * len(q.platforms))
                params += list(q.platforms)
            if q.streams:
                sql += " AND d.processing_stream IN (%s)" % ",".join("?" * len(q.streams))
                params += list(q.streams)
            frames.append(pd.read_sql_query(sql, self.conn, params=params))
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["_id", *cols])
        if df.empty:
            return df.drop(columns=["_id"])
        df = df.drop_duplicates("_id").drop(columns=["_id"])  # polygon parts can share an edge
        inside = q.aoi.contains(df["longitude"].to_numpy(float), df["latitude"].to_numpy(float))
        return df[inside].reset_index(drop=True)

    @_locked
    def availability(self, data_origin):
        return pd.read_sql_query(
            "SELECT platform, processing_stream, date_start, date_end, bbox, batch_id, basis FROM availability WHERE data_origin=?",
            self.conn,
            params=[data_origin],
        )

    @_locked
    def dataset_summary(self, data_origin):
        rows = self.conn.execute(
            """SELECT platform, processing_stream, source_product, COUNT(*), MIN(acq_date), MAX(acq_date),
                      GROUP_CONCAT(DISTINCT version_raw)
               FROM detections WHERE data_origin=? GROUP BY platform, processing_stream, source_product
               ORDER BY platform, processing_stream""",
            (data_origin,),
        ).fetchall()
        return {
            "data_origin": data_origin,
            "series": [
                {
                    "platform": r[0],
                    "processing_stream": r[1],
                    "source_product": r[2],
                    "detections": r[3],
                    "first_date": r[4],
                    "last_date": r[5],
                    "versions": sorted(set((r[6] or "").split(","))),
                }
                for r in rows
            ],
            "batches": len(self.list_batches(data_origin)),
        }

    # ---------------------------------------------------------------- coverage
    @_locked
    def create_coverage_set(self, meta):
        with self._lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO coverage_sets (name, data_origin, grid_size_km, quantity, description, created_at) VALUES (?,?,?,?,?,?)",
                (meta["name"], meta["data_origin"], meta["grid_size_km"], meta["quantity"], meta.get("description"), _now()),
            )
            return int(cur.lastrowid)

    @_locked
    def insert_coverage(self, set_id, df):
        self._frames.pop(("cov", set_id), None)
        rows = list(
            zip(
                [set_id] * len(df),
                df["platform"].tolist(),
                df["cell_id"].tolist(),
                df["date"].tolist(),
                df["valid_looks"].astype(float).tolist(),
                df["nominal_looks"].astype(float).tolist(),
            )
        )
        with self._lock, self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO coverage VALUES (?,?,?,?,?,?)", rows)
        return len(rows)

    @_locked
    def coverage_sets(self, data_origin):
        rows = self.conn.execute("SELECT * FROM coverage_sets WHERE data_origin=? ORDER BY id", (data_origin,))
        return [dict(r) for r in rows]

    @_locked
    def query_coverage(self, set_id, cell_ids, date_start, date_end, platforms):
        if not cell_ids:
            return pd.DataFrame(columns=["platform", "cell_id", "date", "valid_looks", "nominal_looks"])
        if self.memory_cache:
            key = ("cov", set_id)
            hit = self._frames.get(key)
            if hit is None:
                cov = pd.read_sql_query("SELECT platform, cell_id, date, valid_looks, nominal_looks FROM coverage WHERE set_id=?", self.conn, params=[set_id])
                if len(cov) <= self.MEMORY_CACHE_MAX_ROWS:
                    for c in ("platform", "cell_id"):
                        cov[c] = cov[c].astype("category")
                    with self._lock:
                        self._frames[key] = (None, cov)
                    hit = self._frames[key]
            if hit is not None:
                cov = hit[1]
                m = cov["cell_id"].isin(cell_ids).to_numpy() & (cov["date"] >= date_start).to_numpy() & (cov["date"] <= date_end).to_numpy()
                if platforms:
                    m &= cov["platform"].isin(platforms).to_numpy()
                out = cov[m].reset_index(drop=True)
                out["platform"] = out["platform"].astype(object)
                out["cell_id"] = out["cell_id"].astype(object)
                return out
        self.conn.execute("CREATE TEMP TABLE IF NOT EXISTS _cells (cell_id TEXT PRIMARY KEY)")
        with self._lock:
            self.conn.execute("DELETE FROM _cells")
            self.conn.executemany("INSERT OR IGNORE INTO _cells VALUES (?)", [(c,) for c in cell_ids])
            sql = (
                "SELECT c.platform, c.cell_id, c.date, c.valid_looks, c.nominal_looks FROM coverage c "
                "JOIN _cells t ON t.cell_id = c.cell_id WHERE c.set_id=? AND c.date>=? AND c.date<=?"
            )
            params: list = [set_id, date_start, date_end]
            if platforms:
                sql += " AND c.platform IN (%s)" % ",".join("?" * len(platforms))
                params += list(platforms)
            return pd.read_sql_query(sql, self.conn, params=params)

    @_locked
    def insert_landcover(self, set_id, df):
        with self._lock, self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO landcover VALUES (?,?,?)",
                list(zip([set_id] * len(df), df["cell_id"].tolist(), df["lc_class"].tolist())),
            )
        return len(df)

    @_locked
    def query_landcover(self, set_id, cell_ids):
        if not cell_ids:
            return pd.DataFrame(columns=["cell_id", "lc_class"])
        out = []
        for i in range(0, len(cell_ids), 900):
            chunk = cell_ids[i : i + 900]
            out.append(
                pd.read_sql_query(
                    "SELECT cell_id, lc_class FROM landcover WHERE set_id=? AND cell_id IN (%s)" % ",".join("?" * len(chunk)),
                    self.conn,
                    params=[set_id, *chunk],
                )
            )
        return pd.concat(out, ignore_index=True)

    # ---------------------------------------------------------------- cache
    @_locked
    def cache_get(self, key):
        r = self.conn.execute("SELECT payload FROM analysis_cache WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    @_locked
    def cache_put(self, key, payload):
        with self._lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO analysis_cache VALUES (?,?,?)", (key, _now(), payload))

    @_locked
    def data_version(self, data_origin):
        r = self.conn.execute("SELECT COUNT(*), COALESCE(MAX(id),0) FROM import_batches WHERE data_origin=? AND status='complete'", (data_origin,)).fetchone()
        c = self.conn.execute("SELECT COUNT(*), COALESCE(MAX(id),0) FROM coverage_sets WHERE data_origin=?", (data_origin,)).fetchone()
        return f"b{r[0]}-{r[1]}-c{c[0]}-{c[1]}"
