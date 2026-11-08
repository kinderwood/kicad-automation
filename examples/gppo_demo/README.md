# GPPO A/B/C worked example

This example exercises the production Workflow controller with three canonical
revisions. Every revision has exactly one workbook pair, DXF, position manifest,
and saved native KiCad mechanical project:

| Revision | RF connector geometry | Electrical / drawing content |
| --- | --- | --- |
| A | J101 and J102 are straight six-position columns on 0.325-inch pitch | 12 RF signals; no interposer pad circles |
| B | Each RF gang is two columns of three positions on 0.500-inch pitch | 12 RF signals with the A/B signal-class remap; no interposer pad circles |
| C | Same two-by-three RF geometry as B | B's RF signals, one nine-pin DE-9, and a complete 800-circle interposer pad set |

The matching files are `inputs/netlist-X.csv`, `inputs/netlist-X.xlsx`,
`inputs/dxf-X-mils.dxf`, `inputs/positions-X.json`, `mechanical-X.kicad_pcb`,
and `mechanical-X.kicad_pro`, where `X` is A, B, or C. There are no separate
"mixed" workbook variants.

All schematic symbols are in `library/Example.kicad_sym`, and all connector and
interposer footprints are in `library/Example.pretty`. The separate
`Example-DC` symbol library is intentionally gone. The GPPO symbol uses the
conventional coaxial presentation, with pin 1 as the center conductor and pin 2
as the shield. `DSub_9` is a single-unit nine-pin symbol paired with
`DSub_9_Pins_Vertical`.

The DXFs use mils; saved boards and manifests use millimetres. The round 2.5 mm
circle on the `INTERPOSER` layer marks the interposer footprint origin at the
center of its round guide pin, `(94.95, 94.95) mm`. In C, DE-9 pad 1 is the DC
anchor at `(102.41, 190.00) mm`; all nine `DC_CENTERS` circles must match the
transformed footprint pads. C also includes one 12 mil diameter drawing circle
at each of the 800 transformed interposer pad centers. A and B intentionally
omit that optional set so the workflow warns and proceeds; C auto-detects the
complete set and validates it with the same drawing center tolerance used for
RF and DC features. The DC nets connect to adjacent interposer pads `AK11`
through `AK19` on `L2`.

## Current automated run

Run with a tested KiCad Python environment from the repository checkout:

```sh
/path/to/AppDir/AppRun python3.11 examples/gppo_demo/run_demo.py /tmp/new-gppo-demo
```

The destination must be new or empty. The current script applies the complete C
fixture to a fresh project and requires a repeated `NOOP`. Results are written
to `demo-results.json`.

Fresh A/B/C coverage and the A → B → C → A round trip are intentionally not
implemented yet. `tests/test_gppo_demo_revision_matrix.py` is a comment-only
planning stub describing that future coverage. It must remain a no-op until the
plugin has matured and the user explicitly requests implementation.

## Manual workflow

The A/B/C files describe the intended future revision states. The currently
demonstrated path is a fresh C run using `mechanical-C.kicad_pcb`,
`netlist-C.xlsx`, and `positions-C.json`. Do not treat the presence of the A/B
fixtures as evidence that automated fresh A/B runs or cross-revision updates are
supported.

A uses six one-position rows at 325 mil pitch. B and C use two three-position
rows at 500 mil X/Y pitch with a -90-degree pattern rotation; location 1 remains
the anchor. Connector orientation is independent: J101 is 0 degrees and J102
is 180 degrees.

The source inputs and librarian parts are never overwritten by Apply. Generated
outputs include the reconciled board, schematic hierarchy, BOM, report, pinned
inputs, registry, and recovery journal.

## Demonstration-data limits

The library is experimental demonstration data, not librarian-approved
manufacturing data. The GPPO source was synthesized during Stage 0 from the
provided datasheet, and the 800-pad interposer came from the existing wizard.
RF bundles are geometry examples, not tuned impedance solutions. The board is
deliberately unrouted and lacks completed ground planes. Native DRC exposes real
annular-ring/default-rule and cutout-clearance issues; do not fabricate it or
lower constraints merely to erase those findings.
