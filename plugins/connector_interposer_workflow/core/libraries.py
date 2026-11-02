"""Read-only approved footprint/symbol inputs. Never invoke authoring tools."""
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from .sexpr import loads, child, children

@dataclass(frozen=True)
class Pad:
    number: str
    kind: str
    position: tuple
    size: tuple
    layers: tuple

@dataclass(frozen=True)
class ApprovedFootprint:
    library_id: str
    path: str
    sha256: str
    pads: tuple

    @property
    def pad_numbers(self):
        return tuple(sorted({p.number for p in self.pads if p.number and p.kind != "np_thru_hole"}, key=natural_key))

    def pin(self):
        return {"library_id": self.library_id, "path": self.path, "sha256": self.sha256}


def natural_key(value):
    import re
    return tuple((1, int(s)) if s.isdigit() else (0, s) for s in re.split(r'(\d+)', str(value)) if s)


def read_footprint(path, library_id, expected_sha256=None, allow_mechanical=False):
    if not library_id or ':' not in library_id:
        raise ValueError("Select a library-qualified approved footprint")
    path = Path(path)
    if path.suffix != '.kicad_mod':
        raise ValueError("Approved footprint must be a .kicad_mod file")
    data = path.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    if expected_sha256 and checksum != expected_sha256:
        raise ValueError("Pinned approved footprint content changed; select its revision explicitly")
    node = loads(data.decode('utf-8-sig'))
    if node[0] not in ('footprint', 'module'):
        raise ValueError("Not a KiCad footprint")
    pads = []
    for item in children(node, 'pad'):
        if len(item) < 4:
            raise ValueError("Malformed approved pad")
        at, size, layers = child(item, 'at'), child(item, 'size'), child(item, 'layers')
        if at is None or size is None or layers is None:
            raise ValueError("Approved pads require position, size and layers")
        position, dimensions = tuple(float(v) for v in at[1:3]), tuple(float(v) for v in size[1:3])
        if len(position) != 2 or len(dimensions) != 2 or not all(math.isfinite(v) for v in (*position, *dimensions)) or any(v <= 0 for v in dimensions):
            raise ValueError("Invalid approved pad geometry")
        if item[2] not in ('np_thru_hole', 'thru_hole', 'smd', 'connect'):
            raise ValueError("Unsupported approved pad type")
        if item[2] != 'np_thru_hole' and not item[1]:
            raise ValueError("Electrical pads require pad numbers")
        pads.append(Pad(item[1], item[2], position, dimensions, tuple(layers[1:])))
    result = ApprovedFootprint(library_id, str(path.resolve()), checksum, tuple(pads))
    if not result.pad_numbers and not allow_mechanical:
        raise ValueError("Selected footprint has no electrical pads")
    return result


@dataclass(frozen=True)
class ApprovedSymbol:
    library_id: str
    expression: list
    sha256: str
    pin_numbers: tuple


def read_symbol(path, library_id, expected_sha256=None):
    """Require a standalone librarian symbol; unresolved inheritance blocks use."""
    data = Path(path).read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    if expected_sha256 and checksum != expected_sha256:
        raise ValueError("Pinned symbol content changed")
    root = loads(data.decode('utf-8-sig'))
    if root[0] != 'kicad_symbol_lib' or ':' not in library_id:
        raise ValueError("Select a library-qualified KiCad symbol")
    name = library_id.split(':', 1)[1]
    symbol = next((s for s in children(root, 'symbol') if s[1] == name), None)
    if symbol is None:
        raise ValueError(f"Approved symbol {library_id} was not found")
    if child(symbol, 'extends'):
        raise ValueError("Select a resolved standalone approved connector symbol")
    pins = []
    for sub in children(symbol, 'symbol'):
        for pin in children(sub, 'pin'):
            number = child(pin, 'number')
            at = child(pin, 'at')
            if not number or not at or len(at) < 4:
                raise ValueError("Malformed symbol pin")
            pins.append((number[1], float(at[1]), float(at[2]), float(at[3])))
    if len(pins) != 2 or len({p[0] for p in pins}) != 2:
        raise ValueError("Approved connector symbol must expose exactly two distinct pins")
    return ApprovedSymbol(library_id, symbol, checksum, tuple(pins))
