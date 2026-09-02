"""Stage 2a: OSM elements -> shapely geometry in millimetres of model space."""
from shapely.geometry import Polygon, LineString
from shapely.ops import unary_union
from .osm import guess_height, road_width
from .project import LocalPlane


def _ring(geom, plane, scale):
    pts = [plane.xy(p["lon"], p["lat"]) for p in geom]
    return [(x * scale, y * scale) for x, y in pts]


def buildings_mm(elements, plane, scale, vscale=1.0, min_area_mm2=1.5):
    """Return [(polygon_mm, height_mm)] , dropping specks below print resolution."""
    out = []
    for el in elements:
        if "building" not in el.get("tags", {}) or not el.get("geometry"):
            continue
        ring = _ring(el["geometry"], plane, scale)
        if len(ring) < 4:
            continue
        try:
            poly = Polygon(ring)
            if not poly.is_valid:
                poly = poly.buffer(0)
        except Exception:
            continue
        if poly.is_empty or poly.area < min_area_mm2:
            continue
        h = guess_height(el["tags"]) * scale * vscale
        for part in getattr(poly, "geoms", [poly]):
            if part.area >= min_area_mm2:
                out.append((part, h))
    return out


def roads_mm(elements, plane, scale):
    """Road centrelines buffered to their real width, unioned into one polygon."""
    strips = []
    for el in elements:
        tags = el.get("tags", {})
        if "highway" not in tags or "building" in tags or not el.get("geometry"):
            continue
        pts = _ring(el["geometry"], plane, scale)
        if len(pts) < 2:
            continue
        w = road_width(tags) * scale
        strips.append(LineString(pts).buffer(w / 2.0, cap_style=2, join_style=1))
    return unary_union(strips) if strips else None
