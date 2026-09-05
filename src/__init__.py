import bpy
from bpy.props import PointerProperty

from . import ase, pal, panels, preferences, properties

classes = (
    *properties.classes,
    pal.PALETTE_OT_import_jasc,
    ase.PALETTE_OT_import_ase,
    panels.PALETTE_OT_apply_swatch_color,
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
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    panels.register_icons()
    prefs = bpy.context.preferences.addons[__package__].preferences
    preferences.apply_hide_builtin_color_palette(prefs.hide_builtin_color_palette)


def unregister():
    preferences.apply_hide_builtin_color_palette(False)
    panels.unregister_icons()
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    del bpy.types.Palette.palette_import_meta
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
