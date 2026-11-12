"""UI-independent project session. All native mutations belong to the staged backend."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import threading
import uuid
from .core.workbook import read_workbook, parse_sheet
from .core.layers import LayerRules, validate_layers
from .core.model import HEADERS, Issue

PAGES = ("Netlist source", "Netclass type", "Layer assignment", "Routing permissions",
         "Connector assignments", "RF transitions", "Define RF gangs", "Mechanical placement",
         "Interposer microvias", "Board outline", "Review changes", "Apply / results")


def row_ranges(text):
    """Parse explicit worksheet row numbers; reject malformed or descending ranges."""
    result = set()
    for token in text.split(','):
        token = token.strip()
        if not token:
            continue
        ends = token.split('-')
        if len(ends) > 2 or any(not v.strip().isdigit() for v in ends):
            raise ValueError("Excluded rows must be numbers or ranges, for example 3, 8-10")
        start, stop = int(ends[0]), int(ends[-1])
        if start < 1 or stop < start or stop - start > 1_048_576:
            raise ValueError("Excluded row range is invalid")
        result.update(range(start, stop + 1))
    return tuple(sorted(result))


class SavedSettingsError(ValueError):
    """The project sidecar exists but this version cannot load it."""

    def __init__(self, path, reason):
        super().__init__(f'Saved Workflow settings cannot be opened: {reason}')
        self.path = Path(path)
        self.reason = str(reason)


def discard_saved_settings(path):
    """Delete an unloadable sidecar and return its project identity if readable.

    Managed board and schematic objects derive their identities from the
    project id, so a fresh workflow keeps it whenever the old file still
    states a valid one. Nothing else is carried over.
    """
    path = Path(path)
    project_id = None
    try:
        project_id = str(uuid.UUID(json.loads(path.read_text(encoding='utf-8'))['project_id']))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    path.unlink(missing_ok=True)
    return project_id


def open_controller(board_path, confirm_discard, **options):
    """Open the project workflow, offering to discard unloadable saved settings.

    ``confirm_discard(error)`` decides. Return ``None`` when the saved file is
    kept, which leaves it untouched and Workflow closed.
    """
    try:
        return WorkflowController(board_path, **options)
    except SavedSettingsError as error:
        if not confirm_discard(error):
            return None
        project_id = discard_saved_settings(error.path)
        return WorkflowController(board_path, project_id=project_id, **options)


class WorkflowController:
    """Persist drafts separately from accepted backend state and invalidate every preview."""
    def __init__(self, board_path=None, backend=None, dispatch=None, project_id=None):
        self.board_path = Path(board_path).resolve() if board_path else None
        self.config_path = self.board_path.with_suffix('.connector-interposer-workflow.json') if self.board_path else None
        self.backend = backend
        self.dispatch = dispatch or (lambda fn, *args: fn(*args))
        from .core.config import ProjectConfig
        self.config = ProjectConfig(project_id=project_id or str(uuid.uuid4())).to_dict()
        self.config['outline'] = {"enabled": False}
        self.workbook = None
        self.workbook_sha256 = None
        self.table = None
        self.copper_layers = ('F.Cu', 'B.Cu')
        self.board_microvia_rules = {
            'presets': (), 'min_diameter': 0.0, 'min_drill': 0.0,
            'min_clearance': 0.0,
        }
        self.new_rules = ()
        self.preview = None
        self.preview_token = None
        self.issues = ()
        self.busy = False
        self.applied = False
        self.cancelled = threading.Event()
        self._generation = 0
        if self.config_path and self.config_path.exists():
            try:
                saved = json.loads(self.config_path.read_text(encoding='utf-8'))
                saved = ProjectConfig.from_dict(saved).to_dict()
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                raise SavedSettingsError(self.config_path, error) from error
            self.config.update(saved)

    def changed(self):
        self._generation += 1
        self.preview = self.preview_token = None
        self.issues = ()
        self.applied = False

    def reset_current(self):
        """Return the current workflow to defaults without changing project identity or disk."""
        if self.busy:
            raise RuntimeError('Cancel or wait for the current operation before resetting Workflow settings')
        from .core.config import ProjectConfig
        project_id = self.config['project_id']
        self.config = ProjectConfig(project_id=project_id).to_dict()
        self.config['outline'] = {"enabled": False}
        self.workbook = None
        self.workbook_sha256 = None
        self.table = None
        self.new_rules = ()
        self.cancelled.clear()
        self.changed()

    def delete_saved_settings(self):
        """Delete only the persisted project sidecar; keep the current workflow in memory."""
        if self.config_path is None:
            raise ValueError('Save the PCB to a project before deleting Workflow settings')
        if not self.config_path.exists():
            return False
        self.config_path.unlink()
        return True

    def save(self):
        if self.config_path is None:
            raise ValueError('Save the PCB to a project before saving Workflow settings')
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        source = self.config.get('workbook', {})
        if source.get('path') and self.workbook_sha256:
            path = Path(source['path'])
            if path.is_file():
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != self.workbook_sha256:
                    raise ValueError('Workbook changed since parsing; reload it before saving or comparing')
                relative = '.connector-interposer-inputs/' + self.workbook_sha256 + path.suffix.lower()
                snapshot = self.config_path.parent / relative
                snapshot.parent.mkdir(exist_ok=True)
                snapshot.write_bytes(data)
                source['snapshot'] = relative
        from .core.config import ProjectConfig
        ProjectConfig.from_dict(self.config)
        temporary = self.config_path.with_suffix(self.config_path.suffix + '.tmp')
        temporary.write_text(json.dumps(self.config, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        temporary.replace(self.config_path)

    def open_workbook(self, path):
        self.changed()
        self.table = None
        self.workbook = None
        before = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        self.workbook = read_workbook(path)
        self.workbook_sha256 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if self.workbook_sha256 != before:
            self.workbook = None
            raise ValueError('Workbook changed while reading; reload it')
        return self.workbook

    def load_workbook(self, path, worksheet=None, header_row=1, ignored='', mapping=None):
        self.open_workbook(path)
        worksheet = worksheet or self.workbook.sheets[0].name
        return self.parse(worksheet, header_row, ignored, mapping, str(Path(path).resolve()))

    def parse(self, worksheet, header_row=1, ignored='', mapping=None, path=None):
        self.changed()
        self.table = None  # An invalid range or mapping must never leave View data active.
        selection = row_ranges(ignored) if isinstance(ignored, str) else tuple(ignored)
        if self.workbook is None:
            raise ValueError('Choose a workbook first')
        self.table = parse_sheet(self.workbook.sheet(worksheet), int(header_row), mapping, selection)
        self.config['workbook'] = {"path": path or self.config['workbook'].get('path', ''),
                                   "worksheet": worksheet, "header_row": int(header_row),
                                   "mapping": self.table.mapping, "ignored_rows": list(selection), "sha256": self.workbook_sha256}
        rules = LayerRules(deepcopy(self.config['layer_rules']))
        self.new_rules = rules.synchronize({r.netclass for r in self.table.records}, self.copper_layers)
        self.config['layer_rules'] = rules.to_dict()
        return self.table

    @property
    def view_data_enabled(self):
        return not self.busy and self.table is not None and self.table.view_data_enabled

    def set_layers(self, copper_layers):
        self.copper_layers = tuple(copper_layers)
        rules = LayerRules(deepcopy(self.config['layer_rules']))
        self.new_rules = rules.synchronize({r.netclass for r in self.table.records} if self.table else (), self.copper_layers)
        self.config['layer_rules'] = rules.to_dict()
        self.changed()

    def set_board_microvia_rules(self, presets, min_diameter=0, min_drill=0,
                                 min_clearance=0):
        """Set transient Board Setup constraints; they are never copied into the sidecar."""
        pairs = []
        for pair in presets:
            if len(pair) != 2:
                raise ValueError('Predefined via sizes must be diameter/drill pairs')
            pairs.append((round(float(pair[0]), 9), round(float(pair[1]), 9)))
        self.board_microvia_rules = {
            'presets': tuple(pairs), 'min_diameter': float(min_diameter),
            'min_drill': float(min_drill), 'min_clearance': float(min_clearance),
        }
        selected = self.config.get('microvias', {}).get('size', {})
        if selected:
            try:
                selected_pair = (round(float(selected.get('diameter', 0)), 9),
                                 round(float(selected.get('drill', 0)), 9))
            except (AttributeError, TypeError, ValueError):
                selected_pair = None
            if selected_pair not in set(pairs):
                self.config['microvias'].pop('size', None)
        self.changed()

    def interposer_reference(self):
        references = sorted({record.interposer for record in self.table.records}) if self.table else []
        return references[0] if len(references) == 1 else ''

    def microvia_presets(self):
        """Return current Board Setup pairs classified against interposer pitch."""
        from .core.microvias import evaluate_presets
        from .core.libraries import read_footprint
        reference = self.interposer_reference()
        selection = self.config.get('interposers', {}).get(reference, {})
        centres = ()
        if selection.get('path') and selection.get('library_id'):
            try:
                part = read_footprint(
                    self.resolve_path(selection['path'], selection.get('sha256')),
                    selection['library_id'], selection.get('sha256'),
                )
                centres = tuple(pad.position for pad in part.pads
                                if pad.number and pad.kind != 'np_thru_hole')
            except (OSError, ValueError):
                centres = ()
        return evaluate_presets(
            self.board_microvia_rules['presets'],
            min_diameter=self.board_microvia_rules['min_diameter'],
            min_drill=self.board_microvia_rules['min_drill'],
            min_clearance=self.board_microvia_rules['min_clearance'],
            pad_centres=centres,
        )

    def set_microvias_enabled(self, enabled):
        if enabled and not self.microvia_available():
            raise ValueError('Assign an interposer footprint before enabling microvias')
        self.config['microvias']['enabled'] = bool(enabled)
        self.changed()

    def set_microvia_size(self, diameter, drill):
        pair = (round(float(diameter), 9), round(float(drill), 9))
        preset = next((item for item in self.microvia_presets()
                       if (item.diameter, item.drill) == pair), None)
        if preset is None:
            raise ValueError('Select a size from the current Board Setup predefined list')
        if not preset.eligible:
            raise ValueError(preset.reason)
        self.config['microvias']['size'] = {
            'diameter': preset.diameter, 'drill': preset.drill,
        }
        self.changed()

    def microvia_available(self):
        reference = self.interposer_reference()
        return bool(reference and self.config.get('interposers', {}).get(reference, {}).get('path'))

    def microvia_counts(self):
        """Return ground pins, signal pins and derived via count for ready inputs."""
        if not self.table or not self.microvia_available():
            return 0, 0, 0
        from .core.libraries import read_footprint
        from .core.microvias import stack_plan
        reference = self.interposer_reference()
        selection = self.config['interposers'][reference]
        try:
            part = read_footprint(
                self.resolve_path(selection['path'], selection.get('sha256')),
                selection['library_id'], selection.get('sha256'),
            )
        except (OSError, ValueError):
            return 0, 0, 0
        records = {record.interposer_pin: record for record in self.table.records
                   if record.interposer == reference and record.signal.casefold() != 'gnd'}
        ground = signal = total = 0
        for pad in part.pads:
            if not pad.number or pad.kind == 'np_thru_hole':
                continue
            record = records.get(pad.number)
            if record is None:
                ground += 1
                total += len(stack_plan(self.copper_layers, selection.get('side', 'F')))
            else:
                signal += 1
                layer = self.config['layer_mapping'].get(record.layer)
                if layer in self.copper_layers and layer not in (self.copper_layers[0], self.copper_layers[-1]):
                    total += len(stack_plan(self.copper_layers, selection.get('side', 'F'), layer))
        return ground, signal, total

    def set_rule(self, netclass, layer, allowed):
        if layer not in self.copper_layers:
            raise ValueError('Rule layer is absent from the stackup')
        self.config['layer_rules'].setdefault(netclass, {})[layer] = bool(allowed)
        self.changed()

    def set_layer_mapping(self, architecture, copper_layer):
        if copper_layer not in self.copper_layers:
            raise ValueError('Mapped layer is absent from the stackup')
        self.config['layer_mapping'][architecture] = copper_layer
        self.changed()

    def connector_names(self):
        return tuple(sorted({r.connector for r in self.table.records})) if self.table else ()

    def connector_type(self, name):
        from .core.connector_types import connector_type
        return connector_type(self.table.records if self.table else (),
                              self.config['netclass_types'], name)

    def gang_names(self):
        return tuple(name for name in self.connector_names()
                     if self.connector_type(name) == 'RF')

    def dc_connector_names(self):
        return tuple(name for name in self.connector_names()
                     if self.connector_type(name) == 'DC')

    def set_netclass_type(self, netclass, kind):
        from .core.connector_types import VALID_TYPES
        if kind not in VALID_TYPES:
            raise ValueError('Netclass type must be RF or DC')
        self.config['netclass_types'][netclass] = kind
        self.changed()

    def gang(self, name):
        if name not in self.config['gangs']:
            # A new gang has no pattern: any default would be a placeholder that
            # looks meaningful. It becomes defined when its rows, pitch and
            # numbering are entered and match the netlist.
            self.config['gangs'][name] = {"gang": name, "row_lengths": [], "pitch_x": None,
                "pitch_y": None, "row_offsets": [], "numbering": [], "traversal": "row-major",
                "rotation": 0, "connector_rotation": 0, "mirror": False, "anchor": [0, 0],
                "display_units": {"pitch_x": "mil", "pitch_y": "mil", "row_offsets": [],
                                  "anchor_x": "mil", "anchor_y": "mil"},
                "defined": False, "part_id": "", "symbol": {},
                "anchor_accepted": False, "side": "F"}
        return deepcopy(self.config['gangs'][name])

    def set_gang(self, name, values):
        merged = self.gang(name)
        previous = deepcopy(merged)
        merged.update(deepcopy(values))
        merged['gang'] = name
        merged.pop('mpn', None)
        if values.get('part_id') and Path(values['part_id']).is_file() and (values['part_id'] != previous.get('part_id') or not previous.get('part_sha256')):
            from .core.libraries import read_footprint
            part = read_footprint(values['part_id'], values.get('library_id') or library_id(values['part_id']))
            merged['part_sha256'] = part.sha256
            merged['footprint_mpn'] = part.mpn
        self.config['gangs'][name] = merged
        self.changed()

    @staticmethod
    def rf_requirement_key(netclass, layer, footprint):
        return json.dumps([netclass, layer, footprint], ensure_ascii=False,
                          separators=(',', ':'))

    def rf_transition_requirements(self):
        """Group RF locations by the project-wide transition assignment identity."""
        grouped = {}
        if not self.table:
            return ()
        for record in self.table.records:
            if self.connector_type(record.connector) != 'RF':
                continue
            gang = self.gang(record.connector)
            layer = self.config['layer_mapping'].get(record.layer, '')
            footprint = gang.get('library_id', '')
            key = self.rf_requirement_key(record.netclass, layer, footprint)
            requirement = grouped.setdefault(key, {
                'key': key, 'netclass': record.netclass, 'layer': layer,
                'footprint': footprint, 'locations': [], 'part_sha256s': set(),
                'part_paths': set(), 'sides': set(),
            })
            requirement['locations'].append(record.key)
            if gang.get('part_sha256'):
                requirement['part_sha256s'].add(gang['part_sha256'])
            if gang.get('part_id'):
                requirement['part_paths'].add(gang['part_id'])
            requirement['sides'].add(gang.get('side', 'F'))
        result = []
        for requirement in grouped.values():
            item = dict(requirement)
            item['locations'] = tuple(sorted(item['locations']))
            item['part_sha256s'] = tuple(sorted(item['part_sha256s']))
            item['part_paths'] = tuple(sorted(item['part_paths']))
            item['sides'] = tuple(sorted(item['sides']))
            item['assignment'] = deepcopy(self.config['artifacts'].get(item['key'], {}))
            result.append(item)
        return tuple(sorted(result, key=lambda item: (
            item['netclass'], item['layer'], item['footprint'], item['key'])))

    def rf_transition_requirement(self, record):
        gang = self.gang(record.connector)
        key = self.rf_requirement_key(
            record.netclass, self.config['layer_mapping'].get(record.layer, ''),
            gang.get('library_id', ''),
        )
        return next((item for item in self.rf_transition_requirements()
                     if item['key'] == key), None)

    def _checked_transition(self, requirement, path):
        from .artifacts import load_artifact
        if not requirement['layer'] or not requirement['footprint']:
            raise ValueError('Assign the KiCad layer and connector footprint first')
        if len(requirement['part_sha256s']) != 1:
            raise ValueError('The requirement does not resolve to one footprint revision')
        if len(requirement['sides']) != 1:
            raise ValueError('The requirement is used on both board sides')
        artifact = load_artifact(
            path, layers=self.copper_layers,
            part_sha256=requirement['part_sha256s'][0],
            netclass=requirement['netclass'],
        )
        compatibility = artifact['compatibility']
        if compatibility['part_id'] != requirement['footprint']:
            raise ValueError('RF transition was approved for a different connector footprint')
        expected_side = 'front' if requirement['sides'][0] == 'F' else 'back'
        if compatibility['side'] != expected_side:
            raise ValueError('RF transition was approved for a different board side')
        if len(requirement['part_paths']) != 1:
            raise ValueError('The requirement does not resolve to one footprint source')
        from .core.libraries import read_footprint
        footprint = read_footprint(
            self.resolve_path(requirement['part_paths'][0], requirement['part_sha256s'][0]),
            requirement['footprint'], requirement['part_sha256s'][0],
        )
        signal_pads = [pad for pad in footprint.pads if pad.number == '1']
        if not signal_pads:
            raise ValueError('Connector footprint has no signal pad 1')
        kinds = {pad.kind for pad in signal_pads}
        if compatibility['mount'] == 'PTH' and kinds != {'thru_hole'}:
            raise ValueError('PTH RF transition requires a plated through-hole signal pad')
        if compatibility['mount'] == 'SMD' and not kinds <= {'smd', 'connect'}:
            raise ValueError('SMD RF transition requires a surface-mount signal pad')
        signal_layer = artifact['payload']['layer_roles']['signal']
        if signal_layer != requirement['layer']:
            raise ValueError(
                f'RF transition signal layer {signal_layer} differs from mapped layer '
                f'{requirement["layer"]}'
            )
        return artifact

    def set_rf_transition(self, requirement_key, path):
        requirement = next((item for item in self.rf_transition_requirements()
                            if item['key'] == requirement_key), None)
        if requirement is None:
            raise ValueError('RF transition requirement is stale; refresh the page')
        if not path:
            self.config['artifacts'].pop(requirement_key, None)
            self.changed()
            return
        source = Path(path).resolve()
        artifact = self._checked_transition(requirement, source)
        self.config['artifacts'][requirement_key] = {
            'path': str(source), 'sha256': artifact['hashes']['artifact'],
            'artifact_id': artifact['artifact_id'], 'revision': artifact['revision'],
        }
        self.changed()

    def validate_rf_transition(self, requirement):
        assignment = requirement.get('assignment', {})
        if not assignment.get('path'):
            raise ValueError('Choose an approved RF transition')
        path = self.resolve_path(
            assignment['path'], assignment.get('sha256'), 'rf', '.json',
        )
        artifact = self._checked_transition(requirement, path)
        if (assignment.get('sha256') and
                artifact['hashes']['artifact'] != assignment['sha256']):
            raise ValueError(
                'Selected RF transition revision changed; explicitly select the new revision'
            )
        return artifact

    def dc_connector(self, name):
        if name not in self.config['dc_connectors']:
            self.config['dc_connectors'][name] = {
                'reference': name, 'symbol': {}, 'path': '', 'library_id': '',
                'sha256': '', 'anchor_pad': '1', 'anchor': [0, 0],
                'rotation': 0, 'side': 'F', 'anchor_circle': {},
                'anchor_accepted': False,
            }
        return deepcopy(self.config['dc_connectors'][name])

    def set_dc_connector(self, name, values):
        merged = self.dc_connector(name)
        merged.update(deepcopy(values))
        merged['reference'] = name
        self.config['dc_connectors'][name] = merged
        self.changed()

    def pattern(self, name):
        return self.gang_definition(self.gang(name)).positions()

    @staticmethod
    def gang_definition(values):
        """Build the geometry of a gang; a pitch with nothing to space is not needed."""
        from .core.geometry import GangDefinition
        values = dict(values)
        rows = values.get('row_lengths') or []
        if not rows:
            raise ValueError('add a row and enter its positions')
        if values.get('pitch_x') is None and max(rows) == 1:
            values['pitch_x'] = values.get('pitch_y') or 1.0
        if values.get('pitch_y') is None and len(rows) == 1:
            values['pitch_y'] = values.get('pitch_x')
        if values.get('pitch_x') is None:
            raise ValueError('enter the position pitch')
        if values.get('pitch_y') is None:
            raise ValueError('enter the row pitch')
        fields = GangDefinition.__dataclass_fields__
        return GangDefinition(**{k: v for k, v in values.items() if k in fields})

    def copy_gang(self, source, targets):
        values = self.gang(source)
        copied_fields = (
            'row_lengths', 'pitch_x', 'pitch_y', 'row_offsets', 'numbering',
            'traversal', 'rotation', 'connector_rotation', 'mirror', 'display_units',
            'defined', 'side',
        )
        for target in targets:
            if target == source:
                continue
            copied = {key: deepcopy(values[key]) for key in copied_fields if key in values}
            source_units = deepcopy(copied.get('display_units', {}))
            target_units = self.gang(target).get('display_units', {})
            source_units['anchor_x'] = target_units.get('anchor_x', 'mil')
            source_units['anchor_y'] = target_units.get('anchor_y', 'mil')
            copied['display_units'] = source_units
            self.set_gang(target, copied)

    def validate(self):
        issues = list(self.table.issues) if self.table else [Issue('workbook', 'Parse a workbook first')]
        from .core.libraries import read_footprint, read_symbol, footprint_matches_filters

        def pin_count_text(count):
            return f'{count} pin' + ('' if count == 1 else 's')

        def assignment_issue(owner, symbol_spec, footprint_spec, require_two_pin):
            if not symbol_spec.get('path') or not footprint_spec.get('path'):
                return None
            try:
                symbol_path = self.resolve_path(symbol_spec['path'], symbol_spec.get('sha256'),
                                                'symbols', '.kicad_sym')
                symbol = read_symbol(symbol_path, symbol_spec['library_id'], symbol_spec.get('sha256'),
                                     require_two_pin=require_two_pin)
                footprint = read_footprint(
                    self.resolve_path(footprint_spec['path'], footprint_spec.get('sha256')),
                    footprint_spec['library_id'], footprint_spec.get('sha256'),
                )
                if not symbol.footprint_filters:
                    return f'{owner}: selected symbol has no footprint filters'
                if not footprint_matches_filters(footprint.library_id, symbol.footprint_filters):
                    return f'{owner}: {footprint.library_id} is not allowed by {symbol.library_id}'
                if {pin[0] for pin in symbol.pin_numbers} != set(footprint.pad_numbers):
                    return f'{owner}: symbol pins do not exactly match footprint electrical pads'
            except (KeyError, OSError, ValueError) as error:
                return f'{owner}: {error}'
            return None

        if self.config.get('drafts'):
            issues.append(Issue('unaccepted_draft', 'Accept or correct each modified gang pattern before reviewing changes'))
        if self.table:
            from .core.connector_types import (VALID_TYPES, reference_type,
                                               suggest_netclass_types)
            drawing_revision = self.config.get('mechanical', {}).get(
                'inspected_revision', '',
            )
            if not drawing_revision:
                issues.append(Issue(
                    'drawing_not_inspected',
                    'Open Mechanical placement so the imported drawing is read',
                ))
            def stale_circle(feature, accepted):
                if (accepted and drawing_revision and
                        accepted.get('revision') != drawing_revision):
                    issues.append(Issue(
                        'drawing_association_stale',
                        f'{feature}: the drawing changed; pick its circle again in Mechanical placement',
                    ))
            encountered = sorted({record.netclass for record in self.table.records})
            _suggestions, inference_conflicts, _unresolved = suggest_netclass_types(self.table.records)
            for netclass in encountered:
                kind = self.config['netclass_types'].get(netclass)
                if kind not in VALID_TYPES:
                    issues.append(Issue('netclass_type_missing',
                                        f'{netclass}: assign RF or DC in Netclass type'))
                if netclass in inference_conflicts:
                    issues.append(Issue(
                        'netclass_type_conflict',
                        f'{netclass}: connector naming supplies conflicting RF/DC evidence',
                    ))
                if netclass.strip().casefold() == 'dc' and kind and kind != 'DC':
                    issues.append(Issue('netclass_type_conflict',
                                        f'{netclass}: the exact DC netclass cannot be classified as RF'))
            for reference in self.connector_names():
                kinds = {self.config['netclass_types'].get(record.netclass)
                         for record in self.table.records if record.connector == reference}
                if None in kinds or not kinds:
                    continue
                if len(kinds) != 1:
                    issues.append(Issue('connector_type_conflict',
                                        f'{reference}: attached netclasses must all be RF or all be DC'))
                    continue
                inferred = reference_type(reference)
                if inferred and next(iter(kinds)) != inferred:
                    issues.append(Issue(
                        'connector_type_conflict',
                        f'{reference}: project naming identifies this as {inferred}, but its netclasses are {next(iter(kinds))}',
                    ))
            for record in self.table.records:
                if (self.config['netclass_types'].get(record.netclass) == 'RF' and
                        record.signal.casefold() == 'gnd'):
                    issues.append(Issue('rf_ground_signal',
                                        'RF signal locations cannot use GND as their signal net',
                                        record.source_row, 'signal'))
            issues.extend(validate_layers(self.table.records, self.config['layer_mapping'], self.copper_layers,
                                         LayerRules(self.config['layer_rules'])))
            for name in self.gang_names():
                gang = self.gang(name)
                if not gang.get('defined'):
                    issues.append(Issue('gang_definition_missing',
                                        f'{name}: define the RF gang position pattern'))
                if not gang.get('symbol', {}).get('path'):
                    issues.append(Issue('symbol_missing', f'{name}: select an approved connector symbol'))
                if not gang['part_id']:
                    issues.append(Issue('part_missing', f'{name}: select an approved connector footprint'))
                detail = assignment_issue(
                    name, gang.get('symbol', {}),
                    {'path': gang.get('part_id', ''), 'library_id': gang.get('library_id', ''),
                     'sha256': gang.get('part_sha256', '')}, True,
                )
                if detail:
                    issues.append(Issue('part_incompatible', detail))
                if not gang.get('symbol_mpn') and not gang.get('footprint_mpn'):
                    issues.append(Issue('mpn_missing', f'{name}: selected library parts have no MPN metadata', severity='warning'))
                if (gang.get('symbol_mpn') and gang.get('footprint_mpn') and
                        gang['symbol_mpn'] != gang['footprint_mpn']):
                    issues.append(Issue('mpn_conflict', f'{name}: symbol and footprint MPN metadata disagree'))
                if not gang.get('anchor_accepted') or not gang.get('anchor_circle', {}).get('uuid'):
                    issues.append(Issue(
                        'anchor_missing',
                        f'{name}: select the RF location 1 circle in Mechanical placement',
                    ))
                else:
                    stale_circle(
                        f'{name} RF location 1', gang.get('anchor_circle', {}),
                    )
                if gang.get('defined'):
                    try:
                        pins = set(self.pattern(name))
                        expected = {r.connector_pin for r in self.table.records if r.connector == name}
                        if {str(p) for p in pins} != expected:
                            issues.append(Issue('gang_population', f'{name}: numbering must exactly match workbook locations'))
                    except (ValueError, TypeError) as error:
                        issues.append(Issue('gang_pattern', f'{name}: {error}'))
            for requirement in self.rf_transition_requirements():
                if not requirement.get('assignment', {}).get('path'):
                    continue
                label = ' / '.join((
                    requirement['netclass'], requirement['layer'] or 'unmapped layer',
                    requirement['footprint'] or 'unassigned footprint',
                ))
                try:
                    self.validate_rf_transition(requirement)
                except (OSError, ValueError) as error:
                    issues.append(Issue('rf_transition', f'{label}: {error}'))
            for name in self.dc_connector_names():
                selection = self.dc_connector(name)
                if not selection.get('symbol', {}).get('path'):
                    issues.append(Issue('symbol_missing', f'{name}: select an approved DC connector symbol'))
                if not selection.get('path'):
                    issues.append(Issue('part_missing', f'{name}: select an approved DC connector footprint'))
                detail = assignment_issue(name, selection.get('symbol', {}), selection, False)
                if detail:
                    issues.append(Issue('part_incompatible', detail))
                try:
                    symbol = read_symbol(
                        self.resolve_path(selection.get('symbol', {}).get('path', ''),
                                          selection.get('symbol', {}).get('sha256'),
                                          'symbols', '.kicad_sym'),
                        selection.get('symbol', {}).get('library_id', ''),
                        selection.get('symbol', {}).get('sha256'), require_two_pin=False,
                    )
                    if symbol.unit_count != 1:
                        issues.append(Issue('dc_multi_unit',
                                            f'{name}: this release supports single-unit DC symbols only'))
                    used = {record.connector_pin for record in self.table.records
                            if record.connector == name}
                    available = {pin[0] for pin in symbol.pin_numbers}
                    if len(available) < len(used):
                        issues.append(Issue(
                            'dc_pin_count',
                            f'{name}: symbol has {pin_count_text(len(available))}; '
                            f'{pin_count_text(len(used))} are used',
                        ))
                    elif not used <= available:
                        issues.append(Issue('dc_pin_missing',
                                            f'{name}: symbol is missing workbook pins {sorted(used-available)}'))
                except (KeyError, OSError, ValueError):
                    pass
                if (not selection.get('anchor_accepted') or
                        not selection.get('anchor_circle', {}).get('uuid')):
                    issues.append(Issue('anchor_missing',
                                        f'{name}: select the DC pin 1 circle in Mechanical placement'))
                else:
                    stale_circle(
                        f'{name} DC pin 1', selection.get('anchor_circle', {}),
                    )
                if not selection.get('symbol_mpn') and not selection.get('footprint_mpn'):
                    issues.append(Issue('mpn_missing',
                                        f'{name}: selected library parts have no MPN metadata',
                                        severity='warning'))
                if (selection.get('symbol_mpn') and selection.get('footprint_mpn') and
                        selection['symbol_mpn'] != selection['footprint_mpn']):
                    issues.append(Issue('mpn_conflict',
                                        f'{name}: symbol and footprint MPN metadata disagree'))
            references = sorted({record.interposer for record in self.table.records})
            if len(references) != 1:
                issues.append(Issue('interposer_population', 'This release requires exactly one interposer reference'))
            for reference in references:
                selection = self.config['interposers'].get(reference, {})
                if not selection.get('symbol', {}).get('path'):
                    issues.append(Issue('interposer_symbol_missing', f'{reference}: select an approved interposer symbol'))
                if not selection.get('path'):
                    issues.append(Issue('interposer_missing', f'{reference}: select an approved interposer footprint'))
                detail = assignment_issue(reference, selection.get('symbol', {}), selection, False)
                if detail:
                    issues.append(Issue('interposer_incompatible', detail))
                try:
                    symbol_spec = selection.get('symbol', {})
                    symbol = read_symbol(
                        self.resolve_path(symbol_spec.get('path', ''),
                                          symbol_spec.get('sha256'),
                                          'symbols', '.kicad_sym'),
                        symbol_spec.get('library_id', ''),
                        symbol_spec.get('sha256'), require_two_pin=False,
                    )
                    used = {record.interposer_pin for record in self.table.records
                            if record.interposer == reference}
                    available = {pin[0] for pin in symbol.pin_numbers}
                    if len(available) < len(used):
                        issues.append(Issue(
                            'interposer_pin_count',
                            f'{reference}: symbol has {pin_count_text(len(available))}; '
                            f'{pin_count_text(len(used))} are used',
                        ))
                    elif not used <= available:
                        issues.append(Issue(
                            'interposer_pin_missing',
                            f'{reference}: symbol is missing workbook pins {sorted(used-available)}',
                        ))
                except (KeyError, OSError, ValueError):
                    pass
                if (not selection.get('pose_accepted') or
                        not selection.get('pose_circle', {}).get('uuid')):
                    issues.append(Issue(
                        'interposer_pose_missing',
                        f'{reference}: select the interposer origin circle in Mechanical placement',
                    ))
                else:
                    stale_circle(
                        f'{reference} interposer origin',
                        selection.get('pose_circle', {}),
                    )
                if selection.get('pose_accepted') and selection.get('side') not in ('F', 'B'):
                    issues.append(Issue(
                        'interposer_side_missing',
                        f'{reference}: choose Front or Back in Mechanical placement',
                    ))
                if not selection.get('symbol_mpn') and not selection.get('footprint_mpn'):
                    issues.append(Issue('mpn_missing', f'{reference}: selected library parts have no MPN metadata', severity='warning'))
                if (selection.get('symbol_mpn') and selection.get('footprint_mpn') and
                        selection['symbol_mpn'] != selection['footprint_mpn']):
                    issues.append(Issue('mpn_conflict', f'{reference}: symbol and footprint MPN metadata disagree'))
            for hole in self.config.get('mounting_holes', []):
                if not hole.get('pose_circle', {}).get('uuid'):
                    issues.append(Issue(
                        'mounting_hole_anchor_missing',
                        f'{hole.get("reference", "Mounting hole")}: pick its circle in Mechanical placement',
                    ))
                else:
                    stale_circle(
                        f'{hole.get("reference", "Mounting hole")} mounting hole',
                        hole.get('pose_circle', {}),
                    )
            microvias = self.config.get('microvias', {})
            if microvias.get('enabled'):
                reference = self.interposer_reference()
                if not self.microvia_available():
                    issues.append(Issue(
                        'microvia_interposer_missing',
                        'Assign an interposer footprint before generating interposer microvias',
                    ))
                selected = microvias.get('size', {})
                try:
                    pair = (round(float(selected.get('diameter', 0)), 9),
                            round(float(selected.get('drill', 0)), 9))
                except (TypeError, ValueError):
                    pair = (0.0, 0.0)
                preset = next((item for item in self.microvia_presets()
                               if (item.diameter, item.drill) == pair), None)
                if preset is None:
                    issues.append(Issue(
                        'microvia_size_missing',
                        'Select a current Board Setup predefined microvia size',
                    ))
                elif not preset.eligible:
                    issues.append(Issue(
                        'microvia_size_incompatible',
                        f'{reference}: selected microvia size {preset.reason}',
                    ))
                for record in self.table.records:
                    if record.interposer != reference or record.signal.casefold() == 'gnd':
                        continue
                    layer = self.config['layer_mapping'].get(record.layer)
                    if layer in (self.copper_layers[0], self.copper_layers[-1]):
                        issues.append(Issue(
                            'microvia_outer_signal_layer',
                            f'{record.interposer} pin {record.interposer_pin}: '
                            f'interposer signals cannot route on outside layer {layer}',
                            record.source_row, 'layer',
                        ))
                issues.append(Issue(
                    'microvia_ground_connectivity',
                    'Interposer GND sub-stacks are generated on GND; final plane connectivity '
                    'and fabrication validity are verified outside this workflow',
                    severity='warning',
                ))
        self.issues = tuple(issues)
        return self.issues

    def resolve_path(self, path, checksum=None, kind='footprints', suffix='.kicad_mod'):
        if self.board_path and checksum:
            pinned = self.board_path.parent / '.connector-interposer-pinned' / kind / (checksum + suffix)
            if pinned.is_file(): return str(pinned)
        candidate = Path(path)
        if not candidate.is_absolute() and self.board_path:
            candidate = self.board_path.parent / candidate
        return str(candidate)

    def token(self):
        digest = hashlib.sha256(json.dumps(self.config, sort_keys=True).encode())
        paths = [self.board_path, Path(self.config['workbook'].get('path', ''))]
        if self.board_path:
            paths.extend(self.board_path.with_suffix(suffix) for suffix in ('.kicad_pro', '.connector-interposer-instances.json'))
        paths.extend(Path(g['part_id']) for g in self.config['gangs'].values() if g.get('part_id'))
        paths.extend(Path(g['symbol']['path']) for g in self.config['gangs'].values() if g.get('symbol', {}).get('path'))
        paths.extend(Path(v['path']) for v in self.config['dc_connectors'].values() if v.get('path'))
        paths.extend(Path(v['symbol']['path']) for v in self.config['dc_connectors'].values()
                     if v.get('symbol', {}).get('path'))
        paths.extend(Path(selection['path']) for selection in self.config['artifacts'].values()
                     if selection.get('path'))
        paths.extend(Path(v['path']) for v in self.config['interposers'].values() if v.get('path'))
        paths.extend(Path(v['symbol']['path']) for v in self.config['interposers'].values() if v.get('symbol', {}).get('path'))
        paths.extend(Path(v['path']) for v in self.config.get('mounting_holes', []) if v.get('path'))
        for path in paths:
            if path and path.is_file():
                digest.update(str(path).encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def run_async(self, operation, complete, failed, pinned=True):
        """Run in the background. A pinned result is dropped if settings change meanwhile."""
        if self.busy:
            raise RuntimeError('An analysis is already running')
        self.busy = True
        self.cancelled.clear()
        generation = self._generation
        def run():
            try:
                result = operation()
                if pinned and (self.cancelled.is_set() or self._generation != generation):
                    raise RuntimeError('Analysis cancelled or inputs changed; compare again')
            except Exception as error:
                self.busy = False
                self.dispatch(failed, error)
            else:
                self.busy = False
                self.dispatch(complete, result)
        threading.Thread(target=run, daemon=True, name='connector-interposer-analysis').start()

    def cancel(self):
        self.cancelled.set()
        self.changed()

    def review(self):
        self.save()
        issues = self.validate()
        if any(i.severity == 'error' for i in issues):
            raise ValueError('Resolve validation errors before reviewing changes')
        if self.backend is None:
            raise RuntimeError('KiCad project backend is unavailable')
        before = self.token()
        preview = self.backend.preview(self)
        if before != self.token():
            raise RuntimeError('Inputs changed during comparison; compare again')
        self.preview, self.preview_token = preview, before
        return preview

    def apply(self):
        if self.preview is None or self.preview_token != self.token():
            self.preview = None
            raise ValueError('Preview is stale; compare changes again')
        result = self.backend.apply(self, self.preview)
        return result

    def publish(self, prepared, pcbnew_module):
        if self.preview is None or self.preview_token != self.token():
            raise ValueError('Preview became stale before live publication')
        try:
            result = self.backend.publish_live(
                self, self.preview, prepared, pcbnew_module,
            )
        except Exception:
            self.preview = self.preview_token = None
            raise
        self.preview = self.preview_token = None
        self.save()
        self.applied = True
        return result


def library_id(path):
    """A selected .pretty library supplies its native nickname by default."""
    path = Path(path)
    return path.parent.stem + ':' + path.stem


class NativeProcessBackend:
    """Run pcbnew in an isolated interpreter, keeping SWIG off wx worker threads."""
    def __init__(self, python=None):
        import os
        import subprocess
        import sys
        module = sys.modules.get('pcbnew')
        self.expected_version = module.Version() if module else None
        if python is not None:
            command = [str(value) for value in python] if isinstance(python, (list, tuple)) else [str(python)]
            if not command or not command[0]:
                raise RuntimeError('Explicit KiCad Python interpreter command is empty')
            self._validate_interpreter(command, subprocess, os, explicit=True)
            self.python = command
            return
        checked = []
        for description, command in self._interpreter_candidates(module, sys):
            executable = Path(command[0])
            if not executable.is_file():
                checked.append(f'{description}: {executable} was not found')
                continue
            try:
                self._validate_interpreter(command, subprocess, os)
            except RuntimeError as error:
                checked.append(f'{description}: {error}')
                continue
            self.python = command
            return
        expected = f' KiCad {self.expected_version}' if self.expected_version else ''
        shown = checked[:12]
        if len(checked) > len(shown):
            shown.append(f'{len(checked) - len(shown)} additional runtime-relative locations were not usable')
        detail = '\n'.join('  - ' + item for item in shown) or '  - no runtime-relative candidates were available'
        raise RuntimeError(
            f'Cannot locate a bundled Python interpreter matching the active{expected} editor. Checked:\n{detail}\n'
            'Install the matching KiCad Python runtime or configure an explicit command with '
            'NativeProcessBackend(python=...). System PATH interpreters are not selected automatically.'
        )

    @staticmethod
    def _interpreter_candidates(module, sys_module):
        """Yield runtime-relative commands without consulting the system PATH."""
        major, minor = sys_module.version_info.major, sys_module.version_info.minor
        origin = Path(module.__file__).resolve() if module and getattr(module, '__file__', None) else None
        candidates = []
        if origin:
            runtime = next((parent / 'AppRun' for parent in origin.parents if (parent / 'AppRun').is_file()), None)
            if runtime:
                candidates.append(('KiCad AppImage runtime', [str(runtime), f'python{major}.{minor}']))

        executable = Path(sys_module.executable).resolve() if getattr(sys_module, 'executable', None) else None
        windows_layout = bool(executable and executable.suffix.casefold() == '.exe')
        windows_names = ('python.exe', f'python{major}.{minor}.exe', 'python3.exe',
                         'pythonw.exe', f'pythonw{major}.{minor}.exe')
        posix_names = (f'python{major}.{minor}', 'python3', 'python')
        names = windows_names + posix_names if windows_layout else posix_names + windows_names
        roots = []
        if executable:
            roots.append(('active editor executable directory', executable.parent))
        for label, value in (('Python prefix', getattr(sys_module, 'prefix', None)),
                             ('Python execution prefix', getattr(sys_module, 'exec_prefix', None))):
            if value:
                root = Path(value).resolve()
                if root != Path(root.anchor):
                    roots.append((label, root))

        if executable and executable.name.casefold().startswith('python'):
            candidates.append(('active Python executable', [str(executable)]))
        for label, root in roots:
            for directory in (root, root / 'bin'):
                for name in names:
                    candidates.append((label, [str(directory / name)]))

        seen = set()
        for description, command in candidates:
            key = tuple(value.casefold() if windows_layout else value for value in command)
            if key not in seen:
                seen.add(key)
                yield description, command

    @staticmethod
    def _subprocess_options(subprocess_module, os_module):
        if os_module.name == 'nt' and hasattr(subprocess_module, 'CREATE_NO_WINDOW'):
            return {'creationflags': subprocess_module.CREATE_NO_WINDOW}
        return {}

    @staticmethod
    def _worker_diagnostics(stderr, stdout):
        raw = stderr.strip() or stdout.strip()
        lines = [line for line in raw.splitlines()
                 if 'Adding duplicate image handler for' not in line]
        return ('\n'.join(lines).strip() or
                'no process diagnostics (KiCad image-handler warnings omitted)')

    def _validate_interpreter(self, command, subprocess_module, os_module, explicit=False):
        import tempfile
        probe = '''import json,pathlib,traceback
target=pathlib.Path(__import__("sys").argv[1])
try:
 import pcbnew
 payload={"ok":True,"version":pcbnew.Version()}
except Exception as error:
 payload={"ok":False,"error":str(error),"traceback":traceback.format_exc()}
target.write_text(json.dumps(payload),encoding="utf-8")
raise SystemExit(0 if payload["ok"] else 3)
'''
        with tempfile.TemporaryDirectory(prefix='connector-interposer-runtime-') as temporary:
            result = Path(temporary) / 'probe.json'
            environment = dict(os_module.environ, CONNECTOR_INTERPOSER_WORKFLOW_WORKER='1')
            try:
                process = subprocess_module.run(
                    command + ['-c', probe, str(result)], env=environment, capture_output=True,
                    text=True, timeout=30, **self._subprocess_options(subprocess_module, os_module)
                )
            except (OSError, subprocess_module.SubprocessError) as error:
                qualifier = 'Explicit interpreter' if explicit else 'Interpreter'
                raise RuntimeError(f'{qualifier} {command[0]} could not start: {error}') from error
            payload = None
            if result.is_file():
                try:
                    payload = json.loads(result.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    pass
            if process.returncode or not payload or not payload.get('ok'):
                reason = payload.get('error') if isinstance(payload, dict) else None
                reason = reason or process.stderr.strip() or 'the pcbnew validation probe returned no result'
                raise RuntimeError(f'{command[0]} cannot run the KiCad pcbnew worker: {reason}')
            actual = str(payload.get('version', ''))
            if self.expected_version and actual != self.expected_version:
                raise RuntimeError(
                    f'{command[0]} reports KiCad {actual or "unknown"}; active editor is KiCad {self.expected_version}'
                )

    def call(self, operation, **request):
        import os
        import subprocess
        import tempfile
        request['operation'] = operation
        request['expected_kicad_version'] = self.expected_version
        package_dir = Path(__file__).resolve().parent
        script = '''import faulthandler, importlib.util, json, pathlib, sys
faulthandler.enable()
root=pathlib.Path(sys.argv[1])
spec=importlib.util.spec_from_file_location("connector_interposer_worker",root/"__init__.py",submodule_search_locations=[str(root)])
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
from connector_interposer_worker.backend import NativeBackend
request=json.loads(pathlib.Path(sys.argv[2]).read_text())
backend=NativeBackend()
operation=request.pop("operation")
expected=request.pop("expected_kicad_version",None)
if expected and backend.p.Version()!=expected: raise RuntimeError("Worker KiCad version differs from active editor")
if operation=="preview": result=backend.preview(**request)
elif operation=="apply": result=backend.apply(**request)
elif operation=="prepare_live": result=backend.prepare_live(**request)
elif operation=="inspect": result=backend.inspect_graphics(**request)
else: raise ValueError("Unknown worker operation")
pathlib.Path(sys.argv[3]).write_text(json.dumps(result))
# pcbnew may fault while its native globals are torn down in a nested KiCad
# runtime. The completed result is already durably closed; skip that teardown.
__import__("os")._exit(0)
'''
        environment = dict(os.environ, CONNECTOR_INTERPOSER_WORKFLOW_WORKER='1')
        with tempfile.TemporaryDirectory(prefix='connector-interposer-native-') as temporary:
            source, result = Path(temporary)/'request.json', Path(temporary)/'result.json'
            source.write_text(json.dumps(request), encoding='utf-8')
            process = subprocess.run(self.python + ['-c', script, str(package_dir), str(source), str(result)],
                                     env=environment, capture_output=True, text=True, timeout=300,
                                     **self._subprocess_options(subprocess, os))
            if process.returncode:
                diagnostics = self._worker_diagnostics(
                    process.stderr, process.stdout,
                )
                raise RuntimeError(
                    f'Native project analysis failed (exit {process.returncode}): '
                    + diagnostics[-6000:]
                )
            if not result.exists():
                raise RuntimeError('Native project worker returned no result')
            return json.loads(result.read_text())

    def preview(self, controller):
        from .core.libraries import read_footprint, read_symbol, footprint_matches_filters
        from .core.model import validate_records
        from .core.geometry import (
            feature_deviation_message, feature_tolerance, match_circle_features,
            match_selected_circle, suggest_selected_circle,
        )
        from .schematic import PartSelection, generate_schematic, merge_library_table
        if not controller.board_path or not controller.board_path.exists():
            raise ValueError('Save the source PCB before comparing')
        cfg, records = controller.config, controller.table.records
        gang_parts, dc_parts, interposer_parts = {}, {}, {}
        selected_symbols = {}
        for gang in controller.gang_names():
            settings = controller.gang(gang)
            symbol_spec = settings.get('symbol', {})
            if not symbol_spec.get('path') or not symbol_spec.get('library_id'):
                raise ValueError(f'{gang}: select the approved two-pin connector symbol')
            symbol_path = controller.resolve_path(symbol_spec['path'], symbol_spec.get('sha256'), 'symbols', '.kicad_sym')
            symbol = read_symbol(symbol_path, symbol_spec['library_id'], symbol_spec.get('sha256'))
            footprint = read_footprint(controller.resolve_path(settings['part_id'], settings.get('part_sha256')), settings.get('library_id') or library_id(settings['part_id']), settings.get('part_sha256'))
            if not symbol.footprint_filters:
                raise ValueError(f'{gang}: selected symbol has no footprint filters')
            if not footprint_matches_filters(footprint.library_id, symbol.footprint_filters):
                raise ValueError(f'{gang}: {footprint.library_id} is not allowed by {symbol.library_id}')
            footprint = replace(footprint, library_id='WorkflowPart_' + footprint.sha256[:16] + ':' + footprint.library_id.split(':', 1)[1])
            mpns = {value for value in (settings.get('symbol_mpn', ''), settings.get('footprint_mpn', '')) if value}
            if len(mpns) > 1:
                raise ValueError(f'{gang}: symbol and footprint MPN metadata disagree')
            gang_parts[gang] = PartSelection(
                footprint, symbol, settings.get('value') or symbol.value or footprint.value,
                next(iter(mpns), ''),
            )
            selected_symbols[symbol.sha256] = symbol_path
        for reference in controller.dc_connector_names():
            selection = controller.dc_connector(reference)
            symbol_spec = selection.get('symbol', {})
            if not symbol_spec.get('path') or not symbol_spec.get('library_id'):
                raise ValueError(f'{reference}: select the approved DC connector symbol')
            symbol_path = controller.resolve_path(symbol_spec['path'], symbol_spec.get('sha256'),
                                                  'symbols', '.kicad_sym')
            symbol = read_symbol(symbol_path, symbol_spec['library_id'],
                                 symbol_spec.get('sha256'), require_two_pin=False)
            if symbol.unit_count != 1:
                raise ValueError(f'{reference}: this release supports single-unit DC symbols only')
            footprint = read_footprint(
                controller.resolve_path(selection['path'], selection.get('sha256')),
                selection['library_id'], selection.get('sha256'),
            )
            if not symbol.footprint_filters:
                raise ValueError(f'{reference}: selected symbol has no footprint filters')
            if not footprint_matches_filters(footprint.library_id, symbol.footprint_filters):
                raise ValueError(f'{reference}: {footprint.library_id} is not allowed by {symbol.library_id}')
            if {pin[0] for pin in symbol.pin_numbers} != set(footprint.pad_numbers):
                raise ValueError(f'{reference}: symbol pins do not exactly match footprint electrical pads')
            used = {record.connector_pin for record in records if record.connector == reference}
            if not used <= set(footprint.pad_numbers):
                raise ValueError(f'{reference}: selected part is missing workbook pins')
            footprint = replace(
                footprint,
                library_id='WorkflowPart_' + footprint.sha256[:16] + ':' +
                           footprint.library_id.split(':', 1)[1],
            )
            mpns = {value for value in (selection.get('symbol_mpn', ''),
                                        selection.get('footprint_mpn', '')) if value}
            if len(mpns) > 1:
                raise ValueError(f'{reference}: symbol and footprint MPN metadata disagree')
            dc_parts[reference] = PartSelection(
                footprint, symbol, selection.get('value') or symbol.value or footprint.value,
                next(iter(mpns), ''),
            )
            selected_symbols[symbol.sha256] = symbol_path
        for reference, selection in cfg['interposers'].items():
            symbol_spec = selection.get('symbol', {})
            if not symbol_spec.get('path') or not symbol_spec.get('library_id'):
                raise ValueError(f'{reference}: select the approved interposer symbol')
            symbol_path = controller.resolve_path(symbol_spec['path'], symbol_spec.get('sha256'), 'symbols', '.kicad_sym')
            symbol = read_symbol(symbol_path, symbol_spec['library_id'], symbol_spec.get('sha256'), require_two_pin=False)
            footprint = read_footprint(controller.resolve_path(selection['path'], selection.get('sha256')), selection['library_id'], selection.get('sha256'))
            if not symbol.footprint_filters:
                raise ValueError(f'{reference}: selected symbol has no footprint filters')
            if not footprint_matches_filters(footprint.library_id, symbol.footprint_filters):
                raise ValueError(f'{reference}: {footprint.library_id} is not allowed by {symbol.library_id}')
            if {pin[0] for pin in symbol.pin_numbers} != set(footprint.pad_numbers):
                raise ValueError(f'{reference}: symbol pins do not exactly match footprint electrical pads')
            footprint = replace(footprint, library_id='WorkflowPart_' + footprint.sha256[:16] + ':' + footprint.library_id.split(':', 1)[1])
            mpns = {value for value in (selection.get('symbol_mpn', ''), selection.get('footprint_mpn', '')) if value}
            if len(mpns) > 1:
                raise ValueError(f'{reference}: symbol and footprint MPN metadata disagree')
            interposer_parts[reference] = PartSelection(
                footprint, symbol, selection.get('value') or symbol.value or footprint.value,
                next(iter(mpns), ''),
            )
            selected_symbols[symbol.sha256] = symbol_path
        issues = validate_records(records, {ref: part.footprint.pad_numbers for ref, part in interposer_parts.items()})
        if issues: raise ValueError('; '.join(i.message for i in issues))
        # Only saved native graphics are inspected; unsaved canvas changes are never applied.
        mechanical = cfg['mechanical']
        if not mechanical.get('accepted'):
            raise ValueError('Set the imported drawing layer and placement anchors first')
        native = self.call(
            'inspect', board_path=str(controller.board_path),
            source_layer=mechanical.get('layer', 'User.Drawings'),
        )
        if 'circles' not in native:
            raise RuntimeError('Native inspector returned no mechanical circle inventory')
        inspected_revision = mechanical.get('inspected_revision', '')
        if not inspected_revision:
            raise ValueError('Open Mechanical placement so the imported drawing is read')
        if native.get('revision') != inspected_revision:
            raise ValueError(
                'The saved drawing changed since it was read; open Mechanical '
                'placement, then pick again every part it marks'
            )
        rf_records = tuple(record for record in records
                           if controller.connector_type(record.connector) == 'RF')
        rf_keys = {record.key for record in rf_records}
        tolerance = feature_tolerance(mechanical['feature_tolerance_mm'])
        claimed = set()
        def selected_circle(feature, accepted):
            if accepted.get('revision') != inspected_revision:
                suggestion = suggest_selected_circle(
                    feature, accepted, native['circles'], tolerance, claimed,
                )
                detail = (f' Suggested circle: {suggestion["uuid"]} at '
                          f'{suggestion["center"]}.' if suggestion else '')
                raise ValueError(
                    f'{feature}: association belongs to another drawing revision.'
                    f'{detail} Select the circle again to confirm it.'
                )
            return match_selected_circle(
                feature, accepted, native['circles'], tolerance, claimed,
            )
        interposer_anchors = {}
        for reference, selection in cfg['interposers'].items():
            accepted = selection.get('pose_circle', {})
            circle = selected_circle(
                f'{reference} interposer origin circle', accepted,
            )
            position = selection.get('pose', ())[:2]
            if len(position) != 2:
                raise ValueError(f'{reference}: interposer position is incomplete')
            deviation = math.dist(position, circle['center'])
            if deviation > tolerance:
                raise ValueError(feature_deviation_message(
                    f'{reference} interposer origin', deviation, tolerance,
                ))
            claimed.add(circle['uuid'])
            interposer_anchors[reference] = {
                'circle_uuid': circle['uuid'], 'center': list(circle['center']),
            }
        mounting_hole_anchors = {}
        for hole in cfg.get('mounting_holes', []):
            reference = hole['reference']
            accepted = hole.get('pose_circle', {})
            circle = selected_circle(
                f'{reference} mounting-hole circle', accepted,
            )
            position = hole.get('pose', ())[:2]
            if len(position) != 2:
                raise ValueError(f'{reference}: mounting-hole position is incomplete')
            deviation = math.dist(position, circle['center'])
            if deviation > tolerance:
                raise ValueError(feature_deviation_message(
                    f'{reference} mounting-hole origin', deviation, tolerance,
                ))
            claimed.add(circle['uuid'])
            mounting_hole_anchors[reference] = {
                'circle_uuid': circle['uuid'], 'center': list(circle['center']),
            }
        rf_features = []
        required_rf_circles = {}
        rf_anchors = {}
        for gang in controller.gang_names():
            settings = controller.gang(gang)
            accepted = settings.get('anchor_circle', {})
            circle = selected_circle(f'{gang} location 1 circle', accepted)
            required_rf_circles[gang + '-1'] = circle['uuid']
            rf_anchors[gang] = {
                'circle_uuid': circle['uuid'], 'center': list(circle['center']),
            }
            for pin, position in controller.pattern(gang).items():
                rf_features.append((gang + '-' + str(pin), position))
        rf_matches = match_circle_features(
            sorted(rf_features), native['circles'], tolerance,
            claimed=claimed, required=required_rf_circles,
        )
        rf_locations = [
            {name: match[name] for name in ('key', 'center', 'circle_uuid')}
            for match in rf_matches
        ]
        claimed.update(match['circle_uuid'] for match in rf_matches)
        dc_anchors = {}
        for reference in controller.dc_connector_names():
            selection = controller.dc_connector(reference)
            circle = selected_circle(
                f'{reference} pin 1 circle', selection.get('anchor_circle', {}),
            )
            position = selection.get('anchor', ())
            if len(position) != 2:
                raise ValueError(f'{reference}: pin 1 position is incomplete')
            deviation = math.dist(position, circle['center'])
            if deviation > tolerance:
                raise ValueError(feature_deviation_message(
                    f'{reference} pin 1', deviation, tolerance,
                ))
            claimed.add(circle['uuid'])
            dc_anchors[reference] = {
                'circle_uuid': circle['uuid'], 'center': list(circle['center']),
            }
        name = controller.board_path.stem
        ledger_path = controller.board_path.with_suffix(
            '.connector-interposer-instances.json',
        )
        current_registry = (json.loads(ledger_path.read_text())
                            if ledger_path.is_file() else {})
        root_path = controller.board_path.with_suffix('.kicad_sch')
        bundle = generate_schematic(
            records, gang_parts, interposer_parts, cfg['project_id'],
            project_name=name, dc_parts=dc_parts,
            root_schematic=(root_path.read_text(encoding='utf-8-sig')
                            if root_path.is_file() else None),
            expected_root_sheet=current_registry.get('schematic_root'),
        )
        bom_by_reference = {row['Reference']: row for row in bundle.bom}
        symbol_fields = lambda reference: {name: bom_by_reference[reference][name] for name in ('Value', 'MPN')}
        instances = {}
        for record in records:
            if controller.connector_type(record.connector) != 'RF':
                continue
            settings = controller.gang(record.connector)
            part = gang_parts[record.connector]
            x, y = controller.pattern(record.connector)[record.connector_pin]
            instance = {'kind': 'connector', 'connector_type': 'RF', 'gang': record.connector,
                'footprint': dict(part.footprint.pin(), approved=True, source_library_id=settings.get('library_id') or library_id(settings['part_id'])),
                'pose': [x, y, settings.get('connector_rotation', 0)], 'pads': {'1': record.signal, '2': 'GND'},
                'symbol_path': bundle.paths[record.key], 'symbol_fields': symbol_fields(record.key), 'netclass': record.netclass,
                'signal_layer': cfg['layer_mapping'][record.layer], 'side': settings.get('side', 'F')}
            requirement = controller.rf_transition_requirement(record)
            if requirement and requirement.get('assignment', {}).get('path'):
                artifact = controller.validate_rf_transition(requirement)
                instance['rf'] = {
                    'artifact': artifact,
                    'nets': {'signal': record.signal, 'ground': 'GND'},
                    'layers': {},
                    'clock': settings.get('connector_rotation', 0),
                }
            instances[record.key] = instance
        for reference, part in dc_parts.items():
            selection = controller.dc_connector(reference)
            assignments = {record.connector_pin: record.signal for record in records
                           if record.connector == reference}
            anchor = selection.get('anchor', [])
            circle = selection.get('anchor_circle', {})
            instances[reference] = {
                'kind': 'connector', 'connector_type': 'DC', 'gang': reference,
                'footprint': dict(
                    part.footprint.pin(), approved=True,
                    source_library_id=selection.get('library_id'),
                ),
                'pose': [0, 0, float(selection.get('rotation', 0))],
                'anchor': {
                    'pad': '1', 'at': list(anchor),
                    'circle_uuid': circle.get('uuid', ''),
                },
                'pads': assignments, 'symbol_path': bundle.paths[reference],
                'symbol_fields': symbol_fields(reference),
                'side': selection.get('side', 'F'),
            }
        for reference, part in interposer_parts.items():
            signals = {r.interposer_pin: r.signal for r in records if r.interposer == reference}
            selection = cfg['interposers'][reference]
            instances[reference] = {'kind': 'interposer', 'footprint': dict(part.footprint.pin(), approved=True),
                'pose': selection.get('pose', [0, 0, 0]), 'pads': {pin: signals.get(pin, 'GND') for pin in part.footprint.pad_numbers},
                'symbol_path': bundle.paths.get(reference, ''), 'symbol_fields': symbol_fields(reference),
                'side': selection['side']}
        for hole in cfg.get('mounting_holes', []):
            part = read_footprint(controller.resolve_path(hole['path'], hole.get('sha256')), hole['library_id'], hole.get('sha256'), allow_mechanical=True)
            part = replace(part, library_id='WorkflowPart_' + part.sha256[:16] + ':' + part.library_id.split(':', 1)[1])
            instances[hole['reference']] = {'kind': 'mounting-hole', 'footprint': dict(part.pin(), approved=True), 'pose': hole['pose'], 'pads': {}}
        outline = deepcopy(cfg['outline'])
        outline['source_layer'] = mechanical.get('layer', 'User.Drawings')
        if outline.get('cutout_uuids') and isinstance(outline['cutout_uuids'][0], str): outline['cutout_uuids'] = [outline['cutout_uuids']]
        project_files = dict(bundle.files)
        from .core.sexpr import loads, dumps, child, children
        footprint_table = loads(project_files['fp-lib-table'])
        for entry in children(footprint_table, 'lib'):
            nickname = child(entry, 'name')[1]
            child(entry, 'uri')[1] = '${KIPRJMOD}/.connector-interposer-pinned/' + nickname + '.pretty'
        project_files['fp-lib-table'] = dumps(footprint_table) + '\n'
        for table_name in ('sym-lib-table', 'fp-lib-table'):
            if table_name in project_files:
                table_path = controller.board_path.parent / table_name
                project_files[table_name] = dumps(loads(merge_library_table(table_path.read_text() if table_path.exists() else None, project_files[table_name]))) + '\n'
        project_files[name + '-connector-bom.csv'] = bundle.bom_csv()
        managed_files = set(bundle.managed_files)
        managed_files.add(name + '-connector-bom.csv')
        for checksum, path in selected_symbols.items():
            pinned_name = '.connector-interposer-pinned/symbols/' + checksum + '.kicad_sym'
            project_files[pinned_name] = Path(path).read_text()
            managed_files.add(pinned_name)
        from .core.project import merge_project_settings
        project_path = controller.board_path.with_suffix('.kicad_pro')
        project = json.loads(project_path.read_text()) if project_path.exists() else {}
        project = merge_project_settings(project, records)
        project_files[name + '.kicad_pro'] = json.dumps(project, sort_keys=True, indent=2) + '\n'
        desired = {'project_id': cfg['project_id'], 'instances': instances,
                   'outline': outline, 'project_files': project_files,
                   'managed_files': sorted(managed_files),
                   'schematic_root': bundle.schematic_root,
                   'project_file_merges': [name + '.kicad_pro', name + '.kicad_sch',
                                           'sym-lib-table', 'fp-lib-table'],
                   'mechanical': {
                       'source_layer': mechanical.get('layer', 'User.Drawings'),
                       'rf_matches': rf_locations,
                       'rf_keys': sorted(rf_keys),
                       'rf_anchors': rf_anchors,
                       'dc_anchors': dc_anchors,
                       'interposer_anchors': interposer_anchors,
                       'mounting_hole_anchors': mounting_hole_anchors,
                       'feature_tolerance_mm': tolerance,
                       'drawing_revision': inspected_revision,
                   }}
        if cfg.get('microvias', {}).get('enabled'):
            reference = controller.interposer_reference()
            selection = cfg['interposers'][reference]
            signals = {
                record.interposer_pin: {
                    'net': record.signal,
                    'layer': cfg['layer_mapping'][record.layer],
                }
                for record in records
                if record.interposer == reference and record.signal.casefold() != 'gnd'
            }
            desired['interposer_microvias'] = {
                'enabled': True,
                'reference': reference,
                'diameter': cfg['microvias']['size']['diameter'],
                'drill': cfg['microvias']['size']['drill'],
                'layers': list(controller.copper_layers),
                'side': selection['side'],
                'pose': list(selection.get('pose', [0, 0, 0])),
                'footprint_sha256': interposer_parts[reference].footprint.sha256,
                'signals': signals,
                'ground_net': 'GND',
                'protection': 'none',
            }
        return self.call('preview', board_path=str(controller.board_path), desired=desired)

    def apply(self, controller, preview):
        if controller.cancelled.is_set(): raise RuntimeError('Apply cancelled before starting')
        return self.call('prepare_live', preview=preview)

    def publish_live(self, controller, preview, prepared, pcbnew_module):
        from .backend import NativeBackend
        if controller.cancelled.is_set():
            raise RuntimeError('Apply cancelled before live publication')
        board = pcbnew_module.GetBoard()
        if board is None:
            raise RuntimeError('The reviewed PCB is no longer open')
        return NativeBackend(pcbnew_module).publish_live(
            board, preview, prepared,
        )
