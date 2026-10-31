# BGA Interposer Wizard

A KiCad 9 and 10 footprint wizard for BGA-style interposers with a rectangular group
of balls omitted from the exact center. It follows the behavior and drawing style
of KiCad's bundled BGA wizard while adding independent overall and center-opening
row and column controls, symmetric corner depopulation, and optional guide-pin
features.

The default generates a 20 × 20 full grid at 1 mm pitch with an 8 × 8 center
opening: 336 electrical pads over the same 19 mm × 19 mm pad-center span as a
full 20 × 20 grid. Names retain their full-grid locations, so an omitted ball
does not cause later balls to be renumbered. The default 26 mm × 26 mm package
uses 20 mil pads. The guide pin hole and all guide pin slots are disabled by
default.

## Parameters

- Parameter pages are ordered for entry from a mechanical drawing: `Package`,
  `Mounting layout`, `Guide pin hole`, `Guide pin slots`, `Pads`, then
  `Center opening`.
- `Package`: body width, body length, and courtyard margin.
- `Mounting layout`: shared positive X/Y distances from package center. Corner
  selection supplies the coordinate signs.
- `Guide pin hole`: optional round NPTH, exactly one selected corner, hole
  diameter, and optional footprint origin at the guide pin hole center. The
  feature and all corner selections default to off.
- `Guide pin slots`: independent enable checkbox and rotation for each corner,
  plus shared width and overall length. Each rotation is counterclockwise from
  a horizontal long axis.
- `Pads`: metric pitch, pad size in mils, total columns, and total rows. Pad size
  defaults to 20 mil; all other physical dimensions remain metric.
- `Pads` also includes a symmetric corner-depopulation depth. A depth of 4
  removes 4, 3, 2, and 1 pads from successive rows at all four corners.
- `Center opening`: omitted columns and omitted rows.

For an exactly centered opening, each opening dimension must have the same parity
as its corresponding outer dimension. For example, 20/8 and 19/7 are valid;
20/7 is rejected because it would be offset by half a pitch. Set both opening
dimensions to zero to generate a full grid.

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
