"""Stubs out the Blender modules (`bpy`, `gpu`, `mathutils`, ...) the addon
touches at import time, so the pure-Python parsing/conversion logic in
src/utils/color.py and src/palettes/ase.py can be unit tested without a
running Blender.

Stub modules resolve any attribute lazily, so new Blender API usage in the
addon doesn't require updating this file.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _StubModule(types.ModuleType):
    """Module whose missing attributes are produced by `factory(name)`."""

    def __init__(self, name, factory):
        super().__init__(name)
        self._factory = factory

    def __getattr__(self, attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        value = self._factory(attr)
        setattr(self, attr, value)
        return value


def _mock(attr):
    return MagicMock(name=attr)


def _prop_stub(attr):
    return lambda *args, **kwargs: None


def _class_stub(attr):
    # bpy.types / mixins like ImportHelper are subclassed by the addon, so
    # they must be real classes rather than mocks.
    return type(attr, (), {})


def _install(name, factory=_mock):
    module = _StubModule(name, factory)
    sys.modules[name] = module
    parent_name, _, child = name.rpartition(".")
    if parent_name:
        setattr(sys.modules[parent_name], child, module)
    return module


def _install_blender_stubs():
    if "bpy" in sys.modules:
        return

    _install("bpy")
    _install("bpy.props", _prop_stub)
    bpy_types = _install("bpy.types", _class_stub)
    bpy_types.Panel = type("Panel", (), {"__subclasses__": classmethod(lambda cls: [])})
    _install("bpy.utils")
    _install("bpy.utils.previews")
    _install("bpy.data")

    _install("bpy_extras")
    _install("bpy_extras.io_utils", _class_stub)
    _install("bpy_extras.view3d_utils")

    _install("gpu")
    _install("gpu_extras")
    _install("gpu_extras.batch")

    _install("mathutils")
    _install("mathutils.bvhtree")
    _install("mathutils.geometry")


_install_blender_stubs()
