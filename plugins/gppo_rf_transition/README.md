# GPPO RF Transition Designer

Independent librarian authoring ActionPlugin, version **0.1.2 testing**. Supports
the repository's KiCad **10.0.0 through 10.0.8** range. Exact runtime results and
scope are recorded in the repository's
`docs/connector-interposer-implementation.md`.
This is a geometry authoring prototype, not an impedance,
electromagnetic or manufacturing qualification.

Install this directory under KiCad's scripting `plugins` directory, or install
its independent PCM package. Restart PCB Editor and select **Tools → External
Plugins → GPPO RF Transition Designer**. The tool never modifies the open board
or central connector/interposer footprint sources. No other authoring plugin or
Connector to Interposer Workflow installation is required.

## Authoring

1. Choose **Select approved GPPO…** and select an existing librarian-approved
   `.kicad_mod`. State the signal pad, shield pads and mount side. Selection
   asserts that the source has librarian approval; hashes establish integrity,
   not an approval authority or a digital signature.
2. Edit the complete recipe in the native dialog. The initial recipe uses a
   four-layer board, an In2.Cu handoff, two adjacent signal microvias for an
   SMD source, nine through ground vias on a 40°–320° arc, 0.25 mm exit trace
   width and 0.20 mm ground-pour gap. PTH sources use their existing plated
   signal barrel and set `signal_via` to `null` automatically.
3. Set stackup, mapped copper routing layer, netclasses, via spans and dimensions,
   layer-specific copper and antipads. The JSON editor exposes every saved
   setting without an external program. Mount/part/pad roles come from the
   inspected base source. Architecture worksheet layer aliases do not belong
   in the artifact.
   Changing the copper stackup requires selecting the approved source again so
   native inspection covers every actual layer, including existing PTH lands.
4. **Validate / Preview** checks geometry and compatibility. The layer selector
   filters the preview; the arrow shows the default clock's exit direction.
   The capability report identifies supported runtime and explicit limitations.
   Any edit invalidates publication until the new recipe passes validation.
5. **Publish immutable library bundle…** writes an exact artifact with recipe,
   source hash and provenance, plus a catalog `manifest.json`. It writes no
   library footprint because the base source already supplies that geometry;
   routing vias are actual declarative board objects, never footprint graphics.

**Open artifact…** checks integrity and reproduces the payload before reopening
its recipe. An existing revision is immutable: choose **Clone variant** for a
new artifact identity and parent pin, or **New revision** to retain identity and
publish the next revision. Old files and project pins remain unchanged. A
repeated publication of identical content leaves the artifact bytes and mtime
unchanged. Reusing an identity/revision with different content is blocked.

## Recipe geometry

Coordinates are millimetres, +X right, +Y up, with CCW positive angles. Ground
arc angles are relative to the local +X trace exit. Decreasing endpoints wrap
across zero: 310°→50°, count 5 gives 310°, 335°, 0°, 25°, 50°. Partial arcs
include both endpoints and require at least two vias. A ±360° sweep creates a
full circle without duplicating the endpoint. Equal start/end angles are
ambiguous and blocked. Clock is a default instance pose, saved separately from
local geometry; a consumer applies it once to the complete transition.

The ground ring is an **arc of ground vias**, not an added continuous copper
ring. Each configured location has the explicitly requested via span(s).
Signal microvia stacks may have any supported depth through adjacent layers.
An SMD signal stack must form one continuous mount-to-routing-layer chain.
Explicit through or blind/buried spans are also modeled; through vias must
span the board. All vias need pad diameter greater than drill diameter.

`tuned_signal_copper` requires a diameter or `null` on every copper layer.
Additional circular, net-assigned native copper appears only when the requested
diameter exceeds the existing base/via minimum. `null` means no **added** copper;
it never removes or promises omission of base footprint or via lands. Optional
`tuned_ground_copper` enlarges existing ground-via lands on specified layers.
No artifact shrinks or edits an approved central footprint.

Antipad definitions cover exactly their stated span and include all signal
lands, barrels and routing layers. They must include signal copper plus the
configured ground-pour gap. Antipads use circumscribed polygon rule areas with
at most 0.001 mm radial overshoot; they never undersize the circular exclusion.

The signal-layer exit corridor starts at the signal via, extends along +X to
the outermost ground-ring land and has half-width `trace_width/2 +
ground_pour_gap`. Its rule area excludes copper pours, while manually routed
traces remain subject to ordinary project rules. Ground pad, hole/barrel and
added-copper envelopes that span this layer must stay outside the corridor.
Tangency is accepted and intrusion blocks generation. Ground stacks wholly
above/below the routing layer may share this XY region only when they have no
land/barrel/overlay on that layer. For an L3 escape across M5–M1, explicitly
omit L2–L3 and L3–L4 ground spans and their L3 overlays; L1–L2 may remain.
Unsatisfiable requests are reported; parameters are never silently adjusted.

No signal trace is generated, inside or beyond the transition. The signal via
or existing PTH pin is the handoff for designer-owned signal routing.

## Supported sources and limits

Native inspection requires a single centered circular signal pad, plated PTH
or mount-side SMD, with explicit distinct shield pad identifiers. It rejects
NPTH signal holes, noncircular/custom signal lands, unclassified electrical
pads, unclassified source copper graphics and ambiguous roles. The actual
experimental B012-L13-01 source derived from the supplied drawing is supported:
0.381 mm plated hole, 0.508 mm minimum signal land and a custom annular shield.
The shield's single unfilled circle primitive is recognized as an exact
annulus, including its opening; it is never inspected as the signal pad.
Other shield shapes use conservative bounding circles and may block otherwise
feasible same-layer exits pending librarian-specific geometry support.

Only **untreated** via fabrication is currently supported. Filled, capped or
plugged treatment is an explicit blocking error, rather than a silently dropped
annotation. KiCad 10 has richer native via treatment metadata; this prerelease
does not claim to have qualified that renderer path. PTH unused internal land
removal and per-layer custom padstacks remain the base source's responsibility;
the tool conservatively retains their minimum-copper envelope. It does not
certify dielectric data, impedance, assembly tolerances, fabrication feasibility
of arbitrary microvia stacks or complete rendered/exported copper equivalence.

The catalog is a simple directory index. Publish from one librarian process at
a time. Artifact creation is exclusive and complete-file publication prevents
partial immutable files; the catalog is replaced separately and is not a
multi-writer transaction or an atomic library/project commit.

## File/API contract

`model.py` has no KiCad or Workflow imports and provides `default_recipe`,
`generate_payload`, `make_artifact`, `load_artifact`, `clone_artifact`,
`revise_artifact` and `publish_bundle`. `native.read_base_part` reads source
geometry in KiCad and returns a pinned descriptor. The native ActionPlugin
class is `GPPORFTransitionDesignerPlugin`, registered as `PLUGIN` on KiCad import.

The versioned `gppo.artifact/1.0` envelope and `gppo.rf/1.0` payload contain only
data. A consumer binds explicit net/layer roles and materializes supported saved
objects without importing this plugin or executing a recipe. Supported payloads
can remain consumable when a newer generator's recipe cannot be reopened.
Workflow owns placed instances, pins, UUID registries and reconciliation; this
tool owns only librarian-authored reusable recipes and artifacts.

Run `scripts/test_kicad.sh` and repository PCM checks before release. Focused
tests are in `tests/test_gppo_rf_transition.py`; they cover exact arc count,
native source read/round-trip, deep stacks, omission semantics, L3 corridor
fixtures, tangency/intrusion, hash/schema rejection, clone/revision provenance
and immutable no-op publication.
