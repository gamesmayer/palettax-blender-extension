import bpy
import numpy as np
from bpy.props import BoolProperty, IntVectorProperty
from bpy_extras import view3d_utils
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform

from .color import linear_to_srgb


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


def _current_brush_linear_color(image_paint):
    brush = image_paint.brush
    if brush is None:
        return None
    ups = image_paint.unified_paint_settings
    raw = ups.color if ups.use_unified_color else brush.color
    return tuple(raw)


def _brush_color_for_image(image_paint, image):
    linear = _current_brush_linear_color(image_paint)
    if linear is None:
        return None
    if image.colorspace_settings.name == 'sRGB':
        return tuple(linear_to_srgb(c) for c in linear)
    return linear


def _resolve_image_editor_pixel(context, mouse_x, mouse_y):
    image = context.space_data.image
    if image is None or image.size[0] == 0 or image.size[1] == 0:
        return None, 0, 0
    u, v = context.region.view2d.region_to_view(mouse_x, mouse_y)
    if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
        return None, 0, 0
    width, height = image.size
    px = min(int(u * width), width - 1)
    py = min(int(v * height), height - 1)
    return image, px, py


def _resolve_view3d_pixel(context, mouse_x, mouse_y):
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return None, 0, 0

    region = context.region
    rv3d = context.region_data
    coord = (mouse_x, mouse_y)
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)

    depsgraph = context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    bvh = BVHTree.FromObject(obj_eval, depsgraph)
    location, _normal, tri_index, _distance = bvh.ray_cast(origin, direction)
    if location is None:
        return None, 0, 0

    mesh = obj_eval.data
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return None, 0, 0

    tri = mesh.loop_triangles[tri_index]
    matrix_world = obj_eval.matrix_world
    verts_world = [matrix_world @ mesh.vertices[vi].co for vi in tri.vertices]
    uvs = [uv_layer.data[li].uv.to_3d() for li in tri.loops]
    uv_hit = barycentric_transform(location, *verts_world, *uvs)

    image_paint = context.tool_settings.image_paint
    if image_paint.mode == 'MATERIAL':
        material = obj.active_material
        if material is None or material.paint_active_slot >= len(material.texture_paint_images):
            return None, 0, 0
        image = material.texture_paint_images[material.paint_active_slot]
    else:
        image = image_paint.canvas

    if image is None or image.size[0] == 0 or image.size[1] == 0:
        return None, 0, 0

    width, height = image.size
    px = min(max(int(uv_hit.x * width), 0), width - 1)
    py = min(max(int(uv_hit.y * height), 0), height - 1)
    return image, px, py


def _flood_fill_mask(match, x0, y0):
    height, width = match.shape
    filled = np.zeros_like(match)
    if not match[y0, x0]:
        return filled

    stack = [(x0, y0)]
    while stack:
        x, y = stack.pop()
        if filled[y, x]:
            continue

        left = x
        while left - 1 >= 0 and match[y, left - 1] and not filled[y, left - 1]:
            left -= 1
        right = x
        while right + 1 < width and match[y, right + 1] and not filled[y, right + 1]:
            right += 1
        filled[y, left:right + 1] = True

        for nx in range(left, right + 1):
            if y - 1 >= 0 and match[y - 1, nx] and not filled[y - 1, nx]:
                stack.append((nx, y - 1))
            if y + 1 < height and match[y + 1, nx] and not filled[y + 1, nx]:
                stack.append((nx, y + 1))

    return filled


def _replace_pixels(image, target_px, target_py, new_color, contiguous, ignore_alpha):
    width, height = image.size
    channels = image.channels

    pixels = np.empty(width * height * channels, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(height, width, channels)

    compare_channels = 3 if (ignore_alpha and channels >= 4) else channels
    target = pixels[target_py, target_px, :compare_channels].copy()
    match = np.all(pixels[:, :, :compare_channels] == target, axis=-1)

    if contiguous:
        match = _flood_fill_mask(match, target_px, target_py)

    if not np.any(match):
        return False

    write_channels = min(3, channels)
    pixels[match, :write_channels] = new_color[:write_channels]

    image.pixels.foreach_set(pixels.reshape(-1))
    image.update()
    return True


class PALETTE_OT_replace_color(bpy.types.Operator):
    """Replace a clicked color in the image with the active brush color"""

    bl_idname = "palette.replace_color"
    bl_label = "Replace Color"
    bl_options = {'REGISTER', 'UNDO'}

    contiguous: BoolProperty(
        name="Contiguous",
        description="Only replace the region of matching pixels connected to the clicked point",
        default=False,
    )
    ignore_alpha: BoolProperty(
        name="Ignore Alpha",
        description="Ignore the alpha channel when matching pixels to replace",
        default=False,
    )
    location: IntVectorProperty(size=2, options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        self.location = (event.mouse_region_x, event.mouse_region_y)
        return self.execute(context)

    def execute(self, context):
        if context.area.type == 'IMAGE_EDITOR':
            image, px, py = _resolve_image_editor_pixel(context, *self.location)
        else:
            image, px, py = _resolve_view3d_pixel(context, *self.location)

        if image is None:
            self.report({'WARNING'}, "No image found to paint under the cursor")
            return {'CANCELLED'}

        image_paint = context.tool_settings.image_paint
        new_color = _brush_color_for_image(image_paint, image)
        if new_color is None:
            self.report({'WARNING'}, "No active brush")
            return {'CANCELLED'}

        changed = _replace_pixels(image, px, py, new_color, self.contiguous, self.ignore_alpha)
        if not changed:
            return {'CANCELLED'}

        for area in context.screen.areas:
            if area.type in ('IMAGE_EDITOR', 'VIEW_3D'):
                area.tag_redraw()

        return {'FINISHED'}


class _ColorReplacerKeymapMixin:
    bl_label = "Replace Color"
    bl_description = "Replace a clicked color in the image with the active brush color"
    bl_icon = "ops.paint.weight_fill"  # no dedicated icon ships with Blender; it reuses this too
    bl_cursor = 'PAINT_CROSS'
    bl_widget = None
    bl_keymap = (
        (PALETTE_OT_replace_color.bl_idname, {"type": 'LEFTMOUSE', "value": 'PRESS'}, {}),
    )

    def draw_settings(context, layout, tool):
        props = tool.operator_properties(PALETTE_OT_replace_color.bl_idname)
        layout.use_property_split = False
        layout.prop(props, "contiguous")
        layout.prop(props, "ignore_alpha")


class PALETTE_TOOL_color_replacer_view3d(_ColorReplacerKeymapMixin, bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'PAINT_TEXTURE'
    bl_idname = "palette.color_replacer_view3d"


class PALETTE_TOOL_color_replacer_image_editor(_ColorReplacerKeymapMixin, bpy.types.WorkSpaceTool):
    bl_space_type = 'IMAGE_EDITOR'
    bl_context_mode = 'PAINT'
    bl_idname = "palette.color_replacer_image_editor"


tools = (
    PALETTE_TOOL_color_sampler_view3d,
    PALETTE_TOOL_color_sampler_image_editor,
    PALETTE_TOOL_color_replacer_view3d,
    PALETTE_TOOL_color_replacer_image_editor,
)
