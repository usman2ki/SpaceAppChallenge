import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def small_spec(**kw):
    """A small, fast synthetic world (still SYNTHETIC) for tests."""
    from ember_atlas.synthetic.generator import SyntheticSpec

    base = dict(
        bbox=(-48.0, -11.0, -46.5, -9.5),
        start="2013-01-01",
        end="2019-12-31",
        windows={
            "Terra": ("2013-01-01", "2019-12-31"),
            "Aqua": ("2013-01-01", "2018-12-31"),
            "Suomi-NPP": ("2014-01-01", "2019-12-31"),
            "NOAA-20": ("2018-04-01", "2019-12-31"),
        },
        excess_episode={"year": 2017, "doy": (210, 252), "factor": 4.0, "quadrant": "SE"},
        deficit_episode={"year": 2015, "doy": (200, 260), "factor": 0.4},
        mean_level=0.05,
    )
    base.update(kw)
    return SyntheticSpec(**base)


@pytest.fixture(scope="session")
def small_world_store(tmp_path_factory):
    from ember_atlas.store.sqlite_store import SqliteStore
    from ember_atlas.synthetic.seed import seed_demo

    path = tmp_path_factory.mktemp("db") / "small.sqlite"
    store = SqliteStore(path)
    store.migrate()
    res = seed_demo(store, spec=small_spec())
    store._world = res["world"]
    store._seed = res
    return store
