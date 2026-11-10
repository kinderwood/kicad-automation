# Repository guide

This repository contains independently versioned KiCad automation tools. Keep
each distributable tool self-contained under `plugins/<tool>/`.

## Development status and compatibility

This repository is development-only: its current users are the owner and the
owner's coding agents (Codex and Claude Code), with no external/production
users, deployed projects, durable work products, or historical data requiring
compatibility. Unless explicitly requested for a
current requirement, do not add migrations, compatibility shims, deprecated or
legacy interfaces/formats, or fallback behavior. Change internal APIs and
persisted development formats directly; update implementation, tests, fixtures,
and examples together, and remove obsolete compatibility code within task scope.

The supported and tested runtime matrix remains exactly KiCad 10.0.0 and 10.0.8.
KiCad 9 and other versions are unsupported. Do not change this matrix without an
explicit project decision.

## Before changing a tool

- Read the tool's `README.md` and, when present, `DESIGN.md`.
- Treat unit choice primarily as input/display guidance: board and DXF placement,
  routing, and rule settings typically prefer mils, while footprint controls
  should follow their native authoring grid (interposers are usually metric).
  Honor explicit source/project units. Keep calculations and storage in the
  tool/KiCad canonical representation; convert once at boundaries with shared
  or `pcbnew` helpers, retain display-unit metadata separately, and avoid
  conversion round trips or premature rounding.
- Prefer KiCad layer/editor defaults over new wizard parameters when a library
  convention already supplies the value.

## Environment and validation

Run commands from the repository root. Before changing code or running a longer
suite, verify the managed runtimes and dependencies:

```sh
scripts/check_kicad_environment.sh
```

Use targeted unit/CLI checks while developing. The standard pre-delivery gate
covers both supported KiCad versions and all PCM packages:

```sh
scripts/validate_development.sh
```

Native GUI testing is supplemental, not an automatic requirement for UI
changes. Use `--include-native-gui` (or `scripts/test_native_gui.sh`) for final
QA of substantial UI work, when explicitly requested, or to reproduce/debug
complex native interaction issues; prefer CLI coverage for routine UI work. The
Codex command sandbox can block Xvfb from binding `/tmp/.X11-unix`; if that
occurs, rerun the GUI script with host-level execution permission. Claude Code
cloud sessions are not sandboxed this way and run the script directly. A skipped
GUI suite is not a pass.

See `docs/development-environment.md` for runtime paths, individual commands,
native-GUI troubleshooting, and visual-review procedures.

Do not hand-edit or commit generated `dist/` output. The GitHub Pages workflow
builds the PCM repository from source after the compatibility matrix passes.

## Delivery workflow

- After local validation, commit completed work and push it directly to
  `origin/main` unless the user explicitly requests another delivery method.
- Do not create or push remote branches or open pull requests unless the user
  explicitly asks for one. Local branches are allowed while work is in progress.
- This applies to every agent. A Claude Code cloud session is assigned a
  `claude/...` branch by default; the owner has authorized pushing to
  `origin/main` instead, so do not push that session branch unless asked.
- Do not stop at a local-only commit: GitHub Actions on remote `main` is the
  publication path that makes plugin changes available through the Plugin and
  Content Manager.

## Releases

- Bump the tool version in its `metadata.json` for every user-visible release.
- Keep PCM metadata and the tool README aligned with actual behavior.
- A push to `main` publishes the combined PCM repository through GitHub Pages.
