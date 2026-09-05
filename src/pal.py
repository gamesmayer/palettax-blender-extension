import os

import bpy
from bpy.props import StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper

from .color import srgb_to_linear


def parse_jasc_pal(filepath):
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines or lines[0].upper() != "JASC-PAL":
        raise ValueError("Not a JASC-PAL file (missing 'JASC-PAL' header)")

    # lines[1] is the format version, lines[2] is the declared color count.
    # Both are informational only; we parse whatever color lines follow.
    colors = []
    for line in lines[3:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            r, g, b = (int(parts[i]) for i in range(3))
        except ValueError:
            continue
        r = min(255, max(0, r))
        g = min(255, max(0, g))
        b = min(255, max(0, b))
        colors.append((r / 255.0, g / 255.0, b / 255.0))

    return colors


class PALETTE_OT_import_jasc(Operator, ImportHelper):
    """Import a JASC/Gale (.pal) color palette as a Blender color palette"""

    bl_idname = "palette.import_jasc"
    bl_label = "Import JASC/Gale Palette"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".pal"
    filter_glob: StringProperty(
        default="*.pal",
        options={'HIDDEN'},
        maxlen=255,
    )

    def execute(self, context):
        try:
            colors = parse_jasc_pal(self.filepath)
        except (OSError, ValueError) as err:
            self.report({'ERROR'}, f"Could not import palette: {err}")
            return {'CANCELLED'}

        if not colors:
            self.report({'ERROR'}, "No colors found in palette file")
            return {'CANCELLED'}

        name = os.path.splitext(os.path.basename(self.filepath))[0]
        palette = bpy.data.palettes.new(name=name)
        meta = palette.palette_import_meta
        for r, g, b in colors:
            color = palette.colors.new()
            # PaletteColor.color is subtype 'COLOR' (scene-linear), not
            # 'COLOR_GAMMA', so Blender re-applies the display transform on
            # top of it. Decode the sRGB bytes to linear here so the color
            # that's actually displayed matches the original sRGB values.
            color.color = (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b))
            meta.colors.add()

        self.report({'INFO'}, f"Imported {len(colors)} colors into palette '{palette.name}'")
        return {'FINISHED'}
