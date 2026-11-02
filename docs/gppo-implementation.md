# GPPO implementation and acceptance

Implementation through Stage 6 and publication to the testing PCM catalogue
are authorized. The GPPO packages are version 0.1.0 with `testing` status;
publication does not imply manufacturing or RF qualification. The existing
BGA Interposer Wizard remains independent and unchanged.

## Boundaries

Librarians author GPPO and interposer footprints and RF matching artifacts.
Workflow selects read-only approved artifacts and configures placed instances.
It does not invoke authoring tools, create footprints, or edit library recipes.
Project-local schematic generation represents the selected footprint pad
definitions and workbook connectivity. RF authoring is a separate distributable
plugin with its own native dialog and saved artifact contract.

Physical connector identity is gang ID plus pin index. Nets, netclasses and
destinations may change without moving surviving connectors. Complete input
snapshots are reconciled incrementally. Ambiguous ownership and affected manual
routing block mutation. A new artifact revision requires explicit selection and
a fresh preview. No generator code is executed from a saved artifact.

## Stage acceptance

| Stage | Deliverable | Status |
|---|---|---|
| 1 | Workbook, validation, geometry and desired-state core | Implemented; runtime tests |
| 2 | Read-only library selection, stable schematic and BOM | Implemented; native netlist/ERC checks |
| 3 | Native mechanical placement and outline workflow | Implemented within selected-circle/explicit-coordinate boundary |
| 4 | Separate librarian RF artifact authoring | Implemented as an independent testing package |
| 5 | Artifact instantiation and reconciliation | Implemented; native round trips and A/B electrical reconciliation |
| 6 | Recovery, real runtime/loader/PCM checks and worked example | Bounded software checks pass; physical DRC/manufacturing acceptance fails |

Completion requires integration and observed behavior, not only unit-test or
file-format acceptance. Stage 0 evidence and its pass/fail/blocked matrix remain
in the implementation-plan and feasibility-report Pages. New verification
results below record their exact runtime and scope.

## Runtime acceptance matrix

| Actual native version | Environment | Tests | Skips | Real PCM loader |
|---|---|---:|---:|---|
| 9.0.0 | Official OCI binaries, Debian 12 userspace through PRoot | 124 PASS | 0 | All 3 packages PASS |
| 9.0.9 | Extracted official AppImage | 124 PASS | 0 | All 3 packages PASS |
| 10.0.0 | Extracted official AppImage | 124 PASS | 0 | All 3 packages PASS |
| 10.0.8 | Extracted official AppImage | 124 PASS | 0 | All 3 packages PASS |

All runs used the same source hashes, checked before and after the matrix.
The tests include existing BGA regression coverage, pure/core and native PCB
checks, actual CLI schematic export/ERC, standalone native wx controls on
Xvfb, 10,000-row parsed-data inspection, subprocess apply, offline reopen,
ownership/routing conflicts and interrupted recovery. PCM repository validation
passes for all three independently packaged tools. This is runtime execution,
not a file-format-only compatibility claim. No intermediate release or other
operating system is implied to have been individually exercised.

Additional actual PCB Editor checks use the selected 9.0.9.1 AppImage and
10.0.8: normal startup discovers both actions and Workflow opens from the
native Plugins menu. Compare executes through the embedded-editor worker path
on both selected versions; the relocated 9 project returns zero changes and
zero conflicts. RF Designer opens from the native menu on both versions; on
10.0.8 it reopens the saved RF-L2 recipe and validated preview.
The editor's Python executable differs from its bundled runtime, so worker
resolution explicitly finds the corresponding AppRun and verifies its version.
Native application registration happens before an active board exists;
headless imports remain safe. Endpoint menu interaction on 9.0.0/10.0.0 and
Windows/macOS interaction remain unconfirmed.

Machine evidence is retained in the accompanying acceptance bundle:
`matrix/summary.json`, runtime identity/commit records, test and loader logs,
`exports/REPORT.md`, per-run ERC/DRC/Excellon comparisons, and GUI screenshots.
Official KiCad documentation and source interpretations remain separately
classified in the Stage 0 evidence; the results here are empirical checks of
this implementation and these fixtures.

## Worked-project evidence

The example uses 12 GPPOs, an 800-pad interposer, mixed L2/L3 RF artifacts,
108 native RF ground vias, 60 rule areas and a validated outer boundary.
Actual controller runs on the selected 9.0.9.1 AppImage and 10.0.8 create A,
reopen it with a no-op preview, then apply B into a separate project copy.
The selected AppImage's CLI `version --format about` and pcbnew both report
**9.0.9.1**; its abbreviated CLI `version` prints 9.0.9. This is distinct from
the exact 9.0.9 runtime used for the required four-version matrix.
The generated project pins all required library inputs for offline reopening.

Independent native inspection confirms A/B preserve all 13 footprint UUIDs,
pad UUIDs, schematic paths, XY/orientation and all 108 RF via UUIDs/geometry.
Exactly 24 signal-pad net assignments change: 12 connector and 12 interposer
pads. These worked outputs both use DXF B, so this proves electrical
reconciliation, not a full interactive DXF-A-to-B migration.

Native ERC and schematic parity report zero findings in both A and B on both
selected runtimes. Value and MPN are synchronized from the generated BOM onto
project instances without modifying approved library sources. Four-layer Gerber and Excellon exports
succeed. All 122 drill operations match native positions, diameters and
plating at 0.001 mm export precision: 108 RF vias, 12 GPPO signal holes,
one interposer guide hole and one guide slot. Export success is not complete
Gerber copper-equivalence or manufacturing validation.

The unrouted example **fails physical DRC**: 12 annular-width findings,
32 copper-to-edge clearance findings and 108 dangling-via findings, plus
499 unconnected items. The annular ring and cutout relationships need librarian
and fabricator review. The example has no completed ground planes or routes;
neither a qualified Megtron stackup nor 50-ohm impedance is established.
Constraints were not weakened and findings were not waived.

## Open capabilities and improvements

| Item | Current boundary | Evidence needed to expand |
|---|---|---|
| General magnetic point picker | Selected native circle and numeric coordinates | Supported accept/cancel, cleanup and Undo semantics |
| KiCad 10 group Undo | Do not rely on native Undo for complete recovery | Recovery preserving electrical state and memberships |
| Live editor transactions | Stage and verify project copies; guard file replacement | Editor reload coordination, concurrent writes and cancellation |
| Recovery durability | Hash-checked journal and backup restoration | Power-loss durability and concurrent-writer guarantees |
| Cross-platform UI | Linux native wx validation first | Windows/macOS focus, keyboard and accessibility |
| Large-count interaction | Virtual parsed table and stable gang sheets | Full workflow latency, sorting and selection retention |
| DXF variation | Native import and explicit source validation | Arbitrary nested blocks, curves and repair tolerances |
| RF electrical/fabrication acceptance | Geometry and native output controls | Fabricator stackup review and impedance/EM qualification |
| Automatic RF selection | Explicit approved artifact selection | Reviewed rules by connector, netclass, layer and stackup |
| Git checkpoints | Deferred | Project Git integration and recovery policy |
| Example manufacturing rules | Annular-ring and cutout-clearance failures remain | Librarian-approved revised parts and fabricator rules; rerun DRC |
| Full mechanical A/B demonstration | Both DXFs supplied; worked controller run starts from native DXF B | Interactive import/reassociation and review of the A-to-B layout change |
| Full explicit schematic presentation | Stable labelled gang and paginated interposer sheets | Readability review with actual high-count designs |
| RF authoring ergonomics | Native recipe JSON editor and preview | Typed controls for common parameters and librarian usability review |
| Via treatment | Untreated vias only | Native support and fabrication export for filled/capped requirements |

Each unsupported input must produce a specific actionable blocker, not silently
degraded geometry. Experimental fixture libraries are demonstration data and do
not constitute librarian or fabrication approval.
