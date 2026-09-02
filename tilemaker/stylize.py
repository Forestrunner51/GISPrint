"""Stage 2c: low-poly stylisation.

Three operations, in order. Each is lossy on purpose -- the point is a finish
that reads as designed rather than as a raw OSM dump, and that slices fast.

  simplify  Douglas-Peucker at nozzle scale. OSM footprints carry survey-grade
            vertices that are far below what a 0.4 mm nozzle can render, so
            they cost triangles and buy nothing.
  quantise  snap heights to a step. An unquantised skyline is noise; a stepped
            one reads as a deliberate massing model, and coplanar roofs across
            a block merge instead of z-fighting by 0.1 mm.
  merge     union touching footprints that landed on the same height step, so a
            city block becomes one solid instead of eighty prisms.
"""
from shapely.geometry import Polygon
from shapely.ops import unary_union


def stylize(buildings, simplify_mm=0.35, height_step=0.0, merge_gap=0.0,
            min_area=1.0):
    """buildings = [(polygon_mm, height_mm)] -> same, decimated."""
    work = []
    for poly, h in buildings:
        if simplify_mm > 0:
            poly = poly.simplify(simplify_mm, preserve_topology=True)
            if poly.is_empty or not poly.is_valid:
                poly = poly.buffer(0)
        if poly.is_empty or poly.area < min_area:
            continue
        if height_step > 0:
            h = max(height_step, round(h / height_step) * height_step)
        work.append((poly, h))

    if merge_gap <= 0 or height_step <= 0:
        return work

    # group by height step, then dilate/union/erode so footprints separated by
    # less than a nozzle-width alley become one block
    bands = {}
    for poly, h in work:
        bands.setdefault(round(h, 3), []).append(poly)
    out = []
    for h, polys in bands.items():
        merged = unary_union([p.buffer(merge_gap / 2.0, join_style=2) for p in polys])
        merged = merged.buffer(-merge_gap / 2.0, join_style=2)
        for part in getattr(merged, "geoms", [merged]):
            if not part.is_empty and part.area >= min_area:
                out.append((part, h))
    return out


def stats(before, after):
    def verts(bs):
        n = 0
        for p, _ in bs:
            for q in getattr(p, "geoms", [p]):
                n += len(q.exterior.coords)
                n += sum(len(r.coords) for r in q.interiors)
        return n
    return {"solids": (len(before), len(after)),
            "footprint_vertices": (verts(before), verts(after))}
