# Connector to Interposer Workflow

Version **0.4.10**, testing release. The package targets KiCad 9.0.0 through
10.0.8; repository compatibility checks and the bounded runtime evidence must
be considered separately from fabrication or RF qualification.

This independent PCB Action Plugin consumes librarian-approved connector
footprints and symbols, interposer footprints, and optional immutable RF artifact
bundles. RF locations use individual signal-plus-ground/shield parts. DC
connectors use one selected single-unit multi-pin symbol and footprint; unused
DC pins remain explicitly unconnected.
It never invokes the BGA Interposer Wizard or an RF authoring tool and exposes
no footprint creation, recipe editing or variant-authoring controls.

Install through the repository's Plugin and Content Manager catalogue, or copy
this complete package directory into KiCad's user scripting `plugins` directory.
Restart PCB Editor or refresh plugins, then choose **Tools → External Plugins →
Connector to Interposer Workflow**. Save the project before opening the modeless
native wx dialog. The dialog follows the operating system light or dark appearance;
its task panels, assignment states, statuses, tables and previews derive their
colors from wx system colors. Python core modules also run without KiCad or wx
dependencies.

## Saved-state workflow

The dialog keeps edits in a current in-memory workflow. **Save workflow settings**
writes those settings, accepted inputs and source snapshot to the project
sidecar and asks before replacing an existing sidecar. **Delete workflow settings**
removes that saved sidecar while leaving the current dialog entries open.
**Reset workflow settings** returns the current dialog to its initial state while
preserving the project identity, detected stackup and any saved sidecar. None of
these settings actions updates the PCB or schematic. Layer assignments take effect
in the current workflow as soon as they are selected. **Apply** is the separate,
review-gated operation that creates the output project copy.

Apply operates on a new, separate output project directory. The source project
is read-only to the native backend. Save source edits before comparison: native
mechanical inspection and reconciliation use the saved PCB, not unsaved canvas
state. Do not open the output project in another editor during application or
recovery. After Apply, open the output project explicitly; continuing to save
an already-open source board does not update the output copy.

1. **Netlist source:** choose `.xlsx`, UTF-8 `.csv`, or delimited UTF-8 `.txt`.
   The workbook opens automatically, with the worksheet deliberately left blank
   and header row initially set to 1. Choose the netlist worksheet to detect its
   headers. Netlist worksheet, header row and optional excluded source rows
   remain editable. Map each required field with
   a dropdown containing only populated workbook columns. The selectors form
   the first row of an aligned live net table, so each assignment sits directly
   above the parsed values it produces. The assignment band stays visibly
   interactive: assigned cells are blue, missing cells are amber, conflicting
   cells are red, and persistent chevrons distinguish selectors from parsed
   read-only data. Unique exact header matches are
   assigned automatically after worksheet selection or a workbook reload.
   Changing worksheets resets and redetects the assignments; changing only the
   header row preserves assignments by physical source-column position and
   updates their displayed headings. When the mapping is otherwise blank,
   choosing the Signal column
   fills the remaining six fields from consecutive populated columns. All seven
   fields must map uniquely. **Open full table…** shows source-row numbers
   and the seven mapped workflow fields in workflow order for included rows
   only. The grid gutter is a sequential preview index while **Source row** is
   the original worksheet row. Its columns fit the longest header or displayed
   value, and its window is bounded to the active display with a 1600×900
   ceiling. Malformed included rows remain blocking
   errors; sorting has no electrical meaning. **Reload**
   rereads an edited workbook at the same path while retaining the selected
   worksheet when it still exists, header row and excluded rows. It redetects
   mappings from the new headers and invalidates any prior review.
2. **Netclass type:** classify every imported netclass as RF or DC. **Auto
   assign** fills blank rows only. Exact `J#` references identify DC, exact
   `J###` references identify RF, and the exact `DC` netclass identifies DC.
   Unrecognized references remain manual; mixed or conflicting evidence blocks
   review. Manual choices are preserved.
3. **Layer assignment:** map each distinct netlist layer name to an enabled
   KiCad copper layer. Each selection updates the current workflow immediately;
   there is no separate acceptance button. The KiCad-layer cells use the same
   amber-missing and blue-assigned interaction treatment as Netlist source,
   including persistent dropdown chevrons. **Auto assign** fills blank rows only
   when numeric matching is unambiguous. `F.Cu`, `In1.Cu` and `B.Cu` represent
   physical copper layers 1, 2 and the final layer; custom layer names use their
   single embedded number directly. Names with zero or multiple number runs and
   duplicate candidates remain blank. These assignments drive validation
   and RF artifact compatibility.
4. **Routing permissions:** review the workflow's current netclass/layer
   validation policy. Checkbox changes update the current workflow immediately;
   there is no separate acceptance button. New combinations start allowed and
   produce a review notice; existing restrictions
   survive subsequent imports and stackup additions. A disallowed combination
   blocks workflow validation and Review changes. This release does not generate
   KiCad custom routing rules or modify the project's `.kicad_dru` file. A later
   per-net redesign will add search, filters and previewed rule updates.
5. **Connector assignments:** the workbook supplies the single interposer
   reference and connector identities. Select an interposer, an independent
   two-pin connector symbol for each RF gang, and one single-unit multi-pin
   symbol for each DC connector from KiCad's loaded project or global libraries.
   Each symbol must contain footprint filters; its
   footprint cell lists only filtered library parts whose electrical pad
   numbers exactly match the symbol pins. Selections update the workflow
   immediately. The visually separated **All** row in either connector table
   applies its symbol or footprint choice to every connector of that type,
   including filtered rows. Editing an individual row clears the **All** action
   controls without reverting assignments already made. The workflow instantiates these librarian-authored parts; it
   does not generate library symbols or footprints. Placement and BOM overrides
   are not entered on this page.
6. **Define RF gangs:** select an RF gang from the searchable netlist-derived list.
   On **Pattern**, add or remove rows, set the number of positions and X offset
   for each row, then set position and row pitch. Every linear input has its own
   `mm`, `mil`, `inch` or `um` selector, defaulting to `mil`; a suffix such as
   `10mm` or `10 mm` selects that unit when the field is committed. Row offsets
   keep the value and unit selector together in one cell. On **Numbering**,
   choose row-major, serpentine, column-major or custom numbering; set pattern
   rotation independently from connector orientation; choose the mounting side
   and optional layout mirror. The numbered preview keeps location 1 marked as
   the DXF anchor. Valid changes update the gang immediately. New gangs remain
   **Not defined** until edited; incomplete or population-mismatched entries are
   **Invalid**. Copying settings never changes gang anchors or selected library parts.
7. **RF transitions:** optionally assign one immutable published transition to each unique
   `(netclass, mapped KiCad layer, connector footprint)` requirement. One row can
   cover locations in several gangs. Unassigned requirements do not block Review
   Changes or board generation. Once assigned, the workflow validates the exact footprint
   revision and mount type, board side, ordered copper stack, allowed netclass,
   signal layer and artifact hash before Review changes. **Refresh transition
   library** discovers published `.gppo-rf.json` files beneath the project;
   **Browse artifact…** can select a catalog artifact elsewhere. **Open RF
   Transition Designer…** is a disabled placeholder in this release.
8. **Connector placement:** accept the interposer placement, then import the complete mechanical drawing through KiCad's
   native **File → Import → Graphics**, selecting a user layer, then save.
   The bounded association accepts one full `gang-pin` text label per native
   circle. If labels have a drafting offset, explicitly enter and accept the
   label X/Y offset from the circle; association remains one-to-one and is
   previewed before application. Missing, extra, duplicate or ambiguous circles block Review.
   Capture a selected native circle center or enter numeric location-1
   coordinates, then explicitly accept the RF anchor. For DC, select the pin 1
   circle and enter orientation and board side. The native backend measures the
   footprint's transformed pad-1 offset and places that pad at the circle center
   regardless of library origin. Every DC electrical pad, including NC pins,
   must match one imported circle within the current one-nanometre internal
   comparison tolerance. Mounting holes use approved
   footprints selected from the loaded KiCad libraries and are board-only,
   excluded from BOM and position files.
9. **Board outline:** explicitly enable this optional feature and capture
   imported outer-boundary segments and optional center-cutout segments
   separately. Review validates exact closure, intersections and ownership.
   Only managed outline objects are replaced; designer Edge.Cuts stay intact.
10. **Review changes:** compare the full desired population, review additions,
   removals, placement/electrical/RF changes and conflicts, and choose an empty
   output directory outside the source project tree. Accept the current change
   list before Apply. Input changes make the preview stale. Affected designer
   routing and ownership conflicts block application.
11. **Apply / results:** inspect the saved board, generated schematic hierarchy,
   individual-component BOM, registry, report and recovery journal in the output
   copy. Recovery requires a closed output project and matching expected file
   hashes; later edits are not silently overwritten.

The source `.connector-interposer-workflow.json` sidecar stores project identity
and workflow settings,
including mappings, excluded-row selections, netclass types, layer policy,
independent RF gang definitions, per-connector symbol/footprint assignments,
DC pin-1 anchors, the interposer assignment and placement,
requirement-keyed RF transition selections and accepted mechanical inputs. The exact accepted workbook
is snapshotted under `.connector-interposer-inputs`; selected parts, approved
symbols and RF artifacts remain pinned under `.connector-interposer-pinned`.
Generated project-local footprint
aliases avoid taking over designer library-table entries. Generated-file ownership,
instance UUIDs and immutable artifact pins are stored separately in the project.
Existing unowned schematics or conflicting library-table aliases are blocking
conflicts. Native annotation must preserve existing tool references; resetting
annotation can change mechanical identities and is not an accepted update path.

The library picker displays `LibraryNickname:PartName` identities and resolves
KiCad 9 and 10 global, project and nested library tables using KiCad's own path
variable expansion. Project entries take precedence over global entries. The
workflow retains the resolved source and content hash internally so a selected
library revision remains deterministic without exposing filesystem paths as the
primary user interface. Connector assignment footprint dropdowns apply the
selected symbol's `ki_fp_filters` and require exact symbol-pin to footprint-pad
compatibility. RF transition artifacts remain separate immutable
catalog files in this release. This release browses native `.pretty` footprint
libraries and `.kicad_sym` symbol libraries; database, legacy and other
non-native library types are not offered.

Native board inspection and application remain in an isolated subprocess. The
workflow discovers that interpreter relative to the active KiCad runtime:
AppImage `AppRun` on Linux, the active Python installation prefix, or the
bundled `python.exe`/`pythonw.exe` beside the PCB Editor on Windows. Each
candidate must import `pcbnew` and report the active editor's exact KiCad
version before use. Automatic discovery never selects an interpreter merely
because it appears on the system `PATH`; an explicit constructor override is
validated by the same probe.

## Limits of this testing release

The controlled project-copy apply/recovery boundary is intentional. Transparent
live-editor transactions and a generic magnetic point picker have not passed
interaction/recovery validation. The initial schematic presentation uses stable
label-based RF gang sheets, one selected-symbol instance per DC connector, and
a tool-owned split interposer signal/ground
organization. The selected interposer symbol supplies the pin, filter and BOM
metadata contract and is pinned for provenance; its graphical body is not copied
into the generated split presentation. A fully explicit presentation is a
future direction. DXF labels split into separate
gang-only and pin-only texts require unambiguous full identity labels before
use. DC support is limited to single-unit symbols; a future tolerance control
may replace the current one-nanometre pad-circle comparison. RF objects stop at
the signal via; no signal route is generated beyond the
handoff. Saved geometry and native round trips do not establish impedance,
fabricator stackup approval, manufacturing qualification or full board DRC.
