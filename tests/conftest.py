"""Stubs out the `bpy` API surface the addon touches at import time, so the
pure-Python parsing/conversion logic in src/color.py and src/ase.py can be
unit tested without a running Blender.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _install_bpy_stubs():
    if "bpy" in sys.modules:
        return

    def _prop_stub(**kwargs):
        return None

    bpy = types.ModuleType("bpy")
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.StringProperty = _prop_stub
    bpy.props.PointerProperty = _prop_stub
    bpy.props.CollectionProperty = _prop_stub
    bpy.props.FloatVectorProperty = _prop_stub

    bpy.types = types.ModuleType("bpy.types")
    bpy.types.Operator = type("Operator", (), {})
    bpy.types.Panel = type("Panel", (), {})
    bpy.types.PropertyGroup = type("PropertyGroup", (), {})

    bpy.utils = types.ModuleType("bpy.utils")
    bpy.utils.register_class = lambda cls: None
    bpy.utils.unregister_class = lambda cls: None
    bpy.utils.previews = types.ModuleType("bpy.utils.previews")
    bpy.utils.previews.new = lambda: None
    bpy.utils.previews.remove = lambda collection: None

    bpy.data = types.ModuleType("bpy.data")

    bpy_extras = types.ModuleType("bpy_extras")
    bpy_extras.io_utils = types.ModuleType("bpy_extras.io_utils")
    bpy_extras.io_utils.ImportHelper = type("ImportHelper", (), {})

    sys.modules["bpy"] = bpy
    sys.modules["bpy.props"] = bpy.props
    sys.modules["bpy.types"] = bpy.types
    sys.modules["bpy.utils"] = bpy.utils
    sys.modules["bpy.utils.previews"] = bpy.utils.previews
    sys.modules["bpy.data"] = bpy.data
    sys.modules["bpy_extras"] = bpy_extras
    sys.modules["bpy_extras.io_utils"] = bpy_extras.io_utils


_install_bpy_stubs()
