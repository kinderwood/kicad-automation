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
persists it separately from the reviewed live-project Apply operation. Saving
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
navigation. Free-text gang values (custom numbering, pattern rotation and
connector orientation) follow the same commit rule, and one user action
refreshes the dialog at most once. Dimension value-entry cells keep the number left aligned and the
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
**Mounting holes** so project-wide inventory, single-part placement,
repeatable connector placement and optional board-only parts do not compete in
one flat form. The notebook and focused editors are width-bounded; linear
fields use the same compact number-plus-unit control as Define RF gangs, and
coordinate lists include the saved display unit with each value. RF gangs and
DC connectors use compact, searchable selection lists beside one focused
editor, following the stable-selection pattern from Define RF gangs.

Valid orientation, side, drawing-layer and center-tolerance edits update
the current workflow when their complete value is committed. There are no
separate interposer, RF-anchor or DC-placement acceptance buttons. Capturing a
selected native circle is an explicit canvas action and applies its center
immediately. The interposer has its own origin-circle action; RF uses location
1 and DC uses footprint pad 1. Imported text is contextual inventory only and
never supplies identity or placement. Group-local status identifies the selected
circle and current placement state.

Saved-board inspection remains explicit because it launches bounded native
analysis rather than changing an in-memory field. Inspection fingerprints the
complete imported layer, including native UUIDs. Circle selections carry that
accepted drawing revision. A later inspection may offer a unique geometry match
as a suggestion, but changing or reimporting the drawing always requires explicit
reselection. During Apply, exact live/saved drawing content is used to align
KiCad's transient live graphic UUIDs to the already reviewed saved UUIDs; this
prevents a later editor save from manufacturing a false drawing revision without
weakening explicit reassociation for a real reimport. Mounting-hole creation also
remains explicit because it adds a new project item; its selected-circle action
must never reuse the active RF anchor implicitly.
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

## Design source and standing rules

The design file is <https://www.figma.com/design/0AlFgdUMXXgdWwcJDLmx7l>. Agents read and edit it through the Figma MCP server.
Its **Read me** page defines each page's role and repeats the rules below; its
**Decisions log** page records every decision and deferred item and is the
first thing to read.

The target for all twelve steps is drawn on three Spec pages, one 1280 × 800
frame per step with every further tab beside it:

- [Spec — Nets & Layers](https://www.figma.com/design/0AlFgdUMXXgdWwcJDLmx7l?node-id=251-2): Netlist source, Netclass type,
  Layer assignment, Routing permissions.
- [Spec — Parts & Geometry](https://www.figma.com/design/0AlFgdUMXXgdWwcJDLmx7l?node-id=283-2): Connector assignments, RF
  transitions, Define RF gangs, Mechanical placement, Interposer microvias,
  Board outline.
- [Spec — Finish](https://www.figma.com/design/0AlFgdUMXXgdWwcJDLmx7l?node-id=325-2): Review changes, Apply / results.

The plugin is being brought into line with the Spec, shell first and then step
by step. Until a step is implemented, the paragraphs above describe the code
as it runs and the Spec describes where it is going; where they differ, the
Spec wins for new work.

Working with the file:

- Any frame on a Spec page is binding. `Proposal — <topic>` pages are open
  exploration and are not a target. Never edit **Reference** or **Archive**
  pages; the old Guide and its mock-ups are archived.
- Read text content and a screenshot; layer names can be stale.
- A Spec binds layout, grouping, wording, states and colour by token. It is
  not pixel-exact: controls are native wx and differ between platforms.
  Stand-alone drop-downs are the system's own control and draw grey.
- Change the Figma frame and the code in the same task. Every user-facing
  string appears in Figma exactly as shipped. Record new decisions in the
  Decisions log.
- Draw with **Components** instances and **Foundations** variables only. Name a
  component's code counterpart in its description. Colour variable names
  mirror `ui_palette()`.

Standing rules:

- Low mental load: each step asks one thing. Do not merge steps or replace
  guidance with a wall of settings.
- Nothing moves: controls, buttons and status areas keep their position and
  size. Nothing appears, disappears or shifts with state; change content in
  place.
- Predictable buttons: a button always does the same thing and keeps its
  label.
- Glance, then detail: one status icon per step in the step list; the
  About-this-step box under it holds the description and a checklist with one
  line per concern. No other status areas.
- Four statuses. Green check: active and all good. Red X: a problem, or
  something required now is not set. Yellow !: required for a final board, not
  set, not blocking. Grey dash: the drafter's choice.
- Entry versus readout in tables: entry cells are white with a blue outline; a
  missing entry has a red outline and a red X in an always-reserved slot. A
  readout sharing a row with entries is flat grey; a fully read-only table is
  white with striped rows. Stand-alone fields use the normal border.
- Colour is deliberate: no monochrome or low-chrome styling. Selection uses
  the system highlight and status icons keep their colour.
- Motion aids continuity: clicking a checklist line fades a highlight outline
  in over the section, holds it for a few seconds and fades it out, with no
  layout change. Scroll smoothly inside the step's own scroll area; never
  jump. No blinking.
- Columns separate kinds of thing (pick, edit, see); inside a column, content
  reads top to bottom.
- Do not repeat a value already shown on the page.
- Units: entry fields carry their own unit; readouts follow the toolbar's
  mil / mm switch and are never rounded.
- Keep user-facing text short: state the condition and the action, nothing a
  button label already says. Internal identifiers, hashes and raw data appear
  only in Messages.
- Stock wxPython, KiCad's own icons and conventions only; nothing beyond what
  KiCad installs.

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
Mechanical placement owns one drawing-wide center tolerance, defaulting to 0.3
mil. The same validated value governs calculated RF-location, DC-pad and
auto-detected interposer-pad circle checks in controller review and native
backend revalidation. Interposer validation is optional drawing evidence: a
missing or partial pad-circle population produces a visible warning and Apply
continues, while a complete population requires an unambiguous one-to-one match.
A failed check
reports the feature identity, measured center deviation and configured limit;
multiple circles inside the limit remain an ambiguity rather than invoking a
nearest-circle heuristic. Accepted source UUID and complete drawing-revision
checks remain exact because they detect stale source input rather than placement
deviation; geometry-only matching is advisory.
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

Apply first materializes and verifies an isolated candidate, then durably stages
exact before/after project files. On the UI thread it reconciles the active BOARD,
verifies managed identities and designer-owned content, and publishes the staged
files. Schematic Editor must be closed while Apply runs. A separate checked
recovery journal is required because KiCad native Undo does not reliably restore
all saved group membership across supported versions; recovery requires every
project editor to be closed.

The root schematic is designer-owned. One deterministic hierarchical sheet entry
is the ownership boundary for generated child sheets and symbol libraries under
`.connector-interposer-managed/`; unrelated root content is preserved. This
follows KiCad's documented
[hierarchical schematic model](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#hierarchical-schematics).
Although KiCad describes
[Update PCB from Schematic](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#update-pcb-from-schematic)
as the normal forward-annotation path, this workflow uses bounded object-level
reconciliation so preservation of designer PCB content is reviewable. Imported
DXF geometry continues to enter through PCB Editor's native
[vector graphics import](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#importing_vector_graphics)
before the workflow inspects it.

Managed PCB identities are derived from stable project and logical identities,
so remove/reintroduce restores the same UUID. Designer tracks and vias survive a
netlist-only change only when an entire isolated exact-endpoint component connects
at least two affected pads and all boundary pads move to one new net. The route
retains UUID and geometry and changes only its net. Physical changes, incomplete
boundaries, zones and net-bearing shapes block instead of being guessed.
