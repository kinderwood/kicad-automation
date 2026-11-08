# Repository guide

This repository contains independently versioned KiCad automation tools. Keep
each distributable tool self-contained under `plugins/<tool>/`.

## Development status and compatibility

This repository is development-only: its current users are the owner and Codex,
with no external/production users, deployed projects, durable work products, or
historical data requiring compatibility. Unless explicitly requested for a
current requirement, do not add migrations, compatibility shims, deprecated or
legacy interfaces/formats, or fallback behavior. Change internal APIs and
persisted development formats directly; update implementation, tests, fixtures,
and examples together, and remove obsolete compatibility code within task scope.

The supported and tested runtime matrix remains exactly KiCad 10.0.0 and 10.0.8.
KiCad 9 and other versions are unsupported. Do not change this matrix without an
explicit project decision.

## Managed development environment

The canonical extracted KiCad runtimes are:

- `/workspace/tools/kicad/10.0.0/AppDir`
- `/workspace/tools/kicad/10.0.8/AppDir`

The unversioned `kicad` and `kicad-cli` commands select 10.0.8. Explicit
commands are available as `kicad-10.0.0`, `kicad-cli-10.0.0`,
`kicad-10.0.8`, and `kicad-cli-10.0.8`.

The canonical development virtual environment is
`/workspace/tools/venvs/kicad-automation`. Use its Python for development-only
dependencies such as `jsonschema`. Xvfb and `xdotool` are on `PATH`, and
ImageMagick provides both `import` and `magick`.

The managed environment keeps persistent KiCad state in these configured,
writable directories:

- `XDG_CONFIG_HOME=/workspace/shared/kicad/config`
- `XDG_CACHE_HOME=/workspace/shared/kicad/cache`
- `XDG_DATA_HOME=/workspace/shared/kicad/data`

Run commands from the repository root. Before changing code or running a longer
suite, check the published environment, including the XDG directories, KiCad
runtimes and aliases, GUI tools, and development Python dependencies:

```sh
scripts/check_kicad_environment.sh
```

For the standard environment or pre-delivery readiness check, run the combined
gate. It covers both supported KiCad versions and the PCM build:

```sh
scripts/validate_development.sh
```

Add `--include-native-gui` when the change affects visible or interactive
behavior, when diagnosing a GUI-specific failure, or when a complete native GUI
environment check is explicitly requested.

## Before changing a tool

- Read the tool's `README.md` and, when present, `DESIGN.md`.
- For footprint wizards, treat `AddParam` declaration order as user-interface
  behavior. Update the exact-order integration test when intentionally changing
  it.
- Prefer KiCad layer/editor defaults over new wizard parameters when a library
  convention already supplies the value.

## Validate changes

The CLI integration and PCM suites are the standard validation path. Run the
native GUI suite only when a change affects native wx or visible editor
behavior, when diagnosing a GUI-specific failure, or when explicitly requested.
Do not run it routinely for documentation, metadata, packaging, or non-visual
logic changes.

Run the KiCad integration suite against both supported AppDirs:

```sh
KICAD_APPDIR=/workspace/tools/kicad/10.0.0/AppDir scripts/test_kicad.sh
KICAD_APPDIR=/workspace/tools/kicad/10.0.8/AppDir scripts/test_kicad.sh
```

Build and validate the complete PCM repository in a temporary directory:

```sh
pcm_dir=$(mktemp -d)
dev_python=/workspace/tools/venvs/kicad-automation/bin/python
"$dev_python" scripts/build_pcm.py \
  --base-url https://kinderwood.github.io/kicad-automation/kicad \
  --output-dir "$pcm_dir"
"$dev_python" scripts/test_pcm.py "$pcm_dir"
```

Do not hand-edit or commit generated `dist/` output. The GitHub Pages workflow
builds the PCM repository from source after the compatibility matrix passes.

### Native GUI validation

For changes to wx dialogs, editor interaction, focus, sizing, menus, or other
visible behavior, also run the native GUI suite. The first command uses both
canonical AppDirs; the second form shows the equivalent explicit selection:

```sh
scripts/test_native_gui.sh
# Equivalent explicit form:
scripts/test_native_gui.sh \
  /workspace/tools/kicad/10.0.0/AppDir \
  /workspace/tools/kicad/10.0.8/AppDir
```

This repository's managed environment is headless, but that does not mean GUI
checks are unavailable. The script starts a private Xvfb display and runs wx
inside the selected KiCad AppImage runtimes. With no arguments it tests both
canonical AppDirs; explicit AppDir paths can be passed as arguments.

The managed command sandbox can prevent Xvfb from binding its Unix socket even
when the binary and runtimes are present. If the script reports that it cannot
create a listener under `/tmp/.X11-unix`, rerun that script with host-level
execution permission. This is an execution-boundary issue, not evidence that
native GUI testing is unavailable.

Before reporting a native GUI check as unavailable:

1. Check for an existing private display with `pgrep -af Xvfb` and
   `/tmp/.X11-unix/X*`.
2. Prefer `scripts/test_native_gui.sh`, which creates and cleans up its own
   display. If connecting to a display started outside the command sandbox,
   request the host-level permission needed for the X socket/IPC.
3. Treat `skipped 'Native wx checks need an available display'` as a missing
   check, never as a GUI pass.

For visual review or walkthrough evidence, launch the real KiCad runtime on the
private display, interact with it using `xdotool` when needed, capture the
relevant window with ImageMagick `import` (or process it with `magick`), and
inspect the resulting PNG. Keep temporary screenshots outside the repository
unless they are intentional test fixtures or documented evidence. A screenshot
proves only the rendered state; pair it with native assertions or an interaction
procedure for behavior claims.

## Delivery workflow

- After local validation, commit completed work and push it directly to
  `origin/main` unless the user explicitly requests another delivery method.
- Do not create or push remote branches or open pull requests unless the user
  explicitly asks for one. Local branches are allowed while work is in progress.
- Do not stop at a local-only commit: GitHub Actions on remote `main` is the
  publication path that makes plugin changes available through the Plugin and
  Content Manager.

## Releases

- Bump the tool version in its `metadata.json` for every user-visible release.
- Keep PCM metadata and the tool README aligned with actual behavior.
- A push to `main` publishes the combined PCM repository through GitHub Pages.
- The repository-wide GPL-2.0-or-later license applies to every tool.

See `docs/adding-tools.md` for the small amount of scaffolding expected for a
new tool.
