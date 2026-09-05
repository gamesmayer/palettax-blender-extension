import bpy
import bpy.utils.previews
from bpy.props import FloatVectorProperty, StringProperty
from bpy.types import Operator, Panel

from .color import linear_to_srgb

_preview_collection = None
_MAX_CACHED_ICONS = 512


def register_icons():
    global _preview_collection
    _preview_collection = bpy.utils.previews.new()


def unregister_icons():
    global _preview_collection
    if _preview_collection is not None:
        bpy.utils.previews.remove(_preview_collection)
        _preview_collection = None


def _icon_for_gamma_color(rgb):
    key = tuple(round(c * 255) for c in rgb)
    name = f"swatch_{key[0]}_{key[1]}_{key[2]}"
    if name not in _preview_collection:
        if len(_preview_collection) > _MAX_CACHED_ICONS:
            _preview_collection.clear()
        img = _preview_collection.new(name)
        img.image_size = (8, 8)
        img.image_pixels_float = [key[0] / 255, key[1] / 255, key[2] / 255, 1.0] * 64
    return _preview_collection[name].icon_id


class PALETTE_OT_apply_swatch_color(Operator):
    """Set as active brush color"""

    bl_idname = "palette.apply_swatch_color"
    bl_label = "Set Brush Color"
    bl_options = {'REGISTER', 'UNDO'}

    color: FloatVectorProperty(subtype='COLOR_GAMMA', size=3, min=0.0, max=1.0)
    swatch_name: StringProperty(default="")

    @classmethod
    def description(cls, context, properties):
        return properties.swatch_name or "Unnamed color -- click to set as brush color"

    def execute(self, context):
        image_paint = context.tool_settings.image_paint
        brush = image_paint.brush
        if brush is None:
            self.report({'WARNING'}, "No active brush")
            return {'CANCELLED'}

        brush.color = self.color
        ups = image_paint.unified_paint_settings
        if ups.use_unified_color:
            ups.color = self.color

        return {'FINISHED'}


class _PaletteSwatchesMixin:
    bl_label = "Palette Swatches"

    def draw(self, context):
        layout = self.layout
        palette = context.tool_settings.image_paint.palette
        if palette is None:
            layout.label(text="No active palette", icon='INFO')
            return

        colors = list(palette.colors)
        if not colors:
            layout.label(text="Palette is empty", icon='INFO')
            return

        meta = palette.palette_import_meta
        if len(meta.colors) == len(colors):
            entries = [(c, m.name, m.group_name) for c, m in zip(colors, meta.colors)]
            group_names = [g.name for g in meta.groups]
        else:
            # Palette was edited (colors added/removed/reordered) after
            # import, so the metadata no longer lines up by index. Fall
            # back to a flat, unnamed, ungrouped view rather than showing
            # wrong names or crashing.
            entries = [(c, "", "") for c in colors]
            group_names = []

        ungrouped = [e for e in entries if e[2] == ""]
        if ungrouped:
            self._draw_grid(layout, ungrouped)

        seen = set()
        for group_name in group_names:
            if not group_name or group_name in seen:
                continue
            members = [e for e in entries if e[2] == group_name]
            if not members:
                continue
            seen.add(group_name)
            box = layout.box()
            box.label(text=group_name, icon='GROUP')
            self._draw_grid(box, members)

    def _draw_grid(self, layout, entries):
        grid = layout.grid_flow(row_major=True, columns=8, even_columns=True, even_rows=True, align=True)
        for color, name, _group in entries:
            gamma = tuple(linear_to_srgb(c) for c in color.color)
            icon_id = _icon_for_gamma_color(gamma)
            op = grid.operator(
                PALETTE_OT_apply_swatch_color.bl_idname,
                text="",
                icon_value=icon_id,
            )
            op.color = gamma
            op.swatch_name = name


class PALETTE_PT_extended_view(_PaletteSwatchesMixin, Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Palettax"

    @classmethod
    def poll(cls, context):
        return context.mode == 'PAINT_TEXTURE'


class PALETTE_PT_extended_view_image_editor(_PaletteSwatchesMixin, Panel):
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Palettax"

    @classmethod
    def poll(cls, context):
        return context.space_data.mode == 'PAINT'


class PALETTE_PT_extended_view_tool(_PaletteSwatchesMixin, Panel):
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'imagepaint'

    @classmethod
    def poll(cls, context):
        return context.mode == 'PAINT_TEXTURE'
