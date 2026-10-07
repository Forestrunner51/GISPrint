"""A couple standing on the map: two stylised figures holding hands.

Not to scale -- a real person at 1:4000 is 0.5 mm, below the nozzle -- so they
are deliberately oversized, like the figures on an illustrated map. Two
looks, matching the map styles:

  classic  smooth "couple icon" figures: one in a dress, one in trousers
  pixel    blocky Minecraft-proportioned figures built from cubes

Both print support-free in one colour as part of the tile: overhangs are
limited to a head over a neck and a short hand-to-hand bridge. Feet sink
below ground so the union always overlaps the map instead of kissing it.
Figures face -y, i.e. toward the bottom edge of the map.
"""
import numpy as np
import trimesh

SINK = 1.0      # mm below ground level, so the figures fuse with the base


def _to_trimesh(m):
    mesh = m.to_mesh()
    return trimesh.Trimesh(mesh.vert_properties[:, :3], mesh.tri_verts)


def _classic(h):
    """Smooth couple, about h mm tall. Built in an 18 mm design then scaled."""
    import manifold3d as mf
    seg = 40
    cyl = lambda z0, z1, r0, r1, x=0.0, y=0.0: (
        mf.Manifold.cylinder(z1 - z0, r0, r1, seg).translate((x, y, z0)))
    ball = lambda r, x, z: mf.Manifold.sphere(r, seg).translate((x, 0.0, z))

    xa, xb = -3.2, 3.2
    a = (cyl(-SINK, 10.0, 3.4, 1.35, xa)              # dress
         + cyl(10.0, 13.0, 1.35, 1.9, xa)             # torso to shoulders
         + cyl(12.8, 14.2, 0.85, 0.85, xa)            # neck
         + ball(2.3, xa, 15.9))                       # head
    b = (cyl(-SINK, 8.6, 1.05, 1.05, xb - 1.0)        # legs
         + cyl(-SINK, 8.6, 1.05, 1.05, xb + 1.0)
         + cyl(8.4, 14.2, 1.7, 2.05, xb)              # torso, widening upward
         + cyl(14.0, 15.2, 0.85, 0.85, xb)            # neck
         + ball(2.4, xb, 17.0))                       # head
    # joined hands: a short bar between the two at hip height
    hands = mf.Manifold.cylinder(2 * xb - 1.0, 0.75, 0.75, seg) \
        .rotate((0.0, 90.0, 0.0)).translate((xa + 0.5, 0.0, 9.6))
    return _to_trimesh(a + b + hands), h / 19.4


# Minecraft proportions in "pixels": head 8, body 8x4x12, arms/legs 4x4x12.
# (x0, y0, z0, x1, y1, z1), x across, y depth (front = -y), z up.
_PERSON = [
    (-4, -2, 0, 0, 2, 12), (0, -2, 0, 4, 2, 12),     # legs
    (-4, -2, 12, 4, 2, 24),                          # body
    (-8, -2, 12, -4, 2, 24), (4, -2, 12, 8, 2, 24),  # arms
    (-4, -4, 24, 4, 4, 32),                          # head
]
_SKIRT = [(-5, -3, 5, 5, 3, 12)]                     # flares over the legs
_HAIR = [(-4, 4, 18, 4, 5, 32)]                      # long hair down the back


def _pixel(h):
    import manifold3d as mf
    eps = 0.02            # grow every cube a hair so neighbours overlap

    def boxes(spec, dx):
        out = None
        for x0, y0, z0, x1, y1, z1 in spec:
            z0 = -SINK / u if z0 == 0 else z0        # feet sink into the map
            c = mf.Manifold.cube((x1 - x0 + 2 * eps, y1 - y0 + 2 * eps,
                                  z1 - z0 + 2 * eps)).translate(
                (x0 + dx - eps, y0 - eps, z0 - eps))
            out = c if out is None else out + c
        return out

    u = h / 32.0                                     # mm per figure "pixel"
    left = boxes(_PERSON, -8.5)
    right = boxes(_PERSON + _SKIRT + _HAIR, 8.5)
    hands = boxes([(-0.6, -2, 12, 0.6, 2, 15)], 0.0) # inner arms clasp
    return _to_trimesh(left + right + hands), u


def couple(style, height_mm):
    """Mesh with feet at z=0 (sinking SINK below), centred on x=y=0."""
    mesh, k = (_pixel if style == "pixel" else _classic)(height_mm)
    mesh.apply_scale(k)
    return mesh


def place(mesh, figures, x, y):
    """Stand `figures` on `mesh` at model point (x, y) and fuse them in."""
    from .build import ENGINE
    # ground = the tile's top surface under the figures' centre
    hit, _, _ = mesh.ray.intersects_location([[x, y, 1000.0]], [[0, 0, -1.0]])
    if len(hit) == 0:
        return None
    ground = float(hit[:, 2].max())
    f = figures.copy()
    f.apply_translation((x, y, ground))
    out = trimesh.boolean.union([mesh, f], engine=ENGINE)
    out.metadata.update(mesh.metadata)
    return out
