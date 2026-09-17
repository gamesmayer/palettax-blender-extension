import bpy
from bpy.props import PointerProperty

from . import preferences
from .palettes import ase, pal, panels, properties
from .tools import color_tools, move_selection, scale_selection, select, selection

classes = (
    *properties.classes,
    selection.PALETTE_PG_selection,
    pal.PALETTE_OT_import_jasc,
    ase.PALETTE_OT_import_ase,
    panels.PALETTE_OT_apply_swatch_color,
    color_tools.PALETTE_OT_replace_color,
    select.PALETTE_OT_select,
    move_selection.PALETTE_OT_move_selection,
    scale_selection.PALETTE_OT_scale_selection,
    selection.PALETTE_OT_selection_history,
    panels.PALETTE_PT_current_color,
    panels.PALETTE_PT_current_color_image_editor,
    panels.PALETTE_PT_current_color_tool,
    panels.PALETTE_PT_extended_view,
    panels.PALETTE_PT_extended_view_image_editor,
    panels.PALETTE_PT_extended_view_tool,
    preferences.PALETTAX_AddonPreferences,
)


def menu_func_import(self, context):
    self.layout.operator(pal.PALETTE_OT_import_jasc.bl_idname, text="JASC/Gale Palette (.pal)")
    self.layout.operator(ase.PALETTE_OT_import_ase.bl_idname, text="Adobe Swatch Exchange (.ase)")


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Palette.palette_import_meta = PointerProperty(type=properties.PALETTE_PG_import_meta)
    bpy.types.Image.palettax_selection = PointerProperty(type=selection.PALETTE_PG_selection)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    panels.register_icons()
    selection._overlay_handle = selection.register_persistent_overlay()
    separated_space_types = set()
    for tool in color_tools.tools:
        # No `after=` on purpose: bpy.utils.register_tool() appends to the end
        # of the toolbar's tool list when `after` is omitted, which places
        # this as its own group at the very bottom, past Blender's built-in
        # brush, select-mask and annotate tools. Only the first tool per
        # space type gets a separator, so that one gap opens up before our
        # group (after Annotate) but our own tools stay bunched together
        # with no gaps between them.
        needs_separator = tool.bl_space_type not in separated_space_types
        separated_space_types.add(tool.bl_space_type)
        bpy.utils.register_tool(tool, separator=needs_separator)
    prefs = bpy.context.preferences.addons[__package__].preferences
    preferences.apply_hide_builtin_color_palette(prefs.hide_builtin_color_palette)


def unregister():
    preferences.apply_hide_builtin_color_palette(False)
    for tool in reversed(color_tools.tools):
        bpy.utils.unregister_tool(tool)
    selection.unregister_persistent_overlay(selection._overlay_handle)
    panels.unregister_icons()
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    del bpy.types.Image.palettax_selection
    del bpy.types.Palette.palette_import_meta
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
