"""Resolve read-only parts from KiCad's active global and project library tables."""
from dataclasses import dataclass
import json
from pathlib import Path

from .sexpr import child, children, loads


@dataclass(frozen=True)
class LibraryEntry:
    nickname: str
    kind: str
    path: Path
    scope: str
    description: str = ''


@dataclass(frozen=True)
class LibraryPart:
    library_id: str
    path: Path
    scope: str
    description: str = ''

    @property
    def nickname(self):
        return self.library_id.split(':', 1)[0]

    @property
    def name(self):
        return self.library_id.split(':', 1)[1]


def _value(node, name, default=''):
    item = child(node, name)
    return str(item[1]) if item and len(item) > 1 else default


def _overrides(settings_path):
    path = Path(settings_path) / 'kicad_common.json'
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    libraries = value.get('libraries', {})
    return libraries.get('overrides', {}) if isinstance(libraries, dict) else {}


def _disabled(table_path, row, overrides):
    if child(row, 'disabled') is not None or child(row, 'hidden') is not None:
        return True
    table_overrides = overrides.get(str(table_path), {})
    state = table_overrides.get(_value(row, 'name'), {}) if isinstance(table_overrides, dict) else {}
    return bool(state.get('disabled') or state.get('hidden')) if isinstance(state, dict) else False


def _table_entries(table_path, root_name, scope, expand, overrides, visited=None):
    """Return enabled native libraries, recursively expanding KiCad 10 table rows."""
    table_path = Path(table_path).resolve()
    visited = set() if visited is None else visited
    if table_path in visited or not table_path.is_file():
        return {}
    visited.add(table_path)
    root = loads(table_path.read_text(encoding='utf-8-sig'))
    if not root or root[0] != root_name:
        raise ValueError(f'Invalid KiCad library table: {table_path}')
    result = {}
    for row in children(root, 'lib'):
        if _disabled(table_path, row, overrides):
            continue
        nickname, library_type, uri = _value(row, 'name'), _value(row, 'type'), _value(row, 'uri')
        if not nickname or not library_type or not uri:
            continue
        resolved = Path(expand(uri))
        if not resolved.is_absolute():
            resolved = table_path.parent / resolved
        resolved = resolved.resolve()
        if library_type.casefold() == 'table':
            result.update(_table_entries(resolved, root_name, scope, expand, overrides, visited))
        elif library_type.casefold() == 'kicad':
            result[nickname] = LibraryEntry(nickname, root_name, resolved, scope, _value(row, 'descr'))
    return result


class KiCadLibraryCatalog:
    """Loaded-library facade. The pcbnew module remains confined to the UI thread."""
    TABLES = {'footprint': ('fp_lib_table', 'fp-lib-table'), 'symbol': ('sym_lib_table', 'sym-lib-table')}

    def __init__(self, pcbnew_module, project_dir, kind):
        if kind not in self.TABLES:
            raise ValueError('Unsupported KiCad library kind')
        self.pcbnew = pcbnew_module
        self.project_dir = Path(project_dir).resolve()
        self.kind = kind
        self.root_name, self.filename = self.TABLES[kind]
        settings_path = Path(self.pcbnew.SETTINGS_MANAGER.GetUserSettingsPath())
        board = self.pcbnew.GetBoard()
        if board is None:
            raise ValueError('Open a PCB before browsing its loaded libraries')
        project = board.GetProject()

        def expand(uri):
            return str(self.pcbnew.ResolveUriByEnvVars(str(uri), project))

        overrides = _overrides(settings_path)
        entries = _table_entries(settings_path / self.filename, self.root_name, 'Global', expand, overrides)
        entries.update(_table_entries(self.project_dir / self.filename, self.root_name, 'Project', expand, overrides))
        self.entries = entries

    def libraries(self):
        return tuple(sorted(self.entries.values(), key=lambda item: (item.scope != 'Project', item.nickname.casefold())))

    def parts(self, nickname):
        entry = self.entries.get(nickname)
        if entry is None:
            raise ValueError(f'KiCad library {nickname} is not loaded')
        if self.kind == 'footprint':
            if entry.path.suffix.casefold() != '.pretty' or not entry.path.is_dir():
                raise ValueError('The workflow currently supports native .pretty footprint libraries')
            result = [LibraryPart(f'{nickname}:{path.stem}', path, entry.scope, entry.description)
                      for path in entry.path.glob('*.kicad_mod') if path.is_file()]
            return tuple(sorted(result, key=lambda item: item.name.casefold()))
        if entry.path.suffix.casefold() != '.kicad_sym' or not entry.path.is_file():
            raise ValueError('The workflow currently supports native .kicad_sym symbol libraries')
        root = loads(entry.path.read_text(encoding='utf-8-sig'))
        if not root or root[0] != 'kicad_symbol_lib':
            raise ValueError(f'Invalid KiCad symbol library: {entry.path}')
        parts = (LibraryPart(f'{nickname}:{symbol[1]}', entry.path, entry.scope, entry.description)
                 for symbol in children(root, 'symbol') if len(symbol) > 1)
        return tuple(sorted(parts, key=lambda item: item.name.casefold()))

