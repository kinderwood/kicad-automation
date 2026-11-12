"""Propose where a part fits the imported drawing.

Pure logic: no wx and no pcbnew. A part is a set of feature centres relative to
its anchor (RF location 1, DC pin 1 or the interposer origin). A drawing circle
is a candidate anchor when every feature of the part lands on a free drawing
circle within the centre tolerance. One candidate is a proposal; several leave
the choice to the drafter.
"""
import math


class CircleIndex:
    """Drawing circles in a grid so a centre lookup does not scan them all."""

    def __init__(self, circles, tolerance):
        self.tolerance = float(tolerance)
        self.cell = max(2 * self.tolerance, 1e-3)
        self.circles = tuple(circles)
        self._grid = {}
        for circle in self.circles:
            self._grid.setdefault(self._key(circle['center']), []).append(circle)

    def _key(self, point):
        return (math.floor(point[0] / self.cell), math.floor(point[1] / self.cell))

    def at(self, point, excluded=()):
        """Circles whose centre is within the tolerance of ``point``."""
        column, row = self._key(point)
        return [
            circle
            for dx in (-1, 0, 1) for dy in (-1, 0, 1)
            for circle in self._grid.get((column + dx, row + dy), ())
            if circle['uuid'] not in excluded and
            math.dist(circle['center'], point) <= self.tolerance
        ]

    def covered(self, points):
        """UUIDs of the circles under ``points``: what a placed part occupies."""
        return {circle['uuid'] for point in points for circle in self.at(point)}


def candidates(offsets, index, claimed=()):
    """Circles where the anchor can sit so every offset lands on a free circle."""
    offsets = [tuple(offset) for offset in offsets]
    claimed = set(claimed)
    found = []
    for circle in index.circles:
        if circle['uuid'] in claimed:
            continue
        x, y = circle['center']
        if all(index.at((x + dx, y + dy), claimed) for dx, dy in offsets):
            found.append(circle)
    return found


def footprint_offsets(pads, rotation, side, anchor_pad=None):
    """Electrical pad centres of a placed footprint, relative to its anchor.

    Mirrors the backend: a back-side part flips its Y axis, then turns
    counter-clockwise by ``rotation`` degrees in KiCad's Y-down board frame.
    The anchor is ``anchor_pad`` when given, otherwise the footprint origin.
    """
    angle = math.radians(float(rotation))
    cosine, sine = math.cos(angle), math.sin(angle)
    placed = {}
    for pad in pads:
        if not pad.number or pad.kind == 'np_thru_hole':
            continue
        x, y = float(pad.position[0]), float(pad.position[1])
        if side == 'B':
            y = -y
        placed.setdefault(str(pad.number), (x * cosine + y * sine, -x * sine + y * cosine))
    origin = (0.0, 0.0)
    if anchor_pad is not None:
        if str(anchor_pad) not in placed:
            raise ValueError(f'The footprint has no pad {anchor_pad}')
        origin = placed[str(anchor_pad)]
    offsets = [(x - origin[0], y - origin[1]) for x, y in placed.values()]
    if anchor_pad is None:
        offsets.append((0.0, 0.0))
    return offsets


# --- The workflow's parts -------------------------------------------------

INTERPOSER, GANG, DC = 'interposer', 'gang', 'dc'


def parts(controller):
    """Every part that takes its position from a drawing circle."""
    reference = controller.interposer_reference()
    found = [(INTERPOSER, reference)] if reference else []
    found.extend((GANG, name) for name in controller.gang_names())
    found.extend((DC, name) for name in controller.dc_connector_names())
    return found


def part_selection(controller, kind, name):
    if kind == INTERPOSER:
        return controller.config['interposers'].get(name, {})
    if kind == GANG:
        return controller.gang(name)
    return controller.dc_connector(name)


def circle_record(controller, kind, name):
    """The drawing circle a part was placed on, or an empty record."""
    selection = part_selection(controller, kind, name)
    return selection.get('pose_circle' if kind == INTERPOSER else 'anchor_circle') or {}


def anchor(controller, kind, name):
    """The accepted anchor position of a part in millimetres, or ``None``."""
    selection = part_selection(controller, kind, name)
    if kind == INTERPOSER:
        pose = selection.get('pose') or ()
        return tuple(pose[:2]) if selection.get('pose_accepted') and len(pose) >= 2 else None
    position = selection.get('anchor') or ()
    return tuple(position) if selection.get('anchor_accepted') and len(position) == 2 else None


def stale(controller, record):
    """A placement made on a drawing that has since been replaced."""
    revision = controller.config['mechanical'].get('inspected_revision', '')
    return bool(record and record.get('uuid') and revision and
                record.get('revision') != revision)


def _pads(controller, name, selection):
    from .core.libraries import read_footprint
    if not selection.get('path') or not selection.get('library_id'):
        raise ValueError(f'Assign a footprint to {name} in Connector assignments first')
    return read_footprint(
        controller.resolve_path(selection['path'], selection.get('sha256')),
        selection['library_id'], selection.get('sha256'),
    ).pads


def offsets(controller, kind, name):
    """Feature centres of a part relative to its anchor, as it would be placed."""
    selection = part_selection(controller, kind, name)
    if kind == GANG:
        if not selection.get('defined'):
            raise ValueError(f'Define {name} in Define RF gangs first')
        definition = controller.gang_definition(dict(selection, anchor=(0, 0)))
        return list(definition.positions().values())
    pads = _pads(controller, name, selection)
    if kind == DC:
        return footprint_offsets(pads, selection.get('rotation', 0),
                                 selection.get('side', 'F'),
                                 selection.get('anchor_pad') or '1')
    pose = selection.get('pose') or (0, 0, 0)
    return footprint_offsets(pads, pose[2] if len(pose) > 2 else 0,
                             selection.get('side', 'F'))


def occupied(controller, index, kind, name):
    """UUIDs of the drawing circles under a placed part."""
    position = anchor(controller, kind, name)
    if position is None:
        return set()
    try:
        relative = offsets(controller, kind, name)
    except (OSError, TypeError, ValueError):
        relative = [(0.0, 0.0)]
    covered = index.covered((position[0] + dx, position[1] + dy) for dx, dy in relative)
    uuid = circle_record(controller, kind, name).get('uuid')
    return covered | ({uuid} if uuid else set())


def claimed(controller, index, skip=None):
    """Circles already used by every placed part other than ``skip``."""
    used = set()
    for part in parts(controller):
        if part != skip:
            used |= occupied(controller, index, *part)
    for hole in controller.config.get('mounting_holes', []):
        uuid = hole.get('pose_circle', {}).get('uuid')
        if uuid:
            used.add(uuid)
    return used


def circle_index(controller, circles):
    from .core.geometry import feature_tolerance
    return CircleIndex(circles, feature_tolerance(
        controller.config['mechanical']['feature_tolerance_mm']))


def proposals(controller, kind, name, circles):
    """Drawing circles where ``name`` fits, and the circles each fit would cover."""
    index = circle_index(controller, circles)
    relative = offsets(controller, kind, name)
    found = candidates(relative, index, claimed(controller, index, (kind, name)))
    return found, index, relative


def reassociate(controller, circles, revision):
    """Carry placements over to a re-read drawing.

    A circle that is still the same object (UUID) in the same place is the same
    association; only circles that moved or were replaced need picking again.
    """
    from .core.geometry import feature_tolerance
    config = controller.config
    tolerance = feature_tolerance(config['mechanical']['feature_tolerance_mm'])
    by_uuid = {circle['uuid']: circle for circle in circles}
    records = [selection.get('pose_circle') for selection in config['interposers'].values()]
    records += [gang.get('anchor_circle') for gang in config['gangs'].values()]
    records += [item.get('anchor_circle') for item in config['dc_connectors'].values()]
    records += [hole.get('pose_circle') for hole in config.get('mounting_holes', [])]
    carried = False
    for record in records:
        if not record or not record.get('uuid') or record.get('revision') == revision:
            continue
        circle = by_uuid.get(record['uuid'])
        centre = record.get('center') or ()
        if circle and len(centre) == 2 and math.dist(circle['center'], centre) <= tolerance:
            record['revision'] = revision
            carried = True
    return carried
