# Workflow boundaries

The native modeless wx dialog follows Netlist Source → Layer rules → Validation →
Gangs → DXF Anchors → Board Outline → Review Changes → Apply / Results. A
read-only virtual grid displays the complete parsed workbook in a separate
popup. Every page begins with a blue task-description panel whose title exactly
matches its navigation entry. No browser, webview, authoring wizard or generative recipe editor is
part of this package's layout flow.

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

Library parts are immutable read inputs selected by their loaded KiCad library
identities. The UI resolves global, project and nested library tables, while
project snapshots preserve exact selected revisions rather than newly authored
variants. RF payloads are declarative;
opaque recipes are retained as provenance and never executed. Per-location
artifact overrides accommodate different signal layers within the same gang;
the workflow rejects incompatible saved signal-layer roles rather than
silently retuning geometry.

Apply materializes an isolated project copy and verifies saved native geometry,
nets and groups before journaled publication of the output files. The open
source editor is never a concurrent writer to that output. A separate checked
recovery journal is required because KiCad native Undo does not reliably restore
all saved group membership across supported versions. A new destination is
required for each non-noop apply; in-place live-editor mutation is outside this
release boundary.
