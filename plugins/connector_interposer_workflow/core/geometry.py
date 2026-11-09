"""Gang placement and explicit imported-drawing identity."""
import hashlib
import json
import math
import re
from dataclasses import dataclass


DEFAULT_FEATURE_TOLERANCE_MM = 0.3 * 0.0254
MAX_FEATURE_TOLERANCE_MM = 0.5


def feature_tolerance(value):
    """Return a valid drawing-feature center tolerance in millimetres."""
    if not finite(value) or not 0 <= float(value) <= MAX_FEATURE_TOLERANCE_MM:
        raise ValueError(
            f"Drawing feature tolerance must be between 0 and "
            f"{MAX_FEATURE_TOLERANCE_MM:g} mm"
        )
    return float(value)


def distance_text(value_mm):
    """Format a small center deviation in both native and drafting units."""
    return f"{value_mm:.6g} mm ({value_mm / .0254:.6g} mil)"


def feature_deviation_message(feature, deviation_mm, limit_mm):
    return (
        f"{feature}: center deviation from its drawing circle is "
        f"{distance_text(deviation_mm)}; limit is {distance_text(limit_mm)}"
    )


def finite(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def drawing_revision(inventory, *, include_uuids=True):
    """Hash the complete inspected drawing layer, not the containing board.

    UUIDs deliberately participate in the accepted revision.  A native DXF
    replacement can reproduce identical geometry while creating new KiCad
    objects; that is a new revision and requires explicit reassociation.
    ``include_uuids=False`` is only a recovery/suggestion fingerprint.
    """
    fields = {
        'source_layer': inventory.get('source_layer', ''),
        'circles': [], 'segments': [], 'texts': [],
    }
    for circle in inventory.get('circles', ()):
        value = {
            'center': [float(v) for v in circle.get('center', ())],
            'radius': float(circle.get('radius', 0)),
        }
        if include_uuids:
            value['uuid'] = str(circle.get('uuid', ''))
        fields['circles'].append(value)
    for segment in inventory.get('segments', ()):
        value = {
            'start': [float(v) for v in segment.get('start', ())],
            'end': [float(v) for v in segment.get('end', ())],
            'mid': ([float(v) for v in segment['mid']]
                    if segment.get('mid') is not None else None),
            'owner': str(segment.get('owner', 'board')),
        }
        if include_uuids:
            value['uuid'] = str(segment.get('source_uuid', ''))
        fields['segments'].append(value)
    for item in inventory.get('texts', ()):
        value = {
            'text': str(item.get('text', '')),
            'at': [float(v) for v in item.get('at', ())],
        }
        if include_uuids:
            value['uuid'] = str(item.get('uuid', ''))
        fields['texts'].append(value)
    for name in ('circles', 'segments', 'texts'):
        fields[name].sort(key=lambda item: json.dumps(
            item, sort_keys=True, separators=(',', ':'), allow_nan=False,
        ))
    encoded = json.dumps(
        fields, sort_keys=True, separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


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


def match_circle_features(features, circles, tolerance, claimed=(), required=None):
    """Match named expected centers to unique drawing circles within tolerance."""
    tolerance = feature_tolerance(tolerance)
    required = required or {}
    unavailable = set(claimed)
    by_uuid = {circle['uuid']: circle for circle in circles}
    matches = []
    for feature, center in features:
        required_uuid = required.get(feature)
        if required_uuid:
            circle = by_uuid.get(required_uuid)
            if circle is None:
                raise ValueError(f'{feature}: selected drawing circle is missing')
            candidates = [circle] if required_uuid not in unavailable else []
        else:
            candidates = [circle for circle in circles
                          if circle['uuid'] not in unavailable]
        distances = [(circle, math.dist(circle['center'], center))
                     for circle in candidates]
        within = [(circle, deviation) for circle, deviation in distances
                  if deviation <= tolerance]
        if not within:
            if distances:
                closest = min(deviation for _circle, deviation in distances)
                raise ValueError(feature_deviation_message(feature, closest, tolerance))
            raise ValueError(
                f'{feature}: no drawing circles remain to match; limit is '
                f'{distance_text(tolerance)}'
            )
        if len(within) != 1:
            deviations = ', '.join(distance_text(deviation)
                                   for _circle, deviation in within)
            raise ValueError(
                f'{feature}: {len(within)} drawing circles match at center '
                f'deviations {deviations}; limit is {distance_text(tolerance)}'
            )
        circle, deviation = within[0]
        matches.append({
            'key': feature,
            'center': list(circle['center']),
            'circle_uuid': circle['uuid'],
            'deviation_mm': deviation,
        })
    used = {}
    for match in matches:
        prior = used.get(match['circle_uuid'])
        if prior:
            raise ValueError(
                f'{prior} and {match["key"]} match the same drawing circle'
            )
        used[match['circle_uuid']] = match['key']
    return matches


def match_selected_circle(feature, accepted, circles, tolerance, claimed=()):
    """Resolve an explicit selection by UUID; geometry never changes identity."""
    tolerance = feature_tolerance(tolerance)
    center = accepted.get('center', ()) if isinstance(accepted, dict) else ()
    if (len(center) != 2 or not all(finite(value) for value in center)):
        raise ValueError(f'{feature}: selected drawing circle has no valid recorded center')
    unavailable = set(claimed)
    selected_uuid = accepted.get('uuid') or accepted.get('circle_uuid', '')
    if selected_uuid in unavailable:
        raise ValueError(f'{feature}: selected drawing circle is already assigned')
    selected = next((circle for circle in circles
                     if circle.get('uuid') == selected_uuid), None)
    if selected is None:
        suggestion = suggest_selected_circle(
            feature, accepted, circles, tolerance, claimed,
        )
        detail = (f'; unique geometry suggestion is {suggestion["uuid"]}'
                  if suggestion else '')
        raise ValueError(
            f'{feature}: selected drawing circle is missing from the accepted '
            f'drawing revision{detail}; select it again'
        )
    deviation = math.dist(selected['center'], center)
    if deviation > tolerance:
        raise ValueError(feature_deviation_message(feature, deviation, tolerance))
    return selected


def suggest_selected_circle(feature, accepted, circles, tolerance, claimed=()):
    """Return one geometry candidate for user confirmation, never acceptance."""
    tolerance = feature_tolerance(tolerance)
    center = accepted.get('center', ()) if isinstance(accepted, dict) else ()
    if (len(center) != 2 or not all(finite(value) for value in center)):
        raise ValueError(f'{feature}: selected drawing circle has no valid recorded center')
    unavailable = set(claimed)
    candidates = [circle for circle in circles
                  if circle.get('uuid') not in unavailable]
    distances = [(circle, math.dist(circle['center'], center))
                 for circle in candidates]
    matches = [(circle, deviation) for circle, deviation in distances
               if deviation <= tolerance]
    if not matches:
        return None
    if len(matches) != 1:
        return None
    return matches[0][0]
