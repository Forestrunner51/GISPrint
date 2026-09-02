"""Personal-story kits: a route in, a 4-tile set with that route raised on it.

Accepts a GPX track (what a watch or Strava export gives you) or an inline
list of waypoints. The grid is fitted to the route rather than the other way
round, so the whole line lands inside the set with a margin.
"""
import math
import xml.etree.ElementTree as ET
from shapely.geometry import LineString


def from_gpx(path):
    """Track points from a GPX file, namespace-agnostic."""
    root = ET.parse(path).getroot()
    pts = []
    for tag in ("trkpt", "rtept", "wpt"):
        for el in root.iter():
            if el.tag.rsplit("}", 1)[-1] == tag:
                try:
                    pts.append((float(el.get("lat")), float(el.get("lon"))))
                except (TypeError, ValueError):
                    continue
        if pts:
            break
    return pts


def from_string(text):
    """'lat,lon;lat,lon;...'"""
    out = []
    for chunk in text.replace("\n", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        a, b = chunk.split(",")
        out.append((float(a), float(b)))
    return out


def fit_grid(points, cols, rows, margin=0.12, min_span=120.0):
    """Centre and ground-span (metres per tile) that contain the whole route."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    lat0, lon0 = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
    mx = 111320.0 * math.cos(math.radians(lat0))
    w = (max(lons) - min(lons)) * mx
    h = (max(lats) - min(lats)) * 110540.0
    span = max(w / cols, h / rows, min_span) * (1 + margin)
    return (lat0, lon0), span


def polyline_mm(points, plane, scale):
    xs = [plane.xy(lon, lat) for lat, lon in points]
    return LineString([(x * scale, y * scale) for x, y in xs])


def ribbon(points, plane, scale, width_mm=1.6):
    """The route as a printable ridge footprint."""
    line = polyline_mm(points, plane, scale)
    if line.length <= 0:
        return None
    return line.buffer(width_mm / 2.0, cap_style=2, join_style=1)


def length_m(points):
    total = 0.0
    for (a_lat, a_lon), (b_lat, b_lon) in zip(points, points[1:]):
        mx = 111320.0 * math.cos(math.radians((a_lat + b_lat) / 2))
        total += math.hypot((b_lon - a_lon) * mx, (b_lat - a_lat) * 110540.0)
    return total
