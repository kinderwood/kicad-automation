"""Per-step guidance and checklist state for the Workflow dialog.

Pure logic over a ``WorkflowController``: no wx and no pcbnew. Each step has a
one-sentence description and a fixed list of checklist lines. A line is always
present; only its status and count change.

Statuses:
    done      active and all good
    missing   a problem, or something required now is not set
    caution   required for a final board, not set, not blocking
    optional  the drafter's choice
"""
from dataclasses import dataclass

DONE, MISSING, CAUTION, OPTIONAL = 'done', 'missing', 'caution', 'optional'
_SEVERITY = {MISSING: 3, CAUTION: 2, DONE: 1, OPTIONAL: 0}

GROUPS = (
    ('Nets & Layers', ('Netlist source', 'Netclass type', 'Layer assignment',
                       'Routing permissions')),
    ('Parts', ('Connector assignments', 'RF transitions')),
    ('Geometry', ('Define RF gangs', 'Mechanical placement', 'Interposer microvias',
                  'Board outline')),
    ('Finish', ('Review changes', 'Apply / results')),
)

ABOUT = {
    'Netlist source': 'Choose the netlist file and match its columns to the seven '
                      'fields the workflow needs.',
    'Netclass type': 'Say whether each netclass is RF or DC. RF nets use '
                     'single-position connectors arranged in gangs; DC nets use one '
                     'multi-pin connector.',
    'Layer assignment': 'Match each layer name used in the netlist to a copper layer '
                        'of this board.',
    'Routing permissions': 'Untick a box to forbid a netclass on a copper layer. '
                           'Everything is allowed unless you change it.',
    'Connector assignments': 'Choose a library symbol and footprint for the interposer '
                             'and for each connector. The footprint list only offers '
                             'parts that fit the symbol.',
    'RF transitions': 'Optionally attach an approved RF transition to each combination '
                      'of netclass, layer and connector footprint. Unassigned '
                      'combinations do not block Review.',
    'Define RF gangs': "Describe how each RF connector gang's positions are arranged "
                       'and numbered. Pick a gang, set its rows and pitch, then check '
                       'the preview.',
    'Mechanical placement': 'Place each part on a circle of the imported drawing.',
    'Interposer microvias': 'Optionally add a stack of laser microvias under every '
                            'interposer pad. Ground pins go through the board; signal '
                            'pins stop at their routing layer.',
    'Board outline': 'Optionally have the workflow create the board edge from lines '
                     'and arcs of the imported drawing. Your own Edge.Cuts shapes are '
                     'never changed.',
    'Review changes': 'List the problems found and exactly what Apply will change on '
                      'the board. Nothing changes until you apply.',
    'Apply / results': 'Write the reviewed changes to the open board and the managed '
                       'schematic sheets. Keep the Schematic Editor closed while this '
                       'runs.',
}


@dataclass(frozen=True)
class Line:
    status: str
    label: str
    count: str = ''


def _counted(label, done, total, empty=OPTIONAL):
    """A required ``done of total`` line; nothing to do is the drafter's choice."""
    if not total:
        return Line(empty, label, '0')
    return Line(DONE if done == total else MISSING, label, f'{done} of {total}')


def _netlist_source(controller):
    table = controller.table
    path = controller.config.get('workbook', {}).get('path')
    mapped = len(table.mapping) if table else 0
    nets = table.included_count if table else 0
    return (
        Line(DONE if path and table else MISSING, 'File selected'),
        Line(DONE if mapped == 7 else MISSING, 'Columns assigned', f'{mapped} of 7'),
        Line(DONE if nets else MISSING, 'Nets parsed', str(nets)),
    )


def _netclass_type(controller):
    from .core.connector_types import VALID_TYPES
    names = {record.netclass for record in controller.table.records} if controller.table else set()
    types = controller.config['netclass_types']
    done = sum(types.get(name) in VALID_TYPES for name in names)
    return (_counted('Classified', done, len(names), MISSING),)


def _layer_assignment(controller):
    names = {record.layer for record in controller.table.records} if controller.table else set()
    mapping = controller.config['layer_mapping']
    done = sum(mapping.get(name) in controller.copper_layers for name in names)
    return (_counted('Assigned', done, len(names), MISSING),)


def _routing_permissions(controller):
    if not controller.table:
        return (Line(MISSING, 'New combinations to review', '0'),)
    new = len(controller.new_rules)
    return (Line(CAUTION if new else DONE, 'New combinations to review', str(new)),)


def _assigned(selection, footprint_key='library_id'):
    return bool(selection.get('symbol', {}).get('library_id') and selection.get(footprint_key))


def _connector_assignments(controller):
    reference = controller.interposer_reference()
    interposer = controller.config['interposers'].get(reference, {}) if reference else {}
    gangs = controller.gang_names()
    connectors = controller.dc_connector_names()
    if not controller.table:
        return (Line(MISSING, 'Interposer', '0 of 0'), Line(OPTIONAL, 'RF gangs', '0'),
                Line(OPTIONAL, 'DC connectors', '0'))
    return (
        _counted('Interposer', int(_assigned(interposer)), int(bool(reference)), MISSING),
        _counted('RF gangs', sum(_assigned(controller.gang(name)) for name in gangs),
                 len(gangs)),
        _counted('DC connectors',
                 sum(_assigned(controller.dc_connector(name)) for name in connectors),
                 len(connectors)),
    )


def _rf_transitions(controller):
    requirements = controller.rf_transition_requirements()
    if not requirements:
        return (Line(OPTIONAL, 'Assigned', '0'), Line(OPTIONAL, 'Not assigned', '0'),
                Line(OPTIONAL, 'Blocked', '0'))
    assigned = [item for item in requirements if item.get('assignment', {}).get('path')]
    blocked = 0
    for requirement in assigned:
        try:
            controller.validate_rf_transition(requirement)
        except (OSError, ValueError):
            blocked += 1
    open_count = len(requirements) - len(assigned)
    state = CAUTION if open_count else DONE
    return (
        Line(state, 'Assigned', str(len(assigned))),
        Line(state, 'Not assigned', str(open_count)),
        Line(MISSING if blocked else DONE, 'Blocked', str(blocked)),
    )


def gang_defined(controller, name):
    """A gang is defined when it is marked so and its numbering matches the netlist."""
    try:
        gang = controller.gang(name)
        if not gang.get('defined'):
            return False
        expected = {record.connector_pin for record in controller.table.records
                    if record.connector == name}
        return set(controller.pattern(name)) == expected
    except (TypeError, ValueError):
        return False


def _define_rf_gangs(controller):
    gangs = controller.gang_names()
    return (_counted('Gangs defined', sum(gang_defined(controller, name) for name in gangs),
                     len(gangs)),)


def _mechanical_placement(controller):
    config = controller.config
    reference = controller.interposer_reference()
    interposer = config['interposers'].get(reference, {}) if reference else {}
    gangs = controller.gang_names()
    connectors = controller.dc_connector_names()
    holes = len(config.get('mounting_holes', []))
    needs_drawing = bool(reference or gangs or connectors)
    read = bool(config['mechanical'].get('inspected_revision'))
    return (
        Line(DONE if read else MISSING if needs_drawing else OPTIONAL, 'Drawing read'),
        _counted('Interposer', int(bool(interposer.get('pose_accepted'))),
                 int(bool(reference))),
        _counted('RF gangs',
                 sum(bool(controller.gang(name).get('anchor_accepted')) for name in gangs),
                 len(gangs)),
        _counted('DC connectors',
                 sum(bool(controller.dc_connector(name).get('anchor_accepted'))
                     for name in connectors), len(connectors)),
        Line(DONE if holes else OPTIONAL, 'Mounting holes', str(holes)),
    )


def _interposer_microvias(controller):
    microvias = controller.config.get('microvias', {})
    if not microvias.get('enabled'):
        return (Line(OPTIONAL, 'Microvias', 'off'),)
    size = microvias.get('size', {})
    pair = (size.get('diameter'), size.get('drill'))
    eligible = {(item.diameter, item.drill) for item in controller.microvia_presets()
                if item.eligible}
    return (Line(DONE if pair in eligible else MISSING, 'Microvias', 'on'),)


def _board_outline(controller):
    outline = controller.config.get('outline', {})
    cutout = Line(DONE, 'Centre cutout', 'set') if outline.get('cutout_uuids') \
        else Line(OPTIONAL, 'Centre cutout', 'none')
    if not outline.get('enabled'):
        return (Line(CAUTION, 'Outer boundary', 'none'), cutout)
    return (Line(DONE, 'Outer boundary', 'set') if outline.get('outer_uuids')
            else Line(MISSING, 'Outer boundary', 'not set'), cutout)


def _review_lines(controller):
    preview = controller.preview
    current = preview is not None
    issues = tuple(controller.issues)
    errors = sum(getattr(issue, 'severity', '') == 'error' for issue in issues)
    warnings = sum(getattr(issue, 'severity', '') == 'warning' for issue in issues)
    warnings += len(preview.get('warnings', ())) if current else 0
    conflicts = len(preview.get('conflicts', ())) if current else 0
    return current, errors, warnings, conflicts


def _review_changes(controller):
    current, errors, warnings, conflicts = _review_lines(controller)
    unknown = OPTIONAL if not current and not errors else None
    return (
        Line(DONE if current else MISSING, 'Review up to date'),
        Line(unknown or (MISSING if errors else DONE), 'Blocking problems', str(errors)),
        Line(unknown or (CAUTION if warnings else DONE), 'Warnings', str(warnings)),
        Line(unknown or (MISSING if conflicts else DONE), 'Conflicts', str(conflicts)),
    )


def _apply_results(controller):
    current, errors, _warnings, conflicts = _review_lines(controller)
    if getattr(controller, 'applied', False):
        return (Line(DONE, 'Review up to date'), Line(DONE, 'Applied'))
    ready = current and not errors and not conflicts
    return (
        Line(DONE if ready else MISSING, 'Review up to date'),
        Line(MISSING if ready else OPTIONAL, 'Applied', 'not yet'),
    )


_STEPS = {
    'Netlist source': _netlist_source,
    'Netclass type': _netclass_type,
    'Layer assignment': _layer_assignment,
    'Routing permissions': _routing_permissions,
    'Connector assignments': _connector_assignments,
    'RF transitions': _rf_transitions,
    'Define RF gangs': _define_rf_gangs,
    'Mechanical placement': _mechanical_placement,
    'Interposer microvias': _interposer_microvias,
    'Board outline': _board_outline,
    'Review changes': _review_changes,
    'Apply / results': _apply_results,
}


def checklist(controller, step):
    """Return the fixed checklist lines of ``step`` with their current state."""
    return tuple(_STEPS[step](controller))


def overall(lines):
    """One status for the step list: the most pressing of the step's lines."""
    return max((line.status for line in lines), key=_SEVERITY.__getitem__, default=OPTIONAL)


def step_status(controller, step):
    return overall(checklist(controller, step))
