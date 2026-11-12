"""Plain-language tables for Review changes and Apply / results.

Pure logic: no wx and no pcbnew. Review shows two things: the problems found,
each with the step that fixes it, and what Apply will change, as a short
summary whose lines open a list of the items behind them. Raw records and
UUIDs stay out of these tables; they go to the message log.
"""
from dataclasses import dataclass

REVIEW_STEP = 'Review changes'

# The step where the drafter fixes each kind of problem.
_STEP_OF_CODE = {
    'Netlist source': (
        'workbook', 'mapping', 'required', 'cell_error', 'ignored_range',
        'connector_pin', 'pad_missing',
    ),
    'Netclass type': (
        'netclass_type_missing', 'netclass_type_conflict', 'connector_type_conflict',
        'rf_ground_signal',
    ),
    'Layer assignment': ('layer', 'layer_missing', 'layer_unmapped'),
    'Routing permissions': ('layer_disallowed',),
    'Connector assignments': (
        'symbol_missing', 'part_missing', 'part_incompatible', 'mpn_missing',
        'mpn_conflict', 'interposer_missing', 'interposer_symbol_missing',
        'interposer_incompatible', 'interposer_population', 'interposer_pin_count',
        'interposer_pin_missing', 'dc_pin_missing', 'dc_multi_unit', 'dc_pin_count',
    ),
    'RF transitions': ('rf_transition',),
    'Define RF gangs': (
        'gang_definition_missing', 'gang_pattern', 'gang_population', 'unaccepted_draft',
    ),
    'Mechanical placement': (
        'anchor_missing', 'drawing_not_inspected', 'drawing_association_stale',
        'mounting_hole_anchor_missing', 'interposer_pose_missing',
        'interposer_side_missing', 'interposer_pad_circles_missing',
    ),
    'Interposer microvias': ('microvias', 'size'),
}
_STEPS = {code: step for step, codes in _STEP_OF_CODE.items() for code in codes}
_PREFIXES = (('duplicate_', 'Netlist source'), ('microvia', 'Interposer microvias'),
             ('outline', 'Board outline'), ('gang_', 'Define RF gangs'),
             ('layer_', 'Layer assignment'))

_LEVELS = {'error': 'Error', 'warning': 'Warning', 'conflict': 'Conflict'}
_ORDER = {'Error': 0, 'Conflict': 1, 'Warning': 2}


@dataclass(frozen=True)
class Problem:
    level: str
    step: str
    message: str


def step_of(code):
    """The step that owns a problem code; Review itself when none does."""
    code = str(code or '').casefold()
    if code in _STEPS:
        return _STEPS[code]
    return next((step for prefix, step in _PREFIXES if code.startswith(prefix)), REVIEW_STEP)


def problems(issues=(), preview=None, failure=None):
    """Every problem of the last review: blocking first, in a stable order."""
    rows = []
    for issue in issues:
        message = issue.message
        if issue.source_row:
            message = f'Row {issue.source_row}: {message}'
        rows.append(Problem(_LEVELS.get(issue.severity, 'Error'), step_of(issue.code), message))
    for warning in (preview or {}).get('warnings', ()):
        rows.append(Problem('Warning', step_of(warning.get('code')),
                            str(warning.get('message', ''))))
    for conflict in (preview or {}).get('conflicts', ()):
        subject = conflict.get('key') or 'A board item the workflow owns'
        rows.append(Problem('Conflict', REVIEW_STEP,
                            f'{subject}: {conflict.get("message", conflict.get("kind", ""))}'))
    if failure:
        rows.append(Problem('Error', REVIEW_STEP, str(failure)))
    return sorted(rows, key=lambda row: _ORDER.get(row.level, 3))


# --- What Apply will change ----------------------------------------------

# In the order a drafter thinks about a board: parts, vias, outline, files.
GROUPS = (
    ('add', 'Parts to add'), ('update', 'Parts to change'), ('remove', 'Parts to remove'),
    ('microvias', 'Interposer microvia sets to update'),
    ('outline', 'Board outlines to update'),
    ('project-file', 'Project files to write'),
    ('remove-project-file', 'Project files to remove'),
)


@dataclass(frozen=True)
class Change:
    item: str
    before: str
    after: str


def _group_of(change):
    if change.get('key') == 'interposer-microvias':
        return 'microvias'
    return change.get('kind', '')


def _side(value):
    return 'back' if value == 'B' else 'front'


def _part(spec, length):
    """One placed part in a few words: footprint, side, rotation, position."""
    if not spec:
        return '—'
    footprint = spec.get('footprint', {})
    words = [footprint.get('source_library_id') or footprint.get('library_id') or 'part',
             _side(spec.get('side'))]
    pose = spec.get('pose') or ()
    if len(pose) == 3:
        words.append(f'{float(pose[2]):g}°')
        if not spec.get('anchor'):
            words.append(f'at {length(pose[0])}, {length(pose[1])}')
    anchor = (spec.get('anchor') or {}).get('at') or ()
    if len(anchor) == 2:
        words.append(f'pin 1 at {length(anchor[0])}, {length(anchor[1])}')
    return ', '.join(words)


def _field(name, value, length):
    if value is None:
        return None
    if name == 'pose' and len(value) == 3:
        return f'at {length(value[0])}, {length(value[1])}, {float(value[2]):g}°'
    if name == 'side':
        return _side(value)
    if name == 'netclass':
        return f'netclass {value}'
    if name == 'signal_layer':
        return f'layer {value}'
    return str(value)


def _updated(change, length):
    """Only what differs, so a changed part reads as its change."""
    before, after = [], []
    for name in ('pose', 'side', 'netclass', 'signal_layer'):
        if name in change:
            for words, key in ((before, 'before'), (after, 'after')):
                text = _field(name, change[name].get(key), length)
                if text:
                    words.append(text)
    nets = change.get('assignments') or ()
    if nets:
        noun = 'pad net' if len(nets) == 1 else 'pad nets'
        before.append(f'{len(nets)} {noun}')
        after.append(f'{len(nets)} {noun} reassigned')
    if 'rf' in change:
        for words, key in ((before, 'before'), (after, 'after')):
            transition = change['rf'].get(key)
            words.append(f'RF transition {transition["artifact_id"]} rev '
                         f'{transition["revision"]}' if transition else 'no RF transition')
    return ', '.join(before) or '—', ', '.join(after) or '—'


def _detail(change, preview, length):
    group, key = _group_of(change), str(change.get('key', ''))
    desired, registry = preview.get('desired', {}), preview.get('registry', {})
    if group in ('add', 'update', 'remove'):
        after = desired.get('instances', {}).get(key)
        before = registry.get('instances', {}).get(key, {}).get('spec')
        if group == 'update':
            return Change(key, *_updated(change, length))
        return Change(key, _part(before, length), _part(after, length))
    if group == 'microvias':
        def stack(value):
            size = (value or {}).get('size') or {}
            if not value:
                return 'none'
            if size.get('diameter') and size.get('drill'):
                return f'{length(size["diameter"])} pad, {length(size["drill"])} hole'
            return 'set'
        return Change('Interposer microvias', stack(registry.get('microvia_spec')),
                      stack(desired.get('interposer_microvias')))
    if group == 'outline':
        def contour(value):
            if not value or not value.get('enabled'):
                return 'none'
            return 'managed outline' + (' with centre cutout'
                                        if value.get('cutout_uuids') else '')
        return Change('Board outline', contour(registry.get('outline_spec')),
                      contour(desired.get('outline')))
    owned = key in registry.get('generated_files', {})
    if group == 'remove-project-file':
        return Change(key, 'written by an earlier Apply', 'removed')
    return Change(key, 'written by an earlier Apply' if owned else '—', 'written')


def summary(preview, length=lambda value: f'{float(value):g} mm'):
    """``[(label, [Change, ...]), ...]`` for every kind of change in the preview."""
    preview = preview or {}
    found = {}
    for change in preview.get('changes', ()):
        found.setdefault(_group_of(change), []).append(_detail(change, preview, length))
    return [(_label(label, len(found[group])), found[group])
            for group, label in GROUPS if group in found]


_SINGULAR = {'Parts': 'Part', 'Project files': 'Project file',
             'Board outlines': 'Board outline',
             'Interposer microvia sets': 'Interposer microvia set'}


def _label(label, count):
    noun, _, rest = label.partition(' to ')
    return f'{_SINGULAR.get(noun, noun) if count == 1 else noun} to {rest}'


def headline(groups):
    """One line for the Apply tab: ``30 parts to add, 25 project files to write``."""
    if not groups:
        return 'nothing to change'
    return ', '.join(f'{len(changes)} {label[0].lower()}{label[1:]}'
                     for label, changes in groups)


# --- Apply stages ----------------------------------------------------------

STAGES = ('Staging the changes', 'Verifying the staged board', 'Updating the open board',
          'Writing project files', 'Finished')
