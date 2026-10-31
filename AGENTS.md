# Repository guide

This is a multi-tool KiCad automation repository. Keep each distributable tool
self-contained under `plugins/<tool>/`; do not make repository-level wording or
automation assume that BGA Interposer is the only tool.

## Before changing a tool

- Read the tool's `README.md` and, when present, `DESIGN.md`.
- Preserve KiCad 9.0 and 10.0 compatibility unless the task explicitly changes
  the supported range.
- Treat `AddParam` declaration order as user-interface behavior. Update the
  exact-order integration test when intentionally changing it.
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
builds it from source and tests the plugin against KiCad 9.0.0, the latest 9.0
patch in the matrix, 10.0.0, and 10.0.8.

## Releases

- Bump the tool version in its `metadata.json` for every user-visible release.
- Keep PCM metadata and the tool README aligned with actual behavior.
- A push to `main` publishes the combined PCM repository through GitHub Pages.
- The repository-wide GPL-2.0-or-later license applies to every tool.

See `docs/adding-tools.md` for the small amount of scaffolding expected for a
new tool.
