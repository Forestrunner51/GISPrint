"""Pixel-art style: the whole tile snapped to a grid of square columns.

Every cell takes one height, sampled at its centre: water (unprinted), road
(engraved), open ground, or the tallest building covering it, rounded to whole
blocks. The surface is then built directly from that grid -- tops, floors and
the walls between neighbouring columns -- with shared vertices, rather than
by unioning stacked slabs: CSG on thousands of coplanar cube walls leaves
non-manifold edges however the slabs are offset.
"""
import numpy as np
import shapely
import trimesh


MIN_PATCH = 12      # cells


def fix_diagonals(G):
    """Remove checkerboard 2x2 windows, which would pinch the surface.

    If two diagonal columns both rise above the other two at some level, the
    surface touches itself along a single vertical edge (one edge, four
    faces). Raise the taller of the two low columns to the lower of the high
    pair, which bridges the kiss. Repeat until none remain. `G` uses 0 for
    void (water / off-tile); a void cell raised this way becomes land.
    """
    G = G.copy()
    for _ in range(200):
        a, b = G[:-1, :-1], G[:-1, 1:]
        c, d = G[1:, :-1], G[1:, 1:]
        pinch1 = np.minimum(a, d) > np.maximum(b, c)   # a,d high; b,c low
        pinch2 = np.minimum(b, c) > np.maximum(a, d)   # b,c high; a,d low
        if not (pinch1.any() or pinch2.any()):
            return G
        for jj, ii in zip(*np.nonzero(pinch1)):
            hi = min(G[jj, ii], G[jj + 1, ii + 1])
            if G[jj, ii + 1] >= G[jj + 1, ii]:
                G[jj, ii + 1] = hi
            else:
                G[jj + 1, ii] = hi
        for jj, ii in zip(*np.nonzero(pinch2)):
            hi = min(G[jj, ii + 1], G[jj + 1, ii])
            if G[jj, ii] >= G[jj + 1, ii + 1]:
                G[jj, ii] = hi
            else:
                G[jj + 1, ii + 1] = hi
    raise RuntimeError("pixel grid: diagonal fix did not converge")


def voxel_mesh(G, x0, y0, px):
    """Closed surface over a column grid. G[j, i] = top height, 0 = void."""
    ny, nx = G.shape
    P = np.zeros((ny + 2, nx + 2))
    P[1:-1, 1:-1] = G
    verts, index, faces = [], {}, []

    def v(ci, cj, z):                      # corner column (ci, cj) at height z
        key = (ci, cj, round(float(z), 6))
        if key not in index:
            index[key] = len(verts)
            verts.append((x0 + ci * px, y0 + cj * px, float(z)))
        return index[key]

    def column(ci, cj):
        # heights present on this corner column: the four cells around it
        hs = P[cj:cj + 2, ci:ci + 2].ravel()
        return sorted(set(hs[hs > 0].tolist()) | ({0.0} if (hs > 0).any() else set()))

    def wall(p, q, lo, hi):
        """Vertical face along corner p -> q from lo to hi, outward to the
        right of p->q seen from above. Zipped so every split height on either
        column is a vertex and the two walls on an edge agree exactly."""
        L = [z for z in column(*p) if lo <= z <= hi]
        R = [z for z in column(*q) if lo <= z <= hi]
        i = k = 0
        while i < len(L) - 1 or k < len(R) - 1:
            if k < len(R) - 1 and (i == len(L) - 1 or R[k + 1] <= L[i + 1]):
                faces.append((v(*p, L[i]), v(*q, R[k]), v(*q, R[k + 1])))
                k += 1
            else:
                faces.append((v(*p, L[i]), v(*q, R[k]), v(*p, L[i + 1])))
                i += 1

    for j in range(ny):
        for i in range(nx):
            h = G[j, i]
            if h <= 0:
                continue
            a, b, c, d = (i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)
            faces += [(v(*a, h), v(*b, h), v(*c, h)), (v(*a, h), v(*c, h), v(*d, h))]
            faces += [(v(*a, 0), v(*c, 0), v(*b, 0)), (v(*a, 0), v(*d, 0), v(*c, 0))]
            # a wall wherever this column stands above its neighbour
            for (di, dj), (p, q) in (((0, -1), (a, b)), ((1, 0), (b, c)),
                                     ((0, 1), (c, d)), ((-1, 0), (d, a))):
                nb = P[j + 1 + dj, i + 1 + di]
                if nb < h:
                    wall(p, q, nb, h)
    return trimesh.Trimesh(np.array(verts), np.array(faces), process=False)


def heights(centre, tile_mm, buildings, roads, water, opts):
    """Per-cell top height in mm (NaN = water), plus the cell centres."""
    px = opts["pixel"]
    n = max(int(round(tile_mm / px)), 1)
    px = tile_mm / n
    cx, cy = centre
    xs = cx - tile_mm / 2 + px * (np.arange(n) + 0.5)
    ys = cy - tile_mm / 2 + px * (np.arange(n) + 0.5)
    X, Y = np.meshgrid(xs, ys)
    base_h, rd = opts["base_h"], opts["road_depth"]

    H = np.full(X.shape, base_h)
    if roads is not None and rd > 0:
        H[shapely.contains_xy(roads, X, Y)] = base_h - rd
    bld = np.zeros(X.shape)
    for poly, h in buildings:
        x0, y0, x1, y1 = poly.bounds
        sel = (X >= x0) & (X <= x1) & (Y >= y0) & (Y <= y1)
        if not sel.any():
            continue
        h = min(max(h, opts["min_bld_h"]), opts["max_bld_h"])
        inside = np.zeros(X.shape, bool)
        inside[sel] = shapely.contains_xy(poly, X[sel], Y[sel])
        bld = np.where(inside, np.maximum(bld, h), bld)
    # Whole blocks: a building is at least one cube tall, and its height is
    # a multiple of the cell size so every column reads as stacked cubes.
    step = opts["pixel_step"] or px
    blocks = np.where(bld > 0, np.maximum(np.round(bld / step), 1) * step, 0)
    H = np.where(blocks > 0, base_h + blocks, H)
    if water is not None:
        H[shapely.contains_xy(water, X, Y)] = np.nan
    return H, xs, ys, px


def build_pixel_tile(centre, tile_mm, buildings, roads, water, opts):
    """Returns (mesh, n_raised_columns) or (None, 0) when the tile is all water."""
    H, xs, ys, px = heights(centre, tile_mm, buildings, roads, water, opts)
    G = np.nan_to_num(H, nan=0.0)
    # A pier end or rock a few cells across prints as a loose speck that
    # rattles in the frame. Drop land patches under MIN_PATCH cells.
    from scipy.ndimage import label
    lab, n = label(G > 0)
    if n > 1:
        sizes = np.bincount(lab.ravel())
        small = sizes < MIN_PATCH
        small[0] = False
        G[small[lab]] = 0.0
    G = fix_diagonals(G)
    if not (G > 0).any():
        return None, 0
    mesh = voxel_mesh(G, xs[0] - px / 2, ys[0] - px / 2, px)
    if mesh.volume < 0:
        mesh.invert()
    n_bld = int((G > opts["base_h"] + 1e-6).sum())
    return mesh, n_bld
