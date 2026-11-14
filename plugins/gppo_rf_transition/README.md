# GPPO RF Transition Designer

Independent librarian authoring ActionPlugin, version **0.4.1 testing**. Supports
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

The physical rules this tool implements are the owner's decision record in
`docs/rf-transition-contract.md`. This README describes how to use the tool
and the names of its settings.

## Authoring

1. Choose **Select approved GPPO…** and select an existing librarian-approved
   `.kicad_mod`. State the signal pad, the ground pad and the mount side;
   the offered `1;2;front` is the contract's default. Selection asserts that
   the source has librarian approval; hashes establish integrity, not an
   approval authority or a digital signature. The inspection does not depend
   on a board stackup, so one selection serves any copper layer count.
2. Edit the complete recipe in the native dialog. The first selected part
   fills the editor with that part's defaults; see "Defaults" below. A later
   selection replaces only the part, and for a through-hole source sets
   `signal_via` to `null` and adds an antipad entry for every layer.
3. Set stackup, exit layer (`signal_layer`), netclasses, via dimensions, ground
   ring construction, layer-specific copper and antipads. The JSON editor
   exposes every saved setting without an external program. Mount/part/pad
   roles come from the inspected base source. Architecture worksheet layer
   aliases do not belong in the artifact.
4. **Validate / Preview** checks geometry and compatibility. The layer selector
   filters the preview, which draws the exit trace on its layer; the arrow
   shows the default clock's exit direction. The report lists each warning
   under the validation line, then the supported runtime and explicit
   limitations. A warning does not block publication; a blocking error does.
   Any edit invalidates publication until the new recipe passes validation.
5. **Publish immutable library bundle…** writes an exact artifact with recipe,
   source hash and provenance, plus a catalog `manifest.json`. It writes no
   library footprint because the base source already supplies that geometry;
   vias and the exit trace are actual declarative board objects, never
   footprint graphics.

**Open artifact…** checks integrity and reproduces the payload before reopening
its recipe. An existing revision is immutable: choose **Clone variant** for a
new artifact identity and parent pin, or **New revision** to retain identity and
publish the next revision. Old files and project pins remain unchanged. A
repeated publication of identical content leaves the artifact bytes and mtime
unchanged. Reusing an identity/revision with different content is blocked.

## Recipe geometry

Coordinates are millimetres, +X right, +Y up, with CCW positive angles. The
signal is at the origin and the exit runs along +X. Clock is a default instance
pose, saved separately from local geometry; a consumer applies it once to the
complete transition.

### Parameters

| Parameter | Meaning |
|---|---|
| `stackup` | Literal KiCad copper layer names, top to bottom (`F.Cu`, `In1.Cu`, …, `B.Cu`). Compatibility is its length, the copper layer count |
| `signal_layer` | The exit layer. It must be an inner layer |
| `netclasses` | Netclasses the transition may be assigned to |
| `signal_via` | SMD pin: `{diameter, drill}` of the stacked one-layer microvias from the mount layer to the exit layer. Through-hole pin: `null` |
| `ground_via` | `{via_type, diameter, drill, construction}`; see below |
| `ground_ring` | `{radius, start_deg, end_deg, count}` |
| `tuned_signal_copper` | Signal disc diameter or `null` for every copper layer |
| `tuned_ground_copper` | Optional enlarged ground land diameter by layer |
| `antipads` | Antipad diameter by layer |
| `trace_width` | Exit trace width |
| `ground_pour_gap` | Gap between signal copper and the ground flood |
| `clock_deg` | Default clock angle |

Every transition via is copper plated shut. That is fixed, not a setting:
there is no treatment or fill field in a recipe, and the payload marks each
via `"fill": "copper"`. There are two via types only, `through` and one-layer
`microvia`; there is no blind or buried mechanical via.

### Signal

A through-hole pin's own barrel is the signal conductor through every layer and
the transition adds no signal via. An SMD pin gets one microvia per layer pair
from the mount layer to the exit layer, all at the origin; the spans are
derived, not entered.

The exit layer must be an inner layer for both mount types. Routing on the
outer layers is never allowed, so an exit on `F.Cu` or `B.Cu` is blocked.

### Ground via ring

Ground arc angles are relative to the local +X trace exit. Decreasing endpoints
wrap across zero: 310°→50°, count 5 gives 310°, 335°, 0°, 25°, 50°. Partial
arcs include both endpoints and require at least two vias. A ±360° sweep
creates a full circle without duplicating the endpoint. Equal start/end angles
are ambiguous and blocked. The ring is an **arc of ground vias**, not an added
continuous copper ring.

- `via_type: "through"`: mechanically drilled vias spanning the whole board.
  `construction` must be `null`.
- `via_type: "microvia"`: one-layer microvias from the mount layer down.
  `construction` is `"stacked"` (every layer pair at the entered positions) or
  `"staggered"` (alternate layer pairs at the angular midpoints, on the same
  radius). The entered pattern applies to the pair at the mount layer. A
  partial arc of N vias has N−1 midpoints; a full circle keeps N.

The extent of a microvia ring is derived and there is no setting for it. For
a through-hole pin it runs through the entire board, on every layer pair. For
an SMD pin it runs from the mount layer to the layer below the exit layer,
where the ground disc is.

Ground lands may be tangent to each other and may not overlap. This is tested
on every layer where lands exist, with both sets together on the layers a
staggered ring shares, and with any `tuned_ground_copper` enlargement on that
layer.

### Overlay copper

`tuned_signal_copper` requires a diameter or `null` on every copper layer.
Additional circular, net-assigned native copper appears only when the requested
diameter exceeds the existing base/via land. `null` means no **added** copper;
it never removes or promises omission of base footprint or via lands. On a
through-hole pin's inner layers that carry only the bare barrel, a diameter
adds a tuning disc and must exceed the hole. An SMD pin may have signal copper
only from the mount layer to the exit layer. Optional `tuned_ground_copper`
enlarges existing ground-via lands on specified layers. No artifact shrinks or
edits an approved central footprint.

### Antipads

A through-hole pin needs an antipad on every layer of the board. An SMD pin
needs one from the mount layer to the exit layer and nowhere else. `antipads`
must name exactly those layers. Each must clear the signal copper on its layer
plus the ground-pour gap. For a through-hole pin the signal copper is the pad's
land on both outer layers and on the exit layer, and the bare drilled barrel on
every other layer. Ground lands may not intrude. Antipads use circumscribed
polygon rule areas with at most 0.001 mm radial overshoot; they never undersize
the circular exclusion.

### Ground disc (SMD pin)

An SMD transition adds a solid ground copper disc on the layer directly below
the exit layer, with the outer diameter of the footprint's ground pad and no
antipad. The exit layer is an inner layer, so that layer always exists. A
through-hole pin has no ground disc: its barrel passes every layer.

The disc does its job only when it is tied to the footprint's ground pad: KiCad
then reports a foreign track or via that touches it as a short. The ground
ring makes that tie when its lands overlap the annular pad on the mount layer
and the disc on the disc layer. The default via-in-pad ring does. A ring
outside the pad raises the `ground-disc-not-tied-to-pad` warning, because the
disc then relies on the board's flood for its connection.

### Exit trace, handoff and corridor

The transition includes the exit trace: a real track on the exit layer, on the
signal net, `trace_width` wide, from the signal centre `[0, 0]` to
`[ground ring radius, 0]`. The ring radius sets its length. Its end is the
handoff anchor, where designer-owned routing starts.

The exit corridor runs from the signal centre to the trace end and stops
there. Its half-width is `trace_width/2 + ground_pour_gap`. Its rule area
excludes copper pours. Ground pad, hole/barrel and added-copper envelopes on
the exit layer must stay outside the corridor; tangency is accepted and
intrusion blocks generation.

### Warnings

Blocking errors stop validation. Warnings are reported and do not block.

| `code` | Raised when |
|---|---|
| `annular-ring` | An annular ring, `(land − drill) / 2`, is strictly under the limit for its hole type: 5 mil (0.127 mm) for a mechanically drilled plated hole (the through-hole signal pin, through ground vias), 2.5 mil (0.0635 mm) for a microvia. Checked for the signal via, the ground via, the footprint's through-hole signal land, and every signal or ground tuning disc against the barrel or microvia it surrounds on its layer. The record's `limit_mm` is the limit applied |
| `ground-disc-not-tied-to-pad` | SMD pin: the ground ring does not land on both the footprint ground pad and the ground disc |

### Defaults

The owner works in mils. Values are stored in millimetres, converted exactly.
`default_recipe(part)` gives:

- signal microvia (SMD pin): 5 mil drill, 10 mil pad (0.127 / 0.254 mm);
- ground ring: stacked microvias, 5 mil drill, 10 mil pad, via-in-pad. The
  radius is the mid-radius of the part's annular ground pad, 43.75 mil
  (1.11125 mm) for the example GPPO;
- ground ring pattern: eleven positions from 30° to 330°, 30° apart. On the
  example GPPO the first land clears the exit corridor by 0.098 mm (3.9 mil;
  it must sit at least 24.3° off the exit axis), neighbouring lands are
  0.575 mm apart, and the same pattern also validates as a staggered ring;
- exit trace width 10 mil (0.254 mm) and ground-pour gap 8 mil (0.2032 mm);
- antipads 43 mil (1.0922 mm) on every layer that gets one;
- tuned signal copper 25 mil (0.635 mm) on the exit layer, and for an SMD pin
  21 mil (0.5334 mm) on `In1.Cu`;
- a four-layer board and an `In2.Cu` exit.

A 10 mil pad on a 5 mil drill is a 2.5 mil annular ring, exactly the microvia
limit, so the defaults raise no warning of their own.

Unsatisfiable requests are reported; parameters are never silently adjusted.

## Supported sources and limits

Native inspection requires exactly two electrical pads: one signal pad and
one ground pad. The signal pad is a single centered circular pad, plated PTH
or mount-side SMD. A through-hole signal pad must use KiCad's ordinary
padstack and be set to **remove unused layers, keep start and end**; any other
setting is rejected. The ground pad, for either mount type, is exactly one SMD
annular pad on the mount side, centred on the signal: a custom pad drawn as a
single unfilled circle. Its zone connection must resolve to solid: the pad is
set to solid, or the pad inherits and the footprint is set to solid. A pad and
footprint that both inherit leave the choice to each zone's default and are
rejected with a message that says so. Inspection also rejects NPTH signal
holes, noncircular/custom signal lands, any further numbered plated pad and
source copper graphics. A source drawn on the
front cannot be used for back mounting: there is no mirroring, and a
back-mounted part needs its own source and transition.

The actual experimental B012-L13-01 source derived from the supplied drawing is
supported: 15 mil (0.381 mm) plated hole, 23 mil (0.5842 mm) signal land and a
custom annular ground pad. Its 4 mil (0.1016 mm) annular ring, under the 5 mil
limit for a drilled hole, is reported as a warning. The ground pad's single unfilled circle primitive is read as an exact annulus, including
its opening. Its zone connection is set to solid on the pad.

The tool does not check whether a via can be plated shut (drill size, aspect
ratio), dielectric data, impedance, assembly
tolerances or complete rendered/exported copper equivalence. There are no
dogbone or jog launches.

The catalog is a simple directory index. Publish from one librarian process at
a time. Artifact creation is exclusive and complete-file publication prevents
partial immutable files; the catalog is replaced separately and is not a
multi-writer transaction or an atomic library/project commit.

## File/API contract

`model.py` has no KiCad or Workflow imports and provides `default_recipe`,
`review_recipe`, `generate_payload`, `recipe_warnings`, `make_artifact`,
`load_artifact`, `clone_artifact`, `revise_artifact` and `publish_bundle`.
`review_recipe(recipe)` returns `{"payload": …, "warnings": […]}` for a valid
recipe and raises `RFError` for a blocking finding. Each warning has `code`,
`subject` and `message`; a measured one also has `value_mm` and `limit_mm`.
`ground_ring_default_radius(part)` is the via-in-pad ring radius.
`native.read_base_part(path, signal_pad="1", ground_pad="2", side="front",
approved=False)` reads source geometry in KiCad and returns a pinned
descriptor: `signal_pad`, `ground_pad`, `mount`, `side`, `signal_land`,
`signal_drill`, `ground_annulus` (`inner_diameter`, `outer_diameter`) and
`ground_zone_connection` (always `solid`). The native ActionPlugin
class is `GPPORFTransitionDesignerPlugin`, registered as `PLUGIN` on KiCad import.

The versioned `gppo.artifact/2.0` envelope and `gppo.rf/2.0` payload contain only
data. A consumer binds explicit net/layer roles and materializes supported saved
objects without importing this plugin or executing a recipe. Supported payloads
can remain consumable when a newer generator's recipe cannot be reopened.
Workflow owns placed instances, pins, UUID registries and reconciliation; this
tool owns only librarian-authored reusable recipes and artifacts.

Payload object types:

| `type` | Fields | Notes |
|---|---|---|
| `via` | `id`, `net`, `at`, `layers`, `diameter`, `drill`, `via_type`, `fill` | `via_type` is `through` or `microvia`; `fill` is always `copper` |
| `copper` | `id`, `net`, `shape` (`circle`), `at`, `layer`, `diameter`, `purpose` | `purpose` is `signal-overlay`, `ground-overlay` or `ground-disc` |
| `keepout` | `id`, `net`, `shape` (`polygon`), `layers`, `points`, `prohibit`, `purpose` | `purpose` is `antipad` (with `tolerance_mm`) or `exit-corridor` |
| `track` | `id` (`exit-trace`), `net` (`signal`), `layer`, `width`, `start`, `end` | Exactly one, from `[0, 0]` to the handoff |

The payload's `anchors` are `signal` `[0, 0]`, `exit_direction_deg` `0` and
`handoff`, the trace end. `compatibility.copper_layer_count` replaces a stackup
name list.

Run `scripts/test_kicad.sh` and repository PCM checks before release. Focused
tests are in `tests/test_gppo_rf_transition.py`; they cover exact arc count and
midpoints, native source read/round-trip and footprint rejection, signal stacks
of any depth, stacked and staggered ground rings, land tangency/overlap,
antipad layers, the ground disc, the exit trace and corridor, warnings,
hash/schema rejection, clone/revision provenance and immutable no-op
publication.
