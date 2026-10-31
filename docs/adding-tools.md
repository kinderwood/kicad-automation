# Adding a tool

The repository is deliberately light on process. A new distributable tool
normally needs only:

```text
plugins/<tool>/
  __init__.py
  metadata.json
  README.md
  DESIGN.md       # optional; only non-obvious decisions that need rationale
tests/
  test_<tool>.py
```

For a concrete PCM manifest example, see
`plugins/bga_interposer/metadata.json`. Give the tool a stable,
repository-qualified identifier and use the repository compatibility range from
the root `README.md`. The PCM builder discovers all plugin manifests
automatically; do not add a tool-specific packaging branch unless its layout
truly requires one.

Add the tool to the root README's Tools section. Keep user-facing parameters,
installation details, and tool-specific limitations in the tool README. Add a
`DESIGN.md` only when non-obvious implementation decisions need durable
rationale; omit history, meeting notes, and facts already clear from the README,
code, or tests.

Integration tests should load the tool through KiCad's Python environment and
exercise a generated footprint or other real artifact. Cover any exact UI
ordering that matters, KiCad file-format round trips, and compatibility paths.
Run the checks in the root `AGENTS.md` before release, then bump the tool's own
semantic version. Repository releases do not share one global version.
