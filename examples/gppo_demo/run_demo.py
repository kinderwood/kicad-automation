#!/usr/bin/env python3
"""Apply the current complete C fixture to a fresh reviewable project.

Run with KiCad's Python, e.g. AppRun python3.11 run_demo.py /tmp/gppo-demo.
All outputs go to the chosen new directory. Source library parts stay untouched.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'plugins'))

from connector_interposer_workflow.controller import WorkflowController, NativeProcessBackend
from connector_interposer_workflow.core.libraries import read_footprint, read_symbol


def prepare(destination):
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Choose a new empty demo directory')
    workspace = destination / 'gppo-abc-demo-workspace'
    source = workspace / 'source-C'
    source.mkdir(parents=True)
    board = source / 'gppo-demo.kicad_pcb'
    project = source / 'gppo-demo.kicad_pro'
    shutil.copy2(FIXTURE / 'mechanical-C.kicad_pcb', board)
    project_data = json.loads((FIXTURE / 'mechanical-C.kicad_pro').read_text())
    project_data.setdefault('meta', {})['filename'] = project.name
    project.write_text(json.dumps(project_data, indent=2) + '\n')

    controller = WorkflowController(board, backend=NativeProcessBackend())
    controller.set_layers(['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'])
    controller.load_workbook(FIXTURE / 'inputs/netlist-C.xlsx')
    controller.config['project_id'] = 'af0ef9ad-e80c-5a97-adf5-ef89acf8d8b9'
    controller.config['layer_mapping'] = {'L2': 'In1.Cu', 'L3': 'In2.Cu'}
    controller.set_netclass_type('CLOCK', 'RF')
    controller.set_netclass_type('DATA', 'RF')
    controller.set_netclass_type('DC', 'DC')
    controller.config['mechanical'] = {
        'layer': 'User.Drawings',
        'accepted': True,
        'label_offset': [-2.54, -2.54],
    }

    symbol_path = FIXTURE / 'library/Example.kicad_sym'
    gppo_symbol = read_symbol(symbol_path, 'Example:GPPO')
    connector_symbol = {
        'path': str(symbol_path),
        'library_id': gppo_symbol.library_id,
        'sha256': gppo_symbol.sha256,
    }
    footprint = FIXTURE / 'library/Example.pretty/GPPO_B012_L13_01_EXPERIMENTAL.kicad_mod'
    interposer_path = footprint.with_name('Interposer_800.kicad_mod')
    interposer = read_footprint(interposer_path, 'Example:Interposer_800')
    interposer_symbol = read_symbol(
        symbol_path, 'Example:Interposer', require_two_pin=False,
    )
    controller.config['interposers'] = {
        'E1': dict(
            interposer.pin(),
            symbol={
                'path': str(symbol_path),
                'library_id': interposer_symbol.library_id,
                'sha256': interposer_symbol.sha256,
            },
            pose=[94.95, 94.95, 0],
            pose_accepted=True,
            side='F',
        )
    }

    locations = json.loads((FIXTURE / 'inputs/positions-C.json').read_text())
    for name in ('J101', 'J102'):
        anchor = next(row for row in locations if row['reference'] == name + '-1')
        controller.set_gang(name, {
            'row_lengths': [3, 3],
            'pitch_x': 12.7,
            'pitch_y': 12.7,
            'rotation': -90,
            'anchor': [anchor['x_mm'], anchor['y_mm']],
            'anchor_accepted': True,
            'defined': True,
            'part_id': str(footprint),
            'library_id': 'Example:' + footprint.stem,
            'symbol': connector_symbol,
            'connector_rotation': 0 if name == 'J101' else 180,
        })

    dc_path = footprint.with_name('DSub_9_Pins_Vertical.kicad_mod')
    dc_footprint = read_footprint(dc_path, 'Example:DSub_9_Pins_Vertical')
    dc_symbol = read_symbol(symbol_path, 'Example:DSub_9', require_two_pin=False)
    dc_anchor = next(row for row in locations if row['reference'] == 'J1-1')
    controller.set_dc_connector('J1', dict(
        dc_footprint.pin(),
        symbol={
            'path': str(symbol_path),
            'library_id': dc_symbol.library_id,
            'sha256': dc_symbol.sha256,
        },
        anchor=[dc_anchor['x_mm'], dc_anchor['y_mm']],
        anchor_circle={
            'uuid': dc_anchor['uuid'],
            'center': [dc_anchor['x_mm'], dc_anchor['y_mm']],
        },
        rotation=0,
        side='F',
        anchor_accepted=True,
    ))

    for requirement in controller.rf_transition_requirements():
        architecture = next(
            name for name, layer in controller.config['layer_mapping'].items()
            if layer == requirement['layer']
        )
        controller.set_rf_transition(
            requirement['key'],
            FIXTURE / 'library/rf' / f'RF-{architecture}.r1.gppo-rf.json',
        )

    native = controller.backend.call(
        'inspect',
        board_path=str(board),
        source_layer='User.Drawings',
        label_offset=[-2.54, -2.54],
    )
    if native.get('errors'):
        raise ValueError('; '.join(native['errors']))
    segments = native['segments']
    points = [point for segment in segments for point in (segment['start'], segment['end'])]
    minx, maxx = min(point[0] for point in points), max(point[0] for point in points)
    miny, maxy = min(point[1] for point in points), max(point[1] for point in points)
    outer = [segment['source_uuid'] for segment in segments if any(
        all(abs(point[axis] - edge) < 1e-6
            for point in (segment['start'], segment['end']))
        for axis, edge in ((0, minx), (0, maxx), (1, miny), (1, maxy))
    )]
    if len(outer) != 4:
        raise ValueError('Demo boundary does not contain exactly four outer lines')
    controller.config['outline'] = {
        'enabled': True,
        'outer_uuids': outer,
        'cutout_uuids': [],
    }
    controller.config['output_dir'] = str(workspace / 'fresh-C')
    return controller


def run(destination):
    destination = Path(destination).resolve()
    controller = prepare(destination)
    preview = controller.review()
    if preview['conflicts']:
        raise ValueError(preview['conflicts'])
    result = controller.apply()

    output_board = Path(result['board_path'])
    reopened = WorkflowController(output_board, backend=NativeProcessBackend())
    reopened.set_layers(controller.copper_layers)
    source = reopened.config['workbook']
    reopened.load_workbook(
        source['path'],
        source.get('worksheet'),
        source.get('header_row', 1),
        source.get('ignored_rows', []),
        source.get('mapping'),
    )
    repeat = reopened.review()
    if repeat['status'] != 'NOOP':
        raise AssertionError(
            'Repeated controller preview must be NOOP: ' + str(repeat['changes'])
        )

    evidence = {
        'variant': 'C',
        'change_count': len(preview['changes']),
        'repeat_status': repeat['status'],
        'board': result['board_path'],
        'recovery': result.get('journal'),
    }
    (destination / 'demo-results.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    run(args.destination)
