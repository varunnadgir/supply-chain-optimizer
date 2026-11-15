"""Turn a Brazil states GeoJSON file into small SVG paths for the interactive page.

    python src/build_map_outline.py path/to/brazil-states.geojson

Source used: brazil-states.geojson from the codeforgermany/click_that_hood
project on GitHub. Writes docs/brazil_states.json: one simplified SVG path per
state, in the same projected coordinates the page uses for the cities.
"""

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "brazil_states.json"

# Simple equirectangular projection, with longitude squeezed by cos(15 deg S)
# so Brazil keeps roughly the right shape. The page uses the same formula.
LNG0, LAT0, KX = -74.5, 5.6, math.cos(math.radians(15))
SCALE = 20  # SVG units per degree


def project(lng, lat):
    return ((lng - LNG0) * KX * SCALE, (LAT0 - lat) * SCALE)


def simplify(points, tolerance):
    """Douglas-Peucker line simplification."""
    if len(points) < 3:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = math.hypot(dx, dy) or 1e-12
    far, far_i = -1.0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        d = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / norm
        if d > far:
            far, far_i = d, i
    if far <= tolerance:
        return [points[0], points[-1]]
    return simplify(points[: far_i + 1], tolerance)[:-1] + simplify(points[far_i:], tolerance)


def ring_to_path(ring, tolerance=0.6):
    pts = [project(lng, lat) for lng, lat in ring]
    # A closed ring starts and ends at the same point, which breaks Douglas-Peucker,
    # so split it at the point farthest from the start and simplify each half.
    far = max(range(len(pts)), key=lambda i: math.dist(pts[0], pts[i]))
    pts = simplify(pts[: far + 1], tolerance)[:-1] + simplify(pts[far:], tolerance)
    if len(pts) < 4:
        return ""
    return "M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z"


def main(src):
    geo = json.loads(Path(src).read_text())
    states = {}
    for feature in geo["features"]:
        geom = feature["geometry"]
        polygons = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        path = "".join(ring_to_path(poly[0]) for poly in polygons)   # outer rings only
        states[feature["properties"]["sigla"]] = path
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"projection": {"lng0": LNG0, "lat0": LAT0, "kx": KX, "scale": SCALE},
                               "states": states}, separators=(",", ":")))
    print(f"Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e3:.0f} KB, {len(states)} states)")


if __name__ == "__main__":
    main(sys.argv[1])
