"""Holding force between two magnets, by the Gilbert (magnetic charge) model.

Each disc magnet is a pair of uniformly charged end faces, sigma = +-Br/mu0.
Force is the Coulomb-analog sum over face-element pairs. That stays accurate at
the separations tile joints actually use, where the dipole approximation blows
up (it diverges as 1/z^4 once the gap approaches the magnet's own size).
"""
import numpy as np

MU0 = 4e-7 * np.pi
GRADE_BR = {"N35": 1.19, "N42": 1.31, "N52": 1.45}   # tesla, remanence


def _face_points(centre, axis, radius, n=14):
    """Disc of charge elements: centres and per-element area."""
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    tmp = np.array([1.0, 0, 0]) if abs(axis[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(axis, tmp); u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    # concentric rings, equal-area annuli
    pts, areas = [], []
    for i in range(n):
        r0, r1 = radius * i / n, radius * (i + 1) / n
        rm = (r0 + r1) / 2
        k = max(1, int(round(2 * np.pi * rm / (radius / n))))
        a = np.pi * (r1**2 - r0**2) / k
        for j in range(k):
            t = 2 * np.pi * j / k
            pts.append(np.asarray(centre, float) + rm * (np.cos(t) * u + np.sin(t) * v))
            areas.append(a)
    return np.array(pts), np.array(areas)


def force(centre_a, axis_a, centre_b, axis_b, dia=5.0, thick=2.0, grade="N42"):
    """Force on magnet B from magnet A, in newtons. Lengths in mm.

    axis points from S to N. Returns the vector; its magnitude is the pull,
    and a negative projection onto the separation means attraction.
    """
    # Surface charge density in the Wb convention is Br itself. Using Br/mu0
    # here silently scales every force by 1/mu0^2 ~ 6e11.
    sigma = GRADE_BR[grade]
    R = dia / 2.0
    charges = []
    for c, ax, sign in ((centre_a, axis_a, +1), (centre_b, axis_b, +1)):
        ax = np.asarray(ax, float) / np.linalg.norm(ax)
        c = np.asarray(c, float)
        for s in (+1, -1):
            p, a = _face_points(c + s * ax * thick / 2.0, ax, R)
            charges.append((p * 1e-3, sigma * a * 1e-6 * s))   # -> metres, webers
    (pa1, qa1), (pa2, qa2), (pb1, qb1), (pb2, qb2) = charges
    pa = np.vstack([pa1, pa2]); qa = np.concatenate([qa1, qa2])
    pb = np.vstack([pb1, pb2]); qb = np.concatenate([qb1, qb2])
    d = pb[:, None, :] - pa[None, :, :]
    r = np.maximum(np.linalg.norm(d, axis=2), 1e-9)
    k = (qb[:, None] * qa[None, :]) / (4 * np.pi * MU0 * r**3)
    return (k[:, :, None] * d).sum(axis=(0, 1))


def seam_force(inset, axis="vertical", tile_gap=0.0, dia=5.0, thick=2.0,
               wall=0.8, grade="N42"):
    """Attraction across a tile seam for the two candidate pocket orientations.

    vertical   -- pocket axis +z, centres `inset` back from each edge.
                  Adjacent tiles must run antiparallel or they repel.
    horizontal -- pocket axis along the edge normal, magnet faces the seam
                  across `wall` of plastic on each side.
    """
    if axis == "vertical":
        a_c, a_ax = (-inset, 0, 0), (0, 0, 1)
        b_c, b_ax = (+inset + tile_gap, 0, 0), (0, 0, -1)   # antiparallel
    else:
        off = wall + thick / 2.0
        a_c, a_ax = (-off, 0, 0), (1, 0, 0)                 # N faces the seam
        b_c, b_ax = (+off + tile_gap, 0, 0), (-1, 0, 0)     # N faces back
    f = force(a_c, a_ax, b_c, b_ax, dia=dia, thick=thick, grade=grade)
    return float(f[0])          # x-component: negative = pulling tiles together
