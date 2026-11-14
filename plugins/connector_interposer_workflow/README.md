# Connector to Interposer Workflow

Version **0.7.10**, testing release. The package targets KiCad 10.0.0 through
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
its guidance box, assignment states, statuses, tables and previews derive their
colors from wx system colors. Python core modules also run without KiCad or wx
dependencies.

The window opens at 1280 × 800. A toolbar across the top uses KiCad's own icons:
previous and next step, save, delete and reset workflow settings, refresh
libraries, a mil / mm
switch for read-only values, and a toggle for the message log. The step list on
the left groups the twelve steps under Nets & Layers, Parts, Geometry and Finish
and shows one status icon per step: a green check when the step is complete, a
red X when something required is missing or wrong, a yellow exclamation mark
when something needed for a final board is not yet set, and a grey dash when the
step is the designer's choice. Under the list, the About box describes the open
step and lists what it needs, one line per concern. Click a line to have its
control outlined for a few seconds. The message log opens below
the window without moving the step; it records every status line for copying.
Closing the window with unsaved workflow settings asks whether to save them.

## Saved-state workflow

The dialog keeps edits in a current in-memory workflow. **Save workflow settings**
writes those settings, accepted inputs and source snapshot to the project
sidecar and asks before replacing an existing sidecar. **Delete workflow settings**
removes that saved sidecar while leaving the current dialog entries open.
**Reset workflow settings** returns the current dialog to its initial state while
preserving the project identity, detected stackup and any saved sidecar. None of
these settings actions updates the PCB or schematic. Layer assignments take effect
in the current workflow as soon as they are selected. **Apply** is the separate,
review-gated operation that updates the open project.

If the saved sidecar cannot be loaded, typically because a different Workflow
version wrote it, opening Workflow asks whether to **Delete and open** or
**Keep and close**. Deleting removes only that sidecar and starts with new
settings; the project identity is carried over when the old file still states
one, so previously applied managed objects stay recognized. Keeping leaves the
file untouched and does not open the dialog.

Compare auto-saves the open PCB. Apply verifies a complete candidate update
without changing the project, then reconciles the active board and publishes the staged
project files. Close Schematic Editor before applying; PCB Editor remains open.
Native mechanical inspection and comparison use the saved PCB, not unsaved
canvas state. A persistent journal records exact before/after files. Recovery
requires all KiCad editors for the project to be closed and refuses to overwrite
files changed after the transaction.

1. **Netlist source:** choose `.xlsx`, UTF-8 `.csv`, or delimited UTF-8 `.txt`.
   The workbook opens automatically, with the worksheet deliberately left blank
   and header row initially set to 1. Choose the netlist worksheet to detect its
   headers. Netlist worksheet, header row and optional excluded source rows
   remain editable. Click each assignment cell to open a native menu containing
   only populated workbook columns. Dismissing the menu makes no change and
   leaves no grid editor active. The selectors form
   the first row of an aligned live net table, so each assignment sits directly
   above the parsed values it produces. The assignment band stays visibly
   interactive: assigned cells are blue, missing cells are amber, conflicting
   cells are red, and persistent chevrons distinguish selectors from parsed
   read-only data. The table ends after its defined columns instead of expanding
   across the page. Unique exact header matches are
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
   review. Manual choices are preserved. Type cells use the same one-click
   native assignment menu, colored state and click-away behavior as Netlist source.
3. **Layer assignment:** map each distinct netlist layer name to an enabled
   KiCad copper layer. Layer cells use the same one-click native assignment menu
   and colored state as Netlist source. Each selection updates the current workflow immediately;
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
   numbers exactly match the symbol pins. The interposer and DC tables show
   **Pins used** beside **Symbol pins**. An interposer symbol must contain every
   used interposer pin, an RF connector symbol must expose exactly two distinct
   pins, and a DC symbol must be single-unit and contain every used connector
   pin; unused DC symbol pins remain allowed. Invalid selected symbols stay
   visible in amber, keep their footprint cell disabled and do not count as
   assigned. RF gangs and DC connectors have independent filters. Selections update the workflow
   immediately. Footprints use a one-click table-integrated menu; long symbol
   and footprint names are ellipsized inside bounded columns. Hovering a clipped
   assignment cell shows its complete value; fully visible values have no
   tooltip. Footprint menus begin with **Clear assignment**, followed by the
   symbol-filtered library choices. Each composed
   connector table sits inside one bordered window-colour surface with a small
   inset. Equal-height bordered cells align displayed connector data with its
   assignment controls, and one clean divider separates the **All** action row
   from connector data. The visually
   separated **All** row in either connector table
   applies its symbol or footprint choice to every connector of that type,
   including filtered rows. Editing an individual row clears the **All** action
   controls without reverting assignments already made. The workflow instantiates these librarian-authored parts; it
   does not generate library symbols or footprints. Placement and BOM overrides
   are not entered on this page.
6. **RF transitions:** optionally assign one immutable published transition to each unique
   `(netclass, mapped KiCad layer, connector footprint)` requirement. One row can
   cover locations in several gangs. Unassigned requirements do not block Review
   Changes or board generation. Once assigned, the workflow validates the exact footprint
   revision and mount type, board side, ordered copper stack, allowed netclass,
   signal layer and artifact hash before Review changes. **Refresh libraries**
   in the toolbar discovers published `.gppo-rf.json` files beneath the project;
   **Browse artifact…** can select a catalog artifact elsewhere. **Open RF
   Transition Designer…** is a disabled placeholder in this release.
7. **Define RF gangs:** select an RF gang from the searchable netlist-derived list.
   On **Pattern**, add or remove rows, set the number of positions and X offset
   for each row, then set position and row pitch. Every linear input has its own
   `mm`, `mil`, `inch` or `um` selector, defaulting to `mil`; a suffix such as
   `10mm` or `10 mm` selects that unit when the field is committed. Row offsets
   keep the value and unit selector together in one cell. These value-entry
   cells activate on one click and commit a complete value on Enter, focus
   departure, unit selection or cell navigation. A clipped value-entry cell
   exposes its complete typed value on hover. On **Numbering**,
   choose row-major, serpentine, column-major or custom numbering; set pattern
   rotation independently from connector orientation; choose the mounting side
   and optional layout mirror. The numbered preview keeps location 1 marked as
   the DXF anchor. The preview keeps the gang centroid at its center while
   pattern rotation changes, refits after every render and reduces circle radius
   and label font size when necessary to keep dense locations and multi-digit
   identifiers distinct. A short tick on every circle shows the connector
   orientation, and numbers are drawn inside the circles or, when one would not
   fit, beside all of them. Valid changes update the gang immediately; typed custom
   numbering, pattern rotation and connector orientation commit on Enter or focus
   departure, not on each keystroke. A new gang starts empty, with no rows and
   no pitch, and stays **Not defined** until its rows, pitch and numbering match
   the netlist; incomplete or population-mismatched entries are
   **Invalid**. The step has no message area: an entry that is missing or
   wrong turns red where it is, and the total beside the row buttons reads
   `N of M positions` against the netlist, in red when they differ. Hover the
   total for the exact reason; it is also written to the message log. Copying settings never changes gang anchors or selected library parts.
8. **Mechanical placement:** import the complete mechanical drawing through
   KiCad's native **File → Import → Graphics** and save the board. Choose its
   **Drawing layer** (default `User.Drawings`). The workflow reads that layer
   of the saved board by itself when the step opens and shows how many circles
   it found. The **Interposer**, **RF gangs** and **DC connectors** tabs each
   list their parts with full-precision X and Y, side, rotation and status.
   Select a row, then:
   - **Pick in Editor**, then click the part's circle in the PCB Editor (the
     interposer origin, RF location 1 or DC pin 1). If the circle is not in
     the saved board yet, save the board and pick again.
   - **Use Proposed Match** when the workflow found the one place where the
     part's whole pattern fits free drawing circles. The proposed circles are
     highlighted in the PCB Editor. When a part fits several places, pick one.
   - **Show in Editor** highlights the circles a placed part occupies.

   Interposer and DC connector side and rotation are set under their tables;
   RF gang side and rotation come from Define RF gangs. On **Mounting holes**,
   enter a reference, choose a footprint and **Add**, then place the row with
   **Pick in Editor**. Mounting holes use approved footprints from the loaded
   KiCad libraries and are board-only, excluded from BOM and position files.
   **Advanced** holds the centre tolerance (default 0.3 mil): how far a
   drawing circle may be from its calculated position and still count as a
   match. It applies to calculated RF locations, every DC electrical pad
   (including NC pins), and interposer pads when the drawing contains a
   complete interposer pad-circle set. An absent or partial interposer set
   produces a Review warning and does not block Apply. Missing RF or DC
   feature circles and duplicate or ambiguous matches block Review, reporting
   the measured centre deviation and the limit. Drawing text is never used for
   identity or placement. Placements belong to the drawing they were made on:
   a circle that is still the same object in the same place carries over when
   the drawing is read again, while a moved or re-imported circle shows
   **Pick again**. Apply aligns transient live drawing UUIDs to the reviewed
   saved identities so a later PCB Editor save does not make an unchanged
   drawing look new.
9. **Interposer microvias:** optionally choose one eligible predefined Board
   Setup via size for all interposer locations. The workflow creates native,
   locked, adjacent-layer microvias coincident with the placed interposer pads.
   Ground pins stack through every copper-layer span. Signal pins stack from the
   mounting side to the assigned routing layer, omit the next span, and continue
   as GND through the remaining spans. An outside-layer signal assignment,
   missing current size, or size that violates the board microvia minima or
   interposer-pitch clearance blocks Review. Generated vias have no IPC-4761
   tenting, covering, plugging, filling or capping setting. A later run
   reconciles size, placement, side, routing-layer and enable/disable changes;
   manual changes to owned objects are reported as ownership conflicts.
10. **Board outline:** explicitly enable this optional feature and capture
   imported outer-boundary segments and optional center-cutout segments
   separately. Review validates exact closure, intersections and ownership.
   Only managed outline objects are replaced; designer Edge.Cuts stay intact.
11. **Review changes:** click **Review Changes**. Review reads the saved
   board, so it first asks whether to save the PCB. **Problems** lists what
   was found, each with its level (Error, Conflict or Warning) and the step
   that fixes it; select a row and click **Go to Step**. Errors and conflicts
   block Apply; warnings do not. **Changes Apply will make** summarises what
   will happen (parts to add, change or remove, interposer microvias, board
   outline, project files to write or remove); select a summary line to list
   its items with what they are before and after. Changing any setting makes
   the review out of date. Physical or topology changes affecting designer
   copper block Apply. An unchanged, isolated exact-endpoint designer
   track/via component may keep its UUID and geometry when all boundary pads
   move together to one new net; zones, net-bearing shapes and ambiguous
   routes remain blocking.
12. **Apply / results:** **Apply to Board…** shows what is about to happen and
   asks once. Progress then names each stage: staging the changes, verifying
   the staged board, updating the open board, writing project files. The
   result lists the board, the report and the recovery journal. The complete
   file transaction is staged before the live board changes. If Apply is
   interrupted after the open board was updated, close the PCB Editor without
   saving and use the **Recovery** tab with the project closed in every KiCad
   editor. The full records of a review or an Apply are in the message log
   (the **i** button), not in these tables.

The source `.connector-interposer-workflow.json` sidecar stores project identity
and workflow settings,
including mappings, excluded-row selections, netclass types, layer policy,
independent RF gang definitions, per-connector symbol/footprint assignments,
DC pin-1 anchors, the interposer assignment, placement and mounting side,
the optional predefined interposer-microvia size,
requirement-keyed RF transition selections and accepted mechanical inputs,
including the drawing-feature center tolerance. The exact accepted workbook
is snapshotted under `.connector-interposer-inputs`; selected parts, approved
symbols and RF artifacts remain pinned under `.connector-interposer-pinned`.
Generated project-local footprint
aliases avoid taking over designer library-table entries. Generated-file ownership,
instance UUIDs and immutable artifact pins are stored separately in the project.
The project root schematic remains designer-owned. The workflow maintains one
stable root-sheet entry and writes its generated sheets and symbol libraries
under `.connector-interposer-managed/`; unrelated root sheets and symbols survive
reruns. Manual changes to the managed root-sheet entry or generated child files
are ownership conflicts rather than being overwritten. Conflicting library-table aliases are blocking
conflicts. Native annotation must preserve existing tool references; resetting
annotation can change mechanical identities and is not an accepted update path.

This ownership model follows KiCad's documented
[hierarchical schematic](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#hierarchical-schematics)
structure. KiCad documents
[Update PCB from Schematic](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#update-pcb-from-schematic)
as the normal schematic-to-board transfer; the workflow instead performs its
bounded, reviewed native reconciliation so designer PCB content can be verified
object by object. DXF remains a user-driven native
[vector graphics import](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#importing_vector_graphics)
before inspection.

The library picker displays `LibraryNickname:PartName` identities and resolves
KiCad 10 global, project and nested library tables using KiCad's own path
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

Apply is a staged live-project transaction, not a native KiCad Undo unit. Close
Schematic Editor for Apply and close all project editors for recovery. A generic
magnetic point picker has not passed interaction validation. The initial schematic presentation uses stable
label-based RF gang sheets, one selected-symbol instance per DC connector, and
a tool-owned split interposer signal/ground
organization. The selected interposer symbol supplies the pin, filter and BOM
metadata contract and is pinned for provenance; its graphical body is not copied
into the generated split presentation. A fully explicit presentation is a
future direction. DXF text is retained only for visual context; circle identity
comes from explicit center, RF location-1, DC pin-1 and mounting-hole selections
plus geometric matching. DC support is limited to single-unit symbols. RF objects stop at
the signal via; no signal route is generated beyond the handoff. Interposer
microvia generation does not prove plane connectivity, stackup manufacturability
or production acceptance. Saved geometry and native round trips do not establish
impedance, fabricator stackup approval, manufacturing qualification or full
board DRC.
