# BGA Interposer Wizard

A KiCad 10 footprint wizard for BGA-style interposers with a rectangular group
of balls omitted from the exact center. It follows the behavior and drawing style
of KiCad's bundled BGA wizard while adding independent overall and center-opening
row and column controls.

The default generates a 20 × 20 full grid at 1 mm pitch with an 8 × 8 center
opening: 336 pads over the same 19 mm × 19 mm pad-center span as a full 20 × 20
grid. Names retain their full-grid locations, so an omitted ball does not cause
later balls to be renumbered.

## Parameters

- `Pads`: pitch, ball/pad size, total columns, and total rows.
- `Center opening`: omitted columns and omitted rows.
- `Package`: body width, body length, and courtyard margin.

For an exactly centered opening, each opening dimension must have the same parity
as its corresponding outer dimension. For example, 20/8 and 19/7 are valid;
20/7 is rejected because it would be offset by half a pitch. Set both opening
dimensions to zero to generate a full grid.

## Manual installation

Copy the `bga_interposer` directory into KiCad's per-user scripting plugin
folder. On Linux with KiCad 10 this is normally:

```text
~/.local/share/kicad/10.0/scripting/plugins/bga_interposer
```

Restart KiCad, open the Footprint Editor, choose **Create Footprint from
Wizard**, and select **BGA Interposer**.
