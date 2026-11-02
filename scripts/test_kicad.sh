#!/bin/sh
set -eu

if [ "${KICAD_USE_SYSTEM:-0}" = "1" ]; then
    plugin_dir=${KICAD_WIZARD_DIR:-/usr/share/kicad/scripting/plugins}

    run_kicad_python() {
        python3 "$@"
    }
else
    appdir=${KICAD_APPDIR:-/workspace/shared/kicad-10.0.8/AppDir}
    plugin_dir="$appdir/share/kicad/scripting/plugins"
    KICAD_TEST_APP_RUN="$appdir/AppRun"
    export KICAD_TEST_APP_RUN

    run_kicad_python() {
        "$appdir/AppRun" python3.11 "$@"
    }
fi

KICAD_WIZARD_DIR="$plugin_dir" \
    run_kicad_python -m unittest discover -s tests -v

test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

python3 scripts/build_pcm.py \
    --base-url https://example.test/kicad \
    --output-dir "$test_root/dist" > "$test_root/archives.txt"

while IFS= read -r plugin_archive; do
    run_kicad_python scripts/test_kicad_loader.py \
        "$plugin_archive" \
        --builtin-plugins "$plugin_dir"
done < "$test_root/archives.txt"
