"""Stages 2b-3: mesh generation, terrain fusion, joint injection."""
import numpy as np
import shapely
import trimesh
from shapely.geometry import box
from . import joints

ENGINE = "manifold"


def _extrude(poly, height, z0=0.0):
    meshes = []
    for part in getattr(poly, "geoms", [poly]):
        if part.is_empty or part.area <= 0:
            continue
        try:
            m = trimesh.creation.extrude_polygon(part, height)
        except Exception:
            continue
        m.apply_translation((0, 0, z0))
        meshes.append(m)
    return meshes


def _cyl(x, y, d, h, z0):
    m = trimesh.creation.cylinder(radius=d / 2.0, height=h, sections=32)
    m.apply_translation((x, y, z0 + h / 2.0))
    return m


def _pocket(spec, d, z_centre, teardrop=False):
    """Horizontal magnet pocket: a cylinder lying along the edge normal.

    Optional teardrop peak removes the unsupported arch at the pocket roof, at
    the cost of ~0.4*d extra base height.
    """
    R, L = d / 2.0, spec["depth"]
    if teardrop:
        from shapely.geometry import Polygon as _P
        # One clean ring: the circle with its top 90 degrees replaced by a
        # 45-degree peak. Unioning a circle with a triangle whose base sits
        # exactly on the circle yields a self-touching polygon that manifold
        # rejects as "not a volume".
        ts = np.linspace(3 * np.pi / 4, 2 * np.pi + np.pi / 4, 40)
        ring = [(R * np.cos(t), R * np.sin(t)) for t in ts]
        ring.append((0.0, R * np.sqrt(2)))
        m = trimesh.creation.extrude_polygon(_P(ring), L)
    else:
        m = trimesh.creation.cylinder(radius=R, height=L, sections=40)
        m.apply_translation((0, 0, L / 2.0))
    # Place it explicitly. Composing rotation_matrix(-pi/2, X) with a spin
    # about Z sends the extrusion axis to world Y and the teardrop peak to
    # world -Z, i.e. a pocket lying along the seam with its peak underneath.
    # Build the frame directly: local Z -> edge normal, local Y -> world up.
    ax, ay = float(spec["axis"][0]), float(spec["axis"][1])
    M = np.eye(4)
    M[:3, 0] = (-ay, ax, 0.0)      # local X
    M[:3, 1] = (0.0, 0.0, 1.0)     # local Y -> up
    M[:3, 2] = (ax, ay, 0.0)       # local Z -> pocket axis
    m.apply_translation((0, 0, -L / 2.0))    # centre on the magnet centre
    m.apply_transform(M)
    m.apply_translation((spec["xy"][0], spec["xy"][1], z_centre))
    return m


def heightfield_solid(xs, ys, ztop, z0=0.0):
    """Closed solid between a flat floor at z0 and a sampled surface ztop[j,i]."""
    nx, ny = len(xs), len(ys)
    X, Y = np.meshgrid(xs, ys)
    top = np.column_stack([X.ravel(), Y.ravel(), ztop.ravel()])
    bot = np.column_stack([X.ravel(), Y.ravel(), np.full(X.size, z0)])
    verts = np.vstack([top, bot])
    N = X.size

    def quad(a, b, c, d):                      # two CCW triangles
        return [[a, b, c], [a, c, d]]

    idx = np.arange(N).reshape(ny, nx)
    faces = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a, b = idx[j, i], idx[j, i + 1]
            c, d = idx[j + 1, i + 1], idx[j + 1, i]
            faces += quad(a, b, c, d)                       # top, CCW up
            faces += quad(N + a, N + d, N + c, N + b)       # floor, CCW down
    for i in range(nx - 1):                                  # south / north walls
        faces += quad(idx[0, i], N + idx[0, i], N + idx[0, i + 1], idx[0, i + 1])
        faces += quad(idx[ny - 1, i + 1], N + idx[ny - 1, i + 1],
                      N + idx[ny - 1, i], idx[ny - 1, i])
    for j in range(ny - 1):                                  # west / east walls
        faces += quad(idx[j + 1, 0], N + idx[j + 1, 0], N + idx[j, 0], idx[j, 0])
        faces += quad(idx[j, nx - 1], N + idx[j, nx - 1],
                      N + idx[j + 1, nx - 1], idx[j + 1, nx - 1])
    return trimesh.Trimesh(vertices=verts, faces=np.array(faces), process=True)


def build_tile(centre, tile_mm, buildings, roads, opts, relief=None, route=None):
    """One watertight tile. `relief(x, y) -> mm above datum` enables terrain."""
    cx, cy = centre
    half = tile_mm / 2.0
    square = box(cx - half, cy - half, cx + half, cy + half)
    base_h, rd = opts["base_h"], opts["road_depth"]

    if relief is None:
        base = trimesh.util.concatenate(_extrude(square, base_h))
        road_cuts = []
        if roads is not None and rd > 0:
            clipped = roads.intersection(square)
            if not clipped.is_empty:
                road_cuts = _extrude(clipped, rd + 0.2, z0=base_h - rd)
        surf = lambda px, py: np.full(np.shape(px), base_h, dtype=float)
        if route is not None:
            clipped = route.intersection(square)
            if not clipped.is_empty:
                base = trimesh.boolean.union(
                    [base] + _extrude(clipped, opts["route_h"] + 0.4,
                                      z0=base_h - 0.4), engine=ENGINE)
    else:
        n = opts["terrain_grid"]
        xs = np.linspace(cx - half, cx + half, n)
        ys = np.linspace(cy - half, cy + half, n)
        X, Y = np.meshgrid(xs, ys)
        Z = base_h + relief(X.ravel(), Y.ravel()).reshape(X.shape)
        if roads is not None and rd > 0:
            on_road = shapely.contains_xy(roads, X.ravel(), Y.ravel()).reshape(X.shape)
            Z = np.where(on_road, Z - rd, Z)          # engrave by displacing the field
        if route is not None:
            on_route = shapely.contains_xy(route, X.ravel(), Y.ravel()).reshape(X.shape)
            Z = np.where(on_route, Z + opts["route_h"], Z)
        base = heightfield_solid(xs, ys, np.maximum(Z, 0.6))
        road_cuts = []
        surf = lambda px, py: base_h + relief(np.asarray(px, float), np.asarray(py, float))

    cuts = list(road_cuts)
    if opts["joint"] == "jigsaw":
        prof = joints.jigsaw_profile()
        for edge in joints.EDGES:
            tab = joints.place(prof, edge, tile_mm, centre)
            if edge in joints.MALE_EDGES:
                base = trimesh.boolean.union(
                    [base] + _extrude(tab, base_h), engine=ENGINE)
            else:
                cuts += _extrude(tab.buffer(opts["clearance"]),
                                 base_h + 200.0, z0=-0.05)
    if opts["joint"] == "magnet":
        d = opts["magnet_d"] + 0.2
        z_c = opts["magnet_floor"] + d / 2.0
        # A horizontal pocket behind a wall is sealed, so it only works with a
        # print pause. glue mode instead breaks the pocket out through the seam
        # face: magnets sit flush, go in after printing, and touch across the
        # seam -- stronger, but visible and reliant on the glue.
        embed = opts["magnet_mode"] == "embed"
        wall = opts["magnet_wall"] if embed else 0.0
        for spec in joints.magnet_pockets(tile_mm, centre, wall=wall,
                                          thick=opts["magnet_h"]):
            if not embed:
                spec = dict(spec, depth=spec["depth"] + 2.0)   # break the face open
                ax = spec["axis"]
                spec["xy"] = (spec["xy"][0] + ax[0] * 1.0, spec["xy"][1] + ax[1] * 1.0)
            cuts.append(_pocket(spec, d, z_c, teardrop=opts["pocket_teardrop"] and embed))
    if cuts:
        base = trimesh.boolean.difference([base] + cuts, engine=ENGINE)

    solids, n_bld = [base], 0
    for idx, (poly, h) in enumerate(buildings):
        clipped = poly.intersection(square)
        if clipped.is_empty or clipped.area < 1.0:
            continue
        # OSM row buildings touch corner-to-corner; exact contact yields edges
        # with 4 incident faces. A deterministic sub-nozzle jitter (<0.06 mm)
        # turns every kiss into an overlap the union can resolve.
        clipped = clipped.buffer(0.02 + (idx % 11) * 0.004,
                                 join_style=2).intersection(square)
        # A footprint sliced by the tile edge can survive as a needle: nonzero
        # area, zero printable width, and it extrudes to a zero-volume sheet
        # that breaks manifoldness. Reject anything that cannot hold a wall.
        if clipped.is_empty or clipped.area < opts["min_footprint"] or \
                clipped.buffer(-opts["min_wall"] / 2.0).is_empty:
            continue
        h = min(max(h, opts["min_bld_h"]), opts["max_bld_h"])
        pts = np.array(clipped.convex_hull.exterior.coords)
        zs = surf(pts[:, 0], pts[:, 1])
        z_bot, z_top = float(zs.min()) - 1.0, float(zs.max()) + h
        parts = _extrude(clipped, z_top - z_bot, z0=z_bot)
        solids += parts
        n_bld += bool(parts)

    mesh = trimesh.boolean.union(solids, engine=ENGINE) if len(solids) > 1 else base
    mesh = _clean(mesh)
    mesh.metadata["buildings"] = n_bld
    return mesh


def _clean(mesh, min_vol=0.5):
    """Drop zero-volume shells left by the union.

    Where two footprints meet at exactly one point, manifold emits a degenerate
    spike -- a 4-face shell of zero volume standing on a single vertex. It
    carries no material and it is what makes the tile read as non-watertight,
    so cut it. Enclosed magnet cavities are ~45 mm3 and survive the threshold.
    """
    # Drop whole components, never individual faces: stripping degenerate
    # triangles in place opens holes in tiles that were already closed.
    parts = mesh.split(only_watertight=False)
    keep = [p for p in parts if abs(p.volume) >= min_vol]
    if len(keep) == len(parts) or not keep:
        return mesh
    out = trimesh.util.concatenate(keep)
    out.metadata.update(mesh.metadata)
    return out


def report(mesh):
    return {
        "watertight": bool(mesh.is_watertight),
        "winding_consistent": bool(mesh.is_winding_consistent),
        "volume_cm3": round(float(mesh.volume) / 1000.0, 2),
        "bodies": int(mesh.body_count),
        "triangles": int(len(mesh.faces)),
        "bbox_mm": [round(float(v), 2) for v in mesh.extents],
    }
