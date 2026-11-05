#!/bin/sh
set -eu

# Run native wx checks on a private X server. AppDir paths may be supplied as
# arguments; otherwise use the selected compatibility runtimes when available.
xvfb=${XVFB_BIN:-}
if [ -z "$xvfb" ]; then
    xvfb=$(command -v Xvfb 2>/dev/null || true)
fi
if [ -z "$xvfb" ] && [ -x /workspace/work/stage0/tools/x/usr/bin/Xvfb ]; then
    xvfb=/workspace/work/stage0/tools/x/usr/bin/Xvfb
    x_libraries=/workspace/work/stage0/tools/x/usr/lib/x86_64-linux-gnu
    LD_LIBRARY_PATH="$x_libraries${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export LD_LIBRARY_PATH
fi
if [ -z "$xvfb" ]; then
    echo "Xvfb is required for native GUI checks" >&2
    exit 1
fi

if [ "$#" -eq 0 ]; then
    for candidate in \
        /workspace/work/stage0/runtime-gui/appimage9.0.9.1/AppDir \
        /workspace/shared/kicad-10.0.8/AppDir
    do
        if [ -x "$candidate/AppRun" ]; then
            set -- "$@" "$candidate"
        fi
    done
fi
if [ "$#" -eq 0 ]; then
    echo "Pass one or more extracted KiCad AppDir paths to test" >&2
    exit 1
fi

if [ -n "${KICAD_GUI_DISPLAY:-}" ]; then
    display=$KICAD_GUI_DISPLAY
else
    display=
    for number in 119 120 121 122 123 124 125 126 127 128 129; do
        if [ ! -S "/tmp/.X11-unix/X$number" ]; then
            display=:$number
            break
        fi
    done
    if [ -z "$display" ]; then
        echo "No free private X display number was found" >&2
        exit 1
    fi
fi

runtime_root=$(mktemp -d)
xvfb_pid=
cleanup() {
    if [ -n "$xvfb_pid" ]; then
        kill "$xvfb_pid" 2>/dev/null || true
        wait "$xvfb_pid" 2>/dev/null || true
    fi
    rm -rf "$runtime_root"
}
trap cleanup EXIT HUP INT TERM

"$xvfb" "$display" -screen 0 1600x1000x24 -extension MIT-SHM -nolisten tcp \
    >"$runtime_root/xvfb.log" 2>&1 &
xvfb_pid=$!
display_number=${display#:}
ready=0
for _attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if [ -S "/tmp/.X11-unix/X$display_number" ]; then
        ready=1
        break
    fi
    sleep 0.1
done
if [ "$ready" -ne 1 ]; then
    cat "$runtime_root/xvfb.log" >&2
    exit 1
fi

export DISPLAY=$display
export XDG_CONFIG_HOME="$runtime_root/config"
export XDG_DATA_HOME="$runtime_root/data"
export XDG_CACHE_HOME="$runtime_root/cache"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_CACHE_HOME"

for appdir in "$@"; do
    if [ ! -x "$appdir/AppRun" ]; then
        echo "KiCad AppDir has no executable AppRun: $appdir" >&2
        exit 1
    fi
    echo "Native GUI checks: $appdir"
    KICAD_TEST_APP_RUN="$appdir/AppRun" \
    KICAD_WIZARD_DIR="$appdir/share/kicad/scripting/plugins" \
        "$appdir/AppRun" python3.11 -m unittest -v \
        tests.test_connector_interposer_ui.NativeDialogTests \
        tests.test_gppo_rf_transition.RFNativeTests.test_native_dialog_validation_and_stale_publication_gate
done
