"""A two-piece joint test coupon.

Print this before any tile set. It is the same edge, the same pockets and the
same clearances as a real tile, in about a tenth of the plastic, so a magnet
that does not fit or a polarity you got backwards costs 30 minutes rather than
a nine-tile set.
"""
import numpy as np
import trimesh
from shapely.geometry import box
from . import joints
from .build import _pocket, ENGINE


def make(tile_mm=60.0, depth=25.0, base_h=6.8, magnet_d=5.0, magnet_h=2.0,
         wall=0.8, floor=0.8, mode="glue", teardrop=False):
    """Two slabs that mate along y=0, carrying one real edge of pockets each."""
    d = magnet_d + 0.2
    z_c = floor + d / 2.0
    embed = mode == "embed"
    w = 0.0 if not embed else wall
    out = []
    for name, sign, edge in (("coupon_A", -1, "N"), ("coupon_B", +1, "S")):
        # virtual tile centred so that `edge` lands exactly on y = 0
        centre = (0.0, sign * tile_mm / 2.0)
        slab = box(-tile_mm / 2, min(0, sign * depth), tile_mm / 2,
                   max(0, sign * depth))
        mesh = trimesh.creation.extrude_polygon(slab, base_h)
        cuts = []
        specs = [s for s in joints.magnet_pockets(tile_mm, centre, wall=w,
                                                  thick=magnet_h)
                 if s["edge"] == edge]
        for spec in specs:
            if not embed:
                ax = spec["axis"]
                spec = dict(spec, depth=spec["depth"] + 2.0)
                spec["xy"] = (spec["xy"][0] + ax[0], spec["xy"][1] + ax[1])
            cuts.append(_pocket(spec, d, z_c, teardrop=teardrop and embed))
        mesh = trimesh.boolean.difference([mesh] + cuts, engine=ENGINE)
        out.append((name, mesh, specs))
    return out


def guide(specs_a, specs_b, force_n):
    lines = ["# Joint test coupon", "",
             "Print both pieces, fit the magnets, push them together.",
             f"Expected pull at the seam: about {force_n:.1f} N "
             f"({force_n/9.81*1000:.0f} g).", "",
             "| piece | x (mm) | pole facing the seam |", "|---|---|---|"]
    for lbl, specs in (("A", specs_a), ("B", specs_b)):
        for s in specs:
            lines.append(f"| {lbl} | {s['xy'][0]:+.2f} | **{s['pole']}** |")
    lines += ["", "If they repel, one side's magnets are in backwards.",
              "If the seam gaps, raise --clearance or check for elephant foot."]
    return "\n".join(lines) + "\n"
