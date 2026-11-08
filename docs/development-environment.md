# Managed development environment

This is operational reference for the managed cloud workspace. Repository-wide
policy and the short validation decision guide remain in `AGENTS.md`.

## Runtime layout

The canonical extracted runtimes are:

- `/workspace/tools/kicad/10.0.0/AppDir`
- `/workspace/tools/kicad/10.0.8/AppDir`

The unversioned `kicad` and `kicad-cli` commands select 10.0.8. Versioned
commands are `kicad-10.0.0`, `kicad-cli-10.0.0`, `kicad-10.0.8`, and
`kicad-cli-10.0.8`.

Development-only Python dependencies use
`/workspace/tools/venvs/kicad-automation`. Xvfb and `xdotool` are on `PATH`;
ImageMagick provides `import` and `magick`.

Persistent KiCad state uses these writable locations:

- `XDG_CONFIG_HOME=/workspace/shared/kicad/config`
- `XDG_CACHE_HOME=/workspace/shared/kicad/cache`
- `XDG_DATA_HOME=/workspace/shared/kicad/data`

From the repository root, verify the complete published environment with:

```sh
scripts/check_kicad_environment.sh
```

## CLI and package validation

The combined development gate checks both supported KiCad versions and builds
and validates every PCM package:

```sh
scripts/validate_development.sh
```

For focused diagnosis, run the integration suite separately:

```sh
KICAD_APPDIR=/workspace/tools/kicad/10.0.0/AppDir scripts/test_kicad.sh
KICAD_APPDIR=/workspace/tools/kicad/10.0.8/AppDir scripts/test_kicad.sh
```

To inspect PCM output independently, build it only in a temporary directory:

```sh
pcm_dir=$(mktemp -d)
dev_python=/workspace/tools/venvs/kicad-automation/bin/python
"$dev_python" scripts/build_pcm.py \
  --base-url https://kinderwood.github.io/kicad-automation/kicad \
  --output-dir "$pcm_dir"
"$dev_python" scripts/test_pcm.py "$pcm_dir"
```

## Native GUI validation

The default command starts a private Xvfb server and tests both runtimes under
Adwaita light and dark themes:

```sh
scripts/test_native_gui.sh
```

Specific AppDir paths may be passed when narrowing a diagnosis. The same suite
can be added to the combined gate with
`scripts/validate_development.sh --include-native-gui`.

The managed command sandbox may prevent Xvfb from creating its Unix listener
under `/tmp/.X11-unix`, producing `failed to bind listener` or `Cannot establish
any listening sockets`. Rerun `scripts/test_native_gui.sh` with host-level
execution permission; this is an execution-boundary failure, not evidence that
the GUI suite is unavailable. The host-level path is known to run both supported
runtimes and both themes.

Before reporting GUI validation as unavailable:

1. Check `pgrep -af Xvfb` and `/tmp/.X11-unix/X*` for an existing display.
2. Prefer the script, which selects and cleans up its own display. Connecting to
   an externally started display also requires access to its X socket/IPC.
3. Treat `skipped 'Native wx checks need an available display'` as a missing
   check, not a pass.

For visual evidence, run real KiCad on the private display, interact with
`xdotool` if necessary, and capture the relevant window with ImageMagick
`import`. Keep temporary screenshots outside the repository. A screenshot
establishes rendered state only; pair it with native assertions or a documented
interaction procedure for behavioral claims.
