"""Deterministic hierarchical schematics from approved read-only part selections.

No footprint or generic connector symbol is authored here. Only the application-local
interposer connectivity representation is generated from existing pad numbers.
"""
import copy
import csv
import io
import json
import math
import re
from dataclasses import dataclass, field
from .core.identities import stable_uuid
from .core.project import merge_project_settings
from .core.libraries import ApprovedFootprint, ApprovedSymbol, natural_key
from .core.model import validate_records
from .core.sexpr import dumps, loads, children, child


def q(value):
    return json.dumps(str(value), ensure_ascii=False)


def effects(size=1):
    return f'(effects (font (size {size} {size})))'


@dataclass(frozen=True)
class PartSelection:
    footprint: ApprovedFootprint
    symbol: ApprovedSymbol | None = None
    value: str = ''
    mpn: str = ''
    signal_pin: str = '1'
    ground_pin: str = '2'


@dataclass(frozen=True)
class SchematicBundle:
    files: dict
    paths: dict
    bom: tuple
    project_settings: dict
    identities: dict = field(default_factory=dict)

    def bom_csv(self):
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=('Reference', 'Value', 'Footprint', 'MPN', 'Quantity'))
        writer.writeheader()
        writer.writerows(self.bom)
        return stream.getvalue()


def _safe_name(kind, identity, page):
    # Hash guards filename collisions after sanitizing arbitrary external IDs.
    import hashlib
    slug = re.sub(r'[^A-Za-z0-9_-]', '_', identity)[:40] or 'part'
    return f'{kind}_{slug}_{hashlib.sha256(identity.encode()).hexdigest()[:8]}_{page}.kicad_sch'


def _header(identity, library=''):
    return f'(kicad_sch (version 20250114) (generator "connector_interposer_workflow") (uuid {q(identity)}) (paper "A4") (lib_symbols {library})'


def _label(net, x, y, uid):
    return f'(global_label {q(net)} (shape input) (at {x:g} {y:g} 0) {effects(.8)} (uuid {q(uid)}))'


def _instance(ref, lib_id, value, part, x, y, unit, root_id, sheet_id, symbol_id, pins, project_name, uid):
    props = ''.join(f'(property {q(k)} {q(v)} (at {x:g} {yy:g} 0) {effects(1) if k in ("Reference", "Value") else "(effects (font (size 1 1)) (hide yes))"})' for k, v, yy in [('Reference', ref, y-6), ('Value', value, y-3), ('Footprint', part.footprint.library_id, y), ('MPN', part.mpn, y)])
    pin_ids = ''.join(f'(pin {q(pin)} (uuid {q(uid("pin/"+ref+"/"+str(unit)+"/"+pin))}))' for pin in sorted(pins, key=natural_key))
    return f'(symbol (lib_id {q(lib_id)}) (at {x:g} {y:g} 0) (unit {unit}) (in_bom yes) (on_board yes) (dnp no) (uuid {q(symbol_id)}) {props} {pin_ids} (instances (project {q(project_name)} (path {q("/"+root_id+"/"+sheet_id)} (reference {q(ref)}) (unit {unit})))))'


def _interposer_lib(lib_id, unit_pads):
    name = lib_id.split(':', 1)[1]
    units = []
    for unit, pads in enumerate(unit_pads, 1):
        pins = []
        for index, number in enumerate(pads):
            x, y = (index//16)*58.42, -(index%16)*8.89
            pins.append(f'(pin passive line (at {x:g} {y:g} 180) (length 2.54) (name {q(number)} {effects(.8)}) (number {q(number)} {effects(.8)}))')
        units.append(f'(symbol {q(name+"_"+str(unit)+"_1")} '+''.join(pins)+')')
    return f'(symbol {q(lib_id)} (pin_names (offset 0) (hide yes)) (in_bom yes) (on_board yes) (property "Reference" "E" (at 0 5 0) {effects()}) (property "Value" {q(name)} (at 0 7 0) {effects()}) '+''.join(units)+')'


def generate_schematic(records, gang_parts, interposer_parts, project_id, project_name='connector_interposer_workflow'):
    """Return all tool-owned file contents; callers stage and review before saving.

    gang_parts maps gang IDs to PartSelection (approved two-pin symbol required).
    interposer_parts maps references to PartSelection. Its approved symbol supplies
    identity/BOM metadata; the large-pin graphical presentation is generated locally.
    A persistent UUID namespace belongs to the project, never to a workbook.
    """
    if not re.fullmatch(r'[A-Za-z0-9_-]+', project_name):
        raise ValueError('Project name must contain letters, digits, underscores or hyphens')
    records = tuple(records)
    issues = validate_records(records, {ref: p.footprint.pad_numbers for ref, p in interposer_parts.items()})
    if issues:
        raise ValueError('; '.join(f'row {i.source_row}: {i.message}' for i in issues))
    missing = {r.connector for r in records}-set(gang_parts)
    if missing:
        raise ValueError(f'Select approved connector parts for {sorted(missing)}')
    if {r.connector for r in records} & set(interposer_parts):
        raise ValueError('Gang and interposer designations must be distinct')
    uid = lambda key: stable_uuid(project_id, key)
    root_id = uid('schematic/root')
    files, paths, sheets, bom, identities = {}, {}, [], [], {}
    symbol_libraries, footprint_libraries = {}, {}
    def footprint_library(part):
        from pathlib import Path
        name = part.footprint.library_id.split(':', 1)[0]
        path = str(Path(part.footprint.path).parent)
        if name in footprint_libraries and footprint_libraries[name] != path:
            raise ValueError(f'Approved footprint library alias {name} resolves to conflicting paths')
        footprint_libraries[name] = path
    for gang in sorted({r.connector for r in records}, key=natural_key):
        part = gang_parts[gang]
        if not part.symbol:
            raise ValueError(f'Select the librarian-authored two-pin connector symbol for {gang}')
        numbers = {p[0] for p in part.symbol.pin_numbers}
        if {part.signal_pin, part.ground_pin} != numbers or numbers != set(part.footprint.pad_numbers):
            raise ValueError(f'Connector {gang} symbol/footprint signal and ground pads are incompatible')
        footprint_library(part)
        snapshot_alias = 'WorkflowApproved_'+part.symbol.sha256[:16]
        lib_id = snapshot_alias+':'+part.symbol.library_id.split(':', 1)[1]
        expression = copy.deepcopy(part.symbol.expression)
        expression[1] = lib_id
        library = dumps(expression)
        bare = copy.deepcopy(expression)
        bare[1] = lib_id.split(':', 1)[1]
        symbols = symbol_libraries.setdefault(snapshot_alias, {})
        if bare[1] in symbols and symbols[bare[1]] != dumps(bare):
            raise ValueError('Approved symbol identity has conflicting content')
        symbols[bare[1]] = dumps(bare)
        gang_records = sorted((r for r in records if r.connector == gang), key=lambda r: int(r.connector_pin))
        pages = sorted({(int(r.connector_pin)-1)//24 for r in gang_records})
        for page in pages:
            key = f'gang/{gang}/page/{page}'
            sid = uid('sheet/'+key)
            filename = _safe_name('gang', gang, page+1)
            content = [_header(uid('document/'+key), library)]
            for r in gang_records:
                index = int(r.connector_pin)-1
                if index//24 != page:
                    continue
                slot = index % 24
                x, y = 30.48+(slot//8)*86.36, 25.4+(slot%8)*21.59
                symbol_id = uid('symbol/'+r.key)
                paths[r.key] = '/'+sid+'/'+symbol_id
                identities[r.key] = symbol_id
                content.append(_instance(r.key, lib_id, part.value or lib_id.split(':', 1)[1], part, x, y, 1, root_id, sid, symbol_id, numbers, project_name, uid))
                for pin, px, py, angle in part.symbol.pin_numbers:
                    net = r.signal if pin == part.signal_pin else 'GND'
                    content.append(_label(net, x+px, y-py, uid('label/'+r.key+'/'+pin)))
                bom.append({'Reference': r.key, 'Value': part.value or lib_id.split(':', 1)[1], 'Footprint': part.footprint.library_id, 'MPN': part.mpn, 'Quantity': 1})
            content.append('(embedded_fonts no))\n')
            files[filename] = '\n'.join(content)
            sheets.append((key, sid, filename, gang+(' / '+str(page+1) if len(pages)>1 else '')))
    for ref in sorted(interposer_parts, key=natural_key):
        part = interposer_parts[ref]
        footprint_library(part)
        pads = part.footprint.pad_numbers
        assignments = {r.interposer_pin:r.signal for r in records if r.interposer == ref}
        units = [tuple(sorted(pads[start:start+64], key=lambda number:(number not in assignments, natural_key(number)))) for start in range(0, len(pads), 64)]
        lib_id = 'WorkflowProject:Interposer_'+uid('interposer-part/'+ref).replace('-', '')[:16]
        library = _interposer_lib(lib_id, units)
        local_symbol = loads(library)
        local_symbol[1] = lib_id.split(':', 1)[1]
        symbol_libraries.setdefault('WorkflowProject', {})[local_symbol[1]] = dumps(local_symbol)
        assignments = {r.interposer_pin:r.signal for r in records if r.interposer == ref}
        for unit, numbers in enumerate(units, 1):
            key = f'interposer/{ref}/unit/{unit}'
            sid, symbol_id = uid('sheet/'+key), uid('symbol/'+ref+'/unit/'+str(unit))
            filename = _safe_name('interposer', ref, unit)
            content = [_header(uid('document/'+key), library)]
            content.append(f'(text "Signal pins first, then unused pads to GND" (at 30.48 12.7 0) {effects()} (uuid {q(uid("note/"+key))}))')
            x, y = 30.48, 25.4
            content.append(_instance(ref, lib_id, part.value or 'Interposer', part, x, y, unit, root_id, sid, symbol_id, numbers, project_name, uid))
            for index, number in enumerate(numbers):
                px, py = (index//16)*58.42, (index%16)*8.89
                content.append(_label(assignments.get(number, 'GND'), x+px, y+py, uid('label/'+ref+'/'+number)))
            content.append('(embedded_fonts no))\n')
            files[filename] = '\n'.join(content)
            paths[ref if unit == 1 else ref+':'+str(unit)] = '/'+sid+'/'+symbol_id
            sheets.append((key, sid, filename, ref+' / '+str(unit)))
        bom.append({'Reference': ref, 'Value': part.value or 'Interposer', 'Footprint': part.footprint.library_id, 'MPN': part.mpn, 'Quantity': 1})
    # Root overview pagination is stable per sheet key; use bounded A4 grid.
    # Large projects use A0 for a browsable index while child drawings remain A4.
    top = [_header(root_id).replace('(paper "A4")', '(paper "A0")' if len(sheets)>24 else '(paper "A4")')]
    for index, (key, sid, filename, title) in enumerate(sheets):
        columns = 10 if len(sheets)>24 else 4
        x, y = 15+(index%columns)*68, 20+(index//columns)*28
        if y > (820 if len(sheets)>24 else 190):
            raise ValueError('Project exceeds supported 290-sheet schematic index; split into projects')
        top.append(f'(sheet (at {x} {y}) (size 60 16) (stroke (width 0) (type default)) (fill (color 0 0 0 0)) (uuid {q(sid)}) (property "Sheetname" {q(title)} (at {x} {y-1} 0) {effects()}) (property "Sheetfile" {q(filename)} (at {x} {y+17} 0) {effects(.7)}) (instances (project {q(project_name)} (path {q("/"+root_id)} (page {q(index+2)})))))')
    top.append('(sheet_instances (path "/" (page "1"))) (embedded_fonts no))\n')
    files[project_name+'.kicad_sch'] = '\n'.join(top)
    symbol_entries = []
    for alias, symbols in sorted(symbol_libraries.items()):
        filename = alias+'.kicad_sym'
        files[filename] = '(kicad_symbol_lib (version 20241209) (generator "connector_interposer_workflow") '+''.join(symbols[name] for name in sorted(symbols))+')\n'
        symbol_entries.append(f'(lib (name {q(alias)}) (type "KiCad") (uri {q("${KIPRJMOD}/"+filename)}) (options "") (descr "Pinned Workflow project symbols"))')
    files['sym-lib-table'] = '(sym_lib_table '+''.join(symbol_entries)+')\n'
    files['fp-lib-table'] = '(fp_lib_table '+''.join(f'(lib (name {q(alias)}) (type "KiCad") (uri {q(path)}) (options "") (descr "Read-only approved footprint source"))' for alias,path in sorted(footprint_libraries.items()))+')\n'
    # Exact assignments avoid treating names containing *, ?, [, ] as patterns.
    classes = sorted({r.netclass for r in records})
    settings = merge_project_settings({}, records)
    return SchematicBundle(files, paths, tuple(sorted(bom, key=lambda row:natural_key(row['Reference']))), settings, identities)


def merge_library_table(existing, generated):
    """Merge tool entries into a designer table, refusing alias takeover.

    A table may be absent (None/empty). Existing entries and their options stay
    intact. An alias with a different URI is a reviewable conflict.
    """
    if not existing:
        return generated
    current, incoming = loads(existing), loads(generated)
    if current[0] != incoming[0] or current[0] not in ('sym_lib_table', 'fp_lib_table'):
        raise ValueError('Mismatched KiCad library table types')
    by_name = {child(entry, 'name')[1]:entry for entry in children(current, 'lib')}
    for entry in children(incoming, 'lib'):
        name = child(entry, 'name')[1]
        if name in by_name:
            if child(by_name[name], 'uri')[1] != child(entry, 'uri')[1] or child(by_name[name], 'type')[1] != child(entry, 'type')[1]:
                raise ValueError(f'Library alias {name} conflicts with the selected approved source')
        else:
            current.append(entry)
            by_name[name] = entry
    return dumps(current)+'\n'
