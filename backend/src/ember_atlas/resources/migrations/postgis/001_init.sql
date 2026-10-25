-- Ember Atlas PostGIS schema v1.
-- Mirrors the SQLite schema (ember_atlas/store/sqlite_store.py); both stores
-- satisfy tests/test_store_contract.py. Applied by `ember migrate`.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    integer PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS import_batches (
    id              serial PRIMARY KEY,
    file_name       text NOT NULL,
    file_sha256     text NOT NULL,
    data_origin     text NOT NULL CHECK (data_origin IN ('real', 'synthetic')),
    declared_source text,
    source_url      text,
    retrieved_at    text,
    period_start    date,
    period_end      date,
    query_bbox      jsonb,
    parser_version  text NOT NULL,
    imported_at     timestamptz NOT NULL DEFAULT now(),
    status          text NOT NULL DEFAULT 'in_progress',
    report_json     jsonb,
    notes           text,
    UNIQUE (file_sha256, data_origin)
);

-- original uploaded bytes (gzip), never modified
CREATE TABLE IF NOT EXISTS raw_files (
    batch_id integer PRIMARY KEY REFERENCES import_batches (id),
    gz_bytes bytea NOT NULL
);

-- rejected / duplicate / conflicting rows with reasons, never modified
CREATE TABLE IF NOT EXISTS row_issues (
    id          bigserial PRIMARY KEY,
    batch_id    integer NOT NULL REFERENCES import_batches (id),
    line_number integer,
    status      text NOT NULL,
    reasons     jsonb NOT NULL,
    raw_json    jsonb
);
CREATE INDEX IF NOT EXISTS row_issues_batch_ix ON row_issues (batch_id, line_number);

CREATE TABLE IF NOT EXISTS detections (
    id                bigserial PRIMARY KEY,
    record_key        text NOT NULL UNIQUE,
    content_hash      text NOT NULL,
    batch_id          integer NOT NULL REFERENCES import_batches (id),
    line_number       integer,
    data_origin       text NOT NULL,
    instrument        text NOT NULL,
    platform          text NOT NULL,
    source_product    text,
    processing_stream text NOT NULL,
    version_raw       text,
    collection        text,
    acq_date_raw      text,
    acq_time_raw      text,
    acq_datetime_utc  timestamptz NOT NULL,
    acq_date          date NOT NULL,
    latitude          double precision NOT NULL,
    longitude         double precision NOT NULL,
    x_ease            double precision,
    y_ease            double precision,
    confidence_raw    text,
    confidence_class  text,
    confidence_pct    double precision,
    daynight          text,
    frp_mw            double precision,
    brightness_k      double precision,
    brightness2_k     double precision,
    scan_km           double precision,
    track_km          double precision,
    footprint_km2     double precision,
    fire_type         smallint,
    geom              geometry(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)) STORED
);
CREATE INDEX IF NOT EXISTS detections_geom_gix ON detections USING gist (geom);
CREATE INDEX IF NOT EXISTS detections_origin_date_ix ON detections (data_origin, acq_date);
CREATE INDEX IF NOT EXISTS detections_platform_date_ix ON detections (platform, acq_date);

CREATE TABLE IF NOT EXISTS availability (
    id                serial PRIMARY KEY,
    data_origin       text NOT NULL,
    platform          text NOT NULL,
    processing_stream text NOT NULL,
    date_start        date NOT NULL,
    date_end          date NOT NULL,
    bbox              jsonb,
    batch_id          integer REFERENCES import_batches (id),
    basis             text NOT NULL
);

CREATE TABLE IF NOT EXISTS coverage_sets (
    id           serial PRIMARY KEY,
    name         text NOT NULL,
    data_origin  text NOT NULL,
    grid_size_km integer NOT NULL,
    quantity     text NOT NULL,
    description  text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS coverage (
    set_id        integer NOT NULL REFERENCES coverage_sets (id),
    platform      text NOT NULL,
    cell_id       text NOT NULL,
    date          date NOT NULL,
    valid_looks   real NOT NULL,
    nominal_looks real NOT NULL,
    PRIMARY KEY (set_id, platform, cell_id, date)
);
CREATE INDEX IF NOT EXISTS coverage_cell_date_ix ON coverage (set_id, cell_id, date);

CREATE TABLE IF NOT EXISTS landcover (
    set_id   integer NOT NULL REFERENCES coverage_sets (id),
    cell_id  text NOT NULL,
    lc_class text NOT NULL,
    PRIMARY KEY (set_id, cell_id)
);

CREATE TABLE IF NOT EXISTS analysis_cache (
    key        text PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    payload    text NOT NULL
);

-- immutability of raw evidence
CREATE OR REPLACE FUNCTION ember_forbid_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is immutable (append-only raw evidence)', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS raw_files_immutable ON raw_files;
CREATE TRIGGER raw_files_immutable BEFORE UPDATE OR DELETE ON raw_files FOR EACH ROW EXECUTE FUNCTION ember_forbid_change();
DROP TRIGGER IF EXISTS row_issues_immutable ON row_issues;
CREATE TRIGGER row_issues_immutable BEFORE UPDATE OR DELETE ON row_issues FOR EACH ROW EXECUTE FUNCTION ember_forbid_change();
DROP TRIGGER IF EXISTS detections_immutable ON detections;
CREATE TRIGGER detections_immutable BEFORE UPDATE OR DELETE ON detections FOR EACH ROW EXECUTE FUNCTION ember_forbid_change();

INSERT INTO schema_migrations (version) VALUES (1) ON CONFLICT DO NOTHING;
