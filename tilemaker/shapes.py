"""Cut the set to a shape: preset (heart, circle, hexagon) or a named OSM outline.

Everything outside the shape is handled exactly like water -- left unprinted
-- so it composes with --water, the pixel style and the backing-board look
for free.
"""
import hashlib, json, math, os
import numpy as np
import requests
from shapely.geometry import Point, Polygon, shape as geojson_shape
from shapely.ops import transform
from .osm import CACHE, HEADERS

NOMINATIM = "https://nominatim.openstreetmap.org/search"


def preset(name, region, margin=0.02):
    """A shape filling `region` (a box in mm), centred, `margin` inset."""
    x0, y0, x1, y1 = region.bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    r = min(x1 - x0, y1 - y0) / 2 * (1 - margin)
    if name == "circle":
        return Point(cx, cy).buffer(r, quad_segs=64)
    if name == "hexagon":
        return Polygon([(cx + r * math.cos(math.pi / 6 + k * math.pi / 3),
                         cy + r * math.sin(math.pi / 6 + k * math.pi / 3))
                        for k in range(6)])
    if name == "heart":
        t = np.linspace(0, 2 * np.pi, 400, endpoint=False)
        x = 16 * np.sin(t) ** 3
        y = 13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t)
        k = 2 * r / max(np.ptp(x), np.ptp(y))
        x, y = x * k, y * k
        x += cx - (x.max() + x.min()) / 2
        y += cy - (y.max() + y.min()) / 2
        return Polygon(np.column_stack([x, y]))
    raise ValueError(f"unknown shape {name!r}")


def lookup_outline(query):
    """Boundary polygon (lon/lat GeoJSON geometry) for a place name, cached."""
    key = hashlib.sha1(f"nominatim:{query}".encode()).hexdigest()[:16]
    path = os.path.join(CACHE, f"{key}.json")
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    r = requests.get(NOMINATIM, params={"q": query, "format": "jsonv2", "limit": 5,
                                        "polygon_geojson": 1},
                     headers=HEADERS, timeout=60)
    r.raise_for_status()
    hits = [h for h in r.json()
            if h.get("geojson", {}).get("type") in ("Polygon", "MultiPolygon")]
    if not hits:
        raise RuntimeError(f"no outline found for {query!r} "
                           "(points and roads have no area; try a more specific name)")
    hit = {"name": hits[0]["display_name"], "geojson": hits[0]["geojson"]}
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(hit, fh)
    return hit


def outline_fit(hit, cols, rows, margin=0.04):
    """Centre (lat, lon) and metres-per-tile span that frame the outline."""
    g = geojson_shape(hit["geojson"])
    w, s, e, n = g.bounds
    lat, lon = (s + n) / 2, (w + e) / 2
    wm = (e - w) * 111320.0 * math.cos(math.radians(lat))
    hm = (n - s) * 110540.0
    span = max(wm / cols, hm / rows) * (1 + margin)
    return (lat, lon), span


def outline_mm(hit, plane, scale):
    g = geojson_shape(hit["geojson"])
    return transform(lambda x, y, z=None: tuple(
        np.array(plane.xy(np.asarray(x), np.asarray(y))) * scale), g)
