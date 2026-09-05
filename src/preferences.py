import bpy
from bpy.props import BoolProperty
from bpy.types import AddonPreferences

_original_polls = {}  # cls.__name__ -> original poll (or None)


def _builtin_color_palette_panels():
    return [cls for cls in bpy.types.Panel.__subclasses__() if getattr(cls, "bl_label", "") == "Color Palette"]


def apply_hide_builtin_color_palette(hide):
    for cls in _builtin_color_palette_panels():
        key = cls.__name__
        if hide:
            if key in _original_polls:
                continue
            _original_polls[key] = cls.__dict__.get("poll")
            cls.poll = classmethod(lambda c, context: False)
        else:
            if key not in _original_polls:
                continue
            original = _original_polls.pop(key)
            if original is not None:
                cls.poll = original
            elif "poll" in cls.__dict__:
                del cls.poll


def _update_hide_builtin(self, context):
    apply_hide_builtin_color_palette(self.hide_builtin_color_palette)


class PALETTAX_AddonPreferences(AddonPreferences):
    bl_idname = __package__

    hide_builtin_color_palette: BoolProperty(
        name="Hide built-in Color Palette panel",
        description=(
            "Hide Blender's default Color Palette panel (Tool tab) so the "
            "active palette is only managed from the Palettax tab"
        ),
        default=True,
        update=_update_hide_builtin,
    )

    def draw(self, context):
        self.layout.prop(self, "hide_builtin_color_palette")
