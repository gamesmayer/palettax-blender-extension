# Palettax Blender Extension

<div align="center">
  <img src="./src/icon.png" alt="" width="128" />
  <p style="font-size: 18px;">Upgrade color palette management in Blender.</p>
</div>

## Features

- Import JASC/Gale Palette (`.pal`) and Adobe Swatch Exchange (`.ase`) files from **File > Import**.
- Imported colors become a new color palette (`bpy.data.palettes`), ready to
  use in Texture Paint / Vertex Paint / Grease Pencil, matching the
  original file's sRGB values
- For `.ase` files, color group names and individual color names are
  preserved (Blender's native palettes have no name/group fields, so
  Palettax stores this metadata separately)
- An extended, readonly swatch view in Texture Paint mode groups swatches
  the way they were grouped in the source file, shows color names as
  tooltips, and sets the active brush color on a single click

## Requirements

Requires Blender 5.2.0 or newer.

## Install

Build the extension zip, then in Blender go to
**Edit > Preferences > Get Extensions > (dropdown) Install from Disk** and
select the generated zip.

```sh
./build.sh
```

This produces `palettax-1.1.0.zip` in the project root, which can
also be installed by dragging it into a running Blender window.

If `blender` isn't on your `PATH`, point the script at it explicitly:

```sh
BLENDER="/c/Program Files/Blender Foundation/Blender 5.2/blender.exe" ./build.sh
```

## Use

**File > Import > Adobe Swatch Exchange (.ase)**, pick a file. The colors show up as a new entry in
the Color Palette dropdown wherever Blender's palette picker is used (e.g.
the Texture Paint tool settings).

While in Texture Paint mode, open the 3D Viewport's sidebar (press `N`) and
select the **Palettax** tab to see the "Palette Swatches" panel: an
extended, readonly view of the active palette that groups swatches the way
they were grouped in the source `.ase` file, shows each color's name as a
tooltip, and sets the active brush color when you click a swatch.

## Project layout

```
src/
  blender_manifest.toml   extension manifest
  icon.png                extension icon
  __init__.py             registration: classes tuple, import menu
  color.py                sRGB/linear conversion helpers
  properties.py           PropertyGroups storing preserved names/groups
  pal.py                  JASC/Gale (.pal) parser + import operator
  ase.py                  Adobe Swatch Exchange (.ase) parser + import operator
  panels.py               Texture Paint "Palette Swatches" widget
build.sh                  validates and packages the extension into a zip
```
