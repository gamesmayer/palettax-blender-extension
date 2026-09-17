import math

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

MIN_SELECTION_SIZE = 2
_OUTLINE_COLOR = (1.0, 1.0, 1.0, 0.9)
_HOLE_COLOR = (0.0, 0.0, 0.0, 0.6)


def _clamp(value, lo, hi):
    return max(lo, min(value, hi))


def _region_relative_mouse(region, event):
    # Use window-space mouse coords rather than event.mouse_region_x/y, since
    # those are relative to whichever region the mouse currently happens to
    # be over, not necessarily the region this operator started in.
    return event.mouse_x - region.x, event.mouse_y - region.y


def _resolve_pixel_unclamped(space, region, mouse_x, mouse_y):
    """Like the dropper/replace-color pixel resolver, but returns a
    (possibly out-of-bounds) pixel coordinate instead of None when the
    cursor is outside the image's [0,1] UV range, so drags can swing off
    the canvas. Callers clamp whichever result they actually apply."""
    image = space.image
    if image is None or image.size[0] == 0 or image.size[1] == 0:
        return None, 0, 0
    u, v = region.view2d.region_to_view(mouse_x, mouse_y)
    width, height = image.size
    px = math.floor(u * width)
    py = math.floor(v * height)
    return image, px, py


def _normalize_rect(anchor, current, width, height):
    ax, ay = anchor
    cx, cy = current
    x0 = _clamp(min(ax, cx), 0, width)
    x1 = _clamp(max(ax, cx) + 1, 0, width)
    y0 = _clamp(min(ay, cy), 0, height)
    y1 = _clamp(max(ay, cy) + 1, 0, height)
    return x0, y0, x1, y1


def _rect_contains(rect, px, py):
    x0, y0, x1, y1 = rect
    return x0 <= px < x1 and y0 <= py < y1


def _read_block(image, rect):
    x0, y0, x1, y1 = rect
    width, height = image.size
    channels = image.channels
    pixels = np.empty(width * height * channels, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(height, width, channels)
    return pixels[y0:y1, x0:x1, :].copy()


def _commit_move(image, source_rect, dest_rect, cached_pixels):
    width, height = image.size
    channels = image.channels

    pixels = np.empty(width * height * channels, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(height, width, channels)

    sx0, sy0, sx1, sy1 = source_rect
    dx0, dy0, dx1, dy1 = dest_rect

    # Write the destination first, then clear only the part of the source
    # rect the destination doesn't already cover, so overlapping moves
    # never erase pixels that were just written.
    pixels[dy0:dy1, dx0:dx1, :] = cached_pixels

    clear_mask = np.zeros((height, width), dtype=bool)
    clear_mask[sy0:sy1, sx0:sx1] = True
    clear_mask[dy0:dy1, dx0:dx1] = False
    # All-zero clears to transparent when the image has alpha and to black
    # otherwise, so no branching on channel count is needed.
    pixels[clear_mask] = np.zeros(channels, dtype=np.float32)

    image.pixels.foreach_set(pixels.reshape(-1))


def _revert_move(image, entry):
    width, height = image.size
    channels = image.channels
    sx0, sy0, sx1, sy1 = entry['source_rect']
    dx0, dy0, dx1, dy1 = entry['dest_rect']

    pixels = np.empty(width * height * channels, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(height, width, channels)

    # Restore whatever was at the destination first, then restore the
    # source last so it wins in any overlapping area - the exact mirror
    # of _commit_move's dest-first, source-last-to-win ordering.
    pixels[dy0:dy1, dx0:dx1, :] = entry['dest_pixels_before']
    pixels[sy0:sy1, sx0:sx1, :] = entry['source_pixels']

    image.pixels.foreach_set(pixels.reshape(-1))


def _preview_rgba(cached_pixels, channels):
    height, width = cached_pixels.shape[:2]
    rgba = np.ones((height, width, 4), dtype=np.float32)
    if channels == 1:
        rgba[:, :, 0:3] = cached_pixels[:, :, 0:1]
    elif channels == 2:
        rgba[:, :, 0:3] = cached_pixels[:, :, 0:1]
        rgba[:, :, 3] = cached_pixels[:, :, 1]
    else:
        n = min(channels, 4)
        rgba[:, :, :n] = cached_pixels[:, :, :n]
    return np.ascontiguousarray(rgba, dtype=np.float32)


def _make_preview_texture(cached_pixels, channels):
    rgba = _preview_rgba(cached_pixels, channels)
    height, width = rgba.shape[:2]
    buf = gpu.types.Buffer('FLOAT', width * height * 4, rgba.ravel())
    return gpu.types.GPUTexture((width, height), format='RGBA32F', data=buf)


def _rect_to_region_coords(space, region, rect):
    width, height = space.image.size
    x0, y0, x1, y1 = rect
    corners_uv = (
        (x0 / width, y0 / height),
        (x1 / width, y0 / height),
        (x1 / width, y1 / height),
        (x0 / width, y1 / height),
    )
    return [region.view2d.view_to_region(u, v, clip=False) for u, v in corners_uv]


def _draw_filled_rect(coords, color):
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRI_FAN', {"pos": coords})
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.blend_set('NONE')


def _draw_outline(coords):
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'LINE_LOOP', {"pos": coords})
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(1.5)
    shader.bind()
    shader.uniform_float("color", _OUTLINE_COLOR)
    batch.draw(shader)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')


_preview_shader = None


def _get_preview_shader():
    # The builtin 'IMAGE' shader samples with hardware bilinear filtering,
    # which blurs the moved block against its neighbours. texelFetch()
    # reads exact texels instead, so the preview stays pixel-perfect.
    global _preview_shader
    if _preview_shader is not None:
        return _preview_shader

    vert_out = gpu.types.GPUStageInterfaceInfo("palette_move_selection_interface")
    vert_out.smooth('VEC2', "uv_interp")

    shader_info = gpu.types.GPUShaderCreateInfo()
    shader_info.push_constant('MAT4', "ModelViewProjectionMatrix")
    shader_info.sampler(0, 'FLOAT_2D', "image")
    shader_info.vertex_in(0, 'VEC2', "pos")
    shader_info.vertex_in(1, 'VEC2', "texCoord")
    shader_info.vertex_out(vert_out)
    shader_info.fragment_out(0, 'VEC4', "fragColor")
    shader_info.vertex_source(
        "void main() {"
        "  uv_interp = texCoord;"
        "  gl_Position = ModelViewProjectionMatrix * vec4(pos, 0.0, 1.0);"
        "}"
    )
    shader_info.fragment_source(
        "void main() {"
        "  ivec2 texel = ivec2(uv_interp * vec2(textureSize(image, 0)));"
        "  fragColor = texelFetch(image, texel, 0);"
        "}"
    )
    _preview_shader = gpu.shader.create_from_info(shader_info)
    del vert_out
    del shader_info
    return _preview_shader


def _draw_preview_texture(texture, coords):
    shader = _get_preview_shader()
    tex_coords = ((0, 0), (1, 0), (1, 1), (0, 1))
    batch = batch_for_shader(shader, 'TRI_FAN', {"pos": coords, "texCoord": tex_coords})
    gpu.state.blend_set('ALPHA')
    shader.bind()
    matrix = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
    shader.uniform_float("ModelViewProjectionMatrix", matrix)
    shader.uniform_sampler("image", texture)
    batch.draw(shader)
    gpu.state.blend_set('NONE')


def _draw_callback(op):
    if bpy.context.region != op._region or op.rect is None:
        return
    if op._space.image is None:
        return

    if op.state == 'MOVING':
        source_coords = _rect_to_region_coords(op._space, op._region, op.move_source_rect)
        _draw_filled_rect(source_coords, _HOLE_COLOR)

    coords = _rect_to_region_coords(op._space, op._region, op.rect)
    if op.state == 'MOVING' and op._preview_texture is not None:
        _draw_preview_texture(op._preview_texture, coords)
    _draw_outline(coords)


class PALETTE_OT_move_selection(bpy.types.Operator):
    """Drag a rectangle over the image, then drag inside it to move that block of pixels"""

    bl_idname = "palette.move_selection"
    bl_label = "Rectangular Select and Move"
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

        self.state = 'IDLE'
        self.rect = None
        self.drag_anchor_px = None
        self.drag_anchor_rect = None
        self.move_offset = (0, 0)
        self.move_source_pixels = None
        self.move_source_rect = None
        self._preview_texture = None
        self.undo_stack = []
        self.redo_stack = []
        self._space = context.space_data
        self._region = context.region
        self._draw_handle = bpy.types.SpaceImageEditor.draw_handler_add(
            _draw_callback, (self,), 'WINDOW', 'POST_PIXEL'
        )

        context.window_manager.modal_handler_add(self)
        self._begin_new_rect(event)
        return {'RUNNING_MODAL'}

    def _begin_new_rect(self, event):
        mouse_x, mouse_y = _region_relative_mouse(self._region, event)
        image, px, py = _resolve_pixel_unclamped(self._space, self._region, mouse_x, mouse_y)
        width, height = image.size
        anchor = (_clamp(px, 0, width - 1), _clamp(py, 0, height - 1))
        self.drag_anchor_px = anchor
        self.rect = (anchor[0], anchor[1], anchor[0] + 1, anchor[1] + 1)
        self.state = 'DRAWING_RECT'

    def _exit(self, result):
        if self._draw_handle is not None:
            bpy.types.SpaceImageEditor.draw_handler_remove(self._draw_handle, 'WINDOW')
            self._draw_handle = None
        self._region.tag_redraw()
        return result

    def cancel(self, context):
        self.move_source_pixels = None
        self._preview_texture = None
        self._exit({'CANCELLED'})

    def _tag_redraw_all(self, context):
        for area in context.screen.areas:
            if area.type in ('IMAGE_EDITOR', 'VIEW_3D'):
                area.tag_redraw()

    def modal(self, context, event):
        image = self._space.image
        if image is None:
            return self._exit({'CANCELLED'})
        width, height = image.size

        # Handled ourselves rather than via bpy.ops.ed.undo_push(): raw
        # image.pixels edits made from Python don't reliably participate in
        # Blender's paint-mode undo stack, so this tool keeps its own
        # session-scoped undo/redo history for its moves instead.
        if (
            event.type == 'Z'
            and event.value == 'PRESS'
            and event.ctrl
            and self.state in ('IDLE', 'SELECTED')
        ):
            if event.shift:
                if self.redo_stack:
                    entry = self.redo_stack.pop()
                    _commit_move(image, entry['source_rect'], entry['dest_rect'], entry['source_pixels'])
                    image.update()
                    self._tag_redraw_all(context)
                    self.undo_stack.append(entry)
                    self.rect = entry['dest_rect']
                    self.state = 'SELECTED'
                    return {'RUNNING_MODAL'}
            elif self.undo_stack:
                entry = self.undo_stack.pop()
                _revert_move(image, entry)
                image.update()
                self._tag_redraw_all(context)
                self.redo_stack.append(entry)
                self.rect = entry['source_rect']
                self.state = 'SELECTED'
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}

        if self.state == 'IDLE':
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                self._begin_new_rect(event)
                self._region.tag_redraw()
                return {'RUNNING_MODAL'}
            if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
                return self._exit({'FINISHED'})
            return {'PASS_THROUGH'}

        if self.state == 'DRAWING_RECT':
            if event.type == 'MOUSEMOVE':
                mouse_x, mouse_y = _region_relative_mouse(self._region, event)
                _, px, py = _resolve_pixel_unclamped(self._space, self._region, mouse_x, mouse_y)
                self.rect = _normalize_rect(self.drag_anchor_px, (px, py), width, height)
                self._region.tag_redraw()
                return {'RUNNING_MODAL'}
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                x0, y0, x1, y1 = self.rect
                if x1 - x0 < MIN_SELECTION_SIZE or y1 - y0 < MIN_SELECTION_SIZE:
                    self.rect = None
                    self.state = 'IDLE'
                else:
                    self.state = 'SELECTED'
                self._region.tag_redraw()
                return {'RUNNING_MODAL'}
            if event.type == 'ESC' and event.value == 'PRESS':
                self.rect = None
                self.state = 'IDLE'
                self._region.tag_redraw()
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}

        if self.state == 'SELECTED':
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                mouse_x, mouse_y = _region_relative_mouse(self._region, event)
                _, px, py = _resolve_pixel_unclamped(self._space, self._region, mouse_x, mouse_y)
                if _rect_contains(self.rect, px, py):
                    self.drag_anchor_px = (px, py)
                    self.drag_anchor_rect = self.rect
                    self.move_source_rect = self.rect
                    self.move_source_pixels = _read_block(image, self.rect)
                    self._preview_texture = _make_preview_texture(
                        self.move_source_pixels, image.channels
                    )
                    self.move_offset = (0, 0)
                    self.state = 'MOVING'
                else:
                    self.rect = None
                    self._begin_new_rect(event)
                self._region.tag_redraw()
                return {'RUNNING_MODAL'}
            if event.type in {'RET', 'NUMPAD_ENTER', 'ESC'} and event.value == 'PRESS':
                self.rect = None
                return self._exit({'FINISHED'})
            if event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
                self.rect = None
                self.state = 'IDLE'
                self._region.tag_redraw()
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}

        if self.state == 'MOVING':
            if event.type == 'MOUSEMOVE':
                mouse_x, mouse_y = _region_relative_mouse(self._region, event)
                _, px, py = _resolve_pixel_unclamped(self._space, self._region, mouse_x, mouse_y)
                ax, ay = self.drag_anchor_px
                x0, y0, x1, y1 = self.drag_anchor_rect
                dx = _clamp(px - ax, -x0, width - x1)
                dy = _clamp(py - ay, -y0, height - y1)
                self.move_offset = (dx, dy)
                self.rect = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
                self._region.tag_redraw()
                return {'RUNNING_MODAL'}
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                if self.move_offset != (0, 0):
                    dest_rect = self.rect
                    dest_pixels_before = _read_block(image, dest_rect)
                    _commit_move(image, self.move_source_rect, dest_rect, self.move_source_pixels)
                    image.update()
                    self._tag_redraw_all(context)
                    self.undo_stack.append({
                        'source_rect': self.move_source_rect,
                        'dest_rect': dest_rect,
                        'source_pixels': self.move_source_pixels,
                        'dest_pixels_before': dest_pixels_before,
                    })
                    self.redo_stack = []
                else:
                    self.rect = self.drag_anchor_rect
                self.move_source_pixels = None
                self._preview_texture = None
                self.state = 'SELECTED'
                self._region.tag_redraw()
                return {'RUNNING_MODAL'}
            if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
                self.rect = self.drag_anchor_rect
                self.move_source_pixels = None
                self._preview_texture = None
                self.move_offset = (0, 0)
                self.state = 'SELECTED'
                self._region.tag_redraw()
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}

        return {'PASS_THROUGH'}


class PALETTE_TOOL_move_selection_image_editor(bpy.types.WorkSpaceTool):
    bl_space_type = 'IMAGE_EDITOR'
    bl_context_mode = 'PAINT'
    bl_idname = "palette.move_selection_image_editor"
    bl_label = "Move Selection"
    bl_description = "Select a rectangular region of pixels and drag it to a new location"
    bl_icon = "ops.generic.select_box"
    bl_cursor = 'CROSSHAIR'
    bl_widget = None
    bl_keymap = (
        (PALETTE_OT_move_selection.bl_idname, {"type": 'LEFTMOUSE', "value": 'PRESS'}, {}),
    )

    def draw_settings(context, layout, tool):
        layout.label(text="Drag inside selection to move · RMB deselect · Ctrl+Z undo · Enter to finish")
