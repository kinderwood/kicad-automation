#!/bin/sh
set -eu

appdir=${KICAD_APPDIR:-/workspace/shared/kicad-10.0.8/AppDir}
plugin_dir="$appdir/share/kicad/scripting/plugins"

KICAD_WIZARD_DIR="$plugin_dir" \
    "$appdir/AppRun" python3.11 -m unittest discover -s tests -v
