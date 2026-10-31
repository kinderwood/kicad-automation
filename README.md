# KiCad Automation

A collection of KiCad plugins, footprint wizards, and supporting automation.
Each tool lives in its own directory under `plugins/` with its implementation,
metadata, and tool-specific documentation.

## Tools

### BGA Interposer Wizard

Generates BGA-style interposer footprints with independently configurable grid,
center pad depopulation, symmetric corner depopulation, and perimeter pad
naming. Optional features include a guide pin hole and slots plus centered
rectangular board cutouts. The origin can be the package center or guide pin
hole center.

See [`plugins/bga_interposer/README.md`](plugins/bga_interposer/README.md) for
parameters and manual installation instructions.

## Compatibility

All distributed tools support stable KiCad releases from 9.0.0 through 10.0.8.

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

## KiCad plugin repository

The GitHub Pages workflow publishes the complete Plugin and Content Manager
repository after every successful push to `main`. Add this URL to KiCad's
**Plugin and Content Manager → Manage repositories**:

```text
https://kinderwood.github.io/kicad-automation/kicad/repository.json
```

## Development

Repository policy, validation commands, and release steps are in
[`AGENTS.md`](AGENTS.md). The minimal new-tool checklist is in
[`docs/adding-tools.md`](docs/adding-tools.md).

## License

This repository and all tools it contains are licensed under the GNU General
Public License, version 2 or (at your option) any later version. See `LICENSE`.
