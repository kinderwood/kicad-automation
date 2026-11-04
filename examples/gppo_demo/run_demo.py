#!/usr/bin/env python3
"""Create a reviewable demo through the production Workflow controller.

Run with KiCad's Python, e.g. AppRun python3.11 run_demo.py /tmp/gppo-demo.
All outputs go to the chosen new directory. Source library parts stay untouched.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'plugins'))

from connector_interposer_workflow.controller import WorkflowController, NativeProcessBackend
from connector_interposer_workflow.core.libraries import read_footprint, read_symbol


def prepare(destination):
    fixture = Path(__file__).resolve().parent
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Choose a new empty demo directory')
    source = destination / 'input'
    source.mkdir(parents=True)
    board = source / 'gppo-demo.kicad_pcb'
    shutil.copy2(fixture / 'mechanical-B.kicad_pcb', board)
    controller = WorkflowController(board, backend=NativeProcessBackend())
    controller.set_layers(['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'])
    controller.load_workbook(fixture / 'inputs/netlist-A.xlsx')
    controller.config['project_id'] = 'af0ef9ad-e80c-5a97-adf5-ef89acf8d8b9'
    controller.config['layer_mapping'] = {'L2': 'In1.Cu', 'L3': 'In2.Cu'}
    controller.config['mechanical'] = {
        'layer': 'User.Drawings', 'accepted': True, 'label_offset': [-2.54, -2.54],
    }
    symbol_path = fixture / 'library/Example.kicad_sym'
    symbol = read_symbol(symbol_path, 'Example:GPPO')
    connector_symbol = {
        'path': str(symbol_path), 'library_id': symbol.library_id, 'sha256': symbol.sha256,
    }
    footprint = fixture / 'library/Example.pretty/GPPO_B012_L13_01_EXPERIMENTAL.kicad_mod'
    interposer_path = footprint.with_name('Interposer_800.kicad_mod')
    interposer = read_footprint(interposer_path, 'Example:Interposer_800')
    interposer_symbol = read_symbol(symbol_path, 'Example:Interposer', require_two_pin=False)
    controller.config['interposers'] = {'E1': dict(
        interposer.pin(), symbol={'path': str(symbol_path), 'library_id': interposer_symbol.library_id,
                                  'sha256': interposer_symbol.sha256},
        pose=[94.95, 94.95, 0], pose_accepted=True,
    )}
    positions = json.loads((fixture / 'inputs/positions-B.json').read_text())
    for name in ('J101', 'J102'):
        anchor = next(row for row in positions if row['reference'] == name + '-1')
        controller.set_gang(name, {
            'row_lengths': [1] * 6, 'pitch_x': 8.255, 'pitch_y': 8.255,
            'anchor': [anchor['x_mm'], anchor['y_mm']], 'anchor_accepted': True,
            'part_id': str(footprint), 'library_id': 'Example:' + footprint.stem,
            'symbol': connector_symbol,
            'rotation': 0, 'rf_clock': 0 if name == 'J101' else 180,
            'rf_artifacts': {str(i): str(fixture / 'library/rf' /
                ('RF-L2.r1.gppo-rf.json' if i % 2 else 'RF-L3.r1.gppo-rf.json')) for i in range(1, 7)},
        })
    native = controller.backend.call('inspect', board_path=str(board),
        source_layer='User.Drawings', label_offset=[-2.54, -2.54])
    segments = native['segments']
    points = [point for segment in segments for point in (segment['start'], segment['end'])]
    minx, maxx = min(p[0] for p in points), max(p[0] for p in points)
    miny, maxy = min(p[1] for p in points), max(p[1] for p in points)
    outer = [segment['source_uuid'] for segment in segments if any(
        all(abs(point[axis] - edge) < 1e-6 for point in (segment['start'], segment['end']))
        for axis, edge in ((0, minx), (0, maxx), (1, miny), (1, maxy)))]
    if len(outer) != 4:
        raise ValueError('Demo boundary does not contain exactly four outer lines')
    controller.config['outline'] = {'enabled': True, 'outer_uuids': outer, 'cutout_uuids': []}
    controller.config['output_dir'] = str(destination / 'applied-A')
    controller.save()
    return controller


def run(destination):
    controller = prepare(destination)
    preview = controller.review()
    if preview['conflicts']:
        raise ValueError(preview['conflicts'])
    result = controller.apply()
    output_board = Path(result['board_path'])
    reopened = WorkflowController(output_board, backend=NativeProcessBackend())
    reopened.set_layers(controller.copper_layers)
    source = reopened.config['workbook']
    reopened.load_workbook(source['path'], source.get('worksheet'), source.get('header_row', 1),
                           source.get('ignored_rows', []), source.get('mapping'))
    repeat = reopened.review()
    if repeat['status'] != 'NOOP':
        raise AssertionError('Repeated controller preview must be NOOP: ' + str(repeat['changes']))
    reopened.load_workbook(Path(__file__).resolve().parent / 'inputs/netlist-B.xlsx')
    reopened.config['output_dir'] = str(Path(destination).resolve() / 'applied-B')
    update = reopened.review()
    if update['conflicts']:
        raise ValueError(update['conflicts'])
    result_b = reopened.apply()
    evidence = {'initial_changes': preview['changes'], 'repeat_status': repeat['status'],
                'update_changes': update['changes'], 'A': result['board_path'],
                'B': result_b['board_path'], 'recovery_A': result.get('journal'),
                'recovery_B': result_b.get('journal')}
    (Path(destination) / 'demo-results.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    run(args.destination)
