# KiCad Automation

A collection of KiCad plugins, footprint wizards, and supporting automation.
Each tool lives in its own directory under `plugins/` with its implementation,
metadata, and tool-specific documentation.

## Tools

### BGA Interposer Wizard

Generates BGA-style interposer footprints with independently configurable grid
and centered-opening dimensions. The first release targets KiCad 10.

See [`plugins/bga_interposer/README.md`](plugins/bga_interposer/README.md) for
parameters and manual installation instructions.

## Repository layout

```text
plugins/                 KiCad plugins and footprint wizards
scripts/                 Build, packaging, and test automation
tests/                   KiCad API integration tests
.github/workflows/       Continuous delivery workflows
```

Each PCM-distributed plugin contains a `metadata.json` file in its plugin
directory. The repository builder discovers those manifests, packages every
plugin, and publishes a combined catalogue.

## Test with KiCad 10.0.8

The integration suite uses KiCad's bundled Python interpreter and `pcbnew` API:

```sh
scripts/test_kicad.sh
```

Override `KICAD_APPDIR` if KiCad is installed somewhere other than
`/workspace/shared/kicad-10.0.8/AppDir`.

## Build the KiCad plugin repository

```sh
python3 scripts/build_pcm.py \
  --base-url https://example.org/kicad \
  --output-dir dist
python3 scripts/test_pcm.py dist
```

The output is a static KiCad Plugin and Content Manager repository containing a
catalogue, resources archive, and installable archive for every discovered
plugin.

## Publishing

The GitHub Pages workflow builds and validates the complete PCM repository on
every push to `main`. Once Pages is enabled, add this URL to KiCad's **Plugin and
Content Manager → Manage repositories**:

```text
https://kinderwood.github.io/kicad-automation/kicad/repository.json
```

## License

This repository and all tools it contains are licensed under the GNU General
Public License, version 2 or (at your option) any later version. See `LICENSE`.
