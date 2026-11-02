# Connector to Interposer Workflow

Version **0.2.0**, testing release. The package targets KiCad 9.0.0 through
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

Apply operates on a new, separate output project directory. The source project
is read-only to the native backend. Save source edits before comparison: native
mechanical inspection and reconciliation use the saved PCB, not unsaved canvas
state. Do not open the output project in another editor during application or
recovery. After Apply, open the output project explicitly; continuing to save
an already-open source board does not update the output copy.

1. **Spreadsheet:** choose `.xlsx` or UTF-8 `.csv`, worksheet, header row and
   explicit ignored source rows. Header recognition supports arbitrary column
   order. Use the manual mapper for missing headers; column numbers start at 1.
   All seven fields must map uniquely. **View data…** opens the entire parsed
   table with source-row numbers, ignored rows and errors. Malformed included
   rows remain blocking errors; sorting has no electrical meaning.
2. **Layer rules:** map architecture layer names to enabled board copper layers
   and review netclass permissions. New combinations start allowed; existing
   restrictions survive subsequent imports and stackup additions.
3. **Validation:** select the approved interposer footprint and its reference,
   placement, and an approved standalone two-pin connector symbol from a
   `.kicad_sym` library. Supply the symbol's `Library:Symbol` ID. The selected
   interposer's electrical pad universe is authoritative; unused pads become
   ground. Literal external connector references such as `J101-1` are retained.
4. **Gangs:** select a gang, its approved `.kicad_mod`, pattern, units, pitches,
   row offsets, numbering, orientation and explicit pattern mirror. Each gang
   keeps independent settings. Library MPN metadata is displayed read-only; when
   absent, a project BOM MPN override is available. A missing MPN is reported as
   a warning and is never invented. The preview labels locations with indices and
   identifies location 1 as the anchor. Bulk copy affects only selected gangs
   and retains their anchors. Choose an approved RF bundle as the gang default;
   **Per-location RF assignments…** supports clock and artifact overrides for
   mixed routing layers. An artifact's saved signal-layer role must equal the
   workbook's mapped signal layer.
5. **DXF Anchors:** import the complete mechanical drawing through KiCad's
   native **File → Import → Graphics**, selecting a user layer, then save.
   The bounded association accepts one full `gang-pin` text label per native
   circle. If labels have a drafting offset, explicitly enter and accept the
   label X/Y offset from the circle; association remains one-to-one and is
   previewed before application. Missing, extra, duplicate or ambiguous circles block Review.
   Capture a selected native circle center or enter numeric location-1
   coordinates, then explicitly accept the anchor. Mounting holes use approved
   footprints and are board-only, excluded from BOM and position files.
6. **Board Outline:** explicitly enable this optional feature and capture
   imported outer-boundary segments and optional center-cutout segments
   separately. Review validates exact closure, intersections and ownership.
   Only managed outline objects are replaced; designer Edge.Cuts stay intact.
7. **Review Changes:** compare the full desired population, review additions,
   removals, placement/electrical/RF changes and conflicts, and choose an empty
   output directory outside the source project tree. Accept the current change
   list before Apply. Input changes make the preview stale. Affected designer
   routing and ownership conflicts block application.
8. **Apply / Results:** inspect the saved board, generated schematic hierarchy,
   individual-component BOM, registry, report and recovery journal in the output
   copy. Recovery requires a closed output project and matching expected file
   hashes; later edits are not silently overwritten.

The source `.connector-interposer-workflow.json` sidecar stores project identity
and UI drafts,
including mappings, ignored rows, layer policy, independent gang definitions,
artifact selections and accepted mechanical inputs. The exact accepted workbook
is snapshotted under `.connector-interposer-inputs`; selected parts, approved
symbols and RF artifacts remain pinned under `.connector-interposer-pinned`.
Generated project-local footprint
aliases avoid taking over designer library-table entries. Generated-file ownership,
instance UUIDs and immutable artifact pins are stored separately in the project.
Existing unowned schematics or conflicting library-table aliases are blocking
conflicts. Native annotation must preserve existing tool references; resetting
annotation can change mechanical identities and is not an accepted update path.

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
