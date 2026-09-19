import bpy

from . import selection


class PALETTE_OT_copy_selection(bpy.types.Operator):
    """Drag the current selection to a new location, copying its pixels without clearing the source"""

    bl_idname = "palette.copy_selection"
    bl_label = "Copy Selection"
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

        image = context.space_data.image
        rect = selection.get_selection_rect(image)
        if rect is None:
            self.report({'WARNING'}, "No active selection to copy")
            return {'CANCELLED'}

        self._space = context.space_data
        self._region = context.region

        mouse_x, mouse_y = selection._region_relative_mouse(self._region, event)
        _, px, py = selection._resolve_pixel_unclamped(self._space, self._region, mouse_x, mouse_y)
        if not selection._rect_contains(rect, px, py):
            self.report({'WARNING'}, "Click inside the selection to copy it")
            return {'CANCELLED'}

        # Single-shot per drag - see select.py for why this operator no
        # longer stays modal/resting between actions.
        self.state = 'COPYING'
        self.undo_stack = selection.get_undo_stack(image)
        self.redo_stack = selection.get_redo_stack(image)
        # begin_move/update_move/cancel_move are reused as-is: they only
        # track drag state (anchor, offset, rect, preview texture) and never
        # touch image pixels, so their move_* attribute names apply here too.
        selection.begin_move(self, image, rect, px, py)
        selection.is_editing = True
        self._draw_handle = bpy.types.SpaceImageEditor.draw_handler_add(
            selection.draw_selection_overlay, (self,), 'WINDOW', 'POST_PIXEL'
        )
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
        selection.cancel_move(self, self._space.image)
        self._exit(context, {'CANCELLED'})

    def modal(self, context, event):
        image = self._space.image
        if image is None:
            return self._exit(context, {'CANCELLED'})

        if event.type == 'MOUSEMOVE':
            mouse_x, mouse_y = selection._region_relative_mouse(self._region, event)
            _, px, py = selection._resolve_pixel_unclamped(self._space, self._region, mouse_x, mouse_y)
            selection.update_move(self, image, px, py)
            self._region.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            selection.commit_copy(self, context, image)
            return self._exit(context, {'FINISHED'})
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            selection.cancel_move(self, image)
            return self._exit(context, {'CANCELLED'})
        return {'PASS_THROUGH'}


class PALETTE_TOOL_copy_selection_image_editor(bpy.types.WorkSpaceTool):
    bl_space_type = 'IMAGE_EDITOR'
    bl_context_mode = 'PAINT'
    bl_idname = "palette.copy_selection_image_editor"
    bl_label = "Copy Selection"
    bl_description = "Drag the current selection to a new location, copying its pixels"
    bl_icon = "ops.mesh.dupli_extrude_cursor"  # no dedicated icon ships with Blender; it reuses this too
    bl_cursor = 'SCROLL_XY'
    bl_widget = None
    bl_keymap = (
        (PALETTE_OT_copy_selection.bl_idname, {"type": 'LEFTMOUSE', "value": 'PRESS'}, {}),
        ("palette.selection_history", {"type": 'Z', "value": 'PRESS', "ctrl": True},
         {"properties": [("redo", False)]}),
        ("palette.selection_history", {"type": 'Z', "value": 'PRESS', "ctrl": True, "shift": True},
         {"properties": [("redo", True)]}),
    )

    def draw_settings(context, layout, tool):
        layout.label(text="Drag inside selection to copy · Ctrl+Z undo")
