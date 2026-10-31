# BGA Interposer Wizard

A KiCad 10 footprint wizard for BGA-style interposers with a rectangular group
of balls omitted from the exact center. It follows the behavior and drawing style
of KiCad's bundled BGA wizard while adding independent overall and center-opening
row and column controls.

The default generates a 20 × 20 full grid at 1 mm pitch with an 8 × 8 center
opening: 336 electrical pads over the same 19 mm × 19 mm pad-center span as a
full 20 × 20 grid. Names retain their full-grid locations, so an omitted ball
does not cause later balls to be renumbered. The default 26 mm × 26 mm package
also includes a 2.5 mm round NPTH at the top-left (A1) corner, 11.5 mm in X and
Y from package center.

## Parameters

- `Pads`: pitch, ball/pad size, total columns, and total rows.
- `Center opening`: omitted columns and omitted rows.
- `Package`: body width, body length, and courtyard margin.
- `Mounting features`: optional round NPTH, corner, positive X/Y distances from
  package center, hole diameter, and optional origin at the round-hole center.

For an exactly centered opening, each opening dimension must have the same parity
as its corresponding outer dimension. For example, 20/8 and 19/7 are valid;
20/7 is rejected because it would be offset by half a pitch. Set both opening
dimensions to zero to generate a full grid.

When the round hole is enabled, select exactly one corner. The complete hole
must remain inside the package and must not overlap a retained BGA pad. Choosing
the round-hole center as the origin translates the entire footprint without
changing the relative package, pad, outline, or courtyard geometry.

## Manual installation

Copy the `bga_interposer` directory into KiCad's per-user scripting plugin
folder. On Linux with KiCad 10 this is normally:

```text
~/.local/share/kicad/10.0/scripting/plugins/bga_interposer
```

Restart KiCad, open the Footprint Editor, choose **Create Footprint from
Wizard**, and select **BGA Interposer**.
