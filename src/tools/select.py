import bpy

from . import selection


class PALETTE_OT_select(bpy.types.Operator):
    """Drag to select a rectangular region of pixels, or drag inside an
    existing selection to reposition it (the pixel content itself only
    moves with the dedicated Move Selection tool)"""

    bl_idname = "palette.select"
    bl_label = "Select"
    bl_options = {'REGISTER'}

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

        # Single-shot per drag, like Scale: this operator does exactly one
        # gesture (draw a rect, or reposition the existing one) and exits
        # immediately on release, rather than staying modal indefinitely
        # waiting for further clicks. A long-lived "resting" instance
        # turned out to be unreliable in practice - Blender doesn't
        # guarantee it stays the single, exclusive receiver of future
        # events, which led to stray clicks being misread as drags and
        # stale/duplicate draw handlers piling up. Ctrl+Z/Ctrl+Shift+Z are
        # handled by the separate, always-available palette.selection_history
        # operator instead of being intercepted here.
        image = context.space_data.image
        self._space = context.space_data
        self._region = context.region
        self.rect = None
        self.drag_anchor_px = None
        self.relocate_anchor_px = None
        self.relocate_anchor_rect = None
        selection.is_editing = True
        self._draw_handle = bpy.types.SpaceImageEditor.draw_handler_add(
            selection.draw_selection_overlay, (self,), 'WINDOW', 'POST_PIXEL'
        )

        mouse_x, mouse_y = selection._region_relative_mouse(self._region, event)
        _, px, py = selection._resolve_pixel_unclamped(self._space, self._region, mouse_x, mouse_y)
        existing = selection.get_selection_rect(image)
        if existing is not None and selection._rect_contains(existing, px, py):
            # Only repositions the selection rect - never reads or writes a
            # single pixel, unlike the dedicated Move tool's drag.
            self.relocate_anchor_px = (px, py)
            self.relocate_anchor_rect = existing
            self.rect = existing
            self.state = 'RELOCATING'
        else:
            width, height = image.size
            anchor = (selection._clamp(px, 0, width - 1), selection._clamp(py, 0, height - 1))
            self.drag_anchor_px = anchor
            self.rect = (anchor[0], anchor[1], anchor[0] + 1, anchor[1] + 1)
            self.state = 'DRAWING_RECT'

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _exit(self, context, result):
        if self._draw_handle is not None:
            bpy.types.SpaceImageEditor.draw_handler_remove(self._draw_handle, 'WINDOW')
            self._draw_handle = None
        selection.is_editing = False
        selection._tag_redraw_all(context)
        return result

    def cancel(self, context):
        # Nothing to revert: DRAWING_RECT/RELOCATING never write the shared
        # selection until a successful release, so a forced stop mid-drag
        # simply leaves whatever was already committed untouched.
        self._exit(context, {'CANCELLED'})

    def modal(self, context, event):
        image = self._space.image
        if image is None:
            return self._exit(context, {'CANCELLED'})
        width, height = image.size

        if self.state == 'DRAWING_RECT':
            if event.type == 'MOUSEMOVE':
                mouse_x, mouse_y = selection._region_relative_mouse(self._region, event)
                _, px, py = selection._resolve_pixel_unclamped(self._space, self._region, mouse_x, mouse_y)
                self.rect = selection._normalize_rect(self.drag_anchor_px, (px, py), width, height)
                self._region.tag_redraw()
                return {'RUNNING_MODAL'}
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                x0, y0, x1, y1 = self.rect
                if x1 - x0 < selection.MIN_SELECTION_SIZE or y1 - y0 < selection.MIN_SELECTION_SIZE:
                    selection.clear_selection(image)
                else:
                    selection.set_selection_rect(image, self.rect)
                return self._exit(context, {'FINISHED'})
            if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
                return self._exit(context, {'CANCELLED'})
            return {'PASS_THROUGH'}

        if self.state == 'RELOCATING':
            if event.type == 'MOUSEMOVE':
                mouse_x, mouse_y = selection._region_relative_mouse(self._region, event)
                _, px, py = selection._resolve_pixel_unclamped(self._space, self._region, mouse_x, mouse_y)
                ax, ay = self.relocate_anchor_px
                x0, y0, x1, y1 = self.relocate_anchor_rect
                dx = selection._clamp(px - ax, -x0, width - x1)
                dy = selection._clamp(py - ay, -y0, height - y1)
                self.rect = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
                self._region.tag_redraw()
                return {'RUNNING_MODAL'}
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                selection.set_selection_rect(image, self.rect)
                return self._exit(context, {'FINISHED'})
            if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
                # Nothing was written to the shared selection during the
                # drag, so cancelling needs no revert.
                return self._exit(context, {'CANCELLED'})
            return {'PASS_THROUGH'}

        return {'PASS_THROUGH'}


class PALETTE_TOOL_select_image_editor(bpy.types.WorkSpaceTool):
    bl_space_type = 'IMAGE_EDITOR'
    bl_context_mode = 'PAINT'
    bl_idname = "palette.select_image_editor"
    bl_label = "Select"
    bl_description = (
        "Drag to select a rectangular region of pixels, or drag inside an "
        "existing selection to reposition it (use Move Selection to move "
        "the pixel content)"
    )
    bl_icon = "ops.generic.select_box"
    bl_cursor = 'CROSSHAIR'
    bl_widget = None
    bl_keymap = (
        (PALETTE_OT_select.bl_idname, {"type": 'LEFTMOUSE', "value": 'PRESS'}, {}),
        ("palette.selection_history", {"type": 'Z', "value": 'PRESS', "ctrl": True},
         {"properties": [("redo", False)]}),
        ("palette.selection_history", {"type": 'Z', "value": 'PRESS', "ctrl": True, "shift": True},
         {"properties": [("redo", True)]}),
    )

    def draw_settings(context, layout, tool):
        layout.label(text="Drag to select/reposition · RMB cancel drag · Ctrl+Z undo")
