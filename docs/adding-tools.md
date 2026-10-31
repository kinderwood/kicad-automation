# Adding a tool

The repository is deliberately light on process. A new distributable tool
normally needs only:

```text
plugins/<tool>/
  __init__.py
  metadata.json
  README.md
  DESIGN.md       # only durable implementation rules and non-obvious decisions
tests/
  test_<tool>.py
```

Use `plugins/bga_interposer/metadata.json` as a PCM manifest example. Give the
tool a stable, repository-qualified identifier and declare its actual KiCad
version range. The PCM builder discovers all plugin manifests automatically;
do not add a tool-specific packaging branch to the builder unless its layout
truly requires one.

Add the tool to the root README's Tools section. Keep user-facing parameters,
installation details, and supported KiCad versions in the tool README. Put
only invariants that guide future implementation in `DESIGN.md`; omit history,
meeting notes, and explanations that are already obvious from the code.

Integration tests should load the tool through KiCad's Python environment and
exercise a generated footprint or other real artifact. Cover any exact UI
ordering that matters, KiCad file-format round trips, and compatibility paths.
Run the checks in the root `AGENTS.md` before release, then bump the tool's own
semantic version. Repository releases do not share one global version.
