import bpy


class _ColorSamplerKeymapMixin:
    bl_label = "Sample Color"
    bl_description = "Sample a color from the image texture into the active brush color"
    bl_icon = "ops.paint.weight_sample"  # no dedicated icon ships with Blender; it reuses this too
    bl_cursor = 'EYEDROPPER'
    bl_widget = None
    bl_keymap = (
        ("paint.sample_color", {"type": 'LEFTMOUSE', "value": 'PRESS'},
         {"properties": [("merged", False)]}),
    )


class PALETTE_TOOL_color_sampler_view3d(_ColorSamplerKeymapMixin, bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'PAINT_TEXTURE'
    bl_idname = "palette.color_sampler_view3d"


class PALETTE_TOOL_color_sampler_image_editor(_ColorSamplerKeymapMixin, bpy.types.WorkSpaceTool):
    bl_space_type = 'IMAGE_EDITOR'
    bl_context_mode = 'PAINT'
    bl_idname = "palette.color_sampler_image_editor"


tools = (
    PALETTE_TOOL_color_sampler_view3d,
    PALETTE_TOOL_color_sampler_image_editor,
)
