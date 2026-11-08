# Workflow boundaries

The native modeless wx dialog follows Netlist source → Netclass type → Layer assignment →
Routing permissions → Connector assignments → Define RF gangs → RF transitions → Mechanical placement → Interposer microvias → Board outline →
Review changes → Apply / results. A
read-only virtual grid displays the complete parsed workbook in a separate
popup. Every page begins with a blue task-description panel whose title exactly
matches its navigation entry and uses a larger title than the supporting text.
The task panel is a fixed sibling above that page's scrolling viewport. When a
step is taller than the available window, only its controls, previews,
validation and results scroll; the task and purpose remain visible.
Step titles use sentence case; established acronyms such as RF remain uppercase.
The dialog header names the tool without repeating a separate purpose subtitle;
the left step list sizes itself to the native font, stops after the last step,
uses the dialog background inside one list border and bolds the active step.
The bordered page area and compact status/footer regions establish the
layout without a full-height navigation pane. The Layer assignment grid fits
its rows instead of consuming the remaining window. No browser, webview,
authoring wizard or generative recipe editor is part of this package's layout
flow.

The dialog opens at a nominal 1340×800 desktop size, bounded by the active
display, with a smaller resize minimum and per-page scrolling. A page must not
force a larger initial window merely to consume unused space.

Custom surfaces use semantic colors derived from wx system colors. Text and
ordinary native controls inherit the platform appearance; task panels,
assignment states, status badges, grid stripes, headings and drawn previews
adapt those colors for the current light or dark theme. The dialog reapplies
the palette on `wx.EVT_SYS_COLOUR_CHANGED`. Fixed light-theme backgrounds are
not permitted because GTK can pair them with dark-theme text. Native GUI checks
run under both Adwaita light and dark appearances.

Layer assignment and Routing permissions update the current workflow directly
from their table controls. They do not add page-level acceptance buttons;
the global status area reports the change, and **Save workflow settings**
persists it separately from the reviewed project-copy Apply operation. Saving
asks before overwriting an existing sidecar. **Delete workflow settings** removes
only that sidecar and keeps the current dialog entries open. **Reset workflow
settings** clears the current entries while preserving the stable project UUID,
active copper stackup and saved sidecar.
Layer assignment applies the same persistent dropdown chevron and amber/blue
missing/assigned cell states used by the Netlist source assignment row.
Its explicit Auto assign action fills blank rows only. It extracts one numeric
run from each name, accounts for KiCad's default `InN.Cu` offset, matches custom
names directly, preserves manual selections and leaves ambiguity visible. Layer
assignment and Netclass type size their columns from the headers, displayed
values and complete choice sets, with bounds for unusually long project names.
Their tables retain all rows and do not expand horizontally across the page.

The UI has two named table-entry patterns. An **assignment cell** maps a
categorical choice into parsed or read-only data. It is a rendered interaction
surface rather than a persistent wx.Grid editor: amber or blue state remains
visible, a chevron or **Select…** affordance identifies the action, one click
opens the native menu or picker, Space or Enter provides keyboard access, and
dismissal leaves no editor active. Netlist source, Netclass type, Layer
assignment and Connector assignments use this pattern. It avoids
focus-dependent edit state and makes the control distinct from displayed data.
When its displayed value is clipped, hovering anywhere on the assignment cell
shows the complete value; fully visible values do not add a tooltip.
Footprint assignment menus use one radio group whose first item is **Clear
assignment**. This makes the empty state explicit and allows the stored mapping
to be removed without changing the selected symbol.

A **value-entry cell** accepts typed numeric or geometric content in a dense
editable form. It retains a neutral native surface, activates its real editor
on one click and commits on Enter, focus departure, unit selection or cell
navigation. Dimension value-entry cells keep the number left aligned and the
unit selector right aligned in one bordered cell. The active editor must be tall
enough for the native Windows and GTK text and choice controls; typed unit
suffixes are parsed only when the complete value is committed. A clipped
value-entry cell shows its complete typed value on hover and otherwise has no
tooltip. Mapping grids
size columns from bounded content measurements and end after the final defined
column.

Netclass type uses the same native assignment table, amber/blue cell states,
persistent choice affordance, status placement and explicit Auto assign pattern.
Auto assignment follows only the exact project conventions `J#` = DC,
`J###` = RF and exact netclass `DC` = DC. Manual choices survive repeated runs;
conflicting connector evidence remains a blocking validation issue.

Connector assignments owns the deterministic relationship between each loaded
library symbol and footprint. The workbook supplies the interposer and connector
references. Separate RF gang and DC connector sections use the same assignment
cells. Symbol cells launch the native loaded-library browser;
footprint cells offer only parts allowed by the symbol filters and exact pin/pad
compatibility. Interposer and DC rows place **Pins used** and **Symbol pins**
between the reference and adjacent symbol/footprint assignment cells. The symbol
must contain every workbook pin; extra DC pins are allowed, while RF connector
symbols require exactly two distinct pins. A selected symbol that fails these
checks remains visible in the amber assignment state, disables its footprint
cell and is excluded from the assigned count. RF and DC sections have independent
filters. Each composed table has one bordered semantic window-colour
surface and a small inset around its header and cells, matching the visual
containment of native grids. Header, read-only and assignment cells have equal
row height and visible cell borders so their contents share one baseline and
adjacent connector rows remain distinct. Each connector section starts with an
**All** action row followed by one clean full-width divider.
Its choices apply to the complete RF or DC connector set, regardless of the
current filter. An individual edit clears the action row while retaining the
resulting per-connector assignments. Gang geometry, placement and BOM overrides do not belong on this
page. DC selections are single-unit multi-pin librarian parts; the workflow does
not author them. Interposer and DC pin-1 placement are set on Mechanical placement,
including an explicit Front/Back mounting side for the interposer,
and library MPN metadata
is never overridden by the workflow.

The selected interposer symbol supplies pin identity, footprint filters and BOM
metadata. Its graphical body is not reused by the initial generated schematic;
the generator retains its stable split interposer presentation for large pin
counts while pinning the selected library symbol as provenance.

Define RF gangs owns only RF connector position geometry, position numbering,
connector orientation and mounting side. Its editable pattern rows and numbered
preview operate on one netlist-derived gang at a time. RF transition selection
does not belong to a gang definition because locations in the same gang can
have different electrical requirements.

Linear geometry is stored in millimetres at the controller/backend boundary.
Each editable linear field in Define RF gangs and Mechanical placement has an
independent `mm`, `mil`, `inch` or `um` selector. A typed unit suffix updates
that selector when the field is committed; new fields default to `mil`. A grid
dimension keeps its number left aligned and its unit selector right aligned in
the same cell and follows the value-entry-cell commit behavior above. Gang
definitions update live after validation; their list state
is Not defined, Defined or Invalid. The preview uses hollow connector circles,
with pin 1 distinguished by colour only, and does not own apply/revert state.
It maps the gang centroid to the preview center, so rotation is visually stable,
and recomputes zoom, marker radius and label font size after each render to keep
dense patterns and multi-digit identifiers legible inside the available canvas.

Mechanical placement owns the imported-drawing source settings and every
board-space part origin derived from that drawing. A native notebook separates
**Imported drawing**, **Interposer**, **RF anchors**, **DC connectors** and
**Mounting holes** so project-wide association, single-part placement,
repeatable connector placement and optional board-only parts do not compete in
one flat form. RF gangs and DC connectors use compact, searchable selection
lists beside one focused editor, following the stable-selection pattern from
Define RF gangs.

Valid position, orientation, side, drawing-layer and label-offset edits update
the current workflow when their complete value is committed. There are no
separate interposer, RF-anchor or DC-placement acceptance buttons. Capturing a
selected native circle is an explicit canvas action and applies its center
immediately. The interposer has its own center-circle action; RF uses location
1 and DC uses footprint pad 1. Numeric RF coordinates remain available when a
drawing does not provide a selectable location-1 circle. Group-local status
identifies whether the placement came from a selected circle or entered
coordinates.

Saved-board inspection remains explicit because it launches bounded native
analysis rather than changing an in-memory field. Mounting-hole creation also
remains explicit because it adds a new project item; its independent X/Y fields
and selected-circle action must never reuse the active RF anchor implicitly.
The placed-hole list supports removal before Review changes. All source
geometry is revalidated during Review and Apply; live UI updates do not weaken
that boundary.

UI copy follows a control-ownership rule. The blue task box carries the page's
short purpose and any cross-page consequence. Labels name controls; tooltips
hold uncommon details that do not need to stay visible. Do not add nearby prose
that repeats the task box, labels, table headings or current state. Complex
entry groups receive one compact status indicator directly below the controls
they validate. That indicator reports only those controls' state. Cross-group
status is reserved for a result that cannot be assigned to one group. Preview
status reports preview or acceptance state; input errors remain with the input
group. Error text states the failed condition and the corrective action.

RF transitions is an optional, separate project-wide reconciliation step. It derives one
requirement for every unique `(netclass, mapped KiCad layer, connector
footprint)` tuple used by an RF location and assigns one compatible, published
transition artifact to that requirement when one is available. An unassigned
requirement does not block review or board generation. An explicitly selected
but incompatible artifact remains a blocking error. Artifact side, stackup, revision and
footprint hash remain compatibility checks rather than parts of the requirement
identity. Existing artifacts are immutable library inputs. An optional **Open
RF Transition Designer…** action may launch the separately installed authoring
tool, but this workflow must remain usable when that package is absent. The two
tools communicate through the versioned artifact/catalog contract and an
explicit library refresh, not an in-memory runtime dependency.

Interposer microvias is an optional, compact derivation step after placement and
before Board outline. It exposes only an enable checkbox, one choice filtered
from the current Board Setup predefined via-size list, aggregate pin/via counts
and local status. There is no custom-size, netclass-size, treatment, per-pin
table or geometry-preview control. One diameter/drill pair applies everywhere.
Eligible pairs are positive, have a drill smaller than their copper diameter,
satisfy Board Setup's microvia diameter and drill minima, and preserve the
board minimum copper clearance between the closest physical electrical pad
centres. Removing the selected pair from Board Setup clears the selection.

Every generated object is a native, locked, single-adjacent-layer microvia at a
placed interposer pad centre. A ground pin spans every adjacent copper pair. A
signal pin spans from the interposer mounting side to its mapped internal
routing layer, omits one adjacent pair, then uses GND microvias for all remaining
pairs. Back-side mounting reverses traversal without changing physical layer-pair
ordering. Outside-layer signal assignments are blocking input errors. Native
protection properties are explicitly untreated: not tented, covered, plugged,
filled or capped. The step does not claim fill/cap processing, final GND-plane
connectivity, fabricator acceptance or end-to-end board DRC.

The editable [UI Design Guide](https://www.figma.com/design/0AlFgdUMXXgdWwcJDLmx7l?node-id=135-37)
records the current workflow, product principles, approved and retired patterns,
page anatomy, interaction states and open prototypes. Its **Entry controls,
tables, and scrolling** section names the assignment-cell and value-entry-cell
patterns. The file keeps the current guide first, a dedicated KiCad exemplar
and Netlist source reference page second, current workshops next and clearly
named archives last. It records durable design decisions and reasons;
implementation history remains in repository documentation.
Its editable Layer assignment pattern is at
[section 08](https://www.figma.com/design/0AlFgdUMXXgdWwcJDLmx7l?node-id=148-37).
The implemented Define RF gangs and RF transitions patterns are at
[section 09](https://www.figma.com/design/0AlFgdUMXXgdWwcJDLmx7l?node-id=177-37)
and [section 10](https://www.figma.com/design/0AlFgdUMXXgdWwcJDLmx7l?node-id=178-46).
The curated entry-control and scrolling rules are in
[section 11](https://www.figma.com/design/0AlFgdUMXXgdWwcJDLmx7l?node-id=200-60).

`WorkflowController` owns UI drafts and preview invalidation. Core records,
workbook parsing, geometry, validation and schematic generation stay independent
of wx and pcbnew. Native KiCad object loading and writing runs in an isolated
KiCad Python subprocess, so lengthy analysis does not block the modeless dialog
and SWIG objects never cross worker-thread or process boundaries. Selected
circle capture reads the active editor only on the UI thread; accepted UUIDs
are checked against the saved mechanical snapshot before materialization.

The desired state is complete and keyed by mechanical identity, rather than
signal name or row position. The source PCB and all selected library bytes
participate in preview freshness. The backend maintains object and generated
file ownership independently of native group membership. Groups express loose
placement behavior; they do not authorize takeover of designer content.

Interposer microvias have their own locked managed group and deterministic
pad-occurrence, role and layer-span identities. Their selected footprint hash,
placement, side, size, signal layers and nets participate in desired state.
Reapplication preserves UUIDs for surviving identities while reconciling moves,
size, side, routing-layer and enable/disable changes. Expected native state and
serialized checksums detect removed, edited or reparented owned vias and report
them in Review changes rather than silently replacing manual edits.

RF PCB instances retain `gang-pin` identity. A DC connector has one PCB and
schematic instance keyed by its reference, while workbook rows bind nets to that
instance's pads. DC placement stores the selected DXF pin-1 circle, orientation
and side. The native backend resolves the footprint origin from the transformed
pad-1 center and verifies every electrical pad center against imported circles.
Unused DC pads remain netless and receive schematic no-connect markers. Missing
RF locations remain blocking population errors.

Library parts are immutable read inputs selected by their loaded KiCad library
identities. The UI resolves global, project and nested library tables, while
project snapshots preserve exact selected revisions rather than newly authored
variants. RF payloads are declarative;
opaque recipes are retained as provenance and never executed. Transition
assignments are reconciled by electrical requirement across all RF locations;
the workflow rejects incompatible saved layer, side, stackup, revision or
footprint roles rather than silently retuning geometry.

Apply materializes an isolated project copy and verifies saved native geometry,
nets and groups before journaled publication of the output files. The open
source editor is never a concurrent writer to that output. A separate checked
recovery journal is required because KiCad native Undo does not reliably restore
all saved group membership across supported versions. A new destination is
required for each non-noop apply; in-place live-editor mutation is outside this
release boundary.
