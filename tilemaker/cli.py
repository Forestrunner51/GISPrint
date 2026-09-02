"""End-to-end: OSM + DEM -> print-ready modular map tiles."""
import argparse, json, math, os, sys, time
import numpy as np
from .osm import fetch
from .project import LocalPlane
from .geometry import buildings_mm, roads_mm
from .build import build_tile, report
from .stylize import stylize, stats
from . import route as routemod
from .joints import min_base_height
from .threemf import write_project


def main(argv=None):
    p = argparse.ArgumentParser(prog="tilemaker")
    p.add_argument("--center", help="lat,lon of the map centre")
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
    p.add_argument("--relations", action="store_true",
                   help="include building multipolygons (slow, 504s on large areas)")
    p.add_argument("--format", choices=["stl", "3mf", "both"], default="both")
    p.add_argument("--printer", choices=["bambu", "prusa", "ender3v3se", "ender3"],
                   default="bambu", help="bed size and pause dialect")
    p.add_argument("--coupon", action="store_true",
                   help="emit only the two-piece joint test coupon")
    p.add_argument("--out", default="out")
    a = p.parse_args(argv)

    cols, rows = [int(v) for v in a.grid.lower().split("x")]
    pts = None
    if a.route_gpx or a.route:
        pts = routemod.from_gpx(a.route_gpx) if a.route_gpx else routemod.from_string(a.route)
        if len(pts) < 2:
            p.error("route needs at least two points")
        (lat, lon), a.span = routemod.fit_grid(pts, cols, rows)
        print(f"route: {len(pts)} points, {routemod.length_m(pts)/1000:.2f} km -> "
              f"grid fitted at {lat:.5f},{lon:.5f}, span {a.span:.0f} m/tile")
    elif a.center:
        lat, lon = [float(v) for v in a.center.split(",")]
    else:
        p.error("give --center, --route or --route-gpx")

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
            mesh = build_tile((cx, cy), a.tile, blds, roads, opts, relief=relief,
                              route=ribbon)
            mesh.apply_translation((-cx, -cy, 0))   # each tile prints at origin
            name = f"tile_r{j}c{i}"
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
            flag = "OK " if r["watertight"] and r["bodies"] == 1 + voids else "CHECK"
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
