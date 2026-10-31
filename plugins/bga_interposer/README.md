# BGA Interposer Wizard

A KiCad 9 and 10 footprint wizard for BGA-style interposers with a rectangular
group of balls omitted from the exact center. It follows the behavior and
drawing style of KiCad's bundled BGA wizard while adding independent overall
and center-depopulation row and column controls, symmetric corner depopulation,
optional guide-pin features, and an optional centered board cutout.

The default generates a 20 × 20 full grid at 1 mm pitch with an 8 × 8 center
pad depopulation region: 336 electrical pads over the same 19 mm × 19 mm
pad-center span as a full 20 × 20 grid. Names retain their full-grid locations,
so an omitted ball does not cause later balls to be renumbered. The default
26 mm × 26 mm package uses 20 mil pads. The board cutout and all guide pin slots
are disabled by default. The top-left guide pin hole is enabled by default so
changes on the `Mounting layout` page remain visible while entering offsets.

## Parameters

- Parameter order is intentional. It follows a mechanical-drawing workflow:
  establish the package envelope, place guide features, size the full pad field,
  and only then define depopulation and the board cutout. The declaration order
  in the wizard controls the UI, and an integration test locks every page and
  field in the sequence documented below.
- Parameter pages are ordered for entry from a mechanical drawing: `Package`,
  `Mounting layout`, `Guide pin hole`, `Guide pin slots`, `Pads`, `Center pad
  depop`, `Center board cutout`, and `Package graphics`.
- `Package`: body width, body length, one physical package chamfer size with an
  enable checkbox for each corner, and courtyard margin. The courtyard follows
  the selected package chamfers while preserving clearance at diagonal edges.
- `Mounting layout`: shared positive X/Y distances from package center. Corner
  selection supplies the coordinate signs.
- `Guide pin hole`: optional round NPTH, exactly one selected corner, hole
  diameter, and optional footprint origin at the guide pin hole center. The
  feature and all corner selections default to off.
- `Guide pin slots`: independent enable checkbox and rotation for each corner,
  plus shared width and overall length. Each rotation is counterclockwise from
  a horizontal long axis.
- `Pads`: metric pitch, pad size in mils, total columns, and total rows. Pad size
  defaults to 20 mil; all other physical dimensions remain metric. Pad diameter
  may equal the pitch for tangent pads but cannot exceed it.
- `Pads` also includes `corner depop depth`, the symmetric corner-depopulation
  depth. A depth of 4 removes 4, 3, 2, and 1 pads from successive rows at all
  four corners.
- `Center pad depop`: omitted columns and omitted rows.
- `Center board cutout`: optional centered rectangular board cutout with
  independent width, length, and corner radius. It defaults to disabled with
  5 mm × 5 mm dimensions and zero corner radius.
- `Package graphics`: shared silkscreen-corner toggle, configurable silkscreen
  standoff, and independent pin-1-dot and reference-designator toggles. Enabled
  silkscreen corners follow the selected package chamfers. Turning off all three
  silkscreen elements produces no visible silkscreen.

For exactly centered pad depopulation, each depopulation dimension must have the
same parity as its corresponding outer dimension. For example, 20/8 and 19/7
are valid; 20/7 is rejected because it would be offset by half a pitch. Set both
depopulation dimensions to zero to generate a full grid.

The board cutout is independent of center pad depopulation. When enabled, it is
drawn with matching geometry on `Edge.Cuts` and `F.Fab`, must remain inside the
package, and cannot overlap a retained BGA pad. A zero-radius cutout is one
native rectangle object. Rounded cutouts use a native rounded rectangle where
KiCad supports it and exact line-and-arc geometry otherwise.

When the guide pin hole is enabled, select exactly one corner. Slots may be
enabled in any of the other three corners. A hole and slot cannot share a corner,
and guide features cannot overlap each other or a retained BGA pad. Every guide
feature must remain inside the package. Choosing the guide pin hole center as
the origin translates the entire footprint without changing the relative
package, pad, outline, or courtyard geometry. Slots cannot be selected as the
footprint origin.

## Supported KiCad versions

The PCM package declares compatibility with KiCad 9.0 through 10.0. The test
matrix covers KiCad 9.0.0, the latest stable KiCad 9.0 patch release, KiCad
10.0.0, and KiCad 10.0.8.

## Manual installation

Copy the `bga_interposer` directory into KiCad's per-user scripting plugin
folder. On Linux this is normally one of:

```text
~/.local/share/kicad/9.0/scripting/plugins/bga_interposer
~/.local/share/kicad/10.0/scripting/plugins/bga_interposer
```

Restart KiCad, open the Footprint Editor, choose **Create Footprint from
Wizard**, and select **BGA Interposer**.
