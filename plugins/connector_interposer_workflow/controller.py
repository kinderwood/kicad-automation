"""UI-independent project session. All native mutations belong to the staged backend."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import threading
import uuid
from .core.workbook import read_workbook, parse_sheet
from .core.layers import LayerRules, validate_layers
from .core.model import HEADERS, Issue

PAGES = ("Netlist Source", "Layer assignment", "Validation", "Gangs", "DXF Anchors", "Board Outline", "Review Changes", "Apply / Results")


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


class WorkflowController:
    """Persist drafts separately from accepted backend state and invalidate every preview."""
    def __init__(self, board_path=None, backend=None, dispatch=None):
        self.board_path = Path(board_path).resolve() if board_path else None
        self.config_path = self.board_path.with_suffix('.connector-interposer-workflow.json') if self.board_path else None
        self.backend = backend
        self.dispatch = dispatch or (lambda fn, *args: fn(*args))
        self.config = {"schema_version": 1, "project_id": str(uuid.uuid4()), "workbook": {}, "layer_mapping": {}, "layer_rules": {},
                       "gangs": {}, "interposers": {}, "mechanical": {}, "mounting_holes": [],
                       "outline": {"enabled": False}, "presentation": "labels"}
        self.workbook = None
        self.workbook_sha256 = None
        self.table = None
        self.copper_layers = ('F.Cu', 'B.Cu')
        self.new_rules = ()
        self.preview = None
        self.preview_token = None
        self.issues = ()
        self.busy = False
        self.cancelled = threading.Event()
        self._generation = 0
        if self.config_path and self.config_path.exists():
            saved = json.loads(self.config_path.read_text(encoding='utf-8'))
            from .core.config import ProjectConfig
            saved = ProjectConfig.from_dict(saved).to_dict()
            if saved.get('schema_version') != 1:
                raise ValueError('Unsupported Workflow draft version; migrate explicitly before opening')
            self.config.update(saved)

    def changed(self):
        self._generation += 1
        self.preview = self.preview_token = None
        self.issues = ()

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

    def gang_names(self):
        return tuple(sorted({r.connector for r in self.table.records})) if self.table else ()

    def gang(self, name):
        if name not in self.config['gangs']:
            count = sum(r.connector == name for r in self.table.records) if self.table else 0
            self.config['gangs'][name] = {"gang": name, "row_lengths": [max(count, 1)], "pitch_x": 2.54,
                "pitch_y": 2.54, "row_offsets": [], "numbering": [], "traversal": "row-major",
                "rotation": 0, "mirror": False, "anchor": [0, 0], "units": "mm", "part_id": "", "rf_clock": 0,
                "rf_overrides": {}, "rf_artifact": "", "anchor_accepted": False, "side": "F"}
        return deepcopy(self.config['gangs'][name])

    def set_gang(self, name, values):
        merged = self.gang(name)
        previous = deepcopy(merged)
        merged.update(deepcopy(values))
        merged['gang'] = name
        if values.get('part_id') and Path(values['part_id']).is_file() and (values['part_id'] != previous.get('part_id') or not previous.get('part_sha256')):
            data = Path(values['part_id']).read_bytes()
            merged['part_sha256'] = hashlib.sha256(data).hexdigest()
            from .core.sexpr import loads, children
            properties = {str(item[1]).casefold(): str(item[2]) for item in children(loads(data.decode('utf-8-sig')), 'property') if len(item) >= 3}
            merged['approved_mpn'] = next((properties[key] for key in ('mpn', 'manufacturer part number', 'manufacturerpartnumber') if properties.get(key)), '')
        if values.get('rf_artifact') and Path(values['rf_artifact']).is_file() and (values['rf_artifact'] != previous.get('rf_artifact') or not previous.get('rf_artifact_hash')):
            merged['rf_artifact_hash'] = json.loads(Path(values['rf_artifact']).read_text()).get('hashes', {}).get('artifact')
        if values.get('rf_artifacts'):
            merged['rf_artifact_pins'] = {pin: previous.get('rf_artifact_pins', {}).get(pin) if path == previous.get('rf_artifacts', {}).get(pin) and previous.get('rf_artifact_pins', {}).get(pin) else json.loads(Path(path).read_text()).get('hashes', {}).get('artifact') for pin, path in values['rf_artifacts'].items() if Path(path).is_file()}
        self.config['gangs'][name] = merged
        self.changed()

    def pattern(self, name):
        from .core.geometry import GangDefinition
        values = self.gang(name)
        fields = GangDefinition.__dataclass_fields__
        return GangDefinition(**{k: v for k, v in values.items() if k in fields}).positions()

    def copy_gang(self, source, targets):
        values = self.gang(source)
        for target in targets:
            if target == source:
                continue
            copied = {k: deepcopy(v) for k, v in values.items() if k not in ('gang', 'anchor', 'anchor_accepted')}
            self.set_gang(target, copied)

    def validate(self):
        issues = list(self.table.issues) if self.table else [Issue('workbook', 'Parse a workbook first')]
        if self.config.get('drafts'):
            issues.append(Issue('unaccepted_draft', 'Accept or correct each modified gang pattern before reviewing changes'))
        if self.table:
            issues.extend(validate_layers(self.table.records, self.config['layer_mapping'], self.copper_layers,
                                         LayerRules(self.config['layer_rules'])))
            for name in self.gang_names():
                gang = self.gang(name)
                if not gang['part_id']:
                    issues.append(Issue('part_missing', f'{name}: select an approved connector footprint'))
                if not gang.get('approved_mpn') and not gang.get('mpn'):
                    issues.append(Issue('mpn_missing', f'{name}: approved part has no MPN; optionally supply a project BOM override', severity='warning'))
                if not gang.get('anchor_accepted'):
                    issues.append(Issue('anchor_missing', f'{name}: accept an anchor'))
                try:
                    pins = set(self.pattern(name))
                    expected = {r.connector_pin for r in self.table.records if r.connector == name}
                    if {str(p) for p in pins} != expected:
                        issues.append(Issue('gang_population', f'{name}: numbering must exactly match workbook locations'))
                except (ValueError, TypeError) as error:
                    issues.append(Issue('gang_pattern', f'{name}: {error}'))
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
        relevant = {k: v for k, v in self.config.items() if k != 'output_dir'}
        digest = hashlib.sha256(json.dumps(relevant, sort_keys=True).encode())
        paths = [self.board_path, Path(self.config['workbook'].get('path', ''))]
        if self.board_path:
            paths.extend(self.board_path.with_suffix(suffix) for suffix in ('.kicad_pro', '.connector-interposer-instances.json'))
        paths.extend(Path(g[key]) for g in self.config['gangs'].values() for key in ('part_id', 'rf_artifact') if g.get(key))
        paths.extend(Path(path) for g in self.config['gangs'].values() for path in g.get('rf_artifacts', {}).values() if path)
        paths.extend(Path(v['path']) for v in self.config['interposers'].values() if v.get('path'))
        paths.extend(Path(v['path']) for v in self.config.get('mounting_holes', []) if v.get('path'))
        symbol = self.config.get('connector_symbol', {})
        if symbol.get('path'): paths.append(Path(symbol['path']))
        for path in paths:
            if path and path.is_file():
                digest.update(str(path).encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def run_async(self, operation, complete, failed):
        if self.busy:
            raise RuntimeError('An analysis is already running')
        self.busy = True
        self.cancelled.clear()
        generation = self._generation
        def run():
            try:
                result = operation()
                if self.cancelled.is_set() or self._generation != generation:
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
        self.preview = self.preview_token = None
        self.save()
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
        script = '''import importlib.util, json, pathlib, sys
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
elif operation=="inspect": result=backend.inspect_graphics(**request)
else: raise ValueError("Unknown worker operation")
pathlib.Path(sys.argv[3]).write_text(json.dumps(result))
'''
        environment = dict(os.environ, CONNECTOR_INTERPOSER_WORKFLOW_WORKER='1')
        with tempfile.TemporaryDirectory(prefix='connector-interposer-native-') as temporary:
            source, result = Path(temporary)/'request.json', Path(temporary)/'result.json'
            source.write_text(json.dumps(request), encoding='utf-8')
            process = subprocess.run(self.python + ['-c', script, str(package_dir), str(source), str(result)],
                                     env=environment, capture_output=True, text=True, timeout=300,
                                     **self._subprocess_options(subprocess, os))
            if process.returncode:
                raise RuntimeError('Native project analysis failed: ' + process.stderr[-6000:])
            if not result.exists():
                raise RuntimeError('Native project worker returned no result')
            return json.loads(result.read_text())

    def preview(self, controller):
        from .core.libraries import read_footprint, read_symbol
        from .core.model import validate_records
        from .core.geometry import validate_mechanical_population
        from .schematic import PartSelection, generate_schematic, merge_library_table
        from .artifacts import load_artifact
        if not controller.board_path or not controller.board_path.exists():
            raise ValueError('Save the source PCB before comparing')
        cfg, records = controller.config, controller.table.records
        symbol_spec = cfg.get('connector_symbol', {})
        if not symbol_spec.get('path') or not symbol_spec.get('library_id'):
            raise ValueError('Select the approved two-pin connector symbol from the loaded KiCad libraries')
        symbol_path = controller.resolve_path(symbol_spec['path'], symbol_spec.get('sha256'), 'symbols', '.kicad_sym')
        symbol = read_symbol(symbol_path, symbol_spec['library_id'], symbol_spec.get('sha256'))
        gang_parts, interposer_parts = {}, {}
        for gang in controller.gang_names():
            settings = controller.gang(gang)
            footprint = read_footprint(controller.resolve_path(settings['part_id'], settings.get('part_sha256')), settings.get('library_id') or library_id(settings['part_id']), settings.get('part_sha256'))
            footprint = replace(footprint, library_id='WorkflowPart_' + footprint.sha256[:16] + ':' + footprint.library_id.split(':', 1)[1])
            gang_parts[gang] = PartSelection(footprint, symbol, settings.get('value', ''), settings.get('approved_mpn') or settings.get('mpn', ''))
        for reference, selection in cfg['interposers'].items():
            footprint = read_footprint(controller.resolve_path(selection['path'], selection.get('sha256')), selection['library_id'], selection.get('sha256'))
            footprint = replace(footprint, library_id='WorkflowPart_' + footprint.sha256[:16] + ':' + footprint.library_id.split(':', 1)[1])
            interposer_parts[reference] = PartSelection(footprint)
        issues = validate_records(records, {ref: part.footprint.pad_numbers for ref, part in interposer_parts.items()})
        if issues: raise ValueError('; '.join(i.message for i in issues))
        # Only saved native graphics are inspected; unsaved canvas changes are never applied.
        mechanical = cfg['mechanical']
        if not mechanical.get('accepted'): raise ValueError('Accept mechanical anchors and source layer first')
        native = self.call('inspect', board_path=str(controller.board_path), source_layer=mechanical.get('layer', 'Dwgs.User'), label_offset=mechanical.get('label_offset', [0, 0]))
        if native.get('errors'): raise ValueError('; '.join(str(i) for i in native['errors']))
        if 'locations' not in native: raise RuntimeError('Native inspector returned no mechanical association data')
        validate_mechanical_population(records, [p['key'] if isinstance(p, dict) else p for p in native['locations']])
        actual_locations = {location['key']: tuple(location['center']) for location in native['locations']}
        import math
        for gang in controller.gang_names():
            for pin, position in controller.pattern(gang).items():
                key = gang + '-' + str(pin)
                if math.dist(position, actual_locations[key]) > 1e-6:
                    raise ValueError(f'{key}: configured position {position} does not match mechanical circle {actual_locations[key]}; correct anchor, pattern or numbering')
        name = controller.board_path.stem
        bundle = generate_schematic(records, gang_parts, interposer_parts, cfg['project_id'], project_name=name)
        bom_by_reference = {row['Reference']: row for row in bundle.bom}
        symbol_fields = lambda reference: {name: bom_by_reference[reference][name] for name in ('Value', 'MPN')}
        instances = {}
        for record in records:
            settings = controller.gang(record.connector)
            part = gang_parts[record.connector]
            x, y = controller.pattern(record.connector)[record.connector_pin]
            instance = {'kind': 'connector', 'gang': record.connector,
                'footprint': dict(part.footprint.pin(), approved=True, source_library_id=settings.get('library_id') or library_id(settings['part_id'])),
                'pose': [x, y, settings['rotation']], 'pads': {'1': record.signal, '2': 'GND'},
                'symbol_path': bundle.paths[record.key], 'symbol_fields': symbol_fields(record.key), 'netclass': record.netclass,
                'signal_layer': cfg['layer_mapping'][record.layer], 'side': settings.get('side', 'F')}
            artifact_path = settings.get('rf_artifacts', {}).get(record.connector_pin) or settings.get('rf_artifact')
            if artifact_path:
                artifact_hash = settings.get('rf_artifact_pins', {}).get(record.connector_pin) if settings.get('rf_artifacts', {}).get(record.connector_pin) else settings.get('rf_artifact_hash')
                artifact_path = controller.resolve_path(artifact_path, artifact_hash, 'rf', '.json')
                artifact = load_artifact(artifact_path, layers=controller.copper_layers,
                                         part_sha256=part.footprint.sha256, netclass=record.netclass)
                if artifact_hash and artifact['hashes']['artifact'] != artifact_hash:
                    raise ValueError(f'{record.key}: selected RF artifact revision changed; explicitly select the new approved revision')
                signal_role = artifact.get('payload', {}).get('layer_roles', {}).get('signal')
                if signal_role != cfg['layer_mapping'][record.layer]:
                    raise ValueError(f'{record.key}: RF artifact signal layer {signal_role} differs from mapped routing layer {cfg["layer_mapping"][record.layer]}; select a compatible artifact')
                instance['rf'] = {'artifact': artifact, 'nets': {'signal': record.signal, 'ground': 'GND'},
                                  'layers': {}, 'clock': settings.get('rf_overrides', {}).get(record.connector_pin, settings['rf_clock'])}
            instances[record.key] = instance
        for reference, part in interposer_parts.items():
            signals = {r.interposer_pin: r.signal for r in records if r.interposer == reference}
            selection = cfg['interposers'][reference]
            instances[reference] = {'kind': 'interposer', 'footprint': dict(part.footprint.pin(), approved=True),
                'pose': selection.get('pose', [0, 0, 0]), 'pads': {pin: signals.get(pin, 'GND') for pin in part.footprint.pad_numbers},
                'symbol_path': bundle.paths.get(reference, ''), 'symbol_fields': symbol_fields(reference)}
        for hole in cfg.get('mounting_holes', []):
            part = read_footprint(controller.resolve_path(hole['path'], hole.get('sha256')), hole['library_id'], hole.get('sha256'), allow_mechanical=True)
            part = replace(part, library_id='WorkflowPart_' + part.sha256[:16] + ':' + part.library_id.split(':', 1)[1])
            instances[hole['reference']] = {'kind': 'mounting-hole', 'footprint': dict(part.pin(), approved=True), 'pose': hole['pose'], 'pads': {}}
        outline = deepcopy(cfg['outline'])
        outline['source_layer'] = mechanical.get('layer', 'Dwgs.User')
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
        project_files['.connector-interposer-pinned/symbols/' + symbol.sha256 + '.kicad_sym'] = Path(symbol_path).read_text()
        from .core.project import merge_project_settings
        project_path = controller.board_path.with_suffix('.kicad_pro')
        project = json.loads(project_path.read_text()) if project_path.exists() else {}
        project = merge_project_settings(project, records)
        project_files[name + '.kicad_pro'] = json.dumps(project, sort_keys=True, indent=2) + '\n'
        desired = {'instances': instances, 'outline': outline, 'project_files': project_files, 'project_file_merges': [name + '.kicad_pro', 'sym-lib-table', 'fp-lib-table'],
                   'mechanical': {'source_layer': mechanical.get('layer', 'Dwgs.User'), 'label_offset': mechanical.get('label_offset', [0, 0]), 'locations': native['locations']}}
        return self.call('preview', board_path=str(controller.board_path), desired=desired)

    def apply(self, controller, preview):
        output = controller.config.get('output_dir', '')
        if not output: raise ValueError('Choose a new output project directory')
        if controller.cancelled.is_set(): raise RuntimeError('Apply cancelled before starting')
        return self.call('apply', preview=preview, output_dir=str(Path(output).resolve()), closed_project=True)
