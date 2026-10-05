"""End-to-end: OSM + DEM -> print-ready modular map tiles."""
import argparse, json, math, os, sys, time
import numpy as np
from .osm import fetch, fetch_water
from .project import LocalPlane
from .geometry import buildings_mm, roads_mm
from .build import build_tile, report
from .stylize import stylize, stats
from . import route as routemod
from .joints import min_base_height
from .threemf import write_project


def _slug(text):
    import re
    return re.sub(r"[^a-z0-9]+", "-", text.lower().split(",")[0]).strip("-") or "map"


def default_out(a):
    """prints/<style>/<place>_<shape>_<size>mm[_<n>mm-blocks]."""
    if a.coupon:
        return os.path.join("prints", "tests", "joint-coupon")
    place = (a.name or a.place or a.outline
             or (a.center.replace(",", "_") if a.center else "route"))
    parts = [_slug(place), "outline" if a.outline else a.shape, f"{a.tile:g}mm"]
    if a.grid.lower() != "1x1":
        parts.append(a.grid.lower())
    if a.style == "pixel":
        parts.append(f"{a.pixel:g}mm-blocks")
    return os.path.join("prints", a.style, "_".join(parts))


def main(argv=None):
    p = argparse.ArgumentParser(prog="tilemaker")
    p.add_argument("--center", help="lat,lon of the map centre")
    p.add_argument("--place", help="centre on a named place instead of lat,lon, "
                                   "e.g. 'Eiffel Tower, Paris'")
    p.add_argument("--route-gpx", help="GPX track: fit the set to this route")
    p.add_argument("--route", help="'lat,lon;lat,lon;...' inline route")
    p.add_argument("--route-h", type=float, default=1.2, help="route ridge, mm")
    p.add_argument("--route-w", type=float, default=1.6, help="route width, mm")
    p.add_argument("--grid", default="2x2", help="COLSxROWS of tiles")
    p.add_argument("--tile", type=float, default=60.0, help="tile edge, mm")
    p.add_argument("--span", type=float, default=250.0, help="ground metres per tile")
    p.add_argument("--joint", choices=["magnet", "jigsaw", "none"], default="magnet")
    p.add_argument("--clearance", type=float, default=0.25, help="FDM joint gap, mm")
    p.add_argument("--base-h", type=float, default=4.0)
    p.add_argument("--road-depth", type=float, default=0.7, help="0 to disable")
    p.add_argument("--vscale", type=float, default=1.0, help="building exaggeration")
    p.add_argument("--max-bld-h", type=float, default=70.0)
    p.add_argument("--min-bld-h", type=float, default=1.2)
    p.add_argument("--min-wall", type=float, default=0.4,
                   help="nozzle width; footprints thinner than this are dropped")
    p.add_argument("--min-footprint", type=float, default=1.0, help="mm2")
    p.add_argument("--magnet-d", type=float, default=5.0)
    p.add_argument("--magnet-h", type=float, default=2.0)
    p.add_argument("--magnet-wall", type=float, default=0.8,
                   help="plastic between magnet face and seam")
    p.add_argument("--magnet-floor", type=float, default=0.8)
    p.add_argument("--pocket-teardrop", action="store_true",
                   help="peaked pocket roof: no unsupported arch, taller base")
    p.add_argument("--lowpoly", action="store_true", help="stylised low-poly finish")
    p.add_argument("--style", choices=["classic", "pixel", "lithophane"],
                   default="classic",
                   help="pixel: square block columns. lithophane: a thin backlit "
                        "plate, water and streets glow, buildings dark")
    p.add_argument("--litho-px", type=float, default=0.3, help="lithophane relief resolution, mm")
    p.add_argument("--litho-min", type=float, default=0.8, help="lithophane thinnest, mm")
    p.add_argument("--litho-max", type=float, default=3.0, help="lithophane thickest, mm")
    p.add_argument("--litho-border", type=float, default=3.0, help="lithophane solid frame, mm")
    p.add_argument("--pixel", type=float, default=2.0, help="pixel-style cell, mm")
    p.add_argument("--pixel-step", type=float, default=0.0,
                   help="pixel-style height quantum, mm (0 = cell size: cubes)")
    p.add_argument("--simplify", type=float, default=0.35, help="mm, Douglas-Peucker")
    p.add_argument("--height-step", type=float, default=1.5, help="mm height quantum")
    p.add_argument("--merge-gap", type=float, default=0.6, help="mm, block merging")
    p.add_argument("--magnet-mode", choices=["glue", "embed"], default="glue",
                   help="glue: pocket open at the bed. embed: buried pocket + "
                        "a slicer pause so the magnet is captive and invisible")
    p.add_argument("--magnet-z", type=float, default=0.8, help="embed pocket floor, mm")
    p.add_argument("--terrain", action="store_true", help="fuse Terrarium DEM relief")
    p.add_argument("--terrain-vscale", type=float, default=1.0)
    p.add_argument("--dem-zoom", type=int, default=15, help="15 ~ 3.8 m/px")
    p.add_argument("--terrain-grid", type=int, default=96, help="samples per tile edge")
    p.add_argument("--dem-smooth", type=float, default=1.0, help="DEM blur, px")
    p.add_argument("--water", action="store_true",
                   help="leave rivers, lakes and sea unprinted (land-only tiles "
                        "for a coloured backing board); piers are kept")
    p.add_argument("--shape", choices=["square", "circle", "heart", "hexagon"],
                   default="square", help="cut the whole set to this shape")
    p.add_argument("--outline", help="cut the set to a named OSM area, e.g. "
                                     "'Roosevelt Island' -- also centres and sizes it")
    p.add_argument("--relations", action="store_true",
                   help="include building multipolygons (slow, 504s on large areas)")
    p.add_argument("--format", choices=["stl", "3mf", "both"], default="both")
    p.add_argument("--printer", choices=["bambu", "prusa", "ender3v3se", "ender3"],
                   default="bambu", help="bed size and pause dialect")
    p.add_argument("--coupon", action="store_true",
                   help="emit only the two-piece joint test coupon")
    p.add_argument("--name", help="place label for the output folder, e.g. "
                                  "'lower manhattan'")
    p.add_argument("--out", help="output folder (default: prints/<style>/"
                                 "<name>_<shape>_<size>mm)")
    a = p.parse_args(argv)
    if not a.out:
        a.out = default_out(a)

    cols, rows = [int(v) for v in a.grid.lower().split("x")]
    pts = None
    if a.route_gpx or a.route:
        pts = routemod.from_gpx(a.route_gpx) if a.route_gpx else routemod.from_string(a.route)
        if len(pts) < 2:
            p.error("route needs at least two points")
        (lat, lon), a.span = routemod.fit_grid(pts, cols, rows)
        print(f"route: {len(pts)} points, {routemod.length_m(pts)/1000:.2f} km -> "
              f"grid fitted at {lat:.5f},{lon:.5f}, span {a.span:.0f} m/tile")
    elif a.outline:
        from .shapes import lookup_outline, outline_fit
        hit = lookup_outline(a.outline)
        (lat, lon), a.span = outline_fit(hit, cols, rows)
        print(f"outline: {hit['name']}\n  -> centred {lat:.5f},{lon:.5f}, "
              f"span {a.span:.0f} m/tile")
        if a.span > 4000:
            p.error(f"outline needs {a.span/1000:.1f} km per tile: buildings would be "
                    f"far below nozzle size and Overpass would time out. Pick a "
                    f"smaller area (a neighbourhood, island or park).")
    elif a.place:
        from .shapes import lookup_place
        try:
            hit_pt = lookup_place(a.place)
        except RuntimeError as exc:
            p.error(str(exc))
        lat, lon = hit_pt["lat"], hit_pt["lon"]
        print(f"place: {hit_pt['name']}\n  -> centred {lat:.5f},{lon:.5f}")
    elif a.center:
        lat, lon = [float(v) for v in a.center.split(",")]
    else:
        p.error("give --place, --center, --outline, --route or --route-gpx")

    if a.style != "classic" and (a.terrain or a.joint != "none" or pts):
        p.error(f"--style {a.style} supports flat sets with --joint none, no route yet")
    if a.joint == "magnet":
        need = min_base_height(a.magnet_d, floor=a.magnet_floor)
        if a.pocket_teardrop:
            need = round(need + (a.magnet_d + 0.2) * 0.42, 2)
        if a.base_h < need:
            print(f"base height {a.base_h} -> {need} mm "
                  f"(horizontal pocket needs the magnet's diameter of clear height)")
            a.base_h = need
    if a.coupon:
        from .coupon import make, guide
        from .magnetics import seam_force
        os.makedirs(a.out, exist_ok=True)
        pieces = make(a.tile, base_h=a.base_h, magnet_d=a.magnet_d,
                      magnet_h=a.magnet_h, wall=a.magnet_wall,
                      floor=a.magnet_floor, mode=a.magnet_mode,
                      teardrop=a.pocket_teardrop)
        f = abs(seam_force(0, "horizontal",
                           wall=a.magnet_wall if a.magnet_mode == "embed" else 0.0,
                           tile_gap=0.0 if a.magnet_mode == "embed" else 0.2,
                           dia=a.magnet_d, thick=a.magnet_h)) * 2
        meshes = []
        for name, mesh, _ in pieces:
            mesh.export(os.path.join(a.out, name + ".stl"))
            meshes.append((name, mesh))
            r = report(mesh)
            print(f"{'OK ' if r['watertight'] else 'CHECK'} {name}: "
                  f"{r['bbox_mm']} mm  {r['volume_cm3']} cm3  "
                  f"~{r['volume_cm3']*1.24:.0f} g PLA")
        with open(os.path.join(a.out, "COUPON.md"), "w") as fh:
            fh.write(guide(pieces[0][2], pieces[1][2], f))
        info = write_project(os.path.join(a.out, "coupon.3mf"), meshes,
                             tile_mm=a.tile, title="joint test coupon",
                             printer=a.printer)
        print(f"expected seam pull: {f:.1f} N ({f/9.81*1000:.0f} g)")
        print(f"coupon.3mf on a {info['bed'][0]:.0f}x{info['bed'][1]:.0f} bed -> {a.out}/")
        return 0

    scale = a.tile / a.span                      # mm per ground metre
    print(f"scale 1:{round(1000/scale):,}  ({a.span} m -> {a.tile} mm per tile)")

    dlat = (rows * a.span) / 110540.0
    dlon = (cols * a.span) / (111320.0 * math.cos(math.radians(lat)))
    m = 0.06
    bbox = (lat - dlat/2*(1+m), lon - dlon/2*(1+m),
            lat + dlat/2*(1+m), lon + dlon/2*(1+m))

    t0 = time.time()
    els = fetch(bbox, want_roads=a.road_depth > 0, relations=a.relations)["elements"]
    print(f"OSM: {len(els)} elements in {time.time()-t0:.1f}s")

    plane = LocalPlane(bbox)
    blds = buildings_mm(els, plane, scale, a.vscale)
    roads = roads_mm(els, plane, scale) if a.road_depth > 0 else None
    if a.lowpoly:
        before = blds
        blds = stylize(blds, simplify_mm=a.simplify, height_step=a.height_step,
                       merge_gap=a.merge_gap, min_area=a.min_footprint)
        st = stats(before, blds)
        print(f"low-poly: solids {st['solids'][0]} -> {st['solids'][1]}, "
              f"footprint vertices {st['footprint_vertices'][0]} -> "
              f"{st['footprint_vertices'][1]} "
              f"({100*(1-st['footprint_vertices'][1]/max(st['footprint_vertices'][0],1)):.0f}% cut)")
    water = shape_cut = None
    if a.water:
        from shapely.geometry import box as _box
        from .water import water_mm
        t = time.time()
        wels = fetch_water(bbox)["elements"]
        region = _box(-cols * a.tile / 2.0, -rows * a.tile / 2.0,
                      cols * a.tile / 2.0, rows * a.tile / 2.0)
        water, n_piers = water_mm(wels, plane, scale, region, min_wall=a.min_wall)
        pct = 0.0 if water is None else 100.0 * water.area / region.area
        print(f"water: {len(wels)} elements, {pct:.0f}% of the set is water, "
              f"{n_piers} piers kept, {time.time()-t:.1f}s")
    if a.shape != "square" or a.outline:
        from shapely.geometry import box as _box
        from .shapes import preset, outline_mm
        from .water import clean
        region = _box(-cols * a.tile / 2.0, -rows * a.tile / 2.0,
                      cols * a.tile / 2.0, rows * a.tile / 2.0)
        keep = outline_mm(hit, plane, scale) if a.outline else preset(a.shape, region)
        if a.outline and a.shape != "square":
            keep = keep.intersection(preset(a.shape, region))
        outside = region.difference(keep)
        # a lithophane keeps water as glowing plate and only cuts the shape
        shape_cut = clean(region, outside, a.min_wall)
        if a.style != "lithophane":
            water = clean(region, outside if water is None else water.union(outside),
                          a.min_wall)
        print(f"shape: {a.outline or a.shape}, "
              f"{100 * (shape_cut.area if shape_cut else 0) / region.area:.0f}% "
              f"of the square cut away")
    ribbon = routemod.ribbon(pts, plane, scale, a.route_w) if pts else None
    print(f"geometry: {len(blds)} buildings, roads={'yes' if roads else 'no'}"
          + (", route ridge" if ribbon is not None else ""))

    relief = None
    if a.terrain:
        from .dem import DEM
        t = time.time()
        dem = DEM(bbox, zoom=a.dem_zoom, smooth=a.dem_smooth)
        W = cols * a.tile / 2.0, rows * a.tile / 2.0
        gx, gy = np.meshgrid(np.linspace(-W[0], W[0], 160), np.linspace(-W[1], W[1], 160))

        def to_ll(x, y):
            return (plane.lon0 + (x / scale) / plane.mx,
                    plane.lat0 + (y / scale) / plane.my)

        e_ref = float(dem.sample(*to_ll(gx.ravel(), gy.ravel())).min())
        e_max = float(dem.sample(*to_ll(gx.ravel(), gy.ravel())).max())

        def relief(x, y):
            return (dem.sample(*to_ll(x, y)) - e_ref) * scale * a.terrain_vscale

        print(f"DEM: {dem.tiles[2]} tiles @ {dem.res_m:.1f} m/px, relief "
              f"{e_max-e_ref:.0f} m -> {(e_max-e_ref)*scale*a.terrain_vscale:.1f} mm "
              f"in {time.time()-t:.1f}s")

    os.makedirs(a.out, exist_ok=True)
    opts = vars(a)
    manifest = {"scale_denominator": round(1000/scale), "tile_mm": a.tile,
                "span_m": a.span, "joint": a.joint, "terrain": a.terrain,
                "attribution": "(c) OpenStreetMap contributors, ODbL",
                "tiles": []}
    meshes = []

    for j in range(rows):
        for i in range(cols):
            cx = (i + 0.5) * a.tile - cols * a.tile / 2.0
            cy = (j + 0.5) * a.tile - rows * a.tile / 2.0
            t = time.time()
            if a.style == "lithophane":
                from .lithophane import map_tile
                # the plate is the whole tile: water glows instead of being cut
                mesh = map_tile((cx, cy), a.tile, blds, roads, water, opts,
                                cut=shape_cut)
                if mesh is not None:
                    mesh.metadata["buildings"] = len(blds)
                    mesh.metadata["land_parts"] = mesh.body_count
            elif a.style == "pixel":
                from .pixel import build_pixel_tile
                mesh, cols_up = build_pixel_tile((cx, cy), a.tile, blds, roads,
                                                 water, opts)
                if mesh is not None:
                    mesh.metadata["buildings"] = cols_up
                    mesh.metadata["land_parts"] = mesh.body_count
            else:
                mesh = build_tile((cx, cy), a.tile, blds, roads, opts, relief=relief,
                                  route=ribbon, water=water)
            name = f"tile_r{j}c{i}"
            if mesh is None:
                print(f"--  {name}: all water, nothing to print")
                continue
            mesh.apply_translation((-cx, -cy, 0))   # each tile prints at origin
            if a.format in ("stl", "both"):
                mesh.export(os.path.join(a.out, name + ".stl"))
            meshes.append((name, mesh))
            r = report(mesh) | {"name": name, "row": j, "col": i,
                                "buildings": mesh.metadata.get("buildings", 0)}
            manifest["tiles"].append(r)
            # embed mode leaves 8 fully-enclosed magnet cavities: each is a
            # legitimate closed shell, so the expected body count is 1 + voids.
            voids = 8 if (a.joint == "magnet" and a.magnet_mode == "embed") else 0
            r["voids"] = voids
            # With --water a tile can hold several separate landmasses (an
            # island, a pier that only joins land off-tile): one body each.
            land = mesh.metadata.get("land_parts", 1)
            flag = "OK " if r["watertight"] and r["bodies"] <= land + voids else "CHECK"
            print(f"{flag} {name}: {r['buildings']:>3} bld  {r['triangles']:>6} tri  "
                  f"{r['volume_cm3']:>6} cm3  h={r['bbox_mm'][2]:>5} mm  {time.time()-t:.1f}s")

    pause_z = None
    if a.joint == "magnet" and a.magnet_mode == "embed":
        pause_z = round(a.magnet_floor + a.magnet_d + 0.2, 2)
    if a.joint == "magnet":
        from .joints import magnet_pockets
        guide = magnet_pockets(a.tile, (0.0, 0.0), wall=a.magnet_wall, thick=a.magnet_h)
        manifest["magnets"] = [{"edge": g["edge"], "xy": [round(v, 2) for v in g["xy"]],
                                "pole_out": g["pole"]} for g in guide]
        with open(os.path.join(a.out, "MAGNETS.md"), "w") as fh:
            fh.write(f"# Magnet insertion guide\n\n{a.magnet_d}x{a.magnet_h} mm discs, "
                     f"{len(guide)} per tile, identical on every tile.\n\n"
                     "Pole listed is the one that must point OUT of the tile.\n"
                     "Tiles mate at 0 and 180 degrees, not at 90.\n\n"
                     "| edge | x (mm) | y (mm) | pole out |\n|---|---|---|---|\n")
            for g in guide:
                fh.write(f"| {g['edge']} | {g['xy'][0]:.2f} | {g['xy'][1]:.2f} "
                         f"| **{g['pole']}** |\n")
    if a.format in ("3mf", "both"):
        path = os.path.join(a.out, "plate.3mf")
        info = write_project(path, meshes, tile_mm=a.tile, pause_z=pause_z,
                             title=f"Map tiles 1:{round(1000/scale)}",
                             printer=a.printer)
        print(f"3MF project -> {path}  plate {info['plate']} on "
              f"{info['bed'][0]:.0f}x{info['bed'][1]:.0f}, fits: {info['fits_plate']}")
        if pause_z:
            if info["auto_pause"]:
                print(f"  magnet pause written at z={pause_z} mm ({info['pause_cmd']})")
            else:
                print(f"  ! {a.printer} is Marlin: no 3MF pause metadata is read. "
                      f"Add a pause at z={pause_z} mm in the slicer "
                      f"({info['pause_cmd']}), see PAUSE_README.txt inside the 3MF,"
                      f" or build with --magnet-mode glue and skip the pause.")

    with open(os.path.join(a.out, "assembly.scad"), "w") as fh:
        fh.write(f"gap = 0;\nfor (j=[0:{rows-1}]) for (i=[0:{cols-1}])\n"
                 f"  translate([i*({a.tile}+gap), j*({a.tile}+gap), 0])\n"
                 f'    import(str("tile_r", j, "c", i, ".stl"));\n')
    with open(os.path.join(a.out, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"\nwrote {len(manifest['tiles'])} tiles -> {a.out}/")
    print("Data (c) OpenStreetMap contributors, ODbL. Credit required on the product.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
