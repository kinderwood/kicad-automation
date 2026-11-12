# Connector to Interposer implementation and acceptance

Implementation through Stage 6 and publication to the testing PCM catalogue
are authorized. Connector to Interposer Workflow is version 0.6.0 and the
separate GPPO RF Transition Designer is version 0.1.2; both have `testing`
status. Publication does not imply manufacturing or RF qualification. The BGA
Interposer Wizard remains an independent package at version 0.16.1.

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

The 0.3.0 Workflow renames the first step to **Netlist source** and aligns its
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
validation policy that can block Review changes; it does not generate or modify
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
workflow-settings save action. **Layer assignment** uses a compact, content-width mapping table
with no row-number gutter, labels its columns **Netlist layer** and **KiCad
layer**, and updates the current workflow as soon as a layer is selected. It no
longer requires a separate Apply button; board files remain unchanged until the
reviewed workflow is applied.

The 0.3.7 Workflow opens wider when the active display permits so Netlist source
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
pinned for deterministic reopening. **Define RF gangs** owns only geometry,
numbering, connector orientation and mounting side. **RF transitions**
optionally reconciles unique `(netclass, mapped KiCad layer, connector footprint)`
requirements across the project; an unassigned transition does not block review
or board generation. The project BOM MPN
override is removed, and interposer placement moves to
Mechanical placement. Review changes remains the consolidated validation and comparison
gate.

Completion requires integration and observed behavior, not only unit-test or
file-format acceptance. Stage 0 evidence and its pass/fail/blocked matrix remain
in the implementation-plan and feasibility-report Pages. New verification
results below record their exact runtime and scope.

## Runtime acceptance matrix

The supported KiCad 10 endpoints below are the Stage 6 baseline recorded
through version 0.3.7. The 0.3.8 UI/model change has separate current evidence
immediately below the table.

| Actual native version | Environment | Tests | Skips | Real PCM loader |
|---|---|---:|---:|---|
| 10.0.0 | Extracted official AppImage | 136 PASS | 0 | All 3 packages PASS |
| 10.0.8 | Extracted official AppImage | 136 PASS | 0 | All 3 packages PASS |

The two baseline runs used the same 0.3.7 working tree.
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
offline reopen/no-op and B reconciliation on 10.0.8. The 10.0.0 endpoint remains
covered by the repository compatibility matrix.

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
project-wide circle-center tolerance. It defaults to 0.3 mil and validates both
RF location centers and every DC pad center. Review and Apply failures name the
feature and report its measured deviation and configured limit.

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
in the selected KiCad 10.0.8 AppImage runtime.

Version 0.4.10 uses **Define RF gangs** and a
separate **RF transitions** step. Gang geometry uses live validated updates,
editable pattern rows, per-field linear units, a PCB-style preview and local definition status,
numbering presets and independent pattern and connector rotations. Transition
assignments are optional, project-wide and keyed by
netclass, mapped KiCad layer and connector footprint; compatibility validation
retains exact artifact, footprint, side, mount and stackup checks when an
artifact is assigned. The RF
Transition Designer launcher is present but disabled pending a later integration
prototype.

Version 0.4.11 replaces the Netlist source assignment row's persistent
`wx.Grid` choice editors with one-click native choice menus. Dismissing a menu
does not change the assignment and leaves no cell editor active. This removes
the focus-dependent editor state observed after clicking outside an open
assignment control. The mapping grid also ends
after its measured, bounded columns instead of stretching across the page.

Version 0.4.12 applies that one-click assignment-menu pattern to Netclass type,
Layer assignment and connector footprint cells. Connector tables use the
semantic table background and bounded symbol/footprint columns. The dialog now
opens at a display-bounded nominal 1340×800 instead of occupying up to 1600×920.
The RF gang preview centers the point centroid, retains that center through
rotation, refits after input changes and reduces marker radius for dense arrays.

Version 0.4.13 places the composed interposer, RF gang and DC connector tables
inside bordered semantic window-colour surfaces with a small inset. It replaces
the four-part grey separator below each **All** action row with table-colour
space, so headers, read-only connector data and colored assignment cells read as
one contained table.

Version 0.4.14 restores one clean divider below the **All** row and gives every
read-only connector cell a vertically centered bordered surface aligned with
the symbol and footprint assignment cells. Each blue task panel now sits above
its page's scrolling viewport. Define RF gangs uses a one-click, native-height
dimension value editor that commits on Enter, focus departure, unit selection
or cell navigation; field-level dimension inputs no longer process partial
values on every keystroke. Dense gang previews reduce label font size together
with their marker geometry. The Figma guide now names assignment cells and
value-entry cells as separate patterns and keeps KiCad exemplars, active
workshops and archived explorations on clearly named pages.

Version 0.4.15 adds overflow-only hover tooltips to the two established table
entry patterns. Assignment cells expose a clipped mapping, symbol or footprint
identity. Value-entry cells expose a clipped typed dimension. Fully visible
values keep the native table free of redundant tooltips.

Version 0.4.16 attempted to route footprint popup commands through the
assignment cell that opened the menu, but the approach remained unreliable on
Windows 11. Version 0.4.17 therefore removes popup command-event routing and
reads the selected menu item ID synchronously from wx. The rendered interposer,
RF gang and DC connector assignment controls are unchanged. DC workbook pins
remain a subset of the selected symbol and footprint pins; any unused DC pins
are emitted as explicit no-connect pins.

Windows 11 retesting with KiCad 10.0.8 confirmed that version 0.4.17 still
returned no selection. A diagnostic build recorded `wx.ID_NONE` after a visible
footprint option was clicked, before any assignment callback ran. Inspection of
wxWidgets 3.3.1 source then identified the cause: the Windows menu backend
suppresses the command event when an already-checked radio item is clicked, and
a one-item radio group is checked automatically. Version 0.4.19 puts an
explicit **Clear assignment** state first in the radio group, so an unassigned
footprint is represented truthfully and the footprint itself remains unchecked
until selected. Clear assignment also removes the stored interposer, RF, DC or
bulk footprint mapping.

Version 0.4.22 replaces the flat Connector placement form with **Mechanical
placement** and five native tabs for imported drawing inventory, interposer,
RF anchors, DC connectors and mounting holes. RF and DC targets use the stable
selector-plus-editor pattern established by Define RF gangs. Complete numeric,
orientation, side, layer and offset values update the workflow when committed;
the redundant interposer, RF-anchor and DC-placement acceptance buttons are
removed. The interposer can now use a selected native origin circle. Mounting
holes receive independent coordinates, selected-circle capture, a placed-hole
list and removal instead of implicitly reusing the current RF anchor.

Version 0.5.0 inserts **Interposer microvias** after Mechanical placement and
adds the interposer Front/Back mounting-side choice to placement. The optional
step filters the current Board Setup predefined size list against native
microvia minima and the selected interposer's closest-pad copper clearance.
It derives uniform one-layer native microvia stacks: GND through every adjacent
span, or signal from the mounting side to an internal routing layer followed by
one omitted span and GND through the remaining board depth. Generated objects
are locked in a dedicated managed group, explicitly carry no tenting, covering,
plugging, filling or capping treatment, and reconcile size, placement, side,
layer and enable/disable changes while preserving surviving UUIDs. Existing
ownership and serialized-content checks block silent takeover of manually edited
managed vias. Final plane connectivity, fabrication acceptance and comprehensive
board/output DRC remain outside this feature.

Version 0.5.1 adds the unit-aware **Center tolerance** control to Mechanical
placement's Imported drawing tab. Its 0.3 mil default applies uniformly to RF
pattern locations and DC footprint pads. Review and native Apply revalidation
use the same validated value and report the feature, measured center deviation
and limit when a match fails; multiple DC circles inside the limit remain a
blocking ambiguity.

Version 0.5.2 auto-detects optional interposer pad circles in the imported
mechanical drawing. Missing or partial populations produce a nonblocking Review
warning; a complete population is matched one-to-one against all electrical
interposer pads using the same center tolerance as RF and DC geometry. Revision
C of the worked fixture contains all 800 pad circles, while A and B exercise the
warning-and-proceed path. Worker completion now avoids unsafe pcbnew native
teardown after its result is durably written and reports subprocess exit codes
when analysis fails.

Version 0.5.3 bounds the Mechanical placement notebook and its focused editors,
uses the same compact unit-aware linear controls as Define RF gangs, and shows
the applicable display unit directly in coordinate lists. Angular inputs are
explicitly labelled in degrees. Native layout checks prevent the step from
introducing horizontal page overflow.

Version 0.5.4 removes text-label association and manually entered placement
coordinates from Mechanical placement. Saved drawing text remains visible only
as inventory. Explicit interposer-center, RF location-1, DC pin-1 and
mounting-hole circle selections establish identity; complete RF and DC feature
sets are then matched geometrically one-to-one within the configured tolerance.
Unmatched extra circles are ignored, while missing, duplicate and ambiguous
required matches block Review. Optional interposer pad circles retain the
complete-set warning behavior introduced in 0.5.2.

Version 0.5.5 names the interposer selection by its actual role—the footprint
origin—so package-center and guide-pin-origin footprints remain equally valid.
Explicit circle selections survive KiCad UUID differences between the live
canvas and saved board when exactly one saved circle matches the recorded
center within tolerance; missing and ambiguous geometry remain blocking.

Version 0.6.0 replaces copy-only output with a staged live-project transaction.
Compare first saves the open PCB so the accepted preview has one exact native
source.
The verified candidate and persistent before/after journal are complete before
the active PCB is reconciled; project files are published only after managed and
designer-owned board state passes exact verification. Schematic Editor must be
closed during Apply, and all project editors must be closed for journal recovery.
The project root schematic stays designer-owned while one deterministic sheet
entry points to generated children beneath `.connector-interposer-managed/`.
Unrelated root content survives reruns, and edits to the managed fragment block
instead of being overwritten. Stable project-derived UUIDs restore the same
managed identities after removal and reintroduction. Isolated exact-endpoint
designer tracks and vias can survive a netlist-only reassignment as a reviewed
net retag; topology changes, zones, net-bearing shapes and ambiguous routing
remain conflicts. Imported-drawing selections are now bound to a fingerprint of
the complete native layer and its UUIDs. Geometry can suggest a likely selection
after a DXF reimport but cannot silently accept it. At selection time only, an
explicit live-canvas click is resolved uniquely to the current inspected saved
circle, addressing KiCad live/saved UUID differences without carrying an
association across drawing revisions.

Version 0.6.2 removes the interaction delays seen on large projects (measured
on a 2744-pin interposer with 240 nets, five RF gangs and six DC connectors).
The s-expression reader is single-pass, unchanged library content is parsed
once per session while every read still hashes the file to detect changes, and
the interposer minimum pad pitch uses a sorted sweep. One user action now
refreshes the dialog once instead of once per nested selection handler. Custom
numbering, pattern rotation and connector orientation commit on Enter or focus
departure.

Version 0.6.3 handles a saved sidecar that the installed version cannot load.
Opening Workflow offers **Delete and open** or **Keep and close** instead of
only reporting the error. Settings are not converted between schema versions;
only a valid project identity is carried into the new workflow.

Version 0.6.5 fixes display defects found while reviewing every step against
example C: status text is no longer clipped to a fixed width (the microvia
status read "Off — no interposer"), the Board outline note follows the restored
setting, placement lists widen to show complete coordinates, the mounting-hole
table no longer labels mil values as mm, a completed recovery reports recovery
instead of an apply, and typing a recovery journal path no longer discards the
current review.

Version 0.7.0 replaces the dialog shell with the one specified in Figma: a
toolbar with KiCad's own icons, a grouped step list with one status icon per
step, and an About box holding each step's description and checklist. The
per-step blue task panels, the status strip and the footer buttons are gone;
status lines go to a message log that docks below the window. RF transitions
now precedes Define RF gangs. Read-only placement values follow a mil / mm
switch. Closing with unsaved workflow settings asks whether to save. The steps
themselves still use their previous layouts and are being brought into line
with the Spec one at a time.

Version 0.7.1 brings the Nets & Layers steps closer to the Spec. Entry cells in
tables are drawn white with a blue outline and a chevron; a missing or
conflicting entry has a red outline and the red X in an always-reserved slot.
Readouts that share a row with entries are flat grey. The per-section status
strips on Netlist source, Netclass type and Layer assignment are gone, since
the About-box checklist reports the same counts, and the routing "new
combinations" note no longer appears and disappears. The netlist preview shows
twelve rows, the routing grid is sized to its content, and button labels use
KiCad's title case.

Version 0.7.2 completes the Nets & Layers details: the netlist table drops its
"#" column and labels the matching row "Column", entry columns are wide enough
for their reserved status slot, "Showing N of M nets" sits beside Open Full
Table, and Auto Assign sits below its table on the table's left edge.

Version 0.7.3 brings Connector assignments, RF transitions, Interposer
microvias and Board outline into line with the Spec. Connector assignments
uses Interposer / RF gangs / DC connectors tabs; its symbol and footprint
cells are entry cells with the blue outline, the red missing state and a
neutral "Set for all…" bulk row, and its readouts are grey. RF transitions
loads the project's transition files when the dialog opens, offers Search
Project, and drops the permanently disabled Designer button. The microvia
step no longer carries a fabrication note. Board outline lists its two
contours with their size and whether they close, validates automatically,
and replaces the raw JSON box and the validate button with Pick in Editor,
Show in Editor and Clear. Pick in Editor takes the next selection made in the
PCB Editor; Show in Editor highlights the saved items and moves the view to
them.

These boundaries align with KiCad 10's documented
[hierarchical schematic model](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#hierarchical-schematics),
its normal
[Update PCB from Schematic](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#update-pcb-from-schematic)
forward-annotation flow, and PCB Editor's native
[vector graphics import](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#importing_vector_graphics).
The workflow deliberately uses a narrower reviewed native reconciliation rather
than invoking a wholesale schematic-to-PCB update, so it can prove preservation
of designer-owned board objects across reruns.

The complete 172-test native/CLI suite and all three packaged PCM loader checks
pass on both KiCad 10.0.0 and 10.0.8. The native dialog suite passes on
10.0.8 under both Adwaita light and dark themes.

Additional actual PCB Editor checks use the selected 10.0.8 AppImage: normal
startup discovers both actions and Workflow opens from the native Plugins menu.
Compare executes through the embedded-editor worker path and RF Designer opens
from the native menu, reopens the saved RF-L2 recipe and restores its validated
preview. The 0.2.2 library-selector dialog and compatible-part acceptance also
pass native wx checks on Xvfb. The 0.2.3 interpreter regression checks cover
Windows sibling discovery, Linux prefixes, AppImage commands, explicit
overrides, missing interpreters and mismatched KiCad versions.
The editor's Python executable differs from its bundled runtime, so worker
resolution explicitly finds the corresponding AppRun and verifies its version.
Native application registration happens before an active board exists;
headless imports remain safe. Endpoint menu interaction on 10.0.0 and
Windows/macOS interaction remain unconfirmed.

The 0.2.1 Workflow and 0.1.1 RF releases remove an application-name check that
prevented registration in some packaged KiCad builds. Clean-profile GUI tests
installed their PCM archives under KiCad's `3rdparty/plugins` hierarchy and
confirmed both menu entries and Workflow invocation in 10.0.8.
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

The example uses 28 GPPOs, an 800-pad interposer, mixed L2/L3 RF artifacts,
252 native RF ground vias, 140 rule areas and a validated outer boundary. The
A/B native contract covers all 29 footprints and verifies that 56 signal-pad
assignments change: 28 connector pads and 28 interposer pads. The complete C
controller run also adds the nine-pin DC connector, accepts and checks all 800
optional interposer pad circles, reopens cleanly, and produces a no-op repeat
preview without warnings.

The 4.375-inch square fixture deliberately remains unrouted and lacks completed
ground planes. Physical DRC is therefore expected to report annular-width,
copper-to-edge, dangling-via, and unconnected-item findings. The annular ring
and cutout relationships still need librarian and fabricator review; neither a
qualified stackup nor 50-ohm impedance is established. Constraints are not
weakened and findings are not waived merely to make the demonstration pass.

## Open capabilities and improvements

| Item | Current boundary | Evidence needed to expand |
|---|---|---|
| General magnetic point picker | Selected native circle and numeric coordinates | Supported accept/cancel, cleanup and Undo semantics |
| KiCad 10 group Undo | Do not rely on native Undo for complete recovery | Recovery preserving electrical state and memberships |
| Live editor transactions | Stage complete update, reconcile active PCB, then publish journaled files; Schematic Editor closed | Native cross-editor reload coordination and cancellation |
| Recovery durability | Hash-checked journal and backup restoration | Power-loss durability and concurrent-writer guarantees |
| Cross-platform UI | Linux native wx validation first | Windows/macOS focus, keyboard and accessibility |
| Large-count interaction | Virtual parsed table and stable gang sheets | Full workflow latency, sorting and selection retention |
| Per-net routing permissions | Current class-by-layer matrix is workflow validation only | Searchable high-count per-net UI, decision on a separate workflow step, previewed rule changes, managed `.kicad_dru` ownership/merge behavior, and KiCad 10 router and DRC prototypes |
| DXF variation | Native import and explicit source validation | Arbitrary nested blocks, curves and repair tolerances |
| RF electrical/fabrication acceptance | Geometry and native output controls | Fabricator stackup review and impedance/EM qualification |
| Automatic RF selection | Explicit approved artifact selection | Reviewed rules by connector, netclass, layer and stackup |
| Git checkpoints | Deferred | Project Git integration and recovery policy |
| Example manufacturing rules | Annular-ring and cutout-clearance failures remain | Librarian-approved revised parts and fabricator rules; rerun DRC |
| Full mechanical A/B demonstration | Both DXFs supplied; worked controller run starts from native DXF B | Interactive import, explicit revision reselection and review of the A-to-B layout change |
| Full explicit schematic presentation | Designer root plus stable managed labelled gang and paginated interposer sheets | Readability review with actual high-count designs |
| RF authoring ergonomics | Native recipe JSON editor and preview | Typed controls for common parameters and librarian usability review |
| Via treatment | Interposer microvias explicitly use no tenting, covering, plugging, filling or capping | Native support and fabrication export for filled/capped requirements |

Each unsupported input must produce a specific actionable blocker, not silently
degraded geometry. Experimental fixture libraries are demonstration data and do
not constitute librarian or fabrication approval.
