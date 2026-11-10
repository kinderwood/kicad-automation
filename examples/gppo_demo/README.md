# GPPO A/B/C worked example

This example exercises the production Workflow controller with three canonical
revisions. Every revision has exactly one workbook pair, DXF, position manifest,
and saved native KiCad mechanical project:

| Revision | RF connector geometry | Electrical / drawing content |
| --- | --- | --- |
| A | Each side is one 14-position column on 150 mil pitch | 28 RF signals; no interposer pad circles |
| B | Each side is a rectangular 7×2 gang on 150 mil row/position pitch | 28 RF signals with the A/B signal-class remap; no interposer pad circles |
| C | B geometry with the second row staggered 75 mil | B's RF signals, one nine-pin DE-9, and a complete 800-circle interposer pad set |

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

The DXFs use mils; saved boards and manifests use millimetres. Every revision
uses the same 4.375-inch (111.125 mm) square outline. The round 2.5 mm circle
on the `INTERPOSER` layer marks the interposer footprint origin at the center
of its round guide pin, `(42.5625, 42.5625) mm`. In C, DE-9 pad 1 is the DC
anchor at `(50.0225, 92.085) mm`; all nine `DC_CENTERS` circles must match the
transformed footprint pads. C also includes one 12 mil diameter drawing circle
at each of the 800 transformed interposer pad centers. A and B intentionally
omit that optional set so the workflow warns and proceeds; C auto-detects the
complete set and validates it with the same drawing center tolerance used for
RF and DC features. The DC nets connect to adjacent interposer pads `AK11`
through `AK19` on `L2`.

The three drawings share one annotation convention. Each `GPPO_CENTERS` and
`DC_CENTERS` circle is drawn at the finished through-hole diameter of pad 1 in
its library footprint: 15 mil (0.381 mm) for the GPPO center contact and
1.0 mm for the DE-9. Each gang carries one 70 mil identifier on `MECH_LABELS`
(`J101` and `J102` for RF, `J1` for DC), centered over its holes on the
board-top side regardless of gang rotation. Pin numbers are 50 mil and appear
only at the first and last pin of every row, continuing the row axis outward
from the end hole: `1` and `14` in A, `1`, `7`, `8` and `14` in B and C, and
`1`, `5`, `6` and `9` on the DE-9. The interposer is not labelled. All text is
middle-center justified, so placement does not depend on a viewer's font
metrics. Workflow never parses this text; it is drawing context only.

The 14 RF signals per side are the largest balanced population that retains a
complete grounded interposer row between signal rows. Signal rows are `B`, `D`,
`F`, `H`, `K`, `M`, `P`, `T`, `V`, `Y`, `AB`, `AD`, `AF`, and `AH`; the
alternating rows and the end rows remain ground. The outer GPPO courtyard is
0.500 inch from the board edge, while the inner RF row is about 1.007 inches
from the interposer pin-field edge.

## Current automated run

Run with a tested KiCad Python environment from the repository checkout:

```sh
/path/to/AppDir/AppRun python3.11 examples/gppo_demo/run_demo.py /tmp/new-gppo-demo
```

The destination must be new or empty. The current script applies the complete C
fixture to a fresh project and requires a repeated `NOOP`. Results are written
to `demo-results.json`.

The coupled source fixtures are regenerated deterministically with:

```sh
/path/to/AppDir/AppRun python3.11 examples/gppo_demo/generate_fixtures.py
```

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

A uses one 14-position row per gang at 150 mil pitch. B and C use two
seven-position rows at 150 mil X/Y pitch with a -90-degree pattern rotation;
C offsets the second row by 75 mil and location 1 remains the anchor. Connector
orientation is independent: J101 is 0 degrees and J102 is 180 degrees.

The repository fixture inputs and librarian parts are never overwritten. Apply
updates the copied demo project in place. Generated outputs include the
reconciled board, managed schematic hierarchy, BOM, report, pinned inputs,
registry, and recovery journal.

## Demonstration-data limits

The library is experimental demonstration data, not librarian-approved
manufacturing data. The GPPO source was synthesized during Stage 0 from the
provided datasheet, and the 800-pad interposer came from the existing wizard.
RF bundles are geometry examples, not tuned impedance solutions. The board is
deliberately unrouted and lacks completed ground planes. Native DRC exposes real
annular-ring/default-rule and cutout-clearance issues; do not fabricate it or
lower constraints merely to erase those findings.
