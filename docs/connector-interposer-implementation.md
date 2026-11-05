# Connector to Interposer implementation and acceptance

Implementation through Stage 6 and publication to the testing PCM catalogue
are authorized. Connector to Interposer Workflow is version 0.4.2 and the
separate GPPO RF Transition Designer is version 0.1.1; both have `testing`
status. Publication does not imply manufacturing or RF qualification. The existing
BGA Interposer Wizard remains independent and unchanged.

## Boundaries

Librarians author connector and interposer footprints and RF matching artifacts.
The workflow selects read-only approved artifacts and configures placed
instances. RF locations use one signal contact and one ground/shield contact.
DC connectors use selected single-unit multi-pin library parts with explicit NC
handling. It does not invoke authoring tools, create footprints, or
edit library recipes.
Project-local schematic generation represents the selected footprint pad
definitions and workbook connectivity. RF authoring is a separate distributable
plugin with its own native dialog and saved artifact contract.

Physical RF connector identity is gang ID plus pin index. Physical DC connector
identity is its reference, with workbook connector-pin values mapped to pads on
that one instance. Nets, netclasses and destinations may change without moving
surviving connectors. Complete input
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

The 0.2.2 Workflow replaces connector, interposer, mounting-hole and symbol
file-path controls with native wx selectors over KiCad's loaded global and
project libraries. KiCad 10 nested tables and project-over-global precedence
are resolved with KiCad's own path-variable expansion. The selected
`LibraryNickname:PartName` is user-facing; exact source paths and hashes remain
internal deterministic pins. RF artifact catalog selection is unchanged.

The 0.2.3 Workflow fixes bundled-interpreter discovery on Windows installations
where the embedded editor reports `pcbnew.exe` as `sys.executable`. Discovery
now checks runtime-relative interpreter locations on Windows and Linux, retains
the AppImage path, and validates a `pcbnew` import plus exact active KiCad
version before accepting either an automatic candidate or explicit override.
It does not search the system `PATH` automatically.

The 0.2.4 Workflow revises the Spreadsheet page around automatic workbook
loading and live reconciliation. Native wx sections separate source settings
from mapping and preview. Mapping dropdowns list populated columns by workbook
letter and heading, parsed examples update immediately, and the complete data
popup remains available after a valid unique mapping.

The 0.2.5 Workflow corrects XLSX shared-string decoding. Cell column indices
remain independent from shared-string table indices, including across
multi-sheet workbooks with spaces, hyphens and alphanumeric sheet names.

The 0.2.6 Workflow reduces the full-data inspection popup to source row plus
the seven mapped workflow fields. Unique exact headers are redetected after
workbook, worksheet and header-row changes. A Signal selection can seed a
wholly blank mapping across seven consecutive populated columns; partial or
nonconsecutive mappings are not overwritten.

The 0.2.7 Workflow adds an explicit same-path workbook reload. Reload retains
the selected worksheet when present, header row, excluded rows and downstream
draft settings, while rereading the file, redetecting exact column headers and
invalidating the prior review. A removed worksheet falls back visibly to the
first remaining sheet.

The 0.2.8 Workflow sizes parsed-data columns from their longest header or cell
value and sizes the popup to the resulting table. The window remains within
90 percent of the active display's usable area and never exceeds 1600×900;
wider content remains available through native grid scrolling.

The 0.2.9 Workflow retains the parsed-data grid gutter as a sequential preview
index, keeps original worksheet numbering in **Source row**, and omits excluded
rows from the preview so it reflects the records the workflow consumes.

The 0.3.0 Workflow renames the first step to **Netlist Source** and aligns its
seven workbook-column selectors directly above a five-row parsed-net preview.
The selected source is displayed read-only, source controls remain compact, and
column-assignment and parsed-net status sit beside **Open full table…** beneath
the preview. The file picker accepts `.xlsx`, `.csv`, and delimited `.txt` input.
All workflow pages now begin with a blue task-description panel whose title
matches the left navigation entry.

The 0.3.1 Workflow reduces the selected netlist display to the filename. The
resolved path remains available as a tooltip without occupying source-control
space.

The 0.3.2 Workflow renders the assignment row as a persistent interactive band:
blue cells are assigned, amber cells are missing, red cells conflict, and each
selector retains a dropdown chevron while inactive. A stronger divider separates
assignments from parsed nets. The grid height now fits its header and six rows.
Mapping errors identify missing or duplicated fields beside the grid, while
file, worksheet, header-row and excluded-row errors remain in Netlist source
with actionable context.

The 0.3.3 Workflow removes the redundant mapping-error sentence. Opening a
workbook now leaves the worksheet explicitly blank and keeps the default header
row at 1, avoiding a native `wx.Choice` state where the first item looked selected
without being selected. Worksheet changes reset and redetect assignments. Header
row changes preserve each assignment by physical source-column index while
refreshing its displayed heading and parsed preview.

The 0.3.4 Workflow renames **Layer rules** to **Layer assignment** and states
the boundary of its permission matrix in the UI. Workbook layer names map to
actual KiCad copper layers. The netclass/layer matrix is a saved workflow
validation policy that can block Review Changes; it does not generate or modify
KiCad custom routing rules.

The 0.3.5 Workflow separates the small workbook-to-copper mapping task from the
larger routing-policy task. **Layer assignment** now contains only source-layer
mapping. The unchanged class-by-layer validation matrix has its own **Routing
permissions** step, reserving that page for the planned searchable per-net UI
and previewed KiCad rule updates.

The 0.3.6 Workflow replaces the overflowing Listbook navigation with a compact
native step list whose width follows the rendered labels and whose height ends
after the final step. A tool heading, purpose line, bordered current-step area
and explicit status region clarify the dialog hierarchy and the scope of the
workflow-settings save action. **Layer assignment** uses a content-sized mapping table
with no row-number gutter, labels its columns **Netlist layer** and **KiCad
layer**, and updates the current workflow as soon as a layer is selected. It no
longer requires a separate Apply button; board files remain unchanged until the
reviewed workflow is applied.

The 0.3.7 Workflow opens wider when the active display permits so Netlist Source
fits without an initial horizontal scrollbar. The redundant dialog subtitle is
removed, **Steps** and each blue-panel page title have stronger hierarchy, and
the footer action is named **Save workflow settings**. Layer assignment places
its assigned count below the table and removes its explanatory footer; the grid
height ends at the last complete row. Routing-permission checkboxes now update
the current workflow immediately, so their Apply button and static saved-state
sentence are removed. A dynamic notice remains when newly encountered
netclass/layer combinations require review.

The 0.3.8 Workflow replaces the separate Validation page with **Connector
assignments**. The workbook supplies the interposer reference and gang list;
each row selects a symbol from KiCad's loaded libraries and then an allowed
footprint from an in-cell dropdown. Footprint choices satisfy both the symbol's
`ki_fp_filters` and exact electrical pin/pad compatibility. Symbols are stored
per gang, the interposer has its own symbol/footprint assignment, and both are
pinned for deterministic reopening. Gangs now contains geometry and RF settings
only. The project BOM MPN override is removed, and interposer placement moves to
DXF Anchors. Review Changes remains the consolidated validation and comparison
gate.

Completion requires integration and observed behavior, not only unit-test or
file-format acceptance. Stage 0 evidence and its pass/fail/blocked matrix remain
in the implementation-plan and feasibility-report Pages. New verification
results below record their exact runtime and scope.

## Runtime acceptance matrix

The four-version matrix below is the Stage 6 baseline recorded through version
0.3.7. The 0.3.8 UI/model change has separate current evidence immediately
below the table.

| Actual native version | Environment | Tests | Skips | Real PCM loader |
|---|---|---:|---:|---|
| 9.0.0 | Official OCI binaries, Debian 12 userspace through PRoot | 136 PASS | 0 | All 3 packages PASS |
| 9.0.9 | Extracted official AppImage | 136 PASS | 0 | All 3 packages PASS |
| 10.0.0 | Extracted official AppImage | 136 PASS | 0 | All 3 packages PASS |
| 10.0.8 | Extracted official AppImage | 136 PASS | 0 | All 3 packages PASS |

The four baseline runs used the same 0.3.7 working tree.
The tests include existing BGA regression coverage, pure/core and native PCB
checks, actual CLI schematic export/ERC, standalone native wx controls on
Xvfb, automatic populated-column mapping with live examples, 10,000-row
parsed-data inspection, subprocess apply, offline reopen,
ownership/routing conflicts and interrupted recovery. PCM repository validation
passes for all three independently packaged tools. This is runtime execution,
not a file-format-only compatibility claim. No intermediate release or other
operating system is implied to have been individually exercised.

Version 0.3.8 passes the 136-test native suite and packaged-loader checks on the
available KiCad 10.0.8 runtime. Three native wx dialog tests also pass under
Xvfb, including loaded-library filter resolution, independent J101/J102
assignment and the opening-width regression. The complete demo passes A apply,
offline reopen/no-op and B reconciliation on 10.0.8. The earlier four-version
baseline supports the maintained compatibility range, but the 0.3.8 delta has
not been rerun on the other three exact runtimes in this workspace.

Version 0.3.9 fixes the live Connector assignments state transition so selecting
a symbol immediately enables its filtered footprint dropdown. Layer assignment
now uses the established interactive-cell language: missing KiCad layers are
amber, assigned layers are blue, dropdown chevrons remain visible, and one click
opens the native choice editor. Native wx regression coverage checks both states.

Version 0.3.10 adds explicit conservative auto-assignment for blank layer rows.
It accepts names containing exactly one numeric run, maps KiCad defaults by
physical copper-layer number (`F.Cu` = 1, `In1.Cu` = 2, `B.Cu` = final), and
matches custom board-layer numbers directly. Existing manual selections are
preserved. Missing numbers, multiple numeric runs and duplicate board candidates
remain unassigned and visible for review.

Version 0.4.0 adds explicit RF/DC netclass classification and mixed connector
topologies. Conservative type assignment recognizes exact `J#` DC references,
exact `J###` RF references and the exact `DC` netclass while preserving manual
choices and blocking conflicting evidence. RF gangs retain one symbol and
footprint per location. Each DC connector selects one librarian-authored,
single-unit multi-pin symbol and footprint, produces one schematic/PCB/BOM
instance, permits `GND`, preserves duplicate-net rejection, and marks unused
pins NC without assigning their PCB pads a net. DXF placement anchors the
selected footprint by its transformed pad-1 center, independent of library
origin, with explicit rotation and side. Native KiCad 10.0.8 tests confirm exact
front/back pad-1 placement, netless NC pad round trips, complete mixed RF/DC
apply, and generated-schematic ERC. Full DC pad geometry currently uses a
one-nanometre internal circle-center tolerance; an adjustable tolerance remains
future work.

Version 0.4.1 adds explicit saved-settings lifecycle controls. Save asks before
replacing an existing project sidecar. Delete asks before removing that sidecar
and leaves the current dialog state open. Reset asks before clearing the current
dialog and preserves the stable project identity, detected board stackup and
saved sidecar. None of these controls changes PCB or schematic files.

Version 0.4.2 replaces fixed light-theme colors with a semantic palette derived
from wx system colors. Task panels, assignment states, status badges, table
headers and stripes, selections and custom-drawn previews remain distinct in
light and dark appearances. The dialog reapplies the palette when wx reports a
system-color change. Native GUI regression checks execute the same dialog tests
under Adwaita light and dark themes. The complete light/dark dialog set passes
in the selected KiCad 9.0.9.1 and 10.0.8 AppImage runtimes.

Additional actual PCB Editor checks use the selected 9.0.9.1 AppImage and
10.0.8: normal startup discovers both actions and Workflow opens from the
native Plugins menu. Compare executes through the embedded-editor worker path
on both selected versions; the relocated 9 project returns zero changes and
zero conflicts. RF Designer opens from the native menu on both versions; on
10.0.8 it reopens the saved RF-L2 recipe and validated preview. The 0.2.2
library-selector dialog and compatible-part acceptance also pass native wx
checks on Xvfb in 9.0.9.1 and 10.0.8. The 0.2.3 interpreter regression checks
cover Windows sibling discovery, Linux prefixes, AppImage commands, explicit
overrides, missing interpreters and mismatched KiCad versions. The complete
suite also passes on the selected 9.0.9.1 runtime.
The editor's Python executable differs from its bundled runtime, so worker
resolution explicitly finds the corresponding AppRun and verifies its version.
Native application registration happens before an active board exists;
headless imports remain safe. Endpoint menu interaction on 9.0.0/10.0.0 and
Windows/macOS interaction remain unconfirmed.

The 0.2.1 Workflow and 0.1.1 RF releases remove an application-name check that
prevented registration in some packaged KiCad builds. Clean-profile GUI tests
installed their PCM archives under KiCad's `3rdparty/plugins` hierarchy and
confirmed both menu entries and Workflow invocation in 9.0.9.1 and 10.0.8.
The packaged-loader regression also uses a non-`pcbnew` application name and
requires each action to call `register()`. Fedora RPM and Flatpak behavior
remains a user-environment check after the updated packages are installed.

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
| Per-net routing permissions | Current class-by-layer matrix is workflow validation only | Searchable high-count per-net UI, decision on a separate workflow step, previewed rule changes, managed `.kicad_dru` ownership/merge behavior, and KiCad 9/10 router and DRC prototypes |
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
