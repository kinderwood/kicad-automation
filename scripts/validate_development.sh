#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"

run_native_gui=0
if [ "$#" -gt 0 ]; then
    if [ "$#" -eq 1 ] && [ "$1" = "--include-native-gui" ]; then
        run_native_gui=1
    else
        echo "Usage: $0 [--include-native-gui]" >&2
        exit 2
    fi
fi

scripts/check_kicad_environment.sh

for version in 10.0.0 10.0.8; do
    echo "KiCad integration suite: $version"
    KICAD_APPDIR="/workspace/tools/kicad/$version/AppDir" \
        scripts/test_kicad.sh
done

pcm_dir=$(mktemp -d)
cleanup() {
    rm -rf "$pcm_dir"
}
trap cleanup EXIT HUP INT TERM

dev_python=/workspace/tools/venvs/kicad-automation/bin/python
"$dev_python" scripts/build_pcm.py \
    --base-url https://kinderwood.github.io/kicad-automation/kicad \
    --output-dir "$pcm_dir"
"$dev_python" scripts/test_pcm.py "$pcm_dir"

if [ "$run_native_gui" -eq 1 ]; then
    scripts/test_native_gui.sh
else
    echo "Native GUI suite not requested"
fi

echo "Development validation passed"
