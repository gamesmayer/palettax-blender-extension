import bpy
from bpy.props import EnumProperty

from . import selection


def _rect_from_anchor_and_size(anchor_x, anchor_y, dir_x, dir_y, w, h, width, height):
    if dir_x > 0:
        x0, x1 = anchor_x, anchor_x + w
    else:
        x0, x1 = anchor_x - w, anchor_x
    if dir_y > 0:
        y0, y1 = anchor_y, anchor_y + h
    else:
        y0, y1 = anchor_y - h, anchor_y

    x0 = selection._clamp(x0, 0, width)
    x1 = selection._clamp(x1, 0, width)
    y0 = selection._clamp(y0, 0, height)
    y1 = selection._clamp(y1, 0, height)
    if x1 - x0 < 1:
        x1 = min(x0 + 1, width)
        x0 = max(x1 - 1, 0)
    if y1 - y0 < 1:
        y1 = min(y0 + 1, height)
        y0 = max(y1 - 1, 0)
    return (int(x0), int(y0), int(x1), int(y1))


_ANCHOR_FOR_CORNER = {
    # dragged corner -> the OPPOSITE, fixed anchor corner
    'BL': lambda rect: (rect[2], rect[3]),
    'BR': lambda rect: (rect[0], rect[3]),
    'TR': lambda rect: (rect[0], rect[1]),
    'TL': lambda rect: (rect[2], rect[1]),
}


def _draw_callback(op):
    try:
        _draw_scale_overlay(op)
    except ReferenceError:
        # See selection.draw_selection_overlay for why this can happen and
        # why silently no-op'ing (and clearing is_editing) is the right
        # response here.
        selection.is_editing = False


def _draw_scale_overlay(op):
    if bpy.context.region != op._region or op.rect is None:
        return
    if op._space.image is None:
        return

    hole_coords = selection._rect_to_region_coords(op._space, op._region, op.original_rect)
    selection._draw_filled_rect(hole_coords, selection._HOLE_COLOR)

    coords = selection._rect_to_region_coords(op._space, op._region, op.rect)
    if op._preview_texture is not None:
        if op.interpolation == 'LINEAR':
            selection._draw_preview_texture_bilinear(op._preview_texture, coords)
        else:
            selection._draw_preview_texture(op._preview_texture, coords)
    selection._draw_outline(coords)


class PALETTE_OT_scale_selection(bpy.types.Operator):
    """Drag a corner of the current selection to resize its pixel content"""

    bl_idname = "palette.scale_selection"
    bl_label = "Scale Selection"
    bl_options = {'REGISTER'}

    interpolation: EnumProperty(
        name="Interpolation",
        items=(
            ('CLOSEST', "Closest", "Nearest-neighbor resampling"),
            ('LINEAR', "Linear", "Bilinear resampling"),
        ),
        default='CLOSEST',
    )

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None
            and context.area.type == 'IMAGE_EDITOR'
            and context.space_data.image is not None
        )

    def invoke(self, context, event):
        if not self.poll(context):
            self.report({'WARNING'}, "No image to select from")
            return {'CANCELLED'}

        image = context.space_data.image
        rect = selection.get_selection_rect(image)
        if rect is None:
            self.report({'WARNING'}, "No active selection to scale")
            return {'CANCELLED'}

        self._space = context.space_data
        self._region = context.region

        mouse_x, mouse_y = selection._region_relative_mouse(self._region, event)
        corner = selection.hit_test_corner(self._space, self._region, rect, mouse_x, mouse_y)
        if corner is None:
            self.report({'WARNING'}, "Click a corner handle of the selection to scale it")
            return {'CANCELLED'}

        self.original_rect = rect
        self.rect = rect
        self.anchor_point = _ANCHOR_FOR_CORNER[corner](rect)
        self._original_block = selection._read_block(image, rect)
        self._preview_texture = selection._make_preview_texture(self._original_block, image.channels)
        self.undo_stack = selection.get_undo_stack(image)
        self.redo_stack = selection.get_redo_stack(image)
        self.state = 'SCALING'

        selection.is_editing = True
        self._draw_handle = bpy.types.SpaceImageEditor.draw_handler_add(
            _draw_callback, (self,), 'WINDOW', 'POST_PIXEL'
        )
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _aspect_locked_size(self, ax, ay, px, py):
        ox0, oy0, ox1, oy1 = self.original_rect
        orig_w = max(ox1 - ox0, 1)
        orig_h = max(oy1 - oy0, 1)
        ratio = orig_w / orig_h

        raw_w = max(abs(px - ax), 1)
        raw_h = max(abs(py - ay), 1)

        # Whichever axis moved further, proportionally to the original
        # selection's own extent on that axis, drives the result - tracks
        # the user's actual gesture better than a fixed "width always
        # drives" rule for selections that are far from square.
        if (raw_w / orig_w) >= (raw_h / orig_h):
            new_w = raw_w
            new_h = max(round(new_w / ratio), 1)
        else:
            new_h = raw_h
            new_w = max(round(new_h * ratio), 1)
        return int(new_w), int(new_h)

    def _exit(self, result):
        if self._draw_handle is not None:
            bpy.types.SpaceImageEditor.draw_handler_remove(self._draw_handle, 'WINDOW')
            self._draw_handle = None
        self._preview_texture = None
        selection.is_editing = False
        self._region.tag_redraw()
        return result

    def cancel(self, context):
        # No pixel write ever happens mid-drag, so cancelling is a no-op.
        self._exit({'CANCELLED'})

    def modal(self, context, event):
        image = self._space.image
        if image is None:
            return self._exit({'CANCELLED'})
        width, height = image.size

        if event.type == 'MOUSEMOVE':
            mouse_x, mouse_y = selection._region_relative_mouse(self._region, event)
            _, px, py = selection._resolve_pixel_unclamped(self._space, self._region, mouse_x, mouse_y)
            ax, ay = self.anchor_point
            px = selection._clamp(px, 0, width)
            py = selection._clamp(py, 0, height)

            if event.shift:
                new_w, new_h = self._aspect_locked_size(ax, ay, px, py)
            else:
                new_w = max(abs(px - ax), 1)
                new_h = max(abs(py - ay), 1)

            dir_x = 1 if px >= ax else -1
            dir_y = 1 if py >= ay else -1
            self.rect = _rect_from_anchor_and_size(ax, ay, dir_x, dir_y, new_w, new_h, width, height)
            self._region.tag_redraw()
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            dest_rect = self.rect
            if dest_rect != self.original_rect:
                dest_h = dest_rect[3] - dest_rect[1]
                dest_w = dest_rect[2] - dest_rect[0]
                resized_block = selection.resize_block(self._original_block, dest_h, dest_w, self.interpolation)
                dest_pixels_before = selection._read_block(image, dest_rect)
                selection._commit_move(image, self.original_rect, dest_rect, resized_block)
                image.update()
                selection._tag_redraw_all(context)
                self.undo_stack.append({
                    'source_rect': self.original_rect,
                    'dest_rect': dest_rect,
                    'source_pixels': self._original_block,
                    'dest_pixels_before': dest_pixels_before,
                })
                self.redo_stack.clear()
                selection.set_selection_rect(image, dest_rect)
            else:
                selection.set_selection_rect(image, self.original_rect)
            return self._exit({'FINISHED'})

        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._exit({'CANCELLED'})

        return {'PASS_THROUGH'}


class PALETTE_TOOL_scale_selection_image_editor(bpy.types.WorkSpaceTool):
    bl_space_type = 'IMAGE_EDITOR'
    bl_context_mode = 'PAINT'
    bl_idname = "palette.scale_selection_image_editor"
    bl_label = "Scale Selection"
    bl_description = "Drag a corner of the current selection to resize its pixel content"
    bl_icon = "ops.transform.resize"
    bl_cursor = 'CROSSHAIR'
    bl_widget = None
    bl_keymap = (
        (PALETTE_OT_scale_selection.bl_idname, {"type": 'LEFTMOUSE', "value": 'PRESS'}, {}),
        ("palette.selection_history", {"type": 'Z', "value": 'PRESS', "ctrl": True},
         {"properties": [("redo", False)]}),
        ("palette.selection_history", {"type": 'Z', "value": 'PRESS', "ctrl": True, "shift": True},
         {"properties": [("redo", True)]}),
    )

    def draw_settings(context, layout, tool):
        props = tool.operator_properties(PALETTE_OT_scale_selection.bl_idname)
        layout.use_property_split = False
        layout.prop(props, "interpolation", text="")
