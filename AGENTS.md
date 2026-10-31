# Repository guide

This repository contains independently versioned KiCad automation tools. Keep
each distributable tool self-contained under `plugins/<tool>/`.

## Compatibility

Preserve the repository-wide compatibility range documented in `README.md`.
Continuous integration tests the exact versions 9.0.0, 9.0.9, 10.0.0, and
10.0.8. Do not narrow the supported range without an explicit project decision.

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

## Releases

- Bump the tool version in its `metadata.json` for every user-visible release.
- Keep PCM metadata and the tool README aligned with actual behavior.
- A push to `main` publishes the combined PCM repository through GitHub Pages.
- The repository-wide GPL-2.0-or-later license applies to every tool.

See `docs/adding-tools.md` for the small amount of scaffolding expected for a
new tool.
