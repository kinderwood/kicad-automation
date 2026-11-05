"""Gang placement: board X-right/Y-down, KiCad positive CCW angles, mm input/output."""
import math
import re
from dataclasses import dataclass


def finite(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def transform(point, anchor=(0, 0), rotation=0, mirror=False):
    x, y = point
    if mirror:
        x = -x
    angle = math.radians(rotation)
    return (anchor[0]+x*math.cos(angle)+y*math.sin(angle), anchor[1]-x*math.sin(angle)+y*math.cos(angle))


@dataclass(frozen=True)
class GangDefinition:
    gang: str
    row_lengths: tuple
    pitch_x: float
    pitch_y: float
    row_offsets: tuple = ()
    numbering: tuple = ()
    traversal: str = "row-major"
    rotation: float = 0
    mirror: bool = False
    anchor: tuple = (0, 0)
    part_id: str = ""

    def positions(self):
        if not self.gang or not self.row_lengths or any(type(n) is not int or n <= 0 for n in self.row_lengths):
            raise ValueError("Gang and positive integer row lengths are required")
        values = (self.pitch_x, self.pitch_y, self.rotation, *self.anchor, *self.row_offsets)
        if len(self.anchor) != 2 or not all(finite(v) for v in values):
            raise ValueError("Gang coordinates and angles must be finite")
        if self.pitch_x <= 0 or self.pitch_y <= 0:
            raise ValueError("Gang pitch must be positive")
        if self.row_offsets and len(self.row_offsets) != len(self.row_lengths):
            raise ValueError("Provide one stagger/offset per row")
        if self.traversal not in ("row-major", "serpentine", "column-major"):
            raise ValueError("Unsupported numbering traversal")
        coords = [(row, col) for row, count in enumerate(self.row_lengths) for col in range(count)]
        if self.traversal == "serpentine":
            coords = [(row, col) for row, count in enumerate(self.row_lengths) for col in (range(count) if row % 2 == 0 else reversed(range(count)))]
        elif self.traversal == "column-major":
            coords.sort(key=lambda c: (c[1], c[0]))
        numbering = tuple(str(v) for v in self.numbering) if self.numbering else tuple(str(i+1) for i in range(len(coords)))
        if len(numbering) != len(coords) or len(set(numbering)) != len(coords) or '1' not in numbering or any(not re.fullmatch(r'[1-9][0-9]*', pin) for pin in numbering):
            raise ValueError("Numbering requires distinct positive indices, including the location-1 anchor")
        local = {}
        for pin, (row, col) in zip(numbering, coords):
            local[pin] = (col*self.pitch_x+(self.row_offsets[row] if self.row_offsets else 0), row*self.pitch_y)
        ox, oy = local["1"]
        anchor = tuple(float(v) for v in self.anchor)
        result = {pin: transform((x-ox, y-oy), anchor, self.rotation, self.mirror) for pin, (x, y) in local.items()}
        if len({(round(x, 12), round(y, 12)) for x, y in result.values()}) != len(result):
            raise ValueError("Gang locations overlap")
        return result

    def validate_population(self, records):
        expected = {r.connector_pin for r in records if r.connector == self.gang}
        actual = set(self.positions())
        if actual != expected:
            raise ValueError(f"Gang {self.gang} population mismatch: missing {sorted(expected-actual)}, extra {sorted(actual-expected)}")
        return True


def validate_mechanical_population(records, locations):
    """Locations is a sequence of physical keys; duplicates remain visible."""
    expected = {r.key for r in records}
    actual = list(locations)
    duplicate = sorted({key for key in actual if actual.count(key) > 1})
    missing, extra = sorted(expected-set(actual)), sorted(set(actual)-expected)
    if duplicate or missing or extra:
        raise ValueError(f"Mechanical population mismatch: missing {missing}, extra {extra}, duplicate {duplicate}")
    return True
