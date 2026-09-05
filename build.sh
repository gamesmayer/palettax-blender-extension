#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$ROOT_DIR/src"
RELEASES_DIR="$ROOT_DIR/releases"

mkdir -p "$RELEASES_DIR"

if [ -n "${BLENDER:-}" ]; then
    BLENDER_BIN="$BLENDER"
elif command -v blender >/dev/null 2>&1; then
    BLENDER_BIN="blender"
else
    BLENDER_BIN="/c/Program Files/Blender Foundation/Blender 5.2/blender.exe"
fi

if [ ! -x "$BLENDER_BIN" ] && ! command -v "$BLENDER_BIN" >/dev/null 2>&1; then
    echo "error: could not find Blender executable at '$BLENDER_BIN'" >&2
    echo "set the BLENDER environment variable to your blender executable path" >&2
    exit 1
fi

"$BLENDER_BIN" --command extension validate "$SOURCE_DIR"
"$BLENDER_BIN" --command extension build --source-dir "$SOURCE_DIR" --output-dir "$RELEASES_DIR"
