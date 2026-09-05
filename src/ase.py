import os
import struct
from collections import namedtuple

import bpy
from bpy.props import StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper

from .color import linear_to_srgb, srgb_to_linear

SIGNATURE = b"ASEF"
BLOCK_GROUP_START = 0xC001
BLOCK_GROUP_END = 0xC002
BLOCK_COLOR_ENTRY = 0x0001

_MODEL_FLOAT_COUNTS = {"RGB ": 3, "CMYK": 4, "Gray": 1, "LAB ": 3}

# AseColor.rgb is a 3-tuple of gamma-encoded sRGB floats in 0..1, matching
# the convention parse_jasc_pal() already uses for its color values.
AseColor = namedtuple("AseColor", ["name", "group", "rgb"])


def _cmyk_to_rgb(c, m, y, k):
    return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))


def _gray_to_rgb(g):
    return (g, g, g)


def _read_name(data, offset):
    """Decode a length-prefixed UTF-16BE name at offset. Returns (name, new_offset)."""
    (name_len,) = struct.unpack(">H", data[offset:offset + 2])
    offset += 2
    # name_len counts UTF-16 code units including the null terminator.
    raw = data[offset:offset + name_len * 2]
    offset += name_len * 2
    return raw.decode("utf-16-be").rstrip("\x00"), offset


def _lab_to_rgb(l, a, b):
    fy = (l + 16) / 116
    fx = fy + a / 500
    fz = fy - b / 200

    def finv(t):
        t3 = t ** 3
        return t3 if t3 > 0.008856 else (t - 16 / 116) / 7.787

    # D65 reference white -- ASE doesn't record an illuminant; D65 matches
    # sRGB's own reference white and is the standard simplification used by
    # other open-source ASE readers.
    x_n, y_n, z_n = 95.047, 100.0, 108.883
    x = x_n * finv(fx) / 100.0
    y = y_n * finv(fy) / 100.0
    z = z_n * finv(fz) / 100.0

    r_lin = 3.2406 * x - 1.5372 * y - 0.4986 * z
    g_lin = -0.9689 * x + 1.8758 * y + 0.0415 * z
    b_lin = 0.0557 * x - 0.2040 * y + 1.0570 * z

    return tuple(linear_to_srgb(v) for v in (r_lin, g_lin, b_lin))


def parse_ase(filepath):
    """Parse an Adobe Swatch Exchange file.

    Returns (colors: list[AseColor], group_order: list[str]).
    """
    with open(filepath, "rb") as f:
        try:
            signature = f.read(4)
            if signature != SIGNATURE:
                raise ValueError("Not an ASE file (missing 'ASEF' signature)")

            f.read(4)  # version major/minor, informational only
            (block_count,) = struct.unpack(">I", f.read(4))

            colors = []
            group_order = []
            current_group = None

            for _ in range(block_count):
                block_type, block_len = struct.unpack(">HI", f.read(6))
                data = f.read(block_len)
                if len(data) != block_len:
                    raise ValueError("Truncated block")

                if block_type == BLOCK_GROUP_START:
                    current_group, _ = _read_name(data, 0)
                elif block_type == BLOCK_GROUP_END:
                    current_group = None
                elif block_type == BLOCK_COLOR_ENTRY:
                    name, rgb = _parse_color_entry(data)
                    group = current_group or ""
                    colors.append(AseColor(name, group, rgb))
                    if group and group not in group_order:
                        group_order.append(group)
                # Unknown block types are skipped -- block_len already tells
                # us how many bytes to advance past.

            return colors, group_order
        except struct.error as err:
            raise ValueError(f"Malformed ASE file: {err}") from err


def _parse_color_entry(data):
    name, offset = _read_name(data, 0)

    tag = data[offset:offset + 4].decode("ascii")
    offset += 4

    n = _MODEL_FLOAT_COUNTS.get(tag)
    if n is None:
        raise ValueError(f"Unknown ASE color model '{tag}'")
    vals = struct.unpack(f">{n}f", data[offset:offset + n * 4])
    # color_type (Global/Spot/Normal) follows the floats; read and discard.

    if tag == "RGB ":
        rgb = tuple(vals)
    elif tag == "CMYK":
        rgb = _cmyk_to_rgb(*vals)
    elif tag == "Gray":
        rgb = _gray_to_rgb(vals[0])
    else:  # "LAB "
        rgb = _lab_to_rgb(*vals)

    return name, rgb


class PALETTE_OT_import_ase(Operator, ImportHelper):
    """Import an Adobe Swatch Exchange (.ase) color palette as a Blender color palette"""

    bl_idname = "palette.import_ase"
    bl_label = "Import Adobe Swatch Exchange Palette"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".ase"
    filter_glob: StringProperty(
        default="*.ase",
        options={'HIDDEN'},
        maxlen=255,
    )

    def execute(self, context):
        try:
            colors, group_order = parse_ase(self.filepath)
        except (OSError, ValueError) as err:
            self.report({'ERROR'}, f"Could not import palette: {err}")
            return {'CANCELLED'}

        if not colors:
            self.report({'ERROR'}, "No colors found in palette file")
            return {'CANCELLED'}

        name = os.path.splitext(os.path.basename(self.filepath))[0]
        palette = bpy.data.palettes.new(name=name)
        meta = palette.palette_import_meta

        for group_name in group_order:
            meta.groups.add().name = group_name

        for entry in colors:
            r, g, b = entry.rgb
            color = palette.colors.new()
            color.color = (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b))
            color_meta = meta.colors.add()
            color_meta.name = entry.name
            color_meta.group_name = entry.group

        self.report({'INFO'}, f"Imported {len(colors)} colors into palette '{palette.name}'")
        return {'FINISHED'}
