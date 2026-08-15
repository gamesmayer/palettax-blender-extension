# Import Palettes

A Blender extension that adds palette-import entries to **File > Import**.
Currently supports JASC/Gale Palette (`.pal`) files; more formats may be
added later. Imported colors become a new color palette (`bpy.data.palettes`),
ready to use in Texture Paint / Vertex Paint / Grease Pencil, matching the
original file's sRGB values.

Requires Blender 5.2.0 or newer.

## Install

Build the extension zip, then in Blender go to
**Edit > Preferences > Get Extensions > (dropdown) Install from Disk** and
select the generated zip.

```sh
./build.sh
```

This produces `import_palettes-1.0.0.zip` in the project root, which can
also be installed by dragging it into a running Blender window.

If `blender` isn't on your `PATH`, point the script at it explicitly:

```sh
BLENDER="/c/Program Files/Blender Foundation/Blender 5.2/blender.exe" ./build.sh
```

## Use

**File > Import > JASC/Gale Palette (.pal)**, pick a `.pal` file. The colors
show up as a new entry in the Color Palette dropdown wherever Blender's
palette picker is used (e.g. the Texture Paint tool settings).

## Project layout

```
src/
  blender_manifest.toml   extension manifest
  __init__.py             import operator + menu registration
build.sh                  validates and packages the extension into a zip
```
