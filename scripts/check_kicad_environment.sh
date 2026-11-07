#!/bin/sh
set -eu

for version in 10.0.0 10.0.8; do
    appdir="/workspace/tools/kicad/$version/AppDir"
    app_run="$appdir/AppRun"

    if [ ! -x "$app_run" ]; then
        echo "Missing executable KiCad $version AppRun: $app_run" >&2
        exit 1
    fi

    reported_version=$("$app_run" kicad-cli --version)
    if [ "$reported_version" != "$version" ]; then
        echo "Expected KiCad $version at $appdir, got $reported_version" >&2
        exit 1
    fi

    import_versions=$("$app_run" python3.11 -c '
import pcbnew
import sys
import wx

expected = sys.argv[1]
actual = pcbnew.Version()
if actual != expected:
    raise SystemExit(f"expected pcbnew {expected}, got {actual}")
print(f"pcbnew {actual}; wx {wx.version()}")
' "$version")
    echo "KiCad $version: $import_versions"
done

for program in \
    kicad kicad-cli \
    kicad-10.0.0 kicad-cli-10.0.0 \
    kicad-10.0.8 kicad-cli-10.0.8 \
    Xvfb xdotool import magick
do
    program_path=$(command -v "$program" 2>/dev/null) || {
        echo "Required command is not on PATH: $program" >&2
        exit 1
    }
    echo "$program: $program_path"
done

for command_and_version in \
    "kicad-cli:10.0.8" \
    "kicad-cli-10.0.0:10.0.0" \
    "kicad-cli-10.0.8:10.0.8"
do
    command_name=${command_and_version%%:*}
    expected_version=${command_and_version#*:}
    reported_version=$("$command_name" --version)
    if [ "$reported_version" != "$expected_version" ]; then
        echo "Expected $command_name $expected_version, got $reported_version" >&2
        exit 1
    fi
done

dev_python=/workspace/tools/venvs/kicad-automation/bin/python
if [ ! -x "$dev_python" ]; then
    echo "Missing development virtual environment Python: $dev_python" >&2
    exit 1
fi

jsonschema_version=$("$dev_python" -c '
import jsonschema
from importlib.metadata import version

print(version("jsonschema"))
')
echo "Development environment: jsonschema $jsonschema_version"
echo "KiCad environment check passed"
