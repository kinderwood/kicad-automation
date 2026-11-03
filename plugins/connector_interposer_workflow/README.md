# Connector to Interposer Workflow

Version **0.3.7**, testing release. The package targets KiCad 9.0.0 through
10.0.8; repository compatibility checks and the bounded runtime evidence must
be considered separately from fabrication or RF qualification.

This independent PCB Action Plugin consumes librarian-approved two-pin connector
footprints and symbols, interposer footprints, and optional immutable RF artifact
bundles. The validated connector profile is currently a signal contact plus a
ground/shield contact; broader DC and multi-contact profiles are future work.
It never invokes the BGA Interposer Wizard or an RF authoring tool and exposes
no footprint creation, recipe editing or variant-authoring controls.

Install through the repository's Plugin and Content Manager catalogue, or copy
this complete package directory into KiCad's user scripting `plugins` directory.
Restart PCB Editor or refresh plugins, then choose **Tools → External Plugins →
Connector to Interposer Workflow**. Save the project before opening the modeless
native wx dialog. Python core modules also run without KiCad or wx dependencies.

## Saved-state workflow

The dialog keeps edits in a current in-memory workflow. **Save workflow settings**
writes those settings, accepted inputs and source snapshot to the project
sidecar; it does not update the PCB or schematic. Layer assignments take effect
in the current workflow as soon as they are selected. **Apply** is the separate,
review-gated operation that creates the output project copy.

Apply operates on a new, separate output project directory. The source project
is read-only to the native backend. Save source edits before comparison: native
mechanical inspection and reconciliation use the saved PCB, not unsaved canvas
state. Do not open the output project in another editor during application or
recovery. After Apply, open the output project explicitly; continuing to save
an already-open source board does not update the output copy.

1. **Netlist Source:** choose `.xlsx`, UTF-8 `.csv`, or delimited UTF-8 `.txt`.
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
2. **Layer assignment:** map each distinct netlist layer name to an enabled
   KiCad copper layer. Each selection updates the current workflow immediately;
   there is no separate acceptance button. These assignments drive validation
   and RF artifact compatibility.
3. **Routing permissions:** review the workflow's current netclass/layer
   validation policy. Checkbox changes update the current workflow immediately;
   there is no separate acceptance button. New combinations start allowed and
   produce a review notice; existing restrictions
   survive subsequent imports and stackup additions. A disallowed combination
   blocks workflow validation and Review Changes. This release does not generate
   KiCad custom routing rules or modify the project's `.kicad_dru` file. A later
   per-net redesign will add search, filters and previewed rule updates.
4. **Validation:** select the approved interposer footprint and an approved
   standalone two-pin connector symbol from KiCad's loaded project or global
   libraries, then enter the interposer reference and placement. The selected
   interposer's electrical pad universe is authoritative; unused pads become
   ground. Literal external connector references such as `J101-1` are retained.
5. **Gangs:** select a gang, its approved footprint from KiCad's loaded
   libraries, pattern, units, pitches,
   row offsets, numbering, orientation and explicit pattern mirror. Each gang
   keeps independent settings. Library MPN metadata is displayed read-only; when
   absent, a project BOM MPN override is available. A missing MPN is reported as
   a warning and is never invented. The preview labels locations with indices and
   identifies location 1 as the anchor. Bulk copy affects only selected gangs
   and retains their anchors. Choose an approved RF bundle as the gang default;
   **Per-location RF assignments…** supports clock and artifact overrides for
   mixed routing layers. An artifact's saved signal-layer role must equal the
   workbook's mapped signal layer.
6. **DXF Anchors:** import the complete mechanical drawing through KiCad's
   native **File → Import → Graphics**, selecting a user layer, then save.
   The bounded association accepts one full `gang-pin` text label per native
   circle. If labels have a drafting offset, explicitly enter and accept the
   label X/Y offset from the circle; association remains one-to-one and is
   previewed before application. Missing, extra, duplicate or ambiguous circles block Review.
   Capture a selected native circle center or enter numeric location-1
   coordinates, then explicitly accept the anchor. Mounting holes use approved
   footprints selected from the loaded KiCad libraries and are board-only,
   excluded from BOM and position files.
7. **Board Outline:** explicitly enable this optional feature and capture
   imported outer-boundary segments and optional center-cutout segments
   separately. Review validates exact closure, intersections and ownership.
   Only managed outline objects are replaced; designer Edge.Cuts stay intact.
8. **Review Changes:** compare the full desired population, review additions,
   removals, placement/electrical/RF changes and conflicts, and choose an empty
   output directory outside the source project tree. Accept the current change
   list before Apply. Input changes make the preview stale. Affected designer
   routing and ownership conflicts block application.
9. **Apply / Results:** inspect the saved board, generated schematic hierarchy,
   individual-component BOM, registry, report and recovery journal in the output
   copy. Recovery requires a closed output project and matching expected file
   hashes; later edits are not silently overwritten.

The source `.connector-interposer-workflow.json` sidecar stores project identity
and UI drafts,
including mappings, excluded-row selections, layer policy, independent gang definitions,
artifact selections and accepted mechanical inputs. The exact accepted workbook
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
primary user interface. RF transition artifacts remain separate immutable
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
label-based gang sheets and interposer signal/ground organization; a fully
explicit presentation is a future direction. DXF labels split into separate
gang-only and pin-only texts require unambiguous full identity labels before
use. RF objects stop at the signal via; no signal route is generated beyond the
handoff. Saved geometry and native round trips do not establish impedance,
fabricator stackup approval, manufacturing qualification or full board DRC.
