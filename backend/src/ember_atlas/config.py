"""Runtime settings (environment) and the versioned scientific method config."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
# repository checkout (editable install): use it; installed package: use the working directory
PROJECT_ROOT = _REPO if (_REPO / "backend").exists() and (_REPO / "frontend").exists() else Path.cwd()


@dataclass(frozen=True)
class Settings:
    database_url: str
    frontend_dist: Path | None
    artifacts_dir: Path
    firms_map_key_env: str = "FIRMS_MAP_KEY"
    firms_cache_dir: Path | None = None
    firms_max_requests: int = 200

    @property
    def firms_map_key(self) -> str | None:
        # Read on demand; never stored on the object, never logged.
        return os.environ.get(self.firms_map_key_env) or None


def get_settings() -> Settings:
    db = os.environ.get("EMBER_DATABASE_URL", f"sqlite:///{PROJECT_ROOT / 'data' / 'ember.sqlite'}")
    dist = os.environ.get("EMBER_FRONTEND_DIST", str(PROJECT_ROOT / "frontend" / "dist"))
    return Settings(
        database_url=db,
        frontend_dist=Path(dist) if dist else None,
        artifacts_dir=Path(os.environ.get("EMBER_ARTIFACTS_DIR", str(PROJECT_ROOT / "artifacts"))),
        firms_cache_dir=Path(os.environ.get("EMBER_FIRMS_CACHE", str(PROJECT_ROOT / "data" / "firms_cache"))),
        firms_max_requests=int(os.environ.get("EMBER_FIRMS_MAX_REQUESTS", "200")),
    )


@lru_cache(maxsize=1)
def _base_method() -> dict:
    text = resources.files("ember_atlas.resources").joinpath("method_v1.toml").read_text()
    return tomllib.loads(text)


def method_config(overrides: dict | None = None) -> dict:
    cfg = copy.deepcopy(_base_method())
    for section, values in (overrides or {}).items():
        if section not in cfg or not isinstance(values, dict):
            raise ValueError(f"Unknown method config section '{section}'")
        for k, v in values.items():
            if k not in cfg[section]:
                raise ValueError(f"Unknown method config key '{section}.{k}'")
            cfg[section][k] = v
    return cfg


def method_fingerprint(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]
