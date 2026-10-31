#!/bin/sh
set -eu

appdir=${KICAD_APPDIR:-/workspace/shared/kicad-10.0.8/AppDir}
plugin_dir="$appdir/share/kicad/scripting/plugins"

KICAD_WIZARD_DIR="$plugin_dir" \
    "$appdir/AppRun" python3.11 -m unittest discover -s tests -v

test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

python3 scripts/build_pcm.py \
    --base-url https://example.test/kicad \
    --output-dir "$test_root/dist"

"$appdir/AppRun" python3.11 scripts/test_kicad_loader.py \
    "$test_root/dist/bga-interposer-0.2.0.zip" \
    --builtin-plugins "$plugin_dir"
