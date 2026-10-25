import math

import numpy as np
import pytest
from matplotlib.path import Path

from ember_atlas.geo import equal_area as ea
from ember_atlas.geo.polygons import AOIError, aoi_from_bbox, parse_aoi


def test_projection_matches_published_ease2_extent():
    x, _ = ea.forward(180.0, 0.0)
    assert x == pytest.approx(-ea.X_MIN, abs=0.01)  # 17367530.445 m (NSIDC)
    # the global grid's top edge (7314540.83 m) is reached at ~85.044 N
    _, lat = ea.inverse(0.0, ea.Y_MAX)
    assert float(lat) == pytest.approx(85.044, abs=0.001)


def test_projection_round_trip():
    rng = np.random.default_rng(1)
    lon = rng.uniform(-180, 180, 1000)
    lat = rng.uniform(-85, 85, 1000)
    lo2, la2 = ea.inverse(*ea.forward(lon, lat))
    assert np.max(np.abs(lo2 - lon)) < 1e-7
    assert np.max(np.abs(la2 - lat)) < 1e-7


def test_equal_area_box_matches_authalic_formula():
    # exact ellipsoidal area of a lon/lat box: a^2/2 * dlambda * (q(phi2) - q(phi1))
    def q(phi):
        return float(ea._q(np.array(math.sin(math.radians(phi)))))

    aoi = aoi_from_bbox(10, -1, 11, 0)
    exact = ea.A**2 / 2 * math.radians(1) * (q(0) - q(-1)) / 1e6
    assert aoi.area_km2() == pytest.approx(exact, rel=1e-6)


def test_grid_cells_have_equal_area_and_nest():
    g18, g36 = ea.GridSpec(18), ea.GridSpec(36)
    assert g18.cell_area_km2 == pytest.approx((2 * ea.M09_CELL_M / 1000) ** 2)
    x, y = ea.forward(np.array([-47.1, 30.2]), np.array([-10.3, 60.1]))
    c, r = g18.col_row(x, y)
    pc, pr = g18.parent(c, r, g36)
    c36, r36 = g36.col_row(x, y)
    assert np.array_equal(pc, c36) and np.array_equal(pr, r36)
    with pytest.raises(ValueError):
        ea.GridSpec(25)


def test_point_in_polygon_matches_matplotlib_reference():
    ring = [[0, 0], [10, 0], [10, 10], [5, 4], [0, 10], [0, 0]]  # concave
    hole = [[2, 1], [3, 1], [3, 2], [2, 2], [2, 1]]
    aoi = parse_aoi({"type": "Polygon", "coordinates": [ring, hole]})
    rng = np.random.default_rng(2)
    pts = rng.uniform(-1, 11, (5000, 2))
    ours = aoi.contains(pts[:, 0], pts[:, 1])
    ref = Path(ring).contains_points(pts) & ~Path(hole).contains_points(pts)
    # points exactly on the boundary are measure-zero; allow none to differ here
    assert (ours != ref).sum() == 0


def test_polygon_filter_is_exact_not_bbox():
    tri = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [0, 10], [0, 0]]]}
    aoi = parse_aoi(tri)
    # (8, 8) is inside the bbox but outside the triangle
    assert not aoi.contains(np.array([8.0]), np.array([8.0]))[0]
    assert aoi.contains(np.array([1.0]), np.array([1.0]))[0]


def test_antimeridian_polygon_is_split_and_filters_both_sides():
    poly = {"type": "Polygon", "coordinates": [[[170, -20], [-170, -20], [-170, -10], [170, -10], [170, -20]]]}
    aoi = parse_aoi(poly)
    assert aoi.crosses_antimeridian
    assert len(aoi.polygons) == 2
    lon = np.array([175.0, -175.0, 0.0, 179.99, -179.99])
    lat = np.array([-15.0, -15.0, -15.0, -15.0, -15.0])
    assert aoi.contains(lon, lat).tolist() == [True, True, False, True, True]
    # area equals a 20 x 10 degree box
    plain = aoi_from_bbox(0, -20, 20, -10)
    assert aoi.area_km2() == pytest.approx(plain.area_km2(), rel=1e-6)


def test_antimeridian_bbox_shorthand():
    aoi = aoi_from_bbox(170, -20, -170, -10)
    assert aoi.crosses_antimeridian and len(aoi.polygons) == 2


@pytest.mark.parametrize(
    "geom,msg",
    [
        ({"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}, "self-intersecting"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [0, 0]]]}, "at least 3"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 95], [0, 0]]]}, "latitude"),
        ({"type": "Point", "coordinates": [0, 0]}, "Unsupported"),
        (
            {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]], [[5, 5], [6, 5], [6, 6], [5, 5]]]},
            "hole",
        ),
        ({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [2, 0], [0, 0]]]}, "zero area"),
        ({"type": "Polygon", "coordinates": [[[0, 0], ["a", 0], [1, 1], [0, 0]]]}, "non-numeric"),
    ],
)
def test_invalid_polygons_are_rejected_with_reason(geom, msg):
    with pytest.raises(AOIError, match=msg):
        parse_aoi(geom)


def test_unclosed_ring_is_closed_with_warning():
    aoi = parse_aoi({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]})
    assert any("closed automatically" in w for w in aoi.warnings)


def test_cell_overlaps_cover_aoi_area():
    aoi = aoi_from_bbox(-48.0, -11.0, -46.0, -9.5)
    g = ea.GridSpec(18)
    ov = aoi.cell_overlaps(g, samples=8)
    est = sum(ov.values()) * g.cell_area_km2
    assert est == pytest.approx(aoi.area_km2(), rel=0.03)
    assert all(0 < f <= 1 for f in ov.values())
