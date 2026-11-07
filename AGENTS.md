# Repository guide

This repository contains independently versioned KiCad automation tools. Keep
each distributable tool self-contained under `plugins/<tool>/`.

## Compatibility

Preserve the repository-wide compatibility range documented in `README.md`.
Continuous integration tests the exact versions 10.0.0 and 10.0.8. Do not
narrow the supported range without an explicit project decision.

## Before changing a tool

- Read the tool's `README.md` and, when present, `DESIGN.md`.
- For footprint wizards, treat `AddParam` declaration order as user-interface
  behavior. Update the exact-order integration test when intentionally changing
  it.
- Prefer KiCad layer/editor defaults over new wizard parameters when a library
  convention already supplies the value.

## Validate changes

Run the KiCad integration suite:

```sh
scripts/test_kicad.sh
```

Build and validate the complete PCM repository in a temporary directory:

```sh
pcm_dir=$(mktemp -d)
python3 scripts/build_pcm.py \
  --base-url https://kinderwood.github.io/kicad-automation/kicad \
  --output-dir "$pcm_dir"
python3 scripts/test_pcm.py "$pcm_dir"
```

Do not hand-edit or commit generated `dist/` output. The GitHub Pages workflow
builds the PCM repository from source after the compatibility matrix passes.

### Native GUI validation

For changes to wx dialogs, editor interaction, focus, sizing, menus, or other
visible behavior, also run the native GUI suite:

```sh
scripts/test_native_gui.sh
```

This repository's managed environment is headless, but that does not mean GUI
checks are unavailable. The script starts a private Xvfb display and runs wx
inside the selected KiCad AppImage runtimes. By default it uses the established
10.0.8 AppDir when it exists; explicit AppDir paths can be passed as arguments.
The workspace fallback X server is
`/workspace/work/stage0/tools/x/usr/bin/Xvfb`.

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
private display, interact with it using the workspace `xdotool` when needed,
capture the relevant window with `import`, and inspect the resulting PNG. Keep
temporary screenshots outside the repository unless they are intentional test
fixtures or documented evidence. A screenshot proves only the rendered state;
pair it with native assertions or an interaction procedure for behavior claims.

## Releases

- Bump the tool version in its `metadata.json` for every user-visible release.
- Keep PCM metadata and the tool README aligned with actual behavior.
- A push to `main` publishes the combined PCM repository through GitHub Pages.
- The repository-wide GPL-2.0-or-later license applies to every tool.

See `docs/adding-tools.md` for the small amount of scaffolding expected for a
new tool.
