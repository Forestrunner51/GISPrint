"""Stage 3: interlocking mechanics.

ASSEMBLY TOPOLOGY -- the thing most tutorials get wrong:

  jigsaw / dovetail tabs are *in-plane* joints. A tab wider than its neck can
  only enter its socket by sliding along the seam. On a 1xN strip that is fine.
  On an NxM grid the last tile has to slide in two perpendicular directions at
  once, which rigid PLA will not do. Use them for strips, borders and display
  pieces -- not for a grid you want to rearrange.

  magnet pockets are *vertical-drop* joints: any tile, any order, any time.
  That is why every commercial modular terrain line ends up on magnets or on
  separate under-clips (OpenLock et al.) rather than printed puzzle tabs.

POLE ORIENTATION -- measured, not guessed (see magnetics.py):

  A pocket whose axis is vertical puts the magnet's faces up and down, so
  neighbouring tiles couple side-lobe to side-lobe: 0.55 N per seam for 5x2
  N42 at a 3.6 mm inset. A 44 g tile weighs 0.43 N. That is not a snap, it is
  a suggestion.

  Turning the pocket so its axis runs along the edge normal puts the magnet's
  face at the seam behind 0.8 mm of wall: 3.52 N per seam, 6x stronger and 8x
  the tile's own weight. That is the joint that feels like a product.

  Poles alternate along each edge, and the two edge families are complementary
  (E/N: +along is N-out; W/S: +along is S-out). That makes a tile mate with its
  neighbour at 0 and at 180 degrees. 90 degrees does not mate -- no polarised
  scheme survives a quarter turn, so tiles are keyed to two orientations.
"""
import math
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

# edge -> (outward unit normal, along-edge unit vector)
EDGES = {
    "E": ((1, 0), (0, 1)),
    "W": ((-1, 0), (0, 1)),
    "N": ((0, 1), (1, 0)),
    "S": ((0, -1), (1, 0)),
}
MALE_EDGES = ("E", "N")   # tabs point +X / +Y; sockets on -X / -Y


def jigsaw_profile(neck=4.0, neck_out=1.8, head_r=3.0, head_off=0.6):
    """Classic puzzle tab in edge-local coords: x along seam, y outward."""
    neck_rect = box(-neck / 2, -0.6, neck / 2, neck_out)
    head = Polygon([
        (head_r * math.cos(t), neck_out + head_off * head_r + head_r * math.sin(t))
        for t in [i * math.tau / 48 for i in range(48)]
    ])
    return unary_union([neck_rect, head])


def place(profile, edge, tile_mm, centre=(0.0, 0.0)):
    """Map an edge-local profile onto one edge of a tile centred at `centre`."""
    (nx, ny), (ax, ay) = EDGES[edge]
    cx = centre[0] + nx * tile_mm / 2.0
    cy = centre[1] + ny * tile_mm / 2.0
    out = []
    for x, y in profile.exterior.coords:
        out.append((cx + ax * x + nx * y, cy + ay * x + ny * y))
    return Polygon(out)


def apply_jigsaw(base_poly, tile_mm, centre, clearance=0.25, **kw):
    """Add tabs on E/N, cut sockets (tab + clearance) on W/S."""
    prof = jigsaw_profile(**kw)
    poly = base_poly
    for edge in EDGES:
        tab = place(prof, edge, tile_mm, centre)
        if edge in MALE_EDGES:
            poly = poly.union(tab)
        else:
            # socket is the *mating* tile's tab, so mirror it back inward
            poly = poly.difference(place(prof, edge, tile_mm, centre).buffer(clearance))
    return poly


def magnet_pockets(tile_mm, centre, wall=0.8, thick=2.0, clearance=0.15,
                   frac=0.28):
    """Horizontal pockets facing the seam.

    Returns one dict per pocket: centre of the magnet, its axis (pointing
    out of the tile), and the pole that must face outward. Feed `pole` to the
    insertion guide -- getting it wrong makes half the seams repel.
    """
    depth = thick + clearance
    out = []
    for edge, ((nx, ny), (ax, ay)) in EDGES.items():
        male = edge in MALE_EDGES
        # magnet centre sits `wall` in from the face, then half its own depth
        back = tile_mm / 2 - wall - depth / 2.0
        cx = centre[0] + nx * back
        cy = centre[1] + ny * back
        for s in (-1, 1):
            pole = ("N" if s > 0 else "S") if male else ("S" if s > 0 else "N")
            out.append({
                "xy": (cx + ax * s * frac * tile_mm, cy + ay * s * frac * tile_mm),
                "axis": (nx, ny, 0.0),
                "edge": edge,
                "pole": pole,       # pole that must point OUT of the tile
                "depth": depth,
            })
    return out


def min_base_height(magnet_d=5.0, clearance=0.2, floor=0.8, roof=0.8):
    """A horizontal pocket needs the magnet's diameter of clear height."""
    return round(floor + magnet_d + clearance + roof, 2)
