"""Lithophanes: a thin plate whose thickness encodes brightness when backlit.

Thick plastic blocks light and reads dark; thin plastic glows. Two sources:

  photo   any image file (a customer's photo):
              python -m tilemaker.lithophane photo.jpg --width 150 --out out_litho
  map     the OSM city itself via `--style lithophane` in the main CLI: water
          and streets glow, buildings read dark, taller ones darker.

The plate is printed flat, relief up, and viewed from the relief side, so the
image is not mirrored. A full-thickness border keeps it stiff and opaque.
Print in white PLA at 100% infill: infill patterns show through the light.
"""
import argparse, os
import numpy as np
import trimesh
from scipy.ndimage import gaussian_filter


def thickness(bright, t_min=0.8, t_max=3.0, gamma=1.0):
    """Brightness 0..1 (1 = white) -> plate thickness in mm.

    Light through PLA falls off roughly exponentially with thickness, so a
    linear map crushes the highlights; gamma > 1 spends more of the range on
    them. 1.0 is a reasonable start for white PLA.
    """
    b = np.clip(bright, 0.0, 1.0) ** gamma
    return t_max - (t_max - t_min) * b


def add_border(T, px, border_mm, t_max):
    n = int(round(border_mm / px))
    return np.pad(T, n, constant_values=t_max) if n > 0 else T


def plate_mesh(T, px, width_mm=None):
    """Closed solid: relief top T[j, i] (row 0 = south), flat floor at z=0.

    Vectorised. The floor is a fan from its centre to the boundary vertices
    instead of a second full grid, which halves the triangle count -- a
    150 mm plate at 0.3 mm is already 500x500 cells.
    """
    ny, nx = T.shape
    xs, ys = np.arange(nx) * px, np.arange(ny) * px
    X, Y = np.meshgrid(xs, ys)
    top = np.column_stack([X.ravel(), Y.ravel(), T.ravel()])
    idx = np.arange(nx * ny).reshape(ny, nx)
    a, b = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel()
    c, d = idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    faces = [np.column_stack([a, b, c]), np.column_stack([a, c, d])]

    # boundary loop, counter-clockwise seen from above, corners once
    ring = np.concatenate([idx[0, :], idx[1:, -1], idx[-1, -2::-1], idx[-2:0:-1, 0]])
    N, R = nx * ny, len(ring)
    bot = top[ring].copy()
    bot[:, 2] = 0.0
    centre = np.array([[xs[-1] / 2, ys[-1] / 2, 0.0]])
    verts = np.vstack([top, bot, centre])
    t0, t1 = ring, np.roll(ring, -1)
    b0 = N + np.arange(R)
    b1 = np.roll(b0, -1)
    faces += [np.column_stack([t0, b0, b1]), np.column_stack([t0, b1, t1])]
    faces += [np.column_stack([np.full(R, N + R), b1, b0])]
    if width_mm:
        # vertices sit on cell centres, so the grid spans (n-1) cells; stretch
        # it (well under 1%) to the exact width asked for
        verts[:, :2] *= width_mm / max(xs[-1], ys[-1])
    return trimesh.Trimesh(verts, np.vstack(faces), process=False)


def from_photo(path, width_mm, px=0.3, t_min=0.8, t_max=3.0, border_mm=3.0,
               gamma=1.0, blur_px=0.6):
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(path)).convert("L")
    w = int(round((width_mm - 2 * border_mm) / px))
    h = max(int(round(w * img.height / img.width)), 2)
    img = img.resize((w, h), Image.LANCZOS)
    bright = np.asarray(img, dtype=float)[::-1] / 255.0      # row 0 = south
    if blur_px > 0:
        bright = gaussian_filter(bright, blur_px)              # nozzle-scale noise
    T = add_border(thickness(bright, t_min, t_max, gamma), px, border_mm, t_max)
    return plate_mesh(T, px, width_mm)


def map_brightness(centre, tile_mm, buildings, roads, water, opts, px):
    """Rasterise the city: water brightest, then streets, ground, buildings."""
    import shapely
    n = max(int(round(tile_mm / px)), 2)
    px = tile_mm / n
    cx, cy = centre
    xs = cx - tile_mm / 2 + px * (np.arange(n) + 0.5)
    ys = cy - tile_mm / 2 + px * (np.arange(n) + 0.5)
    X, Y = np.meshgrid(xs, ys)
    B = np.full(X.shape, 0.55)                                   # open ground
    if roads is not None:
        B[shapely.contains_xy(roads, X, Y)] = 0.85
    hmax = max([h for _, h in buildings] + [1e-6])
    hcap = min(hmax, opts["max_bld_h"])
    for poly, h in buildings:
        x0, y0, x1, y1 = poly.bounds
        sel = (X >= x0) & (X <= x1) & (Y >= y0) & (Y <= y1)
        if not sel.any():
            continue
        inside = np.zeros(X.shape, bool)
        inside[sel] = shapely.contains_xy(poly, X[sel], Y[sel])
        # taller reads darker: 0.35 for a low block down to 0.0 at the cap
        B = np.where(inside, np.minimum(B, 0.35 * (1 - min(h, hcap) / hcap)), B)
    if water is not None:
        B[shapely.contains_xy(water, X, Y)] = 1.0
    return gaussian_filter(B, 0.5), px


def map_tile(centre, tile_mm, buildings, roads, water, opts, cut=None):
    """`water` glows (thin plastic); `cut` (a shape's outside) is not printed.

    With a cut the border follows the shape: a heart gets a heart-shaped
    solid rim, so the plate is stiff and its edge reads as a clean outline.
    """
    import shapely
    from shapely.geometry import box
    px = opts["litho_px"]
    B, px = map_brightness(centre, tile_mm, buildings, roads, water, opts, px)
    T = thickness(B, opts["litho_min"], opts["litho_max"])
    border, t_max = opts["litho_border"], opts["litho_max"]
    cx, cy = centre
    square = box(cx - tile_mm / 2, cy - tile_mm / 2, cx + tile_mm / 2, cy + tile_mm / 2)
    keep = square if cut is None else square.difference(cut)
    if keep.is_empty or keep.area < 1.0:
        return None
    # border eats into the plate, so the outline stays exactly where asked
    n = T.shape[0]
    g = cx - tile_mm / 2 + px * (np.arange(n) + 0.5)
    X, Y = np.meshgrid(g, cy - tile_mm / 2 + px * (np.arange(n) + 0.5))
    inner = keep.buffer(-border)
    T = np.where(shapely.contains_xy(inner, X, Y), T, t_max)
    mesh = plate_mesh(T, px, tile_mm)
    mesh.apply_translation((cx - tile_mm / 2, cy - tile_mm / 2, 0))
    if cut is not None and not cut.intersection(square).is_empty:
        from .build import _extrude_area, ENGINE
        void = cut.intersection(square).buffer(0.001, join_style=2)
        mesh = trimesh.boolean.difference(
            [mesh] + _extrude_area(void, 100.0, z0=-1.0), engine=ENGINE)
    return mesh


def main(argv=None):
    p = argparse.ArgumentParser(prog="tilemaker.lithophane",
                                description="photo -> printable lithophane")
    p.add_argument("image")
    p.add_argument("--width", type=float, default=150.0, help="plate width, mm")
    p.add_argument("--px", type=float, default=0.3, help="relief resolution, mm")
    p.add_argument("--min", type=float, default=0.8, help="thinnest (brightest), mm")
    p.add_argument("--max", type=float, default=3.0, help="thickest (darkest), mm")
    p.add_argument("--border", type=float, default=3.0, help="solid frame, mm")
    p.add_argument("--gamma", type=float, default=1.0)
    p.add_argument("--out", default="out_lithophane")
    a = p.parse_args(argv)
    mesh = from_photo(a.image, a.width, a.px, a.min, a.max, a.border, a.gamma)
    os.makedirs(a.out, exist_ok=True)
    name = os.path.splitext(os.path.basename(a.image))[0]
    path = os.path.join(a.out, f"{name}_lithophane.stl")
    mesh.export(path)
    e = mesh.extents
    print(f"{'OK ' if mesh.is_watertight else 'CHECK'} {path}: "
          f"{e[0]:.1f} x {e[1]:.1f} x {e[2]:.1f} mm  {len(mesh.faces)} tri  "
          f"{mesh.volume/1000:.1f} cm3  ~{mesh.volume*1.24/1000:.0f} g PLA")
    print("Print flat, relief up, white PLA, 100% infill, 0.12-0.16 mm layers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
