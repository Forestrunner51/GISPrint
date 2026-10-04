"""Land mask: cut rivers, lakes and sea out of the set, keep piers.

The water is not printed. The land prints as the only solid and a coloured
backing board shows through where the water was, which is how framed
single-colour city maps get blue water off a one-filament printer.

OSM gives water two ways, and they need different handling:
- natural=coastline: open lines, land on the LEFT of the way direction, water
  on the right. Sea is never mapped as a polygon, so the sea has to be
  reconstructed by splitting the region along these lines and classifying
  each face.
- natural=water / waterway=riverbank: closed areas (ways or multipolygons).
"""
import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import linemerge, polygonize, unary_union
from .geometry import _ring

PIER_WIDTH_M = 6.0
# Below this a pond or fountain is a pinhole and a pier stub is a loose
# speck in the frame. The 9/11 memorial pools (~50 mm2 at 1:8000) survive.
MIN_HOLE_MM2 = 15.0
MIN_LAND_MM2 = 15.0


def _closed_poly(pts):
    if len(pts) < 4 or pts[0] != pts[-1]:
        return None
    p = Polygon(pts)
    return p if p.is_valid else p.buffer(0)


def _relation_poly(el, plane, scale):
    """Reassemble a multipolygon from its member ways: outers minus inners."""
    rings = {"outer": [], "inner": []}
    for m in el.get("members", []):
        if m.get("type") != "way" or not m.get("geometry"):
            continue
        role = "inner" if m.get("role") == "inner" else "outer"
        rings[role].append(LineString(_ring(m["geometry"], plane, scale)))
    if not rings["outer"]:
        return None
    outer = unary_union(list(polygonize(linemerge(rings["outer"]))))
    if rings["inner"]:
        outer = outer.difference(unary_union(list(polygonize(linemerge(rings["inner"])))))
    return outer if not outer.is_empty else None


def _sea(coast, region):
    """Faces of `region` lying on the water (right-hand) side of the coastline."""
    noded = unary_union([region.exterior] + [c.intersection(region.buffer(1.0))
                                             for c in coast])
    sea = []
    for face in polygonize(noded):
        p = face.representative_point()
        line = min(coast, key=lambda c: c.distance(p))
        s = line.project(p)
        eps = min(0.5, line.length / 4.0)
        a = line.interpolate(max(s - eps, 0.0))
        b = line.interpolate(min(s + eps, line.length))
        q = line.interpolate(s)
        cross = (b.x - a.x) * (p.y - q.y) - (b.y - a.y) * (p.x - q.x)
        if cross < 0:                           # right of the way: water
            sea.append(face)
    return unary_union(sea) if sea else None


def water_mm(elements, plane, scale, region, min_wall=0.4):
    """Return (water_polygon_mm, n_piers), clipped to `region`, or (None, 0).

    Land is cleaned before the water is returned: islands and spits too thin
    to hold a wall would print as loose specks, so they are folded into the
    water instead.
    """
    coast, areas, piers = [], [], []
    for el in elements:
        tags = el.get("tags", {})
        if el["type"] == "relation":
            poly = _relation_poly(el, plane, scale)
            if poly is not None:
                areas.append(poly)
            continue
        if not el.get("geometry"):
            continue
        pts = _ring(el["geometry"], plane, scale)
        if tags.get("natural") == "coastline":
            if len(pts) >= 2:
                coast.append(LineString(pts))
        elif tags.get("man_made") == "pier":
            poly = _closed_poly(pts) if tags.get("area") != "no" else None
            if poly is None and len(pts) >= 2:
                w = max(PIER_WIDTH_M * scale, min_wall * 2)
                poly = LineString(pts).buffer(w / 2.0, cap_style=2, join_style=2)
            if poly is not None:
                piers.append(poly)
        else:
            poly = _closed_poly(pts)
            if poly is not None:
                areas.append(poly)

    parts = [a.intersection(region) for a in areas]
    parts = [g for p in parts for g in getattr(p, "geoms", [p])
             if g.area >= MIN_HOLE_MM2]
    if coast:
        sea = _sea(coast, region)
        if sea is not None:
            parts.append(sea)
    parts = [p for p in parts if not p.is_empty]
    if not parts:
        return None, 0
    water = unary_union(parts)
    if piers:
        water = water.difference(unary_union(piers))

    return clean(region, water, min_wall), len(piers)


def clean(region, void, min_wall=0.4):
    """Fold land too small or thin to hold a wall into the unprinted void.

    Shared by the water cutout and the shape mask: islands, spits and the
    sharp tip of a heart would otherwise print as loose specks or needles.
    """
    land = region.difference(void)
    keep = [g for g in getattr(land, "geoms", [land])
            if g.area >= MIN_LAND_MM2 and not g.buffer(-min_wall).is_empty]
    land = unary_union(keep) if keep else Polygon()
    void = region.difference(land)
    return None if void.is_empty else void
