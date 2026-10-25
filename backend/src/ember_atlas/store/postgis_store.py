"""PostgreSQL/PostGIS store (the Docker Compose backend).

Spatial filtering is done in the database with a GiST index on the
detection points and ``ST_Intersects`` against the exact AOI MultiPolygon
(already split at the antimeridian by ember_atlas.geo.polygons). The schema
lives in ember_atlas/resources/migrations/postgis/*.sql.

Status: written against the same contract as the SQLite store and covered by
tests/test_store_contract.py, which runs only when EMBER_TEST_DATABASE_URL
points at a PostGIS database. The build sandbox had no PostGIS, so this module
has NOT been executed there (see docs/status.md).
"""

from __future__ import annotations

import json
import threading
from importlib import resources

import pandas as pd

from .base import DETECTION_COLUMNS, DetectionQuery, Store

MIGRATIONS = resources.files("ember_atlas.resources").joinpath("migrations", "postgis")

_TEXT_DATES = {
    "acq_date": "to_char(d.acq_date, 'YYYY-MM-DD') AS acq_date",
    "acq_datetime_utc": "to_char(d.acq_datetime_utc AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:00\"Z\"') AS acq_datetime_utc",
}


def _select(cols: list[str]) -> str:
    return ", ".join(_TEXT_DATES.get(c, f"d.{c}") for c in cols)


class PostgisStore(Store):
    kind = "postgis"

    def __init__(self, url: str):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - depends on deployment
            raise RuntimeError("PostGIS store needs psycopg: pip install 'psycopg[binary]'") from exc
        self._psycopg = psycopg
        self.url = url
        self._lock = threading.RLock()
        self.conn = psycopg.connect(url, autocommit=True)

    # ------------------------------------------------------------------ helpers
    def _q(self, sql: str, params=None) -> list[tuple]:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def _df(self, sql: str, params=None) -> pd.DataFrame:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c.name for c in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)

    # ------------------------------------------------------------------ lifecycle
    def migrate(self) -> list[str]:
        applied = []
        with self._lock:
            self.conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            have = {r[0] for r in self._q("SELECT version FROM schema_migrations")}
            for f in sorted((p for p in MIGRATIONS.iterdir() if p.name.endswith(".sql")), key=lambda p: p.name):
                v = int(f.name.split("_", 1)[0])
                if v in have:
                    continue
                with self.conn.transaction():
                    self.conn.execute(f.read_text())
                applied.append(f.name)
        return applied

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ imports
    def find_batch_by_sha(self, sha256, data_origin):
        df = self._df("SELECT * FROM import_batches WHERE file_sha256=%s AND data_origin=%s AND status='complete'", (sha256, data_origin))
        return self._batch(df.iloc[0].to_dict()) if len(df) else None

    def create_batch(self, meta, raw_gz):
        with self._lock, self.conn.transaction():
            self.conn.execute(
                "UPDATE import_batches SET status='failed' WHERE file_sha256=%s AND data_origin=%s AND status='in_progress'",
                (meta["file_sha256"], meta["data_origin"]),
            )
            self.conn.execute(
                "UPDATE import_batches SET file_sha256 = file_sha256 || ':failed:' || id WHERE file_sha256=%s AND data_origin=%s",
                (meta["file_sha256"], meta["data_origin"]),
            )
            row = self.conn.execute(
                """INSERT INTO import_batches (file_name, file_sha256, data_origin, declared_source, source_url, retrieved_at,
                   period_start, period_end, query_bbox, parser_version, notes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
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
                    meta.get("notes"),
                ),
            ).fetchone()
            bid = int(row[0])
            self.conn.execute("INSERT INTO raw_files VALUES (%s, %s)", (bid, raw_gz))
        return bid

    def finish_batch(self, batch_id, report, status="complete"):
        self._q("UPDATE import_batches SET status=%s, report_json=%s WHERE id=%s", (status, json.dumps(report, default=str), batch_id))

    def record_row_issues(self, batch_id, issues):
        if not issues:
            return
        with self._lock, self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO row_issues (batch_id, line_number, status, reasons, raw_json) VALUES (%s,%s,%s,%s,%s)",
                [(batch_id, i.get("line"), i["status"], json.dumps(i["reasons"]), json.dumps(i.get("raw"), default=str)) for i in issues],
            )

    def existing_keys(self, keys):
        if not keys:
            return {}
        return dict(self._q("SELECT record_key, content_hash FROM detections WHERE record_key = ANY(%s)", (list(keys),)))

    def insert_detections(self, df):
        if df.empty:
            return 0
        cols = DETECTION_COLUMNS
        rows = df[cols].astype(object).where(df[cols].notna(), None).values.tolist()
        with self._lock, self.conn.transaction(), self.conn.cursor() as cur:
            with cur.copy(f"COPY detections ({', '.join(cols)}) FROM STDIN") as cp:
                for r in rows:
                    cp.write_row(r)
        return len(rows)

    def add_availability(self, rows):
        if not rows:
            return
        with self._lock, self.conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO availability (data_origin, platform, processing_stream, date_start, date_end, bbox, batch_id, basis)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
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

    def _batch(self, d: dict) -> dict:
        d = {k: (None if (isinstance(v, float) and pd.isna(v)) else v) for k, v in d.items()}
        rep = d.pop("report_json", None)
        if rep:
            d["report"] = rep if isinstance(rep, dict) else json.loads(rep)
        for k in ("period_start", "period_end", "imported_at"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        return d

    def list_batches(self, data_origin=None):
        if data_origin:
            df = self._df("SELECT * FROM import_batches WHERE data_origin=%s ORDER BY id", (data_origin,))
        else:
            df = self._df("SELECT * FROM import_batches ORDER BY id")
        return [self._batch(r) for r in df.to_dict("records")]

    def get_batch(self, batch_id):
        df = self._df("SELECT * FROM import_batches WHERE id=%s", (batch_id,))
        return self._batch(df.iloc[0].to_dict()) if len(df) else None

    def get_raw_file(self, batch_id):
        r = self._q("SELECT gz_bytes FROM raw_files WHERE batch_id=%s", (batch_id,))
        return bytes(r[0][0]) if r else None

    def row_issues(self, batch_id, limit=100):
        rows = self._q("SELECT line_number, status, reasons, raw_json FROM row_issues WHERE batch_id=%s ORDER BY line_number LIMIT %s", (batch_id, limit))
        return [{"line": r[0], "status": r[1], "reasons": r[2], "raw": r[3]} for r in rows]

    # ------------------------------------------------------------------ queries
    def query_detections(self, q: DetectionQuery) -> pd.DataFrame:
        cols = q.columns or DETECTION_COLUMNS
        sql = (
            f"SELECT {_select(cols)} FROM detections d WHERE d.data_origin = %s AND d.acq_date BETWEEN %s AND %s "
            "AND ST_Intersects(d.geom, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))"
        )
        params: list = [q.data_origin, q.date_start, q.date_end, json.dumps(q.aoi.to_geojson())]
        if q.platforms:
            sql += " AND d.platform = ANY(%s)"
            params.append(list(q.platforms))
        if q.streams:
            sql += " AND d.processing_stream = ANY(%s)"
            params.append(list(q.streams))
        df = self._df(sql, params)
        if df.empty:
            return pd.DataFrame(columns=cols)
        for c in ("fire_type", "batch_id", "line_number"):
            if c in df:
                df[c] = pd.to_numeric(df[c])
        return df

    def availability(self, data_origin):
        df = self._df(
            "SELECT platform, processing_stream, to_char(date_start,'YYYY-MM-DD') AS date_start, to_char(date_end,'YYYY-MM-DD') AS date_end, "
            "bbox::text AS bbox, batch_id, basis FROM availability WHERE data_origin=%s",
            (data_origin,),
        )
        return df if len(df) else pd.DataFrame(columns=["platform", "processing_stream", "date_start", "date_end", "bbox", "batch_id", "basis"])

    def dataset_summary(self, data_origin):
        rows = self._q(
            """SELECT platform, processing_stream, source_product, COUNT(*), to_char(MIN(acq_date),'YYYY-MM-DD'), to_char(MAX(acq_date),'YYYY-MM-DD'),
                      array_agg(DISTINCT version_raw)
               FROM detections WHERE data_origin=%s GROUP BY platform, processing_stream, source_product ORDER BY platform, processing_stream""",
            (data_origin,),
        )
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
                    "versions": sorted(v for v in r[6] if v is not None),
                }
                for r in rows
            ],
            "batches": len(self.list_batches(data_origin)),
        }

    # ------------------------------------------------------------------ coverage
    def create_coverage_set(self, meta):
        r = self._q(
            "INSERT INTO coverage_sets (name, data_origin, grid_size_km, quantity, description) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (meta["name"], meta["data_origin"], meta["grid_size_km"], meta["quantity"], meta.get("description")),
        )
        return int(r[0][0])

    def insert_coverage(self, set_id, df):
        with self._lock, self.conn.transaction(), self.conn.cursor() as cur:
            with cur.copy("COPY coverage (set_id, platform, cell_id, date, valid_looks, nominal_looks) FROM STDIN") as cp:
                for row in zip(df["platform"], df["cell_id"], df["date"], df["valid_looks"].astype(float), df["nominal_looks"].astype(float)):
                    cp.write_row((set_id, *row))
        return len(df)

    def coverage_sets(self, data_origin):
        df = self._df("SELECT * FROM coverage_sets WHERE data_origin=%s ORDER BY id", (data_origin,))
        return df.to_dict("records")

    def query_coverage(self, set_id, cell_ids, date_start, date_end, platforms):
        sql = (
            "SELECT platform, cell_id, to_char(date,'YYYY-MM-DD') AS date, valid_looks, nominal_looks FROM coverage "
            "WHERE set_id=%s AND cell_id = ANY(%s) AND date BETWEEN %s AND %s"
        )
        params: list = [set_id, list(cell_ids), date_start, date_end]
        if platforms:
            sql += " AND platform = ANY(%s)"
            params.append(list(platforms))
        df = self._df(sql, params)
        return df if len(df) else pd.DataFrame(columns=["platform", "cell_id", "date", "valid_looks", "nominal_looks"])

    def insert_landcover(self, set_id, df):
        with self._lock, self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO landcover VALUES (%s,%s,%s) ON CONFLICT (set_id, cell_id) DO UPDATE SET lc_class = EXCLUDED.lc_class",
                list(zip([set_id] * len(df), df["cell_id"], df["lc_class"])),
            )
        return len(df)

    def query_landcover(self, set_id, cell_ids):
        df = self._df("SELECT cell_id, lc_class FROM landcover WHERE set_id=%s AND cell_id = ANY(%s)", (set_id, list(cell_ids)))
        return df if len(df) else pd.DataFrame(columns=["cell_id", "lc_class"])

    # ------------------------------------------------------------------ cache
    def cache_get(self, key):
        r = self._q("SELECT payload FROM analysis_cache WHERE key=%s", (key,))
        return r[0][0] if r else None

    def cache_put(self, key, payload):
        self._q(
            "INSERT INTO analysis_cache (key, payload) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET payload=EXCLUDED.payload, created_at=now()",
            (key, payload),
        )

    def data_version(self, data_origin):
        r = self._q("SELECT COUNT(*), COALESCE(MAX(id),0) FROM import_batches WHERE data_origin=%s AND status='complete'", (data_origin,))[0]
        c = self._q("SELECT COUNT(*), COALESCE(MAX(id),0) FROM coverage_sets WHERE data_origin=%s", (data_origin,))[0]
        return f"b{r[0]}-{r[1]}-c{c[0]}-{c[1]}"
