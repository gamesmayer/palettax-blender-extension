from bpy.props import CollectionProperty, StringProperty
from bpy.types import PropertyGroup


class PALETTE_PG_color_meta(PropertyGroup):
    name: StringProperty(name="Color Name", default="")
    group_name: StringProperty(name="Group Name", default="")


class PALETTE_PG_group_meta(PropertyGroup):
    name: StringProperty(name="Group Name", default="")


class PALETTE_PG_import_meta(PropertyGroup):
    groups: CollectionProperty(type=PALETTE_PG_group_meta)
    colors: CollectionProperty(type=PALETTE_PG_color_meta)


classes = (
    PALETTE_PG_color_meta,
    PALETTE_PG_group_meta,
    PALETTE_PG_import_meta,
)
