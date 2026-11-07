# BGA Interposer design notes

Only decisions not apparent from the user guide, code, or tests belong here.

- Geometry is constructed about package center, then the complete footprint is
  translated when the guide pin hole is selected as origin. Building directly
  in either coordinate system risks shifting only some layers or features.
- Every footprint carries the complete wizard input as a versioned embedded
  JSON recipe rather than a custom property. KiCad 10's legacy Python API does
  not expose embedded-file mutation, so the wizard creates the native embedded
  file by parsing a minimal in-memory footprint before drawing any geometry.
  The same seed optionally creates the native package-center `PCB_POINT`, whose
  constructor is also absent from the legacy binding.
- KiCad's embedded-file payload is Zstandard-compressed Base64. The wizard uses
  the Zstandard library shipped with or loaded by KiCad and supplies a legacy
  SHA-256 checksum accepted by the parser; KiCad normalizes that checksum to
  its current MurmurHash3 representation when it loads the seed.
- Perimeter designations enumerate only retained pads in physical row-major
  order, so their sequence has no gaps after corner depopulation. Interior BGA
  designations remain coordinate-derived from the reduced grid and therefore
  preserve gaps for omitted positions.
- A zero-radius board cutout remains one native rectangle per layer to minimize
  object count. Rounded cutouts use KiCad 10's native rounded rectangle.
- No generic 3D model is attached because interposer body and guide geometry are
  vendor-specific; an automatically selected model would be misleading.
