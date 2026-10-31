# BGA Interposer design notes

This file records the non-obvious rules that should survive into later work.
User-facing behavior belongs in `README.md`; implementation history does not.

## Coordinate model

- Construct all geometry about package center first.
- When the guide pin hole is selected as origin, translate every footprint
  item together. Relative geometry must not change.
- X/Y guide offsets are positive magnitudes from package center. Corner choices
  provide their signs.

## Parameters and pads

- `AddParam` declaration order is the wizard UI order and follows mechanical
  drawing entry. The integration test intentionally locks the full sequence.
- Dimensions are metric except BGA pad size, which is entered in mils.
- Electrical pads retain full-grid names across omitted locations, use KiCad's
  `BGA pad` fabrication property, and always have `F.Cu` and `F.Mask`. The
  solder-paste toggle controls only `F.Paste`.
- Guide features use KiCad's standard NPTH `*.Cu` + `*.Mask` layer set, with
  pad size equal to drill size and fabrication property `None`. This produces
  no annular copper; `Mechanical` is for plated mechanical terminals. Exactly
  one round guide pin hole may exist; slots cannot define the origin.
- The overall footprint remains `SMD`; unplated guide holes do not make it a
  through-hole footprint. Saved footprints include library description and
  keyword metadata when the KiCad API exposes those setters.

## Graphics

- Use the active Footprint Editor defaults for line widths on `F.Fab`,
  `F.SilkS`, `F.CrtYd`, and `Edge.Cuts`. Generated items store the resolved
  widths and do not track later settings changes.
- `F.Fab` describes the package body and repeats an enabled board cutout.
  `Edge.Cuts` contains the actual board cutout. The courtyard is a true offset
  of the selected package chamfers and is rounded to KiCad's 0.1 mm grid.
- Sparse silkscreen corner legs use the bundled KiCad BGA-wizard convention:
  each leg reaches one quarter of the corresponding full package edge.
- Silkscreen strokes stay outside the fabrication outline and inside the
  courtyard. The pin-1 dot is anchored diagonally from the top-left package
  vertex, or from that chamfer's midpoint, and must clear the body and corner
  mark.
- A zero-radius board cutout stays one native rectangle per layer. Rounded
  cutouts use a native rounded rectangle when available and exact line/arc
  fallback geometry otherwise.

## Compatibility

Support KiCad 9.0 and 10.0. Avoid newer-only Python API calls without a tested
fallback. The four-version CI matrix and `.kicad_mod` round-trip tests are the
compatibility boundary.

The wizard intentionally does not attach a 3D model: interposer body and guide
geometry are vendor-specific, so a generic model would be misleading.
