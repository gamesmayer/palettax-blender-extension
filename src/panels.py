import bpy
import bpy.utils.previews
from bpy.props import FloatVectorProperty, StringProperty
from bpy.types import Operator, Panel

from .color import linear_to_srgb, srgb_to_linear

_preview_collection = None
_MAX_CACHED_ICONS = 512
_UI_UNIT_PX = 20
_SWATCH_UNITS_X = 1.3
_GRID_MARGIN_PX = 40


def register_icons():
    global _preview_collection
    _preview_collection = bpy.utils.previews.new()


def unregister_icons():
    global _preview_collection
    if _preview_collection is not None:
        bpy.utils.previews.remove(_preview_collection)
        _preview_collection = None


def _quantize(rgb):
    return tuple(round(c * 255) for c in rgb)


def _hex_for_gamma_color(rgb):
    r, g, b = _quantize(rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def _current_brush_color(image_paint):
    brush = image_paint.brush
    if brush is None:
        return None
    ups = image_paint.unified_paint_settings
    raw = ups.color if ups.use_unified_color else brush.color
    return tuple(linear_to_srgb(c) for c in raw)


def _grid_columns(context):
    region = context.region
    if region is None or region.width <= 0:
        return 1
    ui_scale = context.preferences.system.ui_scale
    available = region.width - _GRID_MARGIN_PX * ui_scale
    if available <= 0:
        return 1
    cell_width = _SWATCH_UNITS_X * _UI_UNIT_PX * ui_scale
    return max(1, int(available // cell_width))


def _icon_for_gamma_color(rgb):
    key = _quantize(rgb)
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
        name = properties.swatch_name or "Unnamed color"
        return f"{name}\n{_hex_for_gamma_color(properties.color)}"

    def execute(self, context):
        image_paint = context.tool_settings.image_paint
        brush = image_paint.brush
        if brush is None:
            self.report({'WARNING'}, "No active brush")
            return {'CANCELLED'}

        linear_color = tuple(srgb_to_linear(c) for c in self.color)
        brush.color = linear_color
        ups = image_paint.unified_paint_settings
        if ups.use_unified_color:
            ups.color = linear_color

        return {'FINISHED'}


class _PaletteSwatchesMixin:
    bl_label = "Palette Swatches"

    def draw(self, context):
        layout = self.layout
        image_paint = context.tool_settings.image_paint
        palette = image_paint.palette
        brush_color = _current_brush_color(image_paint)

        current_color = _quantize(brush_color) if brush_color is not None else None

        colors = list(palette.colors) if palette is not None else []
        entries = []
        group_names = []
        if colors:
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

        palette_colors = {}
        for c, name, _group in entries:
            gamma_key = _quantize(tuple(linear_to_srgb(v) for v in c.color))
            if gamma_key not in palette_colors:
                palette_colors[gamma_key] = name

        if brush_color is not None:
            row = layout.row()
            row.label(text="Current Color")
            row.template_icon(icon_value=_icon_for_gamma_color(brush_color), scale=3.0)

            hex_code = _hex_for_gamma_color(brush_color)
            if current_color in palette_colors:
                name = palette_colors[current_color] or "Unnamed color"
                layout.label(text=f"{name} - {hex_code}")
            else:
                layout.label(text=hex_code)

            if entries and current_color not in palette_colors:
                box = layout.box()
                box.alert = True
                box.label(text="Current color is not in the active palette", icon='ERROR')

        layout.template_ID(image_paint, "palette", new="palette.new")
        if palette is None:
            layout.label(text="No active palette", icon='INFO')
            return

        if not colors:
            layout.label(text="Palette is empty", icon='INFO')
            return

        columns = _grid_columns(context)

        ungrouped = [e for e in entries if e[2] == ""]
        if ungrouped:
            self._draw_grid(layout, ungrouped, columns, current_color)

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
            self._draw_grid(box, members, columns, current_color)

    def _draw_grid(self, layout, entries, columns, current_color=None):
        grid = layout.column(align=True)
        for start in range(0, len(entries), columns):
            row = grid.row(align=True)
            for color, name, _group in entries[start:start + columns]:
                gamma = tuple(linear_to_srgb(c) for c in color.color)
                icon_id = _icon_for_gamma_color(gamma)
                cell = row.row(align=True)
                cell.ui_units_x = _SWATCH_UNITS_X
                cell.ui_units_y = _SWATCH_UNITS_X
                op = cell.operator(
                    PALETTE_OT_apply_swatch_color.bl_idname,
                    text="",
                    icon_value=icon_id,
                    depress=(current_color is not None and _quantize(gamma) == current_color),
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
