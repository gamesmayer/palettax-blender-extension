import math

import bpy
import gpu
import numpy as np
from bpy.props import BoolProperty, IntVectorProperty
from bpy.types import PropertyGroup
from gpu_extras.batch import batch_for_shader

MIN_SELECTION_SIZE = 2
_OUTLINE_COLOR = (1.0, 1.0, 1.0, 0.9)
_HOLE_COLOR = (0.0, 0.0, 0.0, 0.6)

# True while any Select/Move/Scale modal session is actively running, so the
# persistent overlay (drawn regardless of which tool/operator is active)
# steps aside and lets that session's own draw handler show the live state.
is_editing = False


class PALETTE_PG_selection(PropertyGroup):
    active: BoolProperty(default=False)
    # (x0, y0, x1, y1) in image-pixel space, x1/y1 exclusive.
    rect: IntVectorProperty(size=4, default=(0, 0, 0, 0))


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
    """Write cached_pixels (shape must match dest_rect) into dest_rect, then
    clear the part of source_rect not covered by dest_rect. Makes no
    assumption that source_rect and dest_rect are the same size, so this
    also serves Scale's "cut original, paste resized block" commit."""
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


def _commit_copy(image, dest_rect, cached_pixels):
    """Write-only half of _commit_move: writes cached_pixels into dest_rect
    and never clears anything. Used by Copy's commit and by history redo
    for copy entries, so the source stays intact on the initial drag and on
    every subsequent redo."""
    width, height = image.size
    channels = image.channels

    pixels = np.empty(width * height * channels, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(height, width, channels)

    dx0, dy0, dx1, dy1 = dest_rect
    pixels[dy0:dy1, dx0:dx1, :] = cached_pixels

    image.pixels.foreach_set(pixels.reshape(-1))


def _revert_copy(image, entry):
    """Undo half for a copy entry. source_rect was never modified by a copy
    commit, so - unlike _revert_move - only dest_rect needs restoring."""
    width, height = image.size
    channels = image.channels

    pixels = np.empty(width * height * channels, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(height, width, channels)

    dx0, dy0, dx1, dy1 = entry['dest_rect']
    pixels[dy0:dy1, dx0:dx1, :] = entry['dest_pixels_before']

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

    vert_out = gpu.types.GPUStageInterfaceInfo("palette_selection_interface")
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


def _draw_preview_texture_bilinear(texture, coords):
    # Builtin 'IMAGE' shader - hardware bilinear filtering. Used only as a
    # cheap live-preview approximation for Scale's 'LINEAR' interpolation
    # setting; the committed result always comes from the exact numpy
    # resize_bilinear(), not from this shader's filtering precision.
    shader = gpu.shader.from_builtin('IMAGE')
    tex_coords = ((0, 0), (1, 0), (1, 1), (0, 1))
    batch = batch_for_shader(shader, 'TRI_FAN', {"pos": coords, "texCoord": tex_coords})
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_sampler("image", texture)
    batch.draw(shader)
    gpu.state.blend_set('NONE')


def draw_selection_overlay(op):
    """Shared POST_PIXEL draw-callback body for Select's, Move's and Copy's
    modal sessions: hole + floating preview + outline while state == 'MOVING',
    floating preview + outline (no hole) while state == 'COPYING', otherwise
    just the outline over op.rect."""
    global is_editing
    try:
        _draw_selection_overlay(op)
    except ReferenceError:
        # Blender can invalidate a modal operator's underlying RNA struct
        # (e.g. on certain tool switches) without ever calling cancel(),
        # leaving this draw handler registered with a now-dead `op`. Every
        # attribute access on such an operator raises ReferenceError, so
        # there's nothing left to clean up here - just stop drawing rather
        # than crashing the whole redraw (and with it the rest of the UI).
        # Also clear is_editing, since whatever session set it True is gone
        # and will never reach its own cleanup to clear it - left stuck
        # True, it would permanently hide the persistent selection outline.
        is_editing = False


def _draw_selection_overlay(op):
    if bpy.context.region != op._region or op.rect is None:
        return
    if op._space.image is None:
        return

    if op.state == 'MOVING':
        source_coords = _rect_to_region_coords(op._space, op._region, op.move_source_rect)
        _draw_filled_rect(source_coords, _HOLE_COLOR)

    coords = _rect_to_region_coords(op._space, op._region, op.rect)
    if op.state in ('MOVING', 'COPYING') and op._preview_texture is not None:
        _draw_preview_texture(op._preview_texture, coords)
    _draw_outline(coords)


def _tag_redraw_all(context):
    for area in context.screen.areas:
        if area.type in ('IMAGE_EDITOR', 'VIEW_3D'):
            area.tag_redraw()


# --- Shared selection-rect accessors (bpy.types.Image.palettax_selection) ---

def get_selection_rect(image):
    """Returns (x0,y0,x1,y1) or None if no active selection."""
    sel = image.palettax_selection
    if not sel.active:
        return None
    return tuple(sel.rect)


def set_selection_rect(image, rect):
    sel = image.palettax_selection
    sel.rect = rect
    sel.active = True


def clear_selection(image):
    image.palettax_selection.active = False


# --- Shared, per-image undo/redo history for pixel commits ---
#
# Kept out of Blender's native undo entirely (see the plan's rationale):
# raw image.pixels edits don't reliably round-trip through Blender's
# paint-mode undo, and mixing native undo (for selection-only changes) with
# this custom stack (for pixel changes) over the same shared rect would let
# the two desync. Keyed per-image (not per-operator) so history survives
# switching between Select/Move/Scale - cache-only, doesn't survive a file
# reload, same caveat as before, just shared across tools instead of scoped
# to one long-lived operator session.

_undo_stacks = {}
_redo_stacks = {}


def get_undo_stack(image):
    return _undo_stacks.setdefault(image.name, [])


def get_redo_stack(image):
    return _redo_stacks.setdefault(image.name, [])


class PALETTE_OT_selection_history(bpy.types.Operator):
    """Undo/redo the last selection move or scale"""

    bl_idname = "palette.selection_history"
    bl_label = "Undo/Redo Selection Edit"
    bl_options = {'REGISTER'}

    redo: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None
            and context.area.type == 'IMAGE_EDITOR'
            and context.space_data.image is not None
        )

    def execute(self, context):
        # A plain, single-shot operator (never modal, never rests) bound
        # directly in each tool's bl_keymap, so Ctrl+Z/Ctrl+Shift+Z work
        # regardless of which of Select/Move/Scale is the active tool, and
        # regardless of whether any of them currently has a modal session
        # running. Earlier designs tried to intercept these keys from
        # inside a long-lived, resting modal operator instead - that
        # turned out to be the root cause of several bugs (stray clicks
        # misread as drags, stale/duplicate draw handlers), since Blender
        # doesn't reliably keep a single such instance alive/exclusive.
        image = context.space_data.image
        stack = get_redo_stack(image) if self.redo else get_undo_stack(image)
        if not stack:
            return {'PASS_THROUGH'}

        entry = stack.pop()
        other_stack = get_undo_stack(image) if self.redo else get_redo_stack(image)
        kind = entry.get('kind', 'move')
        if self.redo:
            if kind == 'copy':
                _commit_copy(image, entry['dest_rect'], entry['source_pixels'])
            else:
                _commit_move(image, entry['source_rect'], entry['dest_rect'], entry['source_pixels'])
            new_rect = entry['dest_rect']
        else:
            if kind == 'copy':
                _revert_copy(image, entry)
            else:
                _revert_move(image, entry)
            new_rect = entry['source_rect']
        image.update()
        other_stack.append(entry)
        set_selection_rect(image, new_rect)
        _tag_redraw_all(context)
        return {'FINISHED'}


# --- Shared "drag to move a block of pixels" mechanics ---
# Used by both the Select tool (dragging from inside an existing selection)
# and the dedicated Move tool. Expects `op` to carry: rect, drag_anchor_px,
# drag_anchor_rect, move_source_rect, move_source_pixels, move_offset,
# _preview_texture, undo_stack, redo_stack.

def begin_move(op, image, rect, px, py):
    op.drag_anchor_px = (px, py)
    op.drag_anchor_rect = rect
    op.move_source_rect = rect
    op.move_source_pixels = _read_block(image, rect)
    op._preview_texture = _make_preview_texture(op.move_source_pixels, image.channels)
    op.move_offset = (0, 0)
    op.rect = rect


def update_move(op, image, px, py):
    width, height = image.size
    ax, ay = op.drag_anchor_px
    x0, y0, x1, y1 = op.drag_anchor_rect
    dx = _clamp(px - ax, -x0, width - x1)
    dy = _clamp(py - ay, -y0, height - y1)
    op.move_offset = (dx, dy)
    op.rect = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)


def commit_move(op, context, image):
    if op.move_offset != (0, 0):
        dest_rect = op.rect
        dest_pixels_before = _read_block(image, dest_rect)
        _commit_move(image, op.move_source_rect, dest_rect, op.move_source_pixels)
        image.update()
        _tag_redraw_all(context)
        op.undo_stack.append({
            'source_rect': op.move_source_rect,
            'dest_rect': dest_rect,
            'source_pixels': op.move_source_pixels,
            'dest_pixels_before': dest_pixels_before,
        })
        # Mutate in place: op.redo_stack is the SAME list object stored in
        # _redo_stacks, so rebinding it (`op.redo_stack = []`) would only
        # change this operator's local reference, leaving the shared entry
        # (and every other tool) still pointing at the stale list.
        op.redo_stack.clear()
        set_selection_rect(image, dest_rect)
    else:
        op.rect = op.drag_anchor_rect
        set_selection_rect(image, op.rect)
    op.move_source_pixels = None
    op._preview_texture = None


def commit_copy(op, context, image):
    """Like commit_move, but writes the source block to the destination
    without ever clearing the source - the only difference between Copy and
    Move's commit step."""
    if op.move_offset != (0, 0):
        dest_rect = op.rect
        dest_pixels_before = _read_block(image, dest_rect)
        _commit_copy(image, dest_rect, op.move_source_pixels)
        image.update()
        _tag_redraw_all(context)
        op.undo_stack.append({
            'kind': 'copy',
            'source_rect': op.move_source_rect,
            'dest_rect': dest_rect,
            'source_pixels': op.move_source_pixels,
            'dest_pixels_before': dest_pixels_before,
        })
        op.redo_stack.clear()
        set_selection_rect(image, dest_rect)
    else:
        op.rect = op.drag_anchor_rect
        set_selection_rect(image, op.rect)
    op.move_source_pixels = None
    op._preview_texture = None


def cancel_move(op, image):
    op.rect = op.drag_anchor_rect
    op.move_source_pixels = None
    op._preview_texture = None
    op.move_offset = (0, 0)
    set_selection_rect(image, op.rect)


# --- Resize (numpy only, channel-count-agnostic) ---

def resize_nearest(block, new_height, new_width):
    old_height, old_width, channels = block.shape
    if new_height <= 0 or new_width <= 0:
        return np.empty((max(new_height, 0), max(new_width, 0), channels), dtype=np.float32)
    row_idx = np.floor((np.arange(new_height) + 0.5) * old_height / new_height).astype(np.int64)
    col_idx = np.floor((np.arange(new_width) + 0.5) * old_width / new_width).astype(np.int64)
    row_idx = np.clip(row_idx, 0, old_height - 1)
    col_idx = np.clip(col_idx, 0, old_width - 1)
    return block[row_idx[:, None], col_idx[None, :], :].astype(np.float32)


def resize_bilinear(block, new_height, new_width):
    old_height, old_width, channels = block.shape
    if new_height <= 0 or new_width <= 0:
        return np.empty((max(new_height, 0), max(new_width, 0), channels), dtype=np.float32)
    if old_height == 1 and old_width == 1:
        return np.repeat(np.repeat(block, new_height, axis=0), new_width, axis=1).astype(np.float32)

    scale_y = old_height / new_height
    scale_x = old_width / new_width
    src_y = np.clip((np.arange(new_height) + 0.5) * scale_y - 0.5, 0, old_height - 1)
    src_x = np.clip((np.arange(new_width) + 0.5) * scale_x - 0.5, 0, old_width - 1)

    y0 = np.floor(src_y).astype(np.int64)
    x0 = np.floor(src_x).astype(np.int64)
    y1 = np.clip(y0 + 1, 0, old_height - 1)
    x1 = np.clip(x0 + 1, 0, old_width - 1)

    wy = (src_y - y0).reshape(-1, 1, 1)
    wx = (src_x - x0).reshape(1, -1, 1)

    top_left = block[y0[:, None], x0[None, :], :]
    top_right = block[y0[:, None], x1[None, :], :]
    bottom_left = block[y1[:, None], x0[None, :], :]
    bottom_right = block[y1[:, None], x1[None, :], :]

    top = top_left * (1 - wx) + top_right * wx
    bottom = bottom_left * (1 - wx) + bottom_right * wx
    return (top * (1 - wy) + bottom * wy).astype(np.float32)


def resize_block(block, new_height, new_width, method):
    if method == 'LINEAR':
        return resize_bilinear(block, new_height, new_width)
    return resize_nearest(block, new_height, new_width)


# --- Corner hit-testing (Scale) ---

_CORNER_NAMES = ('BL', 'BR', 'TR', 'TL')  # matches _rect_to_region_coords's corner order


def hit_test_corner(space, region, rect, mouse_x, mouse_y, tolerance_px=10):
    coords = _rect_to_region_coords(space, region, rect)
    best_name, best_dist_sq = None, tolerance_px * tolerance_px
    for name, (cx, cy) in zip(_CORNER_NAMES, coords):
        dist_sq = (mouse_x - cx) ** 2 + (mouse_y - cy) ** 2
        if dist_sq <= best_dist_sq:
            best_dist_sq = dist_sq
            best_name = name
    return best_name


# --- Persistent selection-outline overlay ---
# Registered once at add-on registration time (not per-operator-invocation)
# so the outline stays visible whenever a selection exists, regardless of
# which tool is currently active or whether any modal session is running.

def _draw_persistent_selection():
    context = bpy.context
    if is_editing or context.area is None or context.area.type != 'IMAGE_EDITOR':
        return
    image = context.space_data.image
    if image is None:
        return
    rect = get_selection_rect(image)
    if rect is None:
        return
    _draw_outline(_rect_to_region_coords(context.space_data, context.region, rect))


def register_persistent_overlay():
    return bpy.types.SpaceImageEditor.draw_handler_add(_draw_persistent_selection, (), 'WINDOW', 'POST_PIXEL')


def unregister_persistent_overlay(handle):
    bpy.types.SpaceImageEditor.draw_handler_remove(handle, 'WINDOW')
