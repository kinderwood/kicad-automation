# BGA Interposer design notes

Only decisions not apparent from the user guide, code, or tests belong here.

- Geometry is constructed about package center, then the complete footprint is
  translated when the guide pin hole is selected as origin. Building directly
  in either coordinate system risks shifting only some layers or features.
- Perimeter designations enumerate only retained pads in physical row-major
  order, so their sequence has no gaps after corner depopulation. Interior BGA
  designations remain coordinate-derived from the reduced grid and therefore
  preserve gaps for omitted positions.
- A zero-radius board cutout remains one native rectangle per layer to minimize
  object count. Rounded cutouts use a native rounded rectangle where available
  and exact line-and-arc fallback geometry otherwise.
- No generic 3D model is attached because interposer body and guide geometry are
  vendor-specific; an automatically selected model would be misleading.
