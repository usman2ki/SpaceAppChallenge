"""Open a store from a URL: ``sqlite:///path.sqlite`` or ``postgresql://...``."""

from __future__ import annotations

from .base import Store


def open_store(url: str) -> Store:
    if url.startswith("sqlite:///"):
        from .sqlite_store import SqliteStore

        return SqliteStore(url[len("sqlite:///") :])
    if url == "sqlite://:memory:" or url == ":memory:":
        from .sqlite_store import SqliteStore

        return SqliteStore(":memory:")
    if url.startswith(("postgresql://", "postgres://", "postgresql+psycopg://")):
        from .postgis_store import PostgisStore

        return PostgisStore(url.replace("postgresql+psycopg://", "postgresql://"))
    raise ValueError("EMBER_DATABASE_URL must start with sqlite:/// or postgresql://")
