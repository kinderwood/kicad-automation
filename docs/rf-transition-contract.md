# RF transition contract

This is the agreed physical contract between three parties: the librarian's
connector **footprint**, the **transition** authored in the GPPO RF Transition
Designer, and the Connector to Interposer **Workflow** that places both on a
board. It records owner decisions made on 2026-11-14.

It is the target. Where the code differs, the last section lists the gap; the
code changes to match this document, not the reverse. The artifact file format
(envelope, hashes, revisions) is separate and stays in
`plugins/gppo_rf_transition/DESIGN.md`.

## Who owns what

| Party | Owns | Never does |
|---|---|---|
| Footprint | Connector pads at their minimum manufacturable size, mask, paste, mechanical outline | Know the exit layer, the stackup or any tuning |
| Transition | Everything added around one connector for one exit layer: signal uvias, overlay copper, ground via ring, antipads, ground disc, exit trace, exit corridor | Shrink, move or remove footprint copper |
| Workflow | Placing the footprint and the transition together at one pose, binding nets, setting via fill, locking the result | Edit transition geometry; route inside it |

## What the footprint must provide

- **Exactly two electrical pads:** one signal pad and one ground pad. There
  are no other numbered plated pads.
- **Pin 1 is signal, pin 2 is ground.** The footprint is drawn mounted on the
  front. These are defaults; the Designer's advanced settings can override
  the pad numbers and the mount side.
- **Signal pad:** one circular pad centred on the footprint origin, either
  plated through-hole or SMD on the mount side.
- **Through-hole signal pad:** the hole passes through the whole board. The
  pad uses KiCad's ordinary padstack with **remove unused layers, keep start
  and end**. KiCad then draws its land on both outer layers and on any inner
  layer where copper connects, all at one diameter. That diameter is the
  minimum manufacturable land for the hole. Unconnected inner layers carry
  the bare barrel only.
- **Ground pad:** exactly one SMD annular (donut) pad on the mount side,
  centred on the signal. This holds for through-hole and SMD signal pins
  alike. Its outer and inner diameters sit tight to the connector body, so
  the board's ground flood fills in around and inside it.
- **Zone connection:** solid on the ground pad. RF connectors never use
  thermal reliefs. The pad itself is set to solid, or it inherits and the
  footprint is set to solid. A pad and footprint that both inherit leave the
  choice to each zone's default, which does not satisfy this contract.

## What a transition defines

A transition is authored for one footprint, one copper layer count, one mount
side and one exit layer. All geometry is local: signal at the origin, exit
along +X.

**The exit layer is always an inner layer.** Routing on the outer layers is
never allowed, for through-hole and SMD parts alike.

### Signal

- **Through-hole pin:** the footprint's own barrel is the signal conductor
  through every layer. The transition adds no signal via.
- **SMD pin:** stacked one-layer microvias from the mount layer down to the
  exit layer, all at the origin.

### Overlay copper

- On any layer the transition may add a circular signal copper disc.
- A disc may enlarge the footprint land or via land on that layer. It may
  never be smaller than what is already there.
- A disc may be added on a layer with no signal connection, as a tuning
  feature. It is drawn copper on the signal net, not a pad. Fabrication notes
  must say inner copper at RF launches is intentional.

### Ground via ring

- Vias sit on one circle of a given radius about the signal, at a given
  count across a given arc.
- Two constructions only:
  - **Through:** mechanically drilled vias spanning the whole board.
  - **Microvia:** one-layer-span microvias, either
    - **stacked:** every layer pair at the same positions, or
    - **staggered:** alternate layer pairs at the angular midpoints of the
      pattern, on the same radius. The pattern as entered applies to the pair
      at the mount layer. A partial arc of N vias has N−1 midpoints; a full
      circle keeps N.
- The extent of the ring is never chosen by the user:
  - **Through-hole pin:** the ring runs through the entire board, as through
    vias or as microvias on every layer pair.
  - **SMD pin:** a microvia ring runs from the mount layer to the layer below
    the exit layer, where the ground disc is. Through vias span the board as
    always.
- Lands may be tangent to each other. They may not overlap. In a staggered
  ring this is tested on every layer where both sets have lands.
- There is no blind or buried mechanical via type.
- **Every transition via is copper plated shut.** This is not a setting: the
  boards cycle between room temperature and cryogenic temperature, so no
  other fill is acceptable.

### Antipads

Each layer has its own antipad diameter: a circular opening in the ground
flood, centred on the signal. It must clear the signal copper on that layer
plus the ground gap. Ground via lands may not intrude into it.

- **Through-hole pin:** lands or bare barrel, and an antipad, on every layer
  of the board.
- **SMD pin:** antipads from the mount layer to the exit layer only.

### Ground disc below the exit (SMD pin only)

On the layer directly below the exit layer the transition places a solid
ground copper disc, with outer diameter equal to the footprint ground pad's
outer diameter. It has no antipad.

- It is real copper on the ground net and joins the ground flood solid.
- Its purpose is to make ordinary clearance checking fail any non-ground
  track or via that passes under the launch.
- A microvia ground ring runs down to this layer and lands on the disc.
- Below this layer the transition adds nothing. Through ground vias still
  pass through those layers; they get no special treatment there.

### Exit trace and corridor

- The transition includes the exit trace: a real track on the exit layer, on
  the signal net, at the given trace width. It runs along the centreline from
  the signal via centre to the ground ring radius. The ring radius sets its
  length; there is no separate length setting.
- The trace is part of the transition so that it stays clocked with the
  ground ring.
- The trace end point is the handoff. Designer-owned routing starts there.
- The corridor is the trace width plus one ground gap on each side. It runs
  from the signal centre to the trace end and stops there. The ground flood
  is kept out of it. Ground lands may be tangent to the corridor
  edge and may not intrude.
- Dogbone and jog launches (signal via offset from the pin, or signal
  microvias stepped between layers) are future capability. The handoff point
  is the place that work will extend.

## Checks the Designer makes

Blocking unless marked as a warning. This list is the contract's minimum, not
the complete set.

- Overlay copper is never smaller than the base land or via land.
- Each antipad clears signal copper plus the ground gap, and no ground land
  intrudes.
- Signal copper plus gap does not reach the footprint ground pad.
- Ground lands do not overlap each other (tangent passes).
- No ground land, barrel or overlay intrudes into the exit corridor on the
  exit layer (tangent passes).
- An SMD signal stack is continuous from the mount layer to the exit layer.
- The footprint matches "What the footprint must provide".
- The exit layer is an inner layer.
- **Warning:** any annular ring, (land − drill) / 2, under 5 mil. This
  applies to transition vias and to footprint lands.
- **Warning (SMD pin):** the ground ring does not land on both the footprint
  ground pad and the ground disc. The disc then relies on the board's flood
  for its connection to ground; see "Verified KiCad 10.0.8 behaviour" for why
  that connection matters.

Unsatisfiable requests are reported. Values are never adjusted silently.

## Designer defaults

The owner works in mils. Millimetres are used only for parts natively drawn
in millimetres. Values are stored in millimetres and converted exactly, once,
at the boundary.

- Signal microvia: 5 mil drill, 10 mil pad.
- Ground ring: stacked microvias, 5 mil drill, 10 mil pad, placed in the
  footprint's ground pad (via-in-pad). The default ring radius is the
  mid-radius of the ground pad annulus.
- Ground ring pattern: eleven positions from 30° to 330°, 30° apart.
- A 10 mil pad on a 5 mil drill is a 2.5 mil annular ring, so these defaults
  raise the annular ring warning. The 5 mil threshold is under review.

## What the Workflow must do

- Place the footprint and its transition at one position and clock angle,
  applying the clock once to the whole transition.
- Create the saved objects exactly: vias, copper discs, antipad and corridor
  areas, the ground disc and the exit trace. Do not re-tessellate or resize.
- Bind the transition's signal and ground roles to project nets.
- Mark every transition via as filled in KiCad, so the fill appears in the
  Gerber, ODB++ and IPC-2581 output. Those files cannot name the material;
  the copper callout belongs in the fabrication notes.
- Lock the placed transition, including the exit trace.
- Accept a transition only when its footprint hash, copper layer count, mount
  side and exit layer match the requirement.
- Start designer routing at the handoff point.

## Limits accepted for now

- **Stackup identity is the copper layer count.** Any six-layer board accepts
  any six-layer transition. Real stackups come later.
- **Back mounting needs its own transition.** Layer names are literal, so a
  front transition cannot be mirrored.
- **Plated-shut feasibility** (drill size, aspect ratio) is not checked here.
  It belongs to board-level DRC and the final output scripts.

## Verified KiCad 10.0.8 behaviour

Tested on a six-layer board on 2026-11-14.

- A through-hole pad set to remove unused layers and keep start and end is
  drawn on both outers and on the inner layer where a track connects, at the
  pad's one diameter. The flood clears the bare barrel on other layers.
- A signal-net copper disc on an otherwise unconnected inner layer does not
  make KiCad draw the pad there. The disc itself is the copper. In Gerber it
  is drawn copper tagged `Conductor` and carries its net; the flood clears
  it.
- KiCad propagates nets from pads to the non-pad copper they touch. A copper
  disc, track or via that touches a pad of another net is renamed to that
  pad's net when the board is saved from Python, with no DRC finding, when
  the disc is not otherwise tied to a pad of its own net. Locking the disc
  does not prevent it.
- When the disc is tied to a pad of its own net, as the ground disc is to the
  footprint's ground pad through the ground ring vias, nothing is renamed and
  a foreign routed track or via touching the disc is reported by DRC as
  "Items shorting two nets". So the ground disc does force a DRC error as
  intended, provided the ground ring connects it to the ground pad. No extra
  keepout is needed.
- Via filling, capping and plugging are settable from Python. Filled vias
  produce separate Gerber files (`…-filling-<span>.gbr`), ODB++ `filling_…`
  layers and IPC-2581 `HOLEFILL` layers. Excellon drill files and copper
  Gerbers carry nothing about fill.

## Not yet implemented

As of GPPO RF Transition Designer 0.3.0 and Connector to Interposer Workflow
0.8.0 the code differs from this contract in this way:

| Contract | Code today |
|---|---|
| Pin 1 / pin 2 / front as defaults | Reader defaults match; the dialog asks every time |
