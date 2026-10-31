# Center Void BGA Wizard for KiCad 10

A footprint wizard for rectangular BGA packages with a rectangular group of
balls omitted from the exact center. It starts from the behavior and drawing
style of KiCad's bundled BGA wizard while adding independent overall and void
row/column controls.

The default generates a 20 × 20 full grid at 1 mm pitch with an 8 × 8 center
void: 336 pads over the same 19 mm × 19 mm pad-center span as a full 20 × 20
grid. Names retain their full-grid locations, so a removed ball is absent rather
than causing later balls to be renumbered.

## Wizard parameters

- `Pads`: pitch, ball/pad size, total columns, and total rows.
- `Center void`: omitted columns and omitted rows.
- `Package`: body width, body length, and courtyard margin.

For an exactly centered void, each void dimension must have the same parity as
its corresponding outer dimension. For example, 20/8 and 19/7 are valid;
20/7 is rejected because it would be offset by half a pitch. Set both void
dimensions to zero to generate a full grid.

## Test with KiCad 10.0.8

The included integration test uses the KiCad AppImage Python and `pcbnew` API:

```sh
chmod +x scripts/test_kicad.sh
scripts/test_kicad.sh
```

Override `KICAD_APPDIR` if KiCad is installed somewhere else. The test confirms
pad count, naming, geometry, rectangular configurations, and validation errors.

## Install manually

Copy `plugins/center_void_bga` into KiCad's per-user scripting plugin folder.
On Linux with KiCad 10 this is normally:

```text
~/.local/share/kicad/10.0/scripting/plugins/center_void_bga
```

Restart KiCad, open the Footprint Editor, and choose **File → Create Footprint →
Create Footprint from Wizard**. Select **BGA, Center Void**.

## Publish as a custom KiCad plugin repository

This project includes a GitHub Pages workflow that builds the PCM package,
catalogue, resources archive, hashes, and repository entry point on every push
to `main`.

1. Push this repository to GitHub.
2. In **Settings → Pages**, select **GitHub Actions** as the source.
3. After the workflow deploys, add this URL in KiCad's **Plugin and Content
   Manager → Manage repositories**:

   ```text
   https://YOUR-GITHUB-USER.github.io/YOUR-REPO/kicad/repository.json
   ```

KiCad can then install and update the wizard directly from the GUI; no ZIP needs
to be transferred manually. To build the same static repository locally:

```sh
python3 scripts/build_pcm.py \
  --base-url https://example.org/kicad \
  --output-dir dist
python3 scripts/test_pcm.py dist
```
