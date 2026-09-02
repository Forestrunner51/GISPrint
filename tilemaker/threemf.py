"""Stage 4: 3MF project export.

The core 3MF (geometry + plate layout) is the open spec and loads anywhere.
The magnet-pause is *not* in the spec -- each slicer keeps custom G-code in its
own metadata file -- so we write both vendor forms into the same container.
Bambu Studio reads Metadata/custom_gcode_per_layer.xml; PrusaSlicer reads
Metadata/Prusa_Slicer_custom_gcode_per_print_z.xml. Neither trips the other up.
"""
import zipfile
import numpy as np

NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"

# Bed sizes and, more importantly, the pause dialect each firmware speaks.
# M601 is Prusa firmware. Marlin printers (every Creality Ender) use M600, and
# sending them M601 does nothing -- the print runs straight over the magnet
# layer and seals the pockets shut.
PRINTERS = {
    "bambu":       {"bed": (256.0, 256.0), "pause": "pause", "flavour": "bambu"},
    "prusa":       {"bed": (250.0, 210.0), "pause": "M601",  "flavour": "prusa"},
    "ender3v3se":  {"bed": (220.0, 220.0), "pause": "M600",  "flavour": "marlin"},
    "ender3":      {"bed": (220.0, 220.0), "pause": "M600",  "flavour": "marlin"},
}
PLATE = PRINTERS["bambu"]["bed"]

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
<Default Extension="xml" ContentType="text/xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>"""


def _layout(n, tile_mm, gap=4.0, margin=10.0, plate=None):
    """Grid the tiles onto the plate, centred, row-major.

    Keep a margin: the nominal bed is not all printable (purge zones, clips),
    and a skirt needs room.
    """
    plate = plate or PLATE
    pitch = tile_mm + gap
    cols = max(1, min(n, int((plate[0] - 2 * margin) // pitch)))
    rows = int(np.ceil(n / cols))
    w, h = cols * pitch - gap, rows * pitch - gap
    x0, y0 = (plate[0] - w) / 2 + tile_mm / 2, (plate[1] - h) / 2 + tile_mm / 2
    fits = w <= plate[0] - 2 * margin and h <= plate[1] - 2 * margin
    return [(x0 + (k % cols) * pitch, y0 + (k // cols) * pitch) for k in range(n)], \
           (cols, rows, fits)


def _object_xml(oid, mesh, name):
    v = mesh.vertices
    f = mesh.faces
    vs = "".join(f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in v)
    ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in f)
    return (f'<object id="{oid}" type="model" name="{name}">'
            f"<mesh><vertices>{vs}</vertices>"
            f"<triangles>{ts}</triangles></mesh></object>")


def _marlin_pause(z, gcode="M600"):
    """Marlin has no 3MF pause metadata, so ship the snippet as a readme."""
    return (f"Insert magnets at Z = {z} mm.\n\n"
            "No slicer reads a pause out of a 3MF for Marlin firmware, so add\n"
            "it by hand after importing:\n\n"
            f"  OrcaSlicer / Cura: right-click the layer slider at Z={z} mm ->\n"
            f"  'Add pause' (Orca) or 'Pause at height' (Cura). Verify the\n"
            f"  emitted command is {gcode}, not M601.\n\n"
            "Then drop the magnets in, poles per MAGNETS.md, and resume.\n"
            "Easier alternative: build with --magnet-mode glue. The pockets\n"
            "open at the seam face, no pause is needed, and the joint is\n"
            "stronger because the magnet faces touch across the seam.\n")


def _bambu_pause(z):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<custom_gcodes_per_layer>'
            '<plate><plate_info id="1"/>'
            f'<layer top_z="{z}" type="2" extruder="1" color="" extra="" gcode="pause"/>'
            '<mode value="SingleExtruder"/></plate></custom_gcodes_per_layer>')


def _prusa_pause(z):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<custom_gcodes_per_print_z>'
            f'<code print_z="{z}" type="1" extruder="1" color="" extra="" gcode="M601"/>'
            '<mode value="SingleExtruder"/></custom_gcodes_per_print_z>')


def write_project(path, meshes, tile_mm, pause_z=None, title="map tiles",
                  printer="bambu"):
    """meshes = [(name, trimesh.Trimesh)], each already centred on its origin."""
    cfg = PRINTERS.get(printer, PRINTERS["bambu"])
    pos, (cols, rows, fits) = _layout(len(meshes), tile_mm, plate=cfg["bed"])
    objs, items = [], []
    for k, (name, mesh) in enumerate(meshes):
        oid = k + 1
        objs.append(_object_xml(oid, mesh, name))
        x, y = pos[k]
        items.append(f'<item objectid="{oid}" transform="1 0 0 0 1 0 0 0 1 '
                     f'{x:.4f} {y:.4f} 0"/>')
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{NS}">'
        f'<metadata name="Title">{title}</metadata>'
        '<metadata name="Designer">tilemaker</metadata>'
        '<metadata name="Description">Map data (c) OpenStreetMap contributors, '
        'ODbL. Elevation: Terrarium / AWS open data.</metadata>'
        '<metadata name="LicenseTerms">Derived work. Attribution required: '
        '(c) OpenStreetMap contributors.</metadata>'
        f'<resources>{"".join(objs)}</resources>'
        f'<build>{"".join(items)}</build></model>')

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("3D/3dmodel.model", model)
        z.writestr("ATTRIBUTION.txt",
                   "Map data (c) OpenStreetMap contributors, licensed ODbL "
                   "(opendatacommons.org/licenses/odbl).\nThis 3D model is a "
                   "Produced Work: attribution required, share-alike does not "
                   "extend to it.\nElevation: Terrarium tiles, AWS open data.\n")
        if pause_z is not None:
            if cfg["flavour"] == "marlin":
                z.writestr("PAUSE_README.txt",
                           _marlin_pause(pause_z, cfg["pause"]))
            else:
                z.writestr("Metadata/custom_gcode_per_layer.xml",
                           _bambu_pause(pause_z))
                z.writestr("Metadata/Prusa_Slicer_custom_gcode_per_print_z.xml",
                           _prusa_pause(pause_z))
    return {"plate": f"{cols}x{rows}", "fits_plate": fits, "pause_z": pause_z,
            "bed": cfg["bed"], "pause_cmd": cfg["pause"],
            "auto_pause": cfg["flavour"] != "marlin"}
