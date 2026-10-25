# Migrations

* PostGIS: `src/ember_atlas/resources/migrations/postgis/*.sql` (packaged with
  the code so installed copies can find them). Applied in order by
  `ember migrate` when `EMBER_DATABASE_URL` is a `postgresql://` URL; applied
  versions are recorded in `schema_migrations`.
* SQLite: the schema is defined in `src/ember_atlas/store/sqlite_store.py`
  (`DDL`, schema version 1) and applied by the same command.
