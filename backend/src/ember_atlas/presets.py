"""Preset areas of interest. Coordinates are WGS84 lon/lat."""

PILOT_BBOX = (-48.5, -11.5, -45.5, -9.0)

PRESETS = [
    {
        "id": "pilot-cerrado",
        "name": "Pilot: eastern Tocantins (Cerrado), Brazil",
        "description": "Pilot region: frequent dry-season burning, long MODIS/VIIRS overlap, low latitude (MODIS swath gaps). The synthetic demo world is placed here; real data must be imported separately.",
        "geometry": {"type": "Polygon", "coordinates": [[[-48.5, -11.5], [-45.5, -11.5], [-45.5, -9.0], [-48.5, -9.0], [-48.5, -11.5]]]},
        "pilot": True,
    },
    {
        "id": "pilot-cerrado-irregular",
        "name": "Pilot sub-area (irregular polygon)",
        "description": "Non-rectangular polygon inside the pilot region, to demonstrate exact polygon filtering (not bounding-box filtering).",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-48.2, -11.2], [-46.9, -11.35], [-45.8, -10.6], [-46.1, -9.6], [-47.0, -9.25], [-47.6, -10.1], [-48.3, -10.2], [-48.2, -11.2]]],
        },
        "pilot": True,
    },
    {
        "id": "top-end-australia",
        "name": "Top End, Northern Territory, Australia",
        "description": "Tropical savanna with a strong early/late dry-season fire regime.",
        "geometry": {"type": "Polygon", "coordinates": [[[130.0, -15.5], [136.5, -15.5], [136.5, -12.0], [130.0, -12.0], [130.0, -15.5]]]},
    },
    {
        "id": "sierra-foothills-california",
        "name": "Sierra Nevada foothills, California, USA",
        "description": "Mediterranean-climate wildfire region.",
        "geometry": {"type": "Polygon", "coordinates": [[[-122.0, 37.0], [-119.0, 37.0], [-119.0, 40.5], [-122.0, 40.5], [-122.0, 37.0]]]},
    },
    {
        "id": "chukotka-antimeridian",
        "name": "Chukotka across the antimeridian (test case)",
        "description": "Crosses 180 degrees longitude; demonstrates explicit antimeridian splitting.",
        "geometry": {"type": "Polygon", "coordinates": [[[172.0, 63.0], [-172.0, 63.0], [-172.0, 67.0], [172.0, 67.0], [172.0, 63.0]]]},
    },
]

PRESET_BY_ID = {p["id"]: p for p in PRESETS}
