# KiCad Automation

A collection of KiCad plugins, footprint wizards, and supporting automation.
Each tool lives in its own directory under `plugins/` with its implementation,
metadata, and tool-specific documentation.

## Tools

### BGA Interposer Wizard

Generates BGA-style interposer footprints with independently configurable grid,
center pad depopulation, and symmetric corner depopulation. Optional features
include a guide pin hole and slots plus centered rectangular board cutouts. The
origin can be the package center or guide pin hole center. The wizard supports
KiCad 9.0 through 10.0.

See [`plugins/bga_interposer/README.md`](plugins/bga_interposer/README.md) for
parameters and manual installation instructions.

## Documentation

- [`AGENTS.md`](AGENTS.md) contains the concise repository workflow and release
  checks for future development sessions.
- [`docs/adding-tools.md`](docs/adding-tools.md) describes the minimal structure
  for adding another tool.
- Each tool owns its user guide and any durable implementation notes. For this
  wizard, see [`plugins/bga_interposer/DESIGN.md`](plugins/bga_interposer/DESIGN.md).

## Repository layout

```text
plugins/                 KiCad plugins and footprint wizards
scripts/                 Build, packaging, and test automation
tests/                   KiCad API integration tests
docs/                    Lightweight contributor guidance
.github/workflows/       Continuous delivery workflows
```

Each PCM-distributed plugin contains a `metadata.json` file in its plugin
directory. The repository builder discovers those manifests, packages every
plugin, and publishes a combined catalogue.

## Test with KiCad

The integration suite uses KiCad's bundled Python interpreter and `pcbnew` API:

```sh
scripts/test_kicad.sh
```

Override `KICAD_APPDIR` if KiCad is installed somewhere other than
`/workspace/shared/kicad-10.0.8/AppDir`.

The continuous-integration matrix covers KiCad 9.0.0, the latest stable 9.0
patch release, 10.0.0, and 10.0.8.

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
