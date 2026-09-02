"""Print-readiness audit: overhangs, bed fit, magnet cavities.

Overhang convention: theta is the angle between a face normal and straight
down. theta = 0 is a horizontal ceiling (worst case, needs support); theta = 90
is a vertical wall (fine). A surface prints unsupported when theta > 45. The
tile's own underside sits on the plate and is excluded.
"""
import sys, numpy as np, trimesh

from tilemaker.threemf import PRINTERS


def bed_of(name):
    b = PRINTERS[name]["bed"]
    return (b[0], b[1], 240.0 if "ender" in name else 256.0)


def audit(path, plate_tol=0.05, bed=(256, 256, 256)):
    m = trimesh.load(path)
    n, a = m.face_normals, m.area_faces
    down = n[:, 2] < -1e-6
    theta = np.degrees(np.arccos(np.clip(-n[down][:, 2], 0, 1)))
    area = a[down]
    zc = m.triangles_center[down][:, 2]
    on_plate = zc <= m.bounds[0][2] + plate_tol
    need = (theta < 45) & ~on_plate
    return {
        "extents": [round(float(v), 1) for v in m.extents],
        "fits_bed": all(e <= b for e, b in zip(sorted(m.extents), sorted(bed))),
        "watertight": bool(m.is_watertight),
        "bodies": int(m.body_count),
        "volume_cm3": round(float(m.volume) / 1000, 1),
        "grams_pla": round(float(m.volume) * 1.24 / 1000, 1),
        "unsupported_mm2": round(float(area[need].sum()), 1),
        "unsupported_pct": round(100 * float(area[need].sum()) / float(m.area), 2),
        "worst_theta": round(float(theta[need].min()), 1) if need.any() else None,
    }


if __name__ == "__main__":
    args = sys.argv[1:]
    printer = "bambu"
    if args and args[0].startswith("--printer="):
        printer = args.pop(0).split("=", 1)[1]
    bed = bed_of(printer)
    print(f"bed: {bed[0]:.0f} x {bed[1]:.0f} x {bed[2]:.0f} mm ({printer})")
    for p in args:
        r = audit(p, bed=bed)
        print(f"\n{p}")
        print(f"  {r['extents']} mm  fits bed {r['fits_bed']}  watertight "
              f"{r['watertight']}  bodies {r['bodies']}")
        print(f"  {r['volume_cm3']} cm3  ~{r['grams_pla']} g PLA")
        print(f"  needs support: {r['unsupported_mm2']} mm2 "
              f"({r['unsupported_pct']}% of surface)"
              + (f", flattest face {r['worst_theta']} deg from down"
                 if r['worst_theta'] is not None else ""))
