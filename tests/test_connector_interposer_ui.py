"""Workflow session invariants and optional real native-dialog smoke coverage."""
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugins.connector_interposer_workflow import progress
from plugins.connector_interposer_workflow.controller import (
    PAGES, SavedSettingsError, WorkflowController, open_controller, row_ranges,
)


def write_transition(path, part_id, part_sha256, netclass='RF', layer='F.Cu',
                     stack=('F.Cu', 'B.Cu')):
    from plugins.connector_interposer_workflow.artifacts import canonical, sha256
    artifact = {
        'schema': 'gppo.artifact/2.0', 'artifact_id': 'test-transition',
        'revision': 1, 'kind': 'rf-transition', 'payload_schema': 'gppo.rf/2.0',
        'recipe': {'fixture': True},
        'compatibility': {
            'part_id': part_id, 'part_sha256': part_sha256, 'mount': 'PTH',
            'side': 'front', 'netclasses': [netclass],
            'copper_layer_count': len(stack),
        },
        'required_capabilities': ['net-copper-shapes', 'native-tracks'],
        'payload': {
            'units': 'mm', 'coordinates': 'x-right-y-up-ccw',
            'net_roles': ['signal', 'ground'],
            'anchors': {'signal': [0, 0], 'exit_direction_deg': 0,
                        'handoff': [2, 0]},
            'layer_roles': {'mount': 'F.Cu', 'signal': layer},
            'objects': [{
                'id': 'signal-land', 'type': 'copper', 'shape': 'circle',
                'net': 'signal', 'at': [0, 0], 'diameter': .8, 'layer': layer,
            }, {
                'id': 'exit-trace', 'type': 'track', 'net': 'signal',
                'layer': layer, 'width': .25, 'start': [0, 0], 'end': [2, 0],
            }],
        },
    }
    unsigned = dict(artifact)
    artifact['hashes'] = {
        'payload': sha256(artifact['payload']), 'recipe': sha256(artifact['recipe']),
        'artifact': sha256(unsigned),
    }
    Path(path).write_bytes(canonical(artifact))
    return Path(path)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.board = self.root/'source.kicad_pcb'
        self.board.write_text('(kicad_pcb)')
        self.workbook = self.root/'signals.csv'
        self.workbook.write_text('title\nLayer,Connector Pin,Signal,Connector,Interposer Pin,NetClass,Interposer\nL1,1,CLOCK,J101,A1,RF,U1\nL1,2,DATA,J101,A2,RF,U1\nL1,1,ENABLE,J202,A3,Control,U1\n')
        self.controller = WorkflowController(self.board)
        self.controller.load_workbook(self.workbook, header_row=2)
        self.controller.set_netclass_type('RF', 'RF')
        self.controller.set_netclass_type('Control', 'RF')
    def tearDown(self): self.temporary.cleanup()

    def test_header_mapping_retains_source_rows_and_explicit_ignored_rows(self):
        self.assertEqual(self.controller.table.records[0].key, 'J101-1')
        self.assertEqual(self.controller.table.records[0].source_row, 3)
        self.controller.parse('signals', 2, '4')
        self.assertTrue(self.controller.view_data_enabled)
        self.assertEqual((self.controller.table.included_count, self.controller.table.ignored_count), (2, 1))
        self.assertEqual(tuple(row.source_row for row in self.controller.table.rows), (3, 4, 5))
        self.assertTrue(self.controller.table.rows[1].ignored)
        self.controller.save()
        restored = WorkflowController(self.board)
        self.assertEqual(restored.config['workbook']['ignored_rows'], [4])
        self.assertEqual(restored.config['project_id'], self.controller.config['project_id'])

    def test_invalid_mapping_and_range_never_leave_old_table_viewable(self):
        self.assertTrue(self.controller.view_data_enabled)
        with self.assertRaises(ValueError): self.controller.parse('signals', 2, '8-3')
        self.assertFalse(self.controller.view_data_enabled)
        self.controller.parse('signals', 2, mapping={'Signal': 0})
        self.assertFalse(self.controller.view_data_enabled)
        self.controller.parse('signals', 2, '99')
        self.assertFalse(self.controller.view_data_enabled)

    def test_reset_and_delete_settings_keep_their_scopes_separate(self):
        self.controller.set_layers(('F.Cu', 'In1.Cu', 'B.Cu'))
        self.controller.set_layer_mapping('L1', 'In1.Cu')
        self.controller.save()
        project_id = self.controller.config['project_id']
        settings_path = self.controller.config_path
        snapshot_directory = self.root / '.connector-interposer-inputs'
        self.assertTrue(settings_path.is_file())
        self.assertTrue(snapshot_directory.is_dir())

        self.controller.reset_current()
        self.assertEqual(self.controller.config['project_id'], project_id)
        self.assertEqual(self.controller.copper_layers, ('F.Cu', 'In1.Cu', 'B.Cu'))
        self.assertEqual(self.controller.config['workbook'], {})
        self.assertEqual(self.controller.config['layer_mapping'], {})
        self.assertEqual(self.controller.config['outline'], {'enabled': False})
        self.assertIsNone(self.controller.workbook)
        self.assertIsNone(self.controller.table)
        self.assertTrue(settings_path.is_file(), 'Reset must not delete saved settings')

        self.assertTrue(self.controller.delete_saved_settings())
        self.assertFalse(settings_path.exists())
        self.assertTrue(snapshot_directory.is_dir(), 'Delete targets the settings sidecar only')
        self.assertFalse(self.controller.delete_saved_settings())

    def test_unloadable_saved_settings_can_be_deleted_to_open_or_kept_to_close(self):
        import json
        self.controller.save()
        settings_path = self.controller.config_path
        project_id = self.controller.config['project_id']
        saved = json.loads(settings_path.read_text())
        saved['schema_version'] -= 1
        stale = json.dumps(saved)
        settings_path.write_text(stale)

        with self.assertRaisesRegex(SavedSettingsError, 'schema version') as raised:
            WorkflowController(self.board)
        self.assertEqual(raised.exception.path, settings_path)

        asked = []
        self.assertIsNone(open_controller(
            self.board, lambda error: asked.append(error) or False,
        ))
        self.assertEqual(len(asked), 1)
        self.assertEqual(settings_path.read_text(), stale, 'Keep must not touch the file')

        reopened = open_controller(self.board, lambda error: True)
        self.assertFalse(settings_path.exists())
        self.assertEqual(reopened.config['project_id'], project_id)
        self.assertEqual(reopened.config['gangs'], {})
        self.assertEqual(reopened.config['workbook'], {})
        reopened.save()
        self.assertEqual(
            open_controller(self.board, self.fail).config['project_id'], project_id,
        )

        for unreadable in ('{not json', '[]', '{"schema_version": 1, "project_id": "x"}'):
            settings_path.write_text(unreadable)
            with self.assertRaises(SavedSettingsError):
                WorkflowController(self.board)
            fresh = open_controller(self.board, lambda error: True)
            self.assertFalse(settings_path.exists())
            self.assertNotEqual(fresh.config['project_id'], project_id)

    def test_proposed_match_finds_where_a_part_fits_the_drawing(self):
        from plugins.connector_interposer_workflow import placement, progress
        from plugins.connector_interposer_workflow.core.libraries import Pad
        controller = self.controller
        controller.set_gang('J101', {'row_lengths': [2], 'pitch_x': 2.54, 'defined': True})
        # Two identical two-position sites and one stray circle.
        centres = [(10, 10), (12.54, 10), (40, 10), (42.54, 10), (70, 70)]
        circles = [{'uuid': f'c{number}', 'center': list(centre), 'radius': .5}
                   for number, centre in enumerate(centres)]
        found, index, relative = placement.proposals(controller, 'gang', 'J101', circles)
        self.assertEqual([circle['uuid'] for circle in found], ['c0', 'c2'],
                         'Several fits leave the choice to the drafter')
        self.assertEqual(index.covered((10 + dx, 10 + dy) for dx, dy in relative),
                         {'c0', 'c1'})

        # A placed part claims its circles, leaving one fit: the proposal.
        controller.set_gang('J202', {
            'row_lengths': [1], 'defined': True, 'anchor': [40, 10], 'anchor_accepted': True,
            'anchor_circle': {'uuid': 'c2', 'center': [40, 10], 'revision': 'r1'},
        })
        found, _index, _relative = placement.proposals(controller, 'gang', 'J101', circles)
        self.assertEqual([circle['uuid'] for circle in found], ['c0'])
        controller.set_gang('J101', {'rotation': 90})
        self.assertEqual(placement.proposals(controller, 'gang', 'J101', circles)[0], [])
        controller.set_gang('J101', {'defined': False})
        with self.assertRaisesRegex(ValueError, 'Define J101 in Define RF gangs first'):
            placement.proposals(controller, 'gang', 'J101', circles)

        # Footprint features follow the backend: back side flips Y, then rotate.
        pads = (Pad('1', 'thru_hole', (0, 0), (1, 1), ()),
                Pad('2', 'thru_hole', (2, 1), (1, 1), ()),
                Pad('', 'np_thru_hole', (5, 5), (1, 1), ()))
        self.assertEqual(placement.footprint_offsets(pads, 0, 'F', '1'), [(0, 0), (2, 1)])
        self.assertEqual(placement.footprint_offsets(pads, 0, 'B', '1')[1], (2, -1))
        x, y = placement.footprint_offsets(pads, 90, 'F', '1')[1]
        self.assertAlmostEqual(x, 1)
        self.assertAlmostEqual(y, -2)
        self.assertEqual(placement.footprint_offsets(pads, 0, 'F'), [(0, 0), (2, 1), (0, 0)])
        with self.assertRaisesRegex(ValueError, 'no pad 7'):
            placement.footprint_offsets(pads, 0, 'F', '7')

        # A re-read drawing keeps placements whose circle is the same object in place.
        mechanical = controller.config['mechanical']
        mechanical['inspected_revision'] = 'r2'
        self.assertTrue(placement.stale(controller, controller.gang('J202')['anchor_circle']))
        self.assertEqual(progress.checklist(controller, 'Mechanical placement')[0].status,
                         'missing')
        self.assertTrue(placement.reassociate(controller, circles, 'r2'))
        self.assertFalse(placement.stale(controller, controller.gang('J202')['anchor_circle']))
        self.assertEqual(progress.checklist(controller, 'Mechanical placement')[0].status,
                         'done')
        mechanical['inspected_revision'] = 'r3'
        circles[2]['center'] = [41, 10]
        self.assertFalse(placement.reassociate(controller, circles, 'r3'))
        self.assertTrue(placement.stale(controller, controller.gang('J202')['anchor_circle']))

    def test_review_tables_put_problems_and_changes_in_plain_words(self):
        from plugins.connector_interposer_workflow import review
        from plugins.connector_interposer_workflow.core.model import Issue
        self.assertEqual(review.step_of('layer_unmapped'), 'Layer assignment')
        self.assertEqual(review.step_of('duplicate_signal'), 'Netlist source')
        self.assertEqual(review.step_of('microvia_size_missing'), 'Interposer microvias')
        self.assertEqual(review.step_of('something_new'), 'Review changes')
        for step in {review.step_of(code) for code in review._STEPS}:
            self.assertIn(step, PAGES)

        preview = {
            'warnings': [{'code': 'interposer_pad_circles_missing', 'message': 'E1: no circles'}],
            'conflicts': [{'kind': 'unowned-file', 'key': 'a.kicad_sch',
                           'message': 'Generated output collides'},
                          {'kind': 'ownership', 'uuid': 'u1', 'message': 'Owned object was edited'}],
            'changes': [
                {'key': 'J101-1', 'kind': 'add'},
                {'key': 'J1', 'kind': 'update', 'side': {'before': 'F', 'after': 'B'},
                 'pose': {'before': [0, 0, 0], 'after': [2.54, 0, 90]},
                 'assignments': [{'pad': '1', 'before': 'A', 'after': 'B'}]},
                {'key': 'J9', 'kind': 'remove'},
                {'key': 'interposer-microvias', 'kind': 'add'},
                {'key': 'board-outline', 'kind': 'outline'},
                {'key': 'a.kicad_sch', 'kind': 'project-file'},
                {'key': 'b.kicad_sch', 'kind': 'project-file'},
                {'key': 'old.kicad_sch', 'kind': 'remove-project-file'},
            ],
            'desired': {
                'instances': {'J101-1': {'footprint': {'library_id': 'Pinned:GPPO',
                                                       'source_library_id': 'Lib:GPPO'},
                                         'side': 'F', 'pose': [25.4, 2.54, 90]}},
                'outline': {'enabled': True, 'cutout_uuids': ['c']},
                'interposer_microvias': {'size': {'diameter': .3, 'drill': .1}},
            },
            'registry': {
                'instances': {'J9': {'spec': {'footprint': {'library_id': 'Lib:DSub'},
                                              'side': 'B', 'pose': [0, 0, 0],
                                              'anchor': {'pad': '1', 'at': [5, 6]}}}},
                'generated_files': {'a.kicad_sch': 'x', 'old.kicad_sch': 'y'},
            },
        }
        issues = [Issue('mpn_missing', 'J101: no part number', severity='warning'),
                  Issue('required', 'Signal is required', source_row=7)]
        rows = review.problems(issues, preview, 'The board is open elsewhere')
        self.assertEqual([(row.level, row.step, row.message) for row in rows], [
            ('Error', 'Netlist source', 'Row 7: Signal is required'),
            ('Error', 'Review changes', 'The board is open elsewhere'),
            ('Conflict', 'Review changes', 'a.kicad_sch: Generated output collides'),
            ('Conflict', 'Review changes',
             'A board item the workflow owns: Owned object was edited'),
            ('Warning', 'Connector assignments', 'J101: no part number'),
            ('Warning', 'Mechanical placement', 'E1: no circles'),
        ])

        groups = review.summary(preview, lambda value: f'{value:g} mm')
        self.assertEqual([(label, len(changes)) for label, changes in groups], [
            ('Part to add', 1), ('Part to change', 1), ('Part to remove', 1),
            ('Interposer microvia set to update', 1), ('Board outline to update', 1),
            ('Project files to write', 2), ('Project file to remove', 1)])
        details = {label: [(c.item, c.before, c.after) for c in changes]
                   for label, changes in groups}
        self.assertEqual(details['Part to add'],
                         [('J101-1', '—', 'Lib:GPPO, front, 90°, at 25.4 mm, 2.54 mm')])
        self.assertEqual(details['Part to change'], [(
            'J1', 'at 0 mm, 0 mm, 0°, front, 1 pad net',
            'at 2.54 mm, 0 mm, 90°, back, 1 pad net reassigned')])
        self.assertEqual(details['Part to remove'],
                         [('J9', 'Lib:DSub, back, 0°, pin 1 at 5 mm, 6 mm', '—')])
        self.assertEqual(details['Interposer microvia set to update'],
                         [('Interposer microvias', 'none', '0.3 mm pad, 0.1 mm hole')])
        self.assertEqual(details['Board outline to update'],
                         [('Board outline', 'none', 'managed outline with centre cutout')])
        self.assertEqual(details['Project files to write'], [
            ('a.kicad_sch', 'written by an earlier Apply', 'written'),
            ('b.kicad_sch', '—', 'written')])
        self.assertEqual(details['Project file to remove'],
                         [('old.kicad_sch', 'written by an earlier Apply', 'removed')])
        self.assertEqual(
            review.headline(groups),
            '1 part to add, 1 part to change, 1 part to remove, '
            '1 interposer microvia set to update, 1 board outline to update, '
            '2 project files to write, 1 project file to remove')
        self.assertEqual(review.summary(None), [])
        self.assertEqual(review.headline([]), 'nothing to change')

    def test_step_checklists_report_fixed_lines_and_the_most_pressing_status(self):
        from plugins.connector_interposer_workflow import progress
        from plugins.connector_interposer_workflow.core.model import Issue
        controller = self.controller
        self.assertEqual([step for _group, steps in progress.GROUPS for step in steps],
                         list(PAGES))
        self.assertEqual(set(progress.ABOUT), set(PAGES))

        def lines(step):
            return [(line.status, line.label, line.count)
                    for line in progress.checklist(controller, step)]

        self.assertEqual(lines('Netlist source'), [
            ('done', 'File selected', ''), ('done', 'Columns assigned', '7 of 7'),
            ('done', 'Nets parsed', '3')])
        self.assertEqual(lines('Netclass type'), [('done', 'Classified', '2 of 2')])
        controller.config['netclass_types'].pop('Control')
        self.assertEqual(lines('Netclass type'), [('missing', 'Classified', '1 of 2')])
        self.assertEqual(progress.step_status(controller, 'Netclass type'), 'missing')
        controller.set_netclass_type('Control', 'RF')

        controller.set_layers(('F.Cu', 'In1.Cu', 'B.Cu'))
        self.assertEqual(lines('Layer assignment'), [('missing', 'Assigned', '0 of 1')])
        controller.set_layer_mapping('L1', 'In1.Cu')
        self.assertEqual(lines('Layer assignment'), [('done', 'Assigned', '1 of 1')])

        self.assertEqual(lines('Connector assignments'), [
            ('missing', 'Interposer', '0 of 1'), ('missing', 'RF gangs', '0 of 2'),
            ('optional', 'DC connectors', '0')])
        self.assertEqual(lines('Define RF gangs'), [('missing', 'Gangs defined', '0 of 2')])
        controller.set_gang('J202', {'row_lengths': [1], 'pitch_x': 2.54, 'pitch_y': 2.54,
                                     'defined': True})
        self.assertEqual(lines('Define RF gangs'), [('missing', 'Gangs defined', '1 of 2')])
        self.assertEqual(lines('Mechanical placement')[0], ('missing', 'Drawing unchanged', ''))
        self.assertEqual(lines('Mechanical placement')[-1], ('optional', 'Mounting holes', '0'))

        # Required for a final board but not blocking: caution, not missing.
        self.assertEqual(lines('Board outline'), [
            ('caution', 'Outer boundary', 'none'), ('optional', 'Centre cutout', 'none')])
        controller.config['outline'] = {'enabled': True}
        self.assertEqual(lines('Board outline')[0], ('missing', 'Outer boundary', 'not set'))
        controller.config['outline'] = {'enabled': True, 'outer_uuids': ['a']}
        self.assertEqual(progress.step_status(controller, 'Board outline'), 'done')
        self.assertEqual(lines('Interposer microvias'), [('optional', 'Microvias', 'off')])
        self.assertEqual(progress.step_status(controller, 'Interposer microvias'), 'optional')
        transitions = lines('RF transitions')
        self.assertEqual([line[1] for line in transitions],
                         ['Assigned', 'Not assigned', 'Blocked'])
        self.assertEqual(transitions[0][0], 'caution')
        self.assertEqual(transitions[2], ('done', 'Blocked', '0'))

        self.assertEqual(lines('Review changes')[0], ('missing', 'Review up to date', ''))
        self.assertEqual(lines('Apply / results'), [
            ('missing', 'Review up to date', ''), ('optional', 'Applied', 'not yet')])
        controller.preview = {'conflicts': [], 'changes': [], 'warnings': ['w']}
        controller.issues = (Issue('mpn_missing', 'no MPN', severity='warning'),)
        self.assertEqual(lines('Review changes'), [
            ('done', 'Review up to date', ''), ('done', 'Blocking problems', '0'),
            ('caution', 'Warnings', '2'), ('done', 'Conflicts', '0')])
        self.assertEqual(lines('Apply / results'), [
            ('done', 'Review up to date', ''), ('missing', 'Applied', 'not yet')])
        controller.applied = True
        self.assertEqual(progress.step_status(controller, 'Apply / results'), 'done')
        controller.changed()
        self.assertFalse(controller.applied)

    def test_layer_policy_survives_stackup_and_class_growth(self):
        self.controller.set_rule('RF', 'F.Cu', False)
        self.controller.set_layers(('F.Cu', 'In1.Cu', 'B.Cu'))
        permissions = self.controller.config['layer_rules']
        self.assertFalse(permissions['RF']['F.Cu'])
        self.assertTrue(permissions['RF']['In1.Cu'])
        self.controller.set_layer_mapping('L1', 'F.Cu')
        issues = self.controller.validate()
        self.assertEqual([issue.source_row for issue in issues if issue.code == 'layer_disallowed'], [3, 4])

    def test_interposer_symbol_must_contain_every_used_workbook_pin(self):
        import hashlib
        symbol_path = self.root / 'Interposer.kicad_sym'
        symbol_path.write_text('''(kicad_symbol_lib (version 20241209) (generator "test")
          (symbol "Small" (in_bom yes) (on_board yes)
            (property "Reference" "U" (at 0 5 0) (effects (font (size 1 1))))
            (property "Value" "Small" (at 0 7 0) (effects (font (size 1 1))))
            (property "ki_fp_filters" "Small" (at 0 0 0) (effects (font (size 1 1)) hide))
            (symbol "Small_1_1"
              (pin passive line (at 0 0 180) (length 2.54) (name "A1" (effects (font (size .8 .8)))) (number "A1" (effects (font (size .8 .8)))))
              (pin passive line (at 0 -2.54 180) (length 2.54) (name "A2" (effects (font (size .8 .8)))) (number "A2" (effects (font (size .8 .8))))))))''')
        digest = hashlib.sha256(symbol_path.read_bytes()).hexdigest()
        self.controller.config['interposers']['U1'] = {
            'symbol': {
                'path': str(symbol_path), 'library_id': 'Approved:Small',
                'sha256': digest,
            },
        }
        issues = self.controller.validate()
        count_issues = [issue for issue in issues if issue.code == 'interposer_pin_count']
        self.assertEqual(len(count_issues), 1)
        self.assertIn('symbol has 2 pins; 3 pins are used', count_issues[0].message)

    def test_dc_symbol_must_contain_every_used_connector_pin(self):
        import hashlib
        self.workbook.write_text(
            'Signal,NetClass,Interposer,Interposer Pin,Connector,Connector Pin,Layer\n'
            'PWR,DC,E1,A1,J1,1,L1\n'
            'CTRL,DC,E1,A2,J1,2,L1\n'
        )
        self.controller.load_workbook(self.workbook)
        self.controller.set_netclass_type('DC', 'DC')
        symbol_path = self.root / 'DC.kicad_sym'
        symbol_path.write_text('''(kicad_symbol_lib (version 20241209) (generator "test")
          (symbol "OnePin" (in_bom yes) (on_board yes)
            (property "Reference" "J" (at 0 5 0) (effects (font (size 1 1))))
            (property "Value" "OnePin" (at 0 7 0) (effects (font (size 1 1))))
            (property "ki_fp_filters" "OnePin" (at 0 0 0) (effects (font (size 1 1)) hide))
            (symbol "OnePin_1_1"
              (pin passive line (at 0 0 180) (length 2.54) (name "1" (effects (font (size .8 .8)))) (number "1" (effects (font (size .8 .8))))))))''')
        digest = hashlib.sha256(symbol_path.read_bytes()).hexdigest()
        self.controller.set_dc_connector('J1', {
            'symbol': {
                'path': str(symbol_path), 'library_id': 'Approved:OnePin',
                'sha256': digest,
            },
        })
        issues = self.controller.validate()
        count_issues = [issue for issue in issues if issue.code == 'dc_pin_count']
        self.assertEqual(len(count_issues), 1)
        self.assertIn('symbol has 1 pin; 2 pins are used', count_issues[0].message)

    def test_microvia_size_is_board_predefined_and_filtered_by_interposer_pitch(self):
        import hashlib
        footprint = self.root / 'Interposer.kicad_mod'
        footprint.write_text(
            '(footprint "Interposer" (layer "F.Cu") '
            '(pad "A1" smd circle (at 0 0) (size .4 .4) (layers "F.Cu")) '
            '(pad "A2" smd circle (at .5 0) (size .4 .4) (layers "F.Cu")) '
            '(pad "A3" smd circle (at 1 0) (size .4 .4) (layers "F.Cu")))'
        )
        self.controller.config['interposers']['U1'] = {
            'path': str(footprint), 'library_id': 'Approved:Interposer',
            'sha256': hashlib.sha256(footprint.read_bytes()).hexdigest(),
            'pose': [0, 0, 0], 'pose_accepted': True, 'side': 'F',
        }
        self.controller.set_layers(('F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'))
        self.controller.set_layer_mapping('L1', 'In1.Cu')
        self.controller.set_board_microvia_rules(
            ((0, 0), (.254, .127), (.305, .152)),
            min_diameter=.2, min_drill=.1, min_clearance=.2,
        )
        presets = self.controller.microvia_presets()
        self.assertTrue(presets[0].eligible)
        self.assertFalse(presets[1].eligible)
        self.controller.set_microvias_enabled(True)
        self.controller.set_microvia_size(.254, .127)
        self.assertEqual(self.controller.config['microvias']['size'], {
            'diameter': .254, 'drill': .127,
        })
        with self.assertRaisesRegex(ValueError, 'spacing'):
            self.controller.set_microvia_size(.305, .152)
        self.assertEqual(self.controller.microvia_counts(), (0, 3, 6))
        self.assertNotIn('microvia_outer_signal_layer', {
            issue.code for issue in self.controller.validate()
        })
        self.controller.set_board_microvia_rules(((0, 0), (.305, .152)), .2, .1, .2)
        self.assertNotIn('size', self.controller.config['microvias'])

    def test_independent_gangs_and_explicit_copy_preserve_anchors(self):
        # A new gang carries no placeholder pattern.
        self.assertEqual(self.controller.gang('J101')['row_lengths'], [])
        self.assertIsNone(self.controller.gang('J101')['pitch_x'])
        self.assertFalse(self.controller.gang('J101')['defined'])
        self.controller.set_gang('J101', {
            'row_lengths': [2], 'pitch_y': 4,
            'pitch_x': 4, 'anchor': [30, 20], 'anchor_accepted': True,
            'display_units': {'pitch_x': 'mil', 'pitch_y': 'mm', 'row_offsets': [],
                              'anchor_x': 'inch', 'anchor_y': 'inch'},
        })
        self.controller.set_gang('J202', {
            'row_lengths': [1], 'pitch_y': 9,
            'pitch_x': 9, 'anchor': [80, 40], 'anchor_accepted': True,
            'display_units': {'pitch_x': 'mm', 'pitch_y': 'mm', 'row_offsets': [],
                              'anchor_x': 'um', 'anchor_y': 'mil'},
        })
        self.assertEqual(self.controller.pattern('J101')['2'], (34, 20))
        self.assertEqual(self.controller.gang('J202')['pitch_x'], 9)
        self.controller.copy_gang('J101', ['J202'])
        self.assertEqual(self.controller.gang('J202')['pitch_x'], 4)
        self.assertEqual(self.controller.gang('J202')['anchor'], [80, 40])
        self.assertEqual(self.controller.gang('J101')['anchor'], [30, 20])
        self.assertEqual(self.controller.gang('J202')['display_units']['pitch_x'], 'mil')
        self.assertEqual(self.controller.gang('J202')['display_units']['anchor_x'], 'um')
        self.assertEqual(self.controller.gang('J202')['display_units']['anchor_y'], 'mil')

    def test_rf_requirements_group_by_netclass_layer_and_footprint(self):
        self.controller.set_layers(('F.Cu', 'B.Cu'))
        self.controller.set_layer_mapping('L1', 'F.Cu')
        self.controller.set_netclass_type('Control', 'DC')
        from plugins.connector_interposer_workflow.core.libraries import read_footprint
        part_path = self.root / 'GPPO.kicad_mod'
        part_path.write_text(
            '(footprint "GPPO" (layer "F.Cu") '
            '(pad "1" thru_hole circle (at 0 0) (size 1 1) (drill .5) '
            '(layers "*.Cu" "*.Mask")) '
            '(pad "2" smd circle (at 0 2) (size 1 1) (layers "F.Cu" "F.Mask")))'
        )
        part = read_footprint(part_path, 'Approved:GPPO')
        part_sha = part.sha256
        self.controller.set_gang('J101', {
            'part_id': str(part_path),
            'library_id': 'Approved:GPPO', 'part_sha256': part_sha,
        })
        requirements = self.controller.rf_transition_requirements()
        self.assertEqual(len(requirements), 1)
        requirement = requirements[0]
        self.assertEqual(
            (requirement['netclass'], requirement['layer'], requirement['footprint']),
            ('RF', 'F.Cu', 'Approved:GPPO'),
        )
        self.assertEqual(requirement['locations'], ('J101-1', 'J101-2'))
        self.assertFalse(any(issue.code == 'rf_transition'
                             for issue in self.controller.validate()))
        artifact = write_transition(
            self.root / 'transition.gppo-rf.json', 'Approved:GPPO', part_sha,
        )
        self.controller.set_rf_transition(requirement['key'], artifact)
        refreshed = self.controller.rf_transition_requirements()[0]
        self.assertEqual(self.controller.validate_rf_transition(refreshed)['artifact_id'],
                         'test-transition')

    def test_changed_external_artifact_blocks_apply(self):
        artifact = self.root/'rf.json'
        artifact.write_text('{"revision":"A"}')
        self.controller.config['artifacts']['test'] = {'path': str(artifact)}
        self.controller.preview = {'reviewed': True}
        self.controller.preview_token = self.controller.token()
        artifact.write_text('{"revision":"B"}')
        with self.assertRaisesRegex(ValueError, 'out of date'): self.controller.apply()
        self.assertIsNone(self.controller.preview)

    def test_every_persisted_workflow_change_invalidates_preview_token(self):
        before = self.controller.token()
        self.controller.config['layer_mapping']['L1'] = 'B.Cu'
        self.assertNotEqual(before, self.controller.token())

    def test_changed_workbook_cannot_reuse_previously_parsed_rows(self):
        self.workbook.write_text(self.workbook.read_text().replace('CLOCK', 'NEW_CLOCK'))
        with self.assertRaisesRegex(ValueError, 'Workbook changed'):
            self.controller.review()

    def test_cancelled_analysis_discards_result(self):
        started, proceed, finished = threading.Event(), threading.Event(), threading.Event()
        results, errors = [], []
        def operation():
            started.set()
            proceed.wait(3)
            return 'obsolete'
        def failed(error):
            errors.append(str(error))
            finished.set()
        self.controller.run_async(operation, results.append, failed)
        self.assertTrue(started.wait(3))
        self.controller.cancel()
        proceed.set()
        self.assertTrue(finished.wait(3))
        self.assertEqual(results, [])
        self.assertIn('Settings changed while this ran', errors[0])


class NativeDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get('DISPLAY'):
            raise unittest.SkipTest('Native wx checks need an available display')
        try:
            import wx
        except ImportError:
            raise unittest.SkipTest('KiCad wx runtime unavailable')
        cls.wx = wx
        cls.app = wx.GetApp() or wx.App(False)

    def test_semantic_palette_follows_wx_appearance_and_keeps_text_legible(self):
        from plugins.connector_interposer_workflow.ui import WorkflowDialog, ui_palette

        def luminance(colour):
            channels = []
            for value in colour.Get()[:3]:
                value /= 255
                channels.append(value / 12.92 if value <= .04045
                                else ((value + .055) / 1.055) ** 2.4)
            return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2]

        def contrast(first, second):
            high, low = sorted((luminance(first), luminance(second)), reverse=True)
            return (high + .05) / (low + .05)

        dialog = WorkflowDialog()
        try:
            palette = ui_palette()
            appearance = self.wx.SystemSettings.GetAppearance()
            if hasattr(appearance, 'IsDark'):
                self.assertEqual(palette.dark, appearance.IsDark())
            for background in (palette.info, palette.assigned, palette.warning,
                               palette.error, palette.success):
                self.assertGreaterEqual(contrast(palette.text, background), 4.5)
            self.assertGreaterEqual(contrast(palette.panel_text, palette.heading), 4.5)

            about_box = dialog.about_box
            self.assertEqual(about_box.GetBackgroundColour().Get()[:3], palette.info.Get()[:3])
            self.assertEqual(dialog.about_title.GetForegroundColour().Get()[:3],
                             palette.text.Get()[:3])
            for status in ('done', 'missing', 'caution'):
                self.assertGreaterEqual(contrast(palette.status[status], palette.window), 3)
            self.assertEqual(dialog.columns_status.GetBackgroundColour().Get()[:3],
                             palette.warning.Get()[:3])

            about_box.SetBackgroundColour(self.wx.Colour(1, 2, 3))
            dialog.apply_theme()
            self.assertEqual(about_box.GetBackgroundColour().Get()[:3],
                             ui_palette().info.Get()[:3])
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_native_page_hierarchy_and_full_10000_row_popup(self):
        from plugins.connector_interposer_workflow.ui import (WorkflowDialog, DataDialog,
                                                               LibraryPartPickerDialog,
                                                               LibrarySelectionCtrl,
                                                               SymbolAssignmentCtrl,
                                                               FootprintAssignmentCtrl,
                                                               AssignmentRenderer, ui_palette)
        from plugins.connector_interposer_workflow.core.library_catalog import LibraryEntry, LibraryPart
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet
        from plugins.connector_interposer_workflow.core.model import HEADERS
        dialog = WorkflowDialog()
        self.assertEqual(tuple(dialog.book.GetPageText(i) for i in range(dialog.book.GetPageCount())), PAGES)
        self.assertEqual(PAGES[0], 'Netlist source')
        self.assertEqual(PAGES[1], 'Netclass type')
        self.assertEqual(PAGES[2], 'Layer assignment')
        self.assertEqual(PAGES[3], 'Routing permissions')
        self.assertEqual(PAGES[4], 'Connector assignments')
        self.assertEqual(PAGES[5], 'RF transitions')
        self.assertEqual(PAGES[6], 'Define RF gangs')
        self.assertEqual(PAGES[7], 'Mechanical placement')
        self.assertEqual(PAGES[8], 'Interposer microvias')
        self.assertEqual(PAGES[9], 'Board outline')
        self.assertEqual(PAGES[10], 'Review changes')
        self.assertEqual(PAGES[11], 'Apply / results')
        from plugins.connector_interposer_workflow import progress
        for page_index, page_name in enumerate(PAGES):
            # Guidance lives in the About box; a step starts with its controls.
            self.assertIs(dialog.pages[page_index].GetParent(), dialog.page_hosts[page_index])
            host_sizer = dialog.page_hosts[page_index].GetSizer()
            self.assertIs(host_sizer.GetItem(0).GetWindow(), dialog.pages[page_index])
            dialog.select_page(page_index)
            self.assertEqual(dialog.about_title.GetLabel(), page_name)
            self.assertEqual(dialog.about_text.GetLabel().replace('\n', ' '),
                             progress.ABOUT[page_name])
            self.assertEqual([line.label for line in dialog.checklist.lines],
                             [line.label for line in progress.checklist(dialog.controller, page_name)])
        dialog.select_page(0)
        self.assertEqual(dialog.step_list.headings,
                         {0: 'Nets & Layers', 4: 'Parts', 6: 'Geometry', 10: 'Finish'})
        self.assertEqual(dialog.step_list.statuses[0], 'missing')
        self.assertEqual(dialog.step_list.statuses[PAGES.index('Interposer microvias')],
                         'optional')
        self.assertIsNone(self.wx.FindWindowByLabel('Previous', dialog))
        self.assertIsNone(self.wx.FindWindowByLabel('Close', dialog))
        self.assertIsNone(self.wx.FindWindowByLabel('Status', dialog))
        self.assertIsNone(self.wx.FindWindowByLabel('Apply layer assignments', dialog))
        self.assertIsNone(self.wx.FindWindowByLabel('Apply routing permissions', dialog))
        self.assertIsNone(self.wx.FindWindowByLabel('Saved permissions retained.', dialog))
        self.assertIs(dialog.layer_map.GetParent().GetParent(), dialog.pages[2])
        self.assertIs(dialog.rules.GetParent(), dialog.pages[3])
        self.assertEqual(dialog.step_list.GetCount(), len(PAGES))
        longest_step = max(PAGES, key=len)
        step_extent = self.wx.ClientDC(dialog.step_list).GetTextExtent(longest_step)[0]
        self.assertGreater(dialog.step_list.GetSize().width, step_extent)
        self.assertLess(dialog.step_list.GetSize().height, dialog.GetClientSize().height)
        self.assertGreater(dialog.about_box.GetSize().height, 120)
        self.assertEqual(dialog.step_list.GetBackgroundColour().Get()[:3],
                         ui_palette().window.Get()[:3])
        self.assertEqual(dialog.step_list.item_font(0).GetWeight(), self.wx.FONTWEIGHT_BOLD)
        self.assertEqual(dialog.step_list.item_font(1).GetWeight(), self.wx.FONTWEIGHT_NORMAL)
        self.assertTrue(dialog.step_list.GetWindowStyleFlag() & self.wx.BORDER_SIMPLE)
        for control in (dialog.previous_button, dialog.next_button, dialog.save_button,
                        dialog.delete_settings_button, dialog.reset_settings_button,
                        dialog.messages_button):
            self.assertIs(control.GetParent(), dialog.toolbar)
            self.assertTrue(control.GetToolTipText())
            self.assertTrue(control.GetBitmap().IsOk())
        # Toolbar icons respond like KiCad's: lighter under the pointer, darker
        # while held or switched on, and all the same size and spacing.
        from plugins.connector_interposer_workflow.ui import ToolButton
        tools = (dialog.previous_button, dialog.next_button, dialog.save_button,
                 dialog.delete_settings_button, dialog.reset_settings_button,
                 dialog.messages_button)
        self.assertEqual({type(control) for control in tools}, {ToolButton})
        self.assertEqual(len({control.GetMinSize().Get() for control in tools}), 1)
        button = dialog.next_button
        self.assertEqual(button.shade(), 0)
        button._set(hover=True)
        hover = button.shade()
        button._set(down=True)
        self.assertGreater(button.shade(), hover)
        self.assertGreater(hover, 0)
        button._set(hover=False, down=False)
        step = dialog.book.GetSelection()
        button.press()
        self.assertEqual(dialog.book.GetSelection(), step + 1)
        dialog.previous_button.press()
        self.assertFalse(dialog.messages_button.GetValue())
        self.assertEqual(dialog.messages_button.shade(), 0)
        # Connector assignments: the search box is in the same place on every tab.
        self.assertEqual(dialog.interposer_assignment_filter.GetPosition(),
                         dialog.assignment_filter.GetPosition())
        self.assertEqual(dialog.interposer_assignment_filter.GetMinSize(),
                         dialog.assignment_filter.GetMinSize())
        # Define RF gangs: no standing status boxes under the editor or preview.
        self.assertFalse(dialog.gang_definition_status.IsShown())
        self.assertFalse(dialog.gang_preview_status.IsShown())
        from plugins.connector_interposer_workflow import icons
        self.assertIsNotNone(icons.archive_path(), "KiCad's icon archive must be found")
        self.assertEqual(
            tuple(dialog.placement_notebook.GetPageText(index)
                  for index in range(dialog.placement_notebook.GetPageCount())),
            ('Interposer', 'RF gangs', 'DC connectors', 'Mounting holes', 'Advanced'),
        )
        for kind in dialog.PLACEMENT_KINDS:
            buttons = dialog.placement_buttons[kind]
            self.assertEqual(
                [buttons[key].GetLabel() for key in ('pick', 'show', 'propose')],
                ['Pick in Editor', 'Show in Editor', 'Use Proposed Match'])
            self.assertFalse(buttons['propose'].IsEnabled())
        self.assertEqual(dialog.user_layer.GetValue(), 'User.Drawings')
        self.assertIsNone(self.wx.FindWindowByLabel('Inspect saved drawing', dialog))
        self.assertIsNone(self.wx.FindWindowByLabel('Accept interposer placement', dialog))
        self.assertIsNone(self.wx.FindWindowByLabel('Accept anchor', dialog))
        self.assertIsNone(self.wx.FindWindowByLabel('Accept DC placement', dialog))
        self.assertIn('without applying board changes', dialog.save_button.GetToolTipText())
        self.assertEqual(dialog.layer_map.GetRowLabelSize(), 0)
        self.assertEqual(dialog.layer_map.GetColLabelValue(0), 'Netlist layer')
        self.assertEqual(dialog.layer_map.GetColLabelValue(1), 'KiCad layer')
        dialog.Show()
        self.app.Yield()
        display_index = self.wx.Display.GetFromWindow(dialog)
        display_area = self.wx.Display(display_index).GetClientArea()
        self.assertEqual(dialog.GetSize().width, min(1280, int(display_area.width * .95)))
        self.assertEqual(dialog.GetSize().height, min(800, int(display_area.height * .92)))

        # The message log docks under the window; the step area does not move.
        book_rect = dialog.book.GetScreenRect()
        height = dialog.GetSize().height
        self.assertFalse(dialog.messages.IsShown())
        dialog.status.SetLabel('First message')
        dialog.messages_button.SetValue(True)
        dialog.toggle_messages()
        self.app.Yield()
        self.assertTrue(dialog.messages.IsShown())
        self.assertEqual(dialog.GetSize().height, height + dialog.MESSAGES_HEIGHT + 10)
        self.assertEqual(dialog.book.GetScreenRect(), book_rect)
        self.assertIn('First message', dialog.messages.GetValue())
        self.assertEqual(dialog.status.GetLabel(), 'First message')
        dialog.messages_button.SetValue(False)
        dialog.toggle_messages()
        self.app.Yield()
        self.assertEqual(dialog.GetSize().height, height)
        self.assertEqual(dialog.book.GetScreenRect(), book_rect)

        # Unsaved settings are detected for the close prompt.
        self.assertEqual(dialog._settings_snapshot(), dialog._saved_snapshot)
        dialog.controller.config['mechanical']['layer'] = 'Cmts.User'
        self.assertNotEqual(dialog._settings_snapshot(), dialog._saved_snapshot)
        dialog.controller.config['mechanical']['layer'] = 'Dwgs.User'
        self.assertLessEqual(dialog.pages[0].GetVirtualSize().width,
                             dialog.pages[0].GetClientSize().width)
        self.assertFalse(dialog.view_button.IsEnabled())
        self.assertFalse(dialog.apply_button.IsEnabled())
        self.assertFalse(dialog.workbook_path.IsShown())
        self.assertEqual(dialog.mapper.GetNumberRows(), 13)
        self.assertEqual(dialog.mapper.GetNumberCols(), 9)
        self.assertIsInstance(dialog.mapper.GetCellRenderer(0, 2), AssignmentRenderer)
        self.assertIsNotNone(self.wx.FindWindowByLabel('Open Full Table…', dialog))
        self.assertIsInstance(dialog.interposer_symbol_assignment, SymbolAssignmentCtrl)
        self.assertIsInstance(dialog.interposer_footprint_assignment, FootprintAssignmentCtrl)
        self.assertIsInstance(dialog.hole_path, LibrarySelectionCtrl)
        self.assertIsInstance(dialog.gang_rows, self.wx.grid.Grid)
        self.assertIsNone(self.wx.FindWindowByLabel('Open RF Transition Designer…', dialog))
        self.assertIsNone(self.wx.FindWindowByLabel('Search Project', dialog))
        self.assertEqual(dialog.refresh_libraries_button.GetToolTipText(), 'Refresh libraries')
        self.assertFalse(dialog.microvia_enable.IsEnabled())
        self.assertEqual(dialog.microvia_status.GetLabel(), 'Assign an interposer footprint first')
        self.assertIsNotNone(self.wx.FindWindowByLabel('Side:', dialog.pages[7]))
        self.assertIsNotNone(self.wx.FindWindowByLabel('Rotation:', dialog.pages[7]))
        for control in (dialog.feature_tolerance,):
            self.assertEqual(control.units.GetStringSelection(), 'mil')
            self.assertLessEqual(control.text.GetMaxSize().width, 110)
        self.assertIsNone(self.wx.FindWindowByLabel('Label offset X', dialog.pages[7]))
        self.assertIsNone(self.wx.FindWindowByLabel('Position X', dialog.pages[7]))
        dialog.select_page(7)
        self.app.Yield()
        self.assertLessEqual(
            dialog.pages[7].GetVirtualSize().width,
            dialog.pages[7].GetClientSize().width,
        )
        self.assertEqual(
            [dialog.anchor_gang_list.GetColumn(index).GetText() for index in range(6)],
            ['Gang', 'Location 1 X', 'Location 1 Y', 'Side', 'Rotation', 'Status'])
        self.assertIsNone(self.wx.FindWindowByLabel('Per-location RF assignments…', dialog))
        self.assertIsNone(self.wx.FindWindowByLabel('Accept library selections', dialog))
        self.assertIsNone(self.wx.FindWindowByLabel('Project BOM MPN override (if absent)', dialog))
        footprint = Path(__file__).resolve().parents[1] / 'examples/gppo_demo/library/Example.pretty/GPPO_B012_L13_01_EXPERIMENTAL.kicad_mod'
        class Catalog:
            kind = 'footprint'
            def libraries(self):
                return (LibraryEntry('Example', 'fp_lib_table', footprint.parent, 'Project'),)
            def parts(self, nickname):
                return (LibraryPart('Example:GPPO_B012_L13_01_EXPERIMENTAL', footprint, 'Project'),)
        picker = LibraryPartPickerDialog(dialog, Catalog(), 'Choose connector',
                                         'Example:GPPO_B012_L13_01_EXPERIMENTAL')
        self.assertEqual(picker.selection['library_id'],
                         'Example:GPPO_B012_L13_01_EXPERIMENTAL')
        self.assertTrue(picker.ok.IsEnabled())
        picker.Destroy()
        rows = (HEADERS,) + tuple((f'SIGNAL_NAME_{i:05}', 'RF', 'U1', f'A{i}', 'J101', str(i), 'L1') for i in range(1, 10001))
        table = parse_sheet(Worksheet('Signals', rows), ignored_rows=(2, 5001))
        dialog.controller.table = table
        dialog.controller.set_layers(('F.Cu', 'In1.Cu', 'B.Cu'))
        dialog.refresh_layers()
        palette = ui_palette()
        self.assertEqual(dialog.netclass_type_grid.GetNumberRows(), 1)
        self.assertEqual(dialog.netclass_type_grid.GetCellBackgroundColour(0, 3).Get()[:3],
                         palette.warning.Get()[:3])
        self.assertTrue(dialog.netclass_type_grid.IsReadOnly(0, 3))
        self.assertFalse(dialog.netclass_type_grid.IsCellEditControlShown())
        menu_labels = []
        def dismiss_menu(_window, menu, _position):
            menu_labels.append(tuple(item.GetItemLabelText() for item in menu.GetMenuItems()))
            return False
        with mock.patch.object(dialog, '_popup_assignment_menu', side_effect=dismiss_menu):
            dialog._show_netclass_type_menu(0)
            dialog._show_layer_assignment_menu(0)
        self.assertEqual(menu_labels[0], ('Clear assignment', 'RF', 'DC'))
        self.assertEqual(menu_labels[1], ('Clear assignment', 'F.Cu', 'In1.Cu', 'B.Cu'))
        def choose_rf(_window, menu, _position):
            return next(
                item.GetId() for item in menu.GetMenuItems()
                if item.GetItemLabelText() == 'RF'
            )
        with mock.patch.object(dialog, '_popup_assignment_menu', side_effect=choose_rf):
            dialog._show_netclass_type_menu(0)
        self.assertEqual(dialog.controller.config['netclass_types']['RF'], 'RF')
        self.assertFalse(dialog.netclass_type_grid.IsCellEditControlShown())
        self.assertFalse(dialog.layer_map.IsCellEditControlShown())
        dialog.auto_assign_netclass_types(None)
        self.assertEqual(dialog.controller.config['netclass_types']['RF'], 'RF')
        self.assertEqual(dialog.netclass_type_status.GetLabel(), '1 of 1 assigned')
        self.assertEqual(dialog.netclass_type_grid.GetCellBackgroundColour(0, 3).Get()[:3],
                         palette.assigned.Get()[:3])
        # Nothing appears on the step; the checklist carries the reminder.
        self.assertFalse(dialog.new_rules.IsShown())
        routing = progress.checklist(dialog.controller, 'Routing permissions')[0]
        self.assertEqual((routing.status, routing.label), ('caution', 'New combinations to review'))
        self.assertGreater(int(routing.count), 0)
        for hidden in (dialog.columns_status, dialog.nets_status,
                       dialog.netclass_type_status, dialog.layer_mapping_status):
            self.assertFalse(hidden.IsShown())
        self.assertTrue(AssignmentRenderer.needs_attention(palette.warning))
        self.assertTrue(AssignmentRenderer.needs_attention(palette.error))
        self.assertFalse(AssignmentRenderer.needs_attention(palette.assigned))
        self.assertEqual(dialog.layer_map.GetNumberRows(), 1)
        self.assertIsInstance(dialog.layer_map.GetCellRenderer(0, 1), AssignmentRenderer)
        self.assertEqual(dialog.layer_map.GetCellBackgroundColour(0, 1).Get()[:3],
                         palette.warning.Get()[:3])
        self.assertIsNotNone(self.wx.FindWindowByLabel('Auto Assign', dialog.pages[2]))
        self.assertLess(dialog.layer_map.GetSize().height, 100)
        self.assertEqual(
            dialog.layer_map.GetSize().height,
            dialog.layer_map.GetColLabelSize() + dialog.layer_map.GetRowSize(0) + 2,
        )
        dialog.auto_assign_layers(None)
        self.assertEqual(dialog.controller.config['layer_mapping']['L1'], 'F.Cu')
        self.assertIn('Auto assigned 1 layer', dialog.status.GetLabel())
        dialog.layer_map.SetCellValue(0, 1, 'In1.Cu')
        class LayerEvent:
            @staticmethod
            def GetCol(): return 1
            @staticmethod
            def GetRow(): return 0
            @staticmethod
            def Skip(): return None
        dialog.layer_assignment_changed(LayerEvent())
        self.assertEqual(dialog.controller.config['layer_mapping']['L1'], 'In1.Cu')
        self.assertEqual(dialog.layer_mapping_status.GetLabel(), '1 of 1 assigned')
        self.assertEqual(dialog.layer_map.GetCellBackgroundColour(0, 1).Get()[:3],
                         palette.assigned.Get()[:3])
        self.assertIn('board files were not modified', dialog.status.GetLabel())
        dialog.auto_assign_layers(None)
        self.assertEqual(dialog.controller.config['layer_mapping']['L1'], 'In1.Cu')
        self.assertIn('Preserved 1 existing assignment', dialog.status.GetLabel())
        dialog.rules.SetCellValue(0, 0, '')
        class PermissionEvent:
            @staticmethod
            def GetCol(): return 0
            @staticmethod
            def GetRow(): return 0
            @staticmethod
            def Skip(): return None
        dialog.routing_permission_changed(PermissionEvent())
        self.assertFalse(dialog.controller.config['layer_rules']['RF']['F.Cu'])
        self.assertIn('not allowed', dialog.status.GetLabel())
        popup = DataDialog(dialog, table)
        native_grid = next(child for child in popup.GetChildren() if isinstance(child, self.wx.grid.Grid))
        self.assertEqual(native_grid.GetNumberRows(), 9998)
        self.assertEqual(native_grid.GetNumberCols(), 8)
        self.assertEqual(tuple(native_grid.GetColLabelValue(column) for column in range(8)),
                         ('Source row',) + HEADERS)
        self.assertGreater(native_grid.GetRowLabelSize(), 0)
        self.assertEqual(native_grid.GetRowLabelValue(0), '1')
        self.assertEqual(native_grid.GetCellValue(0, 0), '3')
        self.assertEqual(native_grid.GetRowLabelValue(9997), '9998')
        self.assertEqual(native_grid.GetCellValue(9997, 0), '10001')
        self.assertEqual(native_grid.GetCellValue(9997, 1), 'SIGNAL_NAME_10000')
        self.assertGreater(native_grid.GetColSize(1), native_grid.GetColSize(2))
        area = self.wx.Display(self.wx.Display.GetFromWindow(popup)).GetClientArea()
        self.assertLessEqual(popup.GetSize().width, min(1600, int(area.width * .9)))
        self.assertLessEqual(popup.GetSize().height, min(900, int(area.height * .9)))
        self.assertFalse(native_grid.IsEditable())
        popup.Destroy()
        dialog.Destroy()

    def test_settings_actions_require_confirmation_and_keep_distinct_scopes(self):
        from plugins.connector_interposer_workflow.ui import WorkflowDialog
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            board = root / 'source.kicad_pcb'
            board.write_text('(kicad_pcb)')
            workbook = root / 'signals.csv'
            workbook.write_text(
                'Signal,NetClass,Interposer,Interposer Pin,Connector,Connector Pin,Layer\n'
                'CLOCK,RF,U1,A1,J101,1,L1\n'
            )
            controller = WorkflowController(board)
            controller.load_workbook(workbook)
            controller.save()
            dialog = WorkflowDialog(controller=controller)
            saved = controller.config_path.read_text()

            controller.config['layer_mapping']['L1'] = 'changed'
            with mock.patch.object(dialog, '_confirm', return_value=False):
                self.assertFalse(dialog.save(None))
                self.assertFalse(dialog.reset_workflow_settings(None))
                self.assertFalse(dialog.delete_workflow_settings(None))
            self.assertEqual(controller.config_path.read_text(), saved)
            self.assertIsNotNone(controller.table)

            with mock.patch.object(dialog, '_confirm', return_value=True):
                self.assertTrue(dialog.reset_workflow_settings(None))
            self.assertEqual(dialog.workbook_path.GetValue(), '')
            self.assertEqual(dialog.header.GetValue(), 1)
            self.assertIsNone(controller.table)
            self.assertTrue(controller.config_path.is_file())

            with mock.patch.object(dialog, '_confirm', return_value=True):
                self.assertTrue(dialog.delete_workflow_settings(None))
            self.assertFalse(controller.config_path.exists())
            dialog.Destroy()
        self.app.Yield()

    def test_spreadsheet_auto_mapping_uses_populated_columns_and_live_examples(self):
        from plugins.connector_interposer_workflow.ui import WorkflowDialog, ui_palette
        from plugins.connector_interposer_workflow.core.model import HEADERS
        with tempfile.TemporaryDirectory() as temporary:
            workbook = Path(temporary) / 'signals.csv'
            workbook.write_text(
                'Signal,NetClass,Interposer,Interposer Pin,Connector,Connector Pin,Layer,\n'
                'CLOCK1,CLOCK,E1,K3,J101,1,L2,\n'
                'DATA1,DATA,E1,M6,J101,2,L3,\n',
                encoding='utf-8',
            )
            dialog = WorkflowDialog()
            try:
                dialog.workbook_path.SetValue(str(workbook))
                deadline = time.monotonic() + 2
                while dialog.controller.workbook is None and time.monotonic() < deadline:
                    self.app.Yield()
                    time.sleep(.01)
                self.assertIsNotNone(dialog.controller.workbook)
                self.assertIsNone(dialog.controller.table)
                self.assertEqual(dialog.sheet.GetStringSelection(), '')
                self.assertEqual(dialog.header.GetValue(), 1)
                self.assertEqual(dialog.spreadsheet_feedback.GetLabel(), 'Choose a netlist worksheet.')
                dialog.sheet.SetStringSelection('signals')
                dialog._parse_spreadsheet('redetect')
                dialog.Show()
                self.app.Yield()
                self.assertIsNotNone(dialog.controller.table)
                self.assertEqual(dialog.sheet.GetStringSelection(), 'signals')
                self.assertLessEqual(dialog.sheet.GetSize().width, 180)
                self.assertEqual(dialog.mapper.GetNumberCols(), 9)
                self.assertEqual(dialog.mapper.GetCellValue(0, 2), 'A · Signal')
                self.assertEqual(dialog.mapper.GetCellValue(1, 0), '1')
                self.assertEqual(dialog.mapper.GetCellValue(1, 1), '2')
                self.assertEqual(dialog.mapper.GetCellValue(1, 2), 'CLOCK1')
                self.assertEqual(dialog.workbook_filename.GetLabel(), 'signals.csv')
                self.assertEqual(dialog.mapper.GetCellBackgroundColour(0, 2).Get()[:3],
                                 ui_palette().assigned.Get()[:3])
                self.assertEqual(len(dialog._spreadsheet_columns), 7)
                self.assertFalse(any(label.startswith('H ·') for label in dialog._spreadsheet_columns))
                self.assertEqual(dialog.columns_status.GetLabel(), '7 of 7 columns assigned')
                self.assertEqual(dialog.nets_status.GetLabel(), '2 nets parsed')
                self.assertTrue(dialog.mapper.IsReadOnly(0, 2))
                self.assertFalse(dialog.mapper.IsCellEditControlShown())
                menu_labels = []
                checked_labels = []
                def dismiss_menu(_window, menu, _position):
                    menu_labels.extend(item.GetItemLabelText() for item in menu.GetMenuItems())
                    checked_labels.extend(item.GetItemLabelText() for item in menu.GetMenuItems()
                                          if item.IsChecked())
                    return False
                with mock.patch.object(dialog, '_popup_assignment_menu', side_effect=dismiss_menu):
                    dialog._show_assignment_menu(2)
                self.assertIn('Clear assignment', menu_labels)
                self.assertIn('A · Signal', menu_labels)
                self.assertEqual(checked_labels, ['A · Signal'])
                self.assertFalse(dialog.mapper.IsCellEditControlShown())
                expected_width = sum(
                    dialog.mapper.GetColSize(column)
                    for column in range(dialog.mapper.GetNumberCols())
                ) + dialog.mapper.GetNumberCols() + 2
                self.assertEqual(dialog.mapper.GetSize().width, expected_width)
                self.assertLess(dialog.mapper.GetSize().width,
                                dialog.book.GetClientSize().width)

                dialog.ignored.ChangeValue('2')
                dialog._parse_spreadsheet('mapping')
                self.assertEqual(dialog.mapper.GetCellValue(1, 1), '3')
                self.assertEqual(dialog.mapper.GetCellValue(1, 2), 'DATA1')
                self.assertEqual(dialog.nets_status.GetLabel(), '1 net parsed')

                dialog._set_mapping_choice(2, 'B · NetClass')
                dialog._set_mapping_choice(3, 'A · Signal')
                dialog._cancel_spreadsheet_timer()
                dialog._parse_spreadsheet('mapping')
                self.assertEqual(dialog.controller.table.mapping['Signal'], 1)
                self.assertEqual(dialog.controller.table.mapping['NetClass'], 0)
                self.assertIsNone(self.wx.FindWindowByLabel('Load / parse', dialog))
                self.assertIsNone(self.wx.FindWindowByLabel('Apply manual mapping', dialog))
                self.assertIsNotNone(self.wx.FindWindowByLabel('Reload', dialog))

                workbook.write_text(workbook.read_text(encoding='utf-8').replace('DATA1', 'DATA9'),
                                    encoding='utf-8')
                dialog.reload_workbook(None)
                self.assertEqual(dialog.ignored.GetValue(), '2')
                self.assertEqual(dialog.mapper.GetCellValue(0, 2), 'A · Signal')
                self.assertEqual(dialog.mapper.GetCellValue(1, 2), 'DATA9')
                self.assertEqual(tuple(dialog.controller.table.mapping[name] for name in HEADERS),
                                 tuple(range(7)))

                wide = Path(temporary) / 'wide.csv'
                headings = [f'Other {index}' for index in range(1, 18)] + [
                    'Wire', 'Kind', 'Device', 'Ball', 'Gang', 'Position', 'Route',
                ]
                values = [f'ignored-{index}' for index in range(1, 18)] + [
                    'CLOCK7', 'CLOCK', 'E1', 'K3', 'J101', '1', 'L2',
                ]
                wide.write_text(','.join(headings) + '\n' + ','.join(values) + '\n', encoding='utf-8')
                dialog.header.SetValue(2)
                dialog.workbook_path.SetValue(str(wide))
                deadline = time.monotonic() + 2
                while (dialog.controller.workbook is None or dialog.sheet.GetCount() < 2) and time.monotonic() < deadline:
                    self.app.Yield()
                    time.sleep(.01)
                self.assertEqual(dialog.sheet.GetStringSelection(), '')
                self.assertEqual(dialog.header.GetValue(), 1)
                dialog.sheet.SetStringSelection('wide')
                dialog._parse_spreadsheet('redetect')
                self.assertEqual([dialog.mapper.GetCellValue(0, column) for column in range(2, 9)], [''] * 7)
                self.assertEqual(dialog.mapper.GetCellBackgroundColour(0, 2).Get()[:3],
                                 ui_palette().warning.Get()[:3])
                dialog.mapper.SetCellValue(0, 2, 'R · Wire')
                self.assertTrue(dialog._autofill_consecutive_mapping())
                self.assertEqual(
                    [dialog.mapper.GetCellValue(0, column).split(' · ', 1)[0] for column in range(2, 9)],
                    ['R', 'S', 'T', 'U', 'V', 'W', 'X'],
                )
                dialog._parse_spreadsheet('mapping')
                self.assertEqual(tuple(dialog.controller.table.mapping[name] for name in HEADERS),
                                 tuple(range(17, 24)))

                dialog.header.SetValue(2)
                dialog._parse_spreadsheet('mapping')
                self.assertEqual(tuple(dialog.controller.table.mapping[name] for name in HEADERS),
                                 tuple(range(17, 24)))
                self.assertEqual(dialog.mapper.GetCellValue(0, 2), 'R · CLOCK7')
                dialog.header.SetValue(1)
                dialog._parse_spreadsheet('mapping')
                self.assertEqual(tuple(dialog.controller.table.mapping[name] for name in HEADERS),
                                 tuple(range(17, 24)))
                self.assertEqual(dialog.mapper.GetCellValue(0, 2), 'R · Wire')

                dialog.header.SetValue(999)
                dialog._parse_spreadsheet('mapping')
                self.assertIn(
                    "Header row 999 does not exist in worksheet 'wide'",
                    dialog.spreadsheet_feedback.GetLabel(),
                )
                self.assertEqual(dialog.mapper.GetCellValue(0, 2), 'R · Wire')

                dialog.header.SetValue(1)
                dialog._parse_spreadsheet('mapping')
                dialog.mapper.SetCellValue(0, 2, 'S · Kind')
                dialog._parse_spreadsheet('mapping')
                self.assertEqual(dialog.controller.table.mapping['Signal'], 18)
                dialog._parse_spreadsheet('redetect')
                self.assertEqual([dialog.mapper.GetCellValue(0, column) for column in range(2, 9)], [''] * 7)
                dialog.sheet.SetSelection(0)
                dialog._parse_spreadsheet('redetect')
                self.assertIsNone(dialog.controller.table)
                self.assertEqual([dialog.mapper.GetCellValue(0, column) for column in range(2, 9)], [''] * 7)
            finally:
                dialog.Destroy()
                self.app.Yield()

    def test_connector_assignments_filter_loaded_footprints_per_gang(self):
        import hashlib
        from plugins.connector_interposer_workflow.ui import (
            WorkflowDialog, TableDividerCell, TableTextCell, ui_palette,
        )
        from plugins.connector_interposer_workflow.core.libraries import read_symbol
        example = Path(__file__).resolve().parents[1] / 'examples/gppo_demo'
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            board = project / 'demo.kicad_pcb'
            board.write_text('(kicad_pcb (version 20240108) (generator pcbnew))')
            settings = project / 'settings'
            settings.mkdir()
            (project / 'fp-lib-table').write_text(
                '(fp_lib_table (lib (name "Example") (type "KiCad") '
                f'(uri "{example / "library/Example.pretty"}") (options "")))'
            )

            class Settings:
                @classmethod
                def GetUserSettingsPath(cls): return str(settings)
            class Board:
                @staticmethod
                def GetProject(): return object()
            class Pcbnew:
                SETTINGS_MANAGER = Settings
                @staticmethod
                def GetBoard(): return Board()
                @staticmethod
                def ResolveUriByEnvVars(value, native_project): return str(value)

            controller = WorkflowController(board)
            controller.load_workbook(example / 'inputs/netlist-A.xlsx')
            for netclass in {record.netclass for record in controller.table.records}:
                controller.set_netclass_type(netclass, 'RF')
            dialog = WorkflowDialog(controller=controller, pcbnew_module=Pcbnew)
            try:
                symbols = example / 'library/Example.kicad_sym'
                digest = hashlib.sha256(symbols.read_bytes()).hexdigest()
                interposer = read_symbol(symbols, 'Example:Interposer', require_two_pin=False)
                interposer_spec = {'path': str(symbols), 'library_id': interposer.library_id,
                                   'sha256': digest}
                dialog.assign_interposer_symbol(interposer_spec, interposer)
                self.assertTrue(dialog.interposer_footprint_assignment.IsEnabled())
                self.assertEqual(
                    [option['library_id'] for option in dialog.interposer_footprint_assignment.options],
                    ['Example:Interposer_800'],
                )
                footprint_menu = []
                def dismiss_footprint_menu(_window, menu, _position):
                    footprint_menu.extend(item.GetItemLabelText() for item in menu.GetMenuItems())
                    return False
                with mock.patch.object(
                        dialog, '_popup_assignment_menu', side_effect=dismiss_footprint_menu):
                    dialog.interposer_footprint_assignment._show_menu()
                self.assertEqual(
                    footprint_menu,
                    ['Clear assignment', 'Example:Interposer_800'],
                )
                dialog.interposer_footprint_assignment.select_index(0)
                self.assertEqual(dialog.interposer_assignment_status.GetLabel(),
                                 '1 of 1 interposers assigned')
                dialog.interposer_footprint_assignment._select({})
                self.assertEqual(dialog.interposer_assignment_status.GetLabel(),
                                 '0 of 1 interposers assigned')
                dialog.interposer_footprint_assignment.select_index(0)

                connector = read_symbol(symbols, 'Example:GPPO')
                connector_spec = {'path': str(symbols), 'library_id': connector.library_id,
                                  'sha256': digest}
                for gang in ('J101', 'J102'):
                    dialog.assign_gang_symbol(gang, connector_spec, connector)
                    footprint = dialog.gang_assignment_controls[gang]['footprint']
                    self.assertTrue(footprint.IsEnabled())
                    self.assertEqual([option['library_id'] for option in footprint.options],
                                     ['Example:GPPO_B012_L13_01_EXPERIMENTAL'])
                    footprint.select_index(0)
                self.assertEqual(dialog.gang_assignment_status.GetLabel(),
                                 '2 of 2 gangs assigned')
                self.assertEqual(
                    dialog.gang_assignment_rows.GetBackgroundColour().Get()[:3],
                    ui_palette().window.Get()[:3],
                )
                self.assertTrue(
                    dialog.gang_assignment_rows.GetWindowStyleFlag() & self.wx.BORDER_SIMPLE,
                )
                self.assertGreaterEqual(dialog.gang_assignment_table.GetPosition().x, 4)
                self.assertGreaterEqual(dialog.gang_assignment_table.GetPosition().y, 4)
                self.assertFalse(any(
                    isinstance(child, self.wx.StaticLine)
                    for child in dialog.gang_assignment_table.GetChildren()
                ))
                self.assertEqual(
                    sum(isinstance(child, TableDividerCell)
                        for child in dialog.gang_assignment_table.GetChildren()),
                    4,
                )
                self.assertEqual(
                    [dialog.assignment_notebook.GetPageText(index)
                     for index in range(dialog.assignment_notebook.GetPageCount())],
                    ['Interposer', 'RF gangs', 'DC connectors'],
                )
                dialog.select_page(PAGES.index('Connector assignments'))
                dialog.assignment_notebook.SetSelection(1)
                dialog.Show()
                dialog.Layout()
                self.app.Yield()
                read_only_cells = [
                    child for child in dialog.gang_assignment_table.GetChildren()
                    if isinstance(child, TableTextCell) and child.GetLabel() == 'J101'
                ]
                self.assertEqual(len(read_only_cells), 1)
                j101_cell = read_only_cells[0]
                self.assertEqual(j101_cell.GetSize().height, 38)
                self.assertGreater(j101_cell.text.GetPosition().y, 0)
                self.assertLessEqual(
                    dialog.gang_assignment_controls['J101']['symbol'].GetSize().width, 300,
                )
                self.assertLessEqual(
                    dialog.gang_assignment_controls['J101']['footprint'].GetSize().width, 320,
                )
                self.assertIsNone(self.wx.FindWindowByLabel('Status', dialog.pages[4]))
            finally:
                dialog.Destroy()
                self.app.Yield()

    def test_overflow_tooltips_cover_assignment_and_value_entry_cells(self):
        from plugins.connector_interposer_workflow.ui import (
            DimensionCellRenderer, WorkflowDialog,
        )

        dialog = WorkflowDialog()
        try:
            dialog.select_page(4)
            dialog.Show()
            self.app.Yield()
            long_symbol = 'Project:' + 'Very_Long_Connector_Symbol_' * 8
            dialog.interposer_symbol_assignment.set_selection({
                'library_id': long_symbol,
            })
            dialog.interposer_symbol_assignment.Layout()
            dialog.interposer_symbol_assignment._refresh_overflow_tooltip()
            self.assertEqual(
                dialog.interposer_symbol_assignment.display.GetToolTipText(),
                long_symbol,
            )
            dialog.interposer_symbol_assignment.set_selection({'library_id': 'J1'})
            dialog.interposer_symbol_assignment._refresh_overflow_tooltip()
            self.assertEqual(
                dialog.interposer_symbol_assignment.display.GetToolTipText(), '',
            )

            long_footprint = 'Project:' + 'Very_Long_Connector_Footprint_' * 8
            selection = {'library_id': long_footprint, 'path': '/tmp/example.kicad_mod'}
            dialog.interposer_footprint_assignment.set_options([selection], selection)
            dialog.interposer_footprint_assignment.Layout()
            dialog.interposer_footprint_assignment._refresh_overflow_tooltip()
            self.assertEqual(
                dialog.interposer_footprint_assignment.chevron.GetToolTipText(),
                long_footprint,
            )

            long_assignment = 'Workbook column with a deliberately long heading'
            dialog.mapper.SetCellValue(0, 2, long_assignment)
            dialog.mapper.SetColSize(2, 90)
            self.assertEqual(
                dialog._grid_overflow_text(dialog.mapper, 0, 2), long_assignment,
            )
            dialog.mapper.SetColSize(2, 600)
            self.assertEqual(dialog._grid_overflow_text(dialog.mapper, 0, 2), '')

            long_dimension = '123456789.123456 inch'
            dialog.gang_rows.SetCellRenderer(0, 2, DimensionCellRenderer())
            dialog.gang_rows.SetCellValue(0, 2, long_dimension)
            dialog.gang_rows.SetColSize(2, 90)
            self.assertEqual(
                dialog._grid_overflow_text(dialog.gang_rows, 0, 2), long_dimension,
            )
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_footprint_popup_routes_selection_through_its_assignment_cell(self):
        from plugins.connector_interposer_workflow.ui import (
            FootprintAssignmentCtrl, WorkflowDialog,
        )

        dialog = WorkflowDialog()
        selected = []
        control = FootprintAssignmentCtrl(dialog, selected.append)
        option = {
            'path': '/tmp/Connector_25.kicad_mod',
            'library_id': 'Project:Connector_25',
            'sha256': 'test-revision',
        }
        control.set_options((option,), {})
        control.Enable(True)

        def choose_first(window, menu, _position):
            self.assertIs(window, control)
            clear_item, footprint_item = menu.GetMenuItems()
            self.assertEqual(clear_item.GetItemLabelText(), 'Clear assignment')
            self.assertEqual(clear_item.GetKind(), self.wx.ITEM_RADIO)
            self.assertTrue(clear_item.IsChecked())
            self.assertEqual(footprint_item.GetKind(), self.wx.ITEM_RADIO)
            self.assertFalse(footprint_item.IsChecked())
            return footprint_item.GetId()

        try:
            with mock.patch.object(
                    dialog, '_popup_assignment_menu', side_effect=choose_first):
                control._show_menu()
            self.assertEqual(control.selection(), option)
            self.assertEqual(selected, [option])

            def choose_clear(_window, menu, _position):
                clear_item, footprint_item = menu.GetMenuItems()
                self.assertFalse(clear_item.IsChecked())
                self.assertTrue(footprint_item.IsChecked())
                return clear_item.GetId()

            with mock.patch.object(
                    dialog, '_popup_assignment_menu', side_effect=choose_clear):
                control._show_menu()
            self.assertEqual(control.selection(), {})
            self.assertEqual(selected, [option, {}])
        finally:
            control.Destroy()
            dialog.Destroy()
            self.app.Yield()

    def test_all_connector_assignment_rows_apply_and_individual_edits_clear_them(self):
        from types import SimpleNamespace
        from plugins.connector_interposer_workflow.ui import WorkflowDialog, ui_palette
        from plugins.connector_interposer_workflow.core.model import HEADERS
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            footprint = root / 'Connector.kicad_mod'
            footprint.write_text(
                '(footprint "Connector" (layer "F.Cu") '
                '(pad "1" thru_hole circle (at 0 0) (size 1 1) (drill .5) '
                '(layers "*.Cu" "*.Mask")))'
            )
            symbol_path = root / 'Connector.kicad_sym'
            symbol_path.write_text('(kicad_symbol_lib (version 20241209) (generator test))')
            table = parse_sheet(Worksheet('Signals', (
                HEADERS,
                ('RF1', 'RF', 'E1', 'A1', 'J101', '1', 'L1'),
                ('RF2', 'RF', 'E1', 'A2', 'J102', '1', 'L1'),
                ('DC1', 'DC', 'E1', 'A3', 'J1', '1', 'L1'),
                ('DC2', 'DC', 'E1', 'A4', 'J2', '1', 'L1'),
            )))
            dialog = WorkflowDialog()
            try:
                dialog.controller.table = table
                dialog.controller.set_netclass_type('RF', 'RF')
                dialog.controller.set_netclass_type('DC', 'DC')
                option = {
                    'path': str(footprint), 'library_id': 'Example:Connector',
                    'sha256': '',
                }
                symbol_spec = {
                    'path': str(symbol_path), 'library_id': 'Example:Connector',
                    'sha256': '',
                }
                symbol = SimpleNamespace(
                    library_id='Example:Connector', value='Connector', mpn='',
                    unit_count=1, footprint_filters=('Connector',),
                    pin_numbers=(('1', 0, 0, 0), ('2', 0, 0, 0)),
                )
                dialog._allowed_footprint_specs = lambda _symbol: (option,)
                dialog.assignment_filter.ChangeValue('J101')
                dialog.refresh_connector_assignments(True)
                self.assertEqual(set(dialog.gang_assignment_controls), {'J101'})
                self.assertEqual(set(dialog.dc_assignment_controls), {'J1', 'J2'})
                dialog.dc_assignment_filter.ChangeValue('J1')
                dialog.refresh_connector_assignments(True)
                self.assertEqual(set(dialog.dc_assignment_controls), {'J1'})

                dialog.assign_all_gang_symbols(symbol_spec, symbol)
                dialog.assign_all_gang_footprints(option)
                for name in ('J101', 'J102'):
                    self.assertEqual(
                        dialog.controller.gang(name)['symbol']['library_id'],
                        'Example:Connector',
                    )
                    self.assertEqual(
                        dialog.controller.gang(name)['library_id'], 'Example:Connector',
                    )
                dialog.assign_all_gang_footprints({})
                for name in ('J101', 'J102'):
                    self.assertEqual(dialog.controller.gang(name)['library_id'], '')
                dialog.assign_all_gang_footprints(option)
                self.assertEqual(
                    dialog.gang_assignment_all_controls['symbol'].selection['library_id'],
                    'Example:Connector',
                )
                dialog.assign_gang_symbol('J101', symbol_spec, symbol)
                self.assertEqual(dialog.gang_assignment_all_controls['symbol'].selection, {})
                self.assertFalse(dialog.gang_assignment_all_controls['footprint'].IsEnabled())
                # The bulk row is a shortcut, never a missing entry.
                bulk_symbol = dialog.gang_assignment_all_controls['symbol']
                self.assertFalse(bulk_symbol.needs_attention)
                self.assertEqual(bulk_symbol.display.GetLabel(), 'Set for all…')
                self.assertEqual(bulk_symbol.GetBackgroundColour().Get()[:3],
                                 ui_palette().window.Get()[:3])

                dialog.assign_all_dc_symbols(symbol_spec, symbol)
                dialog.assign_all_dc_footprints(option)
                for name in ('J1', 'J2'):
                    self.assertEqual(
                        dialog.controller.dc_connector(name)['symbol']['library_id'],
                        'Example:Connector',
                    )
                    self.assertEqual(
                        dialog.controller.dc_connector(name)['library_id'],
                        'Example:Connector',
                    )
                dialog.assign_dc_footprint('J1', option)
                self.assertEqual(dialog.dc_assignment_all_controls['symbol'].selection, {})
                self.assertEqual(
                    dialog.dc_assignment_all_controls['symbol_pins'].GetLabel(), '—',
                )
                self.assertFalse(dialog.dc_assignment_all_controls['footprint'].IsEnabled())
                dialog.assign_dc_footprint('J1', {})
                self.assertEqual(dialog.controller.dc_connector('J1')['library_id'], '')
            finally:
                dialog.Destroy()
                self.app.Yield()

    def test_connector_assignment_pin_counts_gate_dc_completion(self):
        import hashlib
        from plugins.connector_interposer_workflow.ui import WorkflowDialog, ui_palette
        from plugins.connector_interposer_workflow.core.libraries import read_symbol
        from plugins.connector_interposer_workflow.core.model import HEADERS
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            symbols = root / 'Connectors.kicad_sym'
            symbols.write_text('''(kicad_symbol_lib (version 20241209) (generator "test")
              (symbol "OnePin" (in_bom yes) (on_board yes)
                (property "Reference" "J" (at 0 5 0) (effects (font (size 1 1))))
                (property "Value" "OnePin" (at 0 7 0) (effects (font (size 1 1))))
                (property "ki_fp_filters" "DC3" (at 0 0 0) (effects (font (size 1 1)) hide))
                (symbol "OnePin_1_1"
                  (pin passive line (at 0 0 180) (length 2.54)
                    (name "1" (effects (font (size .8 .8))))
                    (number "1" (effects (font (size .8 .8)))))))
              (symbol "ThreePin" (in_bom yes) (on_board yes)
                (property "Reference" "J" (at 0 5 0) (effects (font (size 1 1))))
                (property "Value" "ThreePin" (at 0 7 0) (effects (font (size 1 1))))
                (property "ki_fp_filters" "DC3" (at 0 0 0) (effects (font (size 1 1)) hide))
                (symbol "ThreePin_1_1"
                  (pin passive line (at 0 0 180) (length 2.54) (name "1" (effects (font (size .8 .8)))) (number "1" (effects (font (size .8 .8)))))
                  (pin passive line (at 0 -2.54 180) (length 2.54) (name "2" (effects (font (size .8 .8)))) (number "2" (effects (font (size .8 .8)))))
                  (pin passive line (at 0 -5.08 180) (length 2.54) (name "3" (effects (font (size .8 .8)))) (number "3" (effects (font (size .8 .8))))))))''')
            footprint = root / 'DC3.kicad_mod'
            footprint.write_text(
                '(footprint "DC3" (layer "F.Cu") '
                '(pad "1" thru_hole circle (at 0 0) (size 1 1) (drill .5) (layers "*.Cu" "*.Mask")) '
                '(pad "2" thru_hole circle (at 2 0) (size 1 1) (drill .5) (layers "*.Cu" "*.Mask")) '
                '(pad "3" thru_hole circle (at 4 0) (size 1 1) (drill .5) (layers "*.Cu" "*.Mask")))'
            )
            table = parse_sheet(Worksheet('Signals', (
                HEADERS,
                ('PWR', 'DC', 'E1', 'A1', 'J1', '1', 'L1'),
                ('CTRL', 'DC', 'E1', 'A2', 'J1', '2', 'L1'),
            )))
            dialog = WorkflowDialog()
            try:
                dialog.controller.table = table
                dialog.controller.set_netclass_type('DC', 'DC')
                option = {
                    'path': str(footprint), 'library_id': 'Example:DC3', 'sha256': '',
                }
                dialog._allowed_footprint_specs = lambda _symbol: (option,)
                dialog.refresh_connector_assignments(True)
                digest = hashlib.sha256(symbols.read_bytes()).hexdigest()

                one = read_symbol(symbols, 'Example:OnePin', require_two_pin=False)
                one_spec = {'path': str(symbols), 'library_id': one.library_id, 'sha256': digest}
                dialog.assign_dc_symbol('J1', one_spec, one)
                controls = dialog.dc_assignment_controls['J1']
                self.assertEqual(controls['symbol_pins'].GetLabel(), '1')
                self.assertFalse(controls['footprint'].IsEnabled())
                # A symbol that cannot serve the used pins is drawn as missing:
                # white surface, red outline and the red X.
                self.assertTrue(controls['symbol'].needs_attention)
                self.assertEqual(controls['symbol'].GetBackgroundColour().Get()[:3],
                                 ui_palette().window.Get()[:3])
                self.assertEqual(dialog.dc_assignment_status.GetLabel(),
                                 '0 of 1 DC connectors assigned')

                three = read_symbol(symbols, 'Example:ThreePin', require_two_pin=False)
                three_spec = {'path': str(symbols), 'library_id': three.library_id, 'sha256': digest}
                dialog.assign_dc_symbol('J1', three_spec, three)
                self.assertEqual(controls['symbol_pins'].GetLabel(), '3')
                self.assertTrue(controls['footprint'].IsEnabled())
                controls['footprint'].select_index(0)
                self.assertEqual(dialog.dc_assignment_status.GetLabel(),
                                 '1 of 1 DC connectors assigned')
            finally:
                dialog.Destroy()
                self.app.Yield()

    def test_netclass_and_layer_tables_fit_their_columns_and_all_rows(self):
        from plugins.connector_interposer_workflow.ui import WorkflowDialog
        from plugins.connector_interposer_workflow.core.model import HEADERS
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet

        rows = [HEADERS]
        rows.extend(
            (f'S{index}', f'CLASS{index}', 'E1', f'A{index}', f'J{100 + index}',
             '1', f'L{index}')
            for index in range(1, 13)
        )
        dialog = WorkflowDialog()
        try:
            dialog.controller.table = parse_sheet(Worksheet('Signals', tuple(rows)))
            dialog.controller.set_layers(tuple(f'In{index}.Cu' for index in range(1, 13)))
            dialog.refresh_layers()
            dialog.Show()
            self.app.Yield()
            self.assertEqual(dialog.netclass_type_grid.GetNumberRows(), 12)
            self.assertEqual(dialog.layer_map.GetNumberRows(), 12)
            for view in (dialog.netclass_type_grid, dialog.layer_map):
                content_height = (
                    view.GetColLabelSize()
                    + sum(view.GetRowSize(row) for row in range(12)) + 2
                )
                self.assertEqual(view.GetSize().height, content_height)
                self.assertLess(view.GetSize().width,
                                dialog.book.GetClientSize().width * .75)
            self.assertLessEqual(dialog.layer_map.GetColSize(0), 220)
            self.assertLessEqual(dialog.layer_map.GetColSize(1), 220)
            self.assertLessEqual(dialog.netclass_type_grid.GetColSize(0), 240)
            self.assertLessEqual(dialog.netclass_type_grid.GetColSize(2), 260)
            for view, button in (
                (dialog.netclass_type_grid, dialog.netclass_auto_assign),
                (dialog.layer_map, dialog.layer_auto_assign),
            ):
                # The Spec puts the action below its table, on the table's left edge.
                self.assertLessEqual(abs(view.GetPosition().x - button.GetPosition().x), 8)
                self.assertGreaterEqual(button.GetPosition().y,
                                        view.GetPosition().y + view.GetSize().height)
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_live_gang_definitions_units_and_requirement_transition_assignment(self):
        from plugins.connector_interposer_workflow.core.model import HEADERS
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet
        from plugins.connector_interposer_workflow.ui import (
            DimensionCellEditor, DimensionCellRenderer, WorkflowDialog,
        )

        dialog = WorkflowDialog()
        try:
            from plugins.connector_interposer_workflow.core.libraries import read_footprint
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            part_path = Path(temporary.name) / 'GPPO.kicad_mod'
            part_path.write_text(
                '(footprint "GPPO" (layer "F.Cu") '
                '(pad "1" thru_hole circle (at 0 0) (size 1 1) (drill .5) '
                '(layers "*.Cu" "*.Mask")) '
                '(pad "2" smd circle (at 0 2) (size 1 1) (layers "F.Cu" "F.Mask")))'
            )
            part = read_footprint(part_path, 'Approved:GPPO')
            table = parse_sheet(Worksheet('Signals', (
                HEADERS,
                ('CLOCK1', 'CLOCK', 'E1', 'A1', 'J101', '1', 'L1'),
                ('CLOCK2', 'CLOCK', 'E1', 'A2', 'J101', '2', 'L1'),
            )))
            dialog.controller.table = table
            dialog.controller.set_netclass_type('CLOCK', 'RF')
            dialog.controller.set_layers(('F.Cu', 'B.Cu'))
            dialog.controller.set_layer_mapping('L1', 'F.Cu')
            dialog.controller.set_gang('J101', {
                'part_id': str(part_path),
                'library_id': 'Approved:GPPO', 'part_sha256': part.sha256,
            })
            dialog.refresh_gangs()
            dialog.select_page(PAGES.index('Define RF gangs'))
            dialog.Show()
            self.app.Yield()
            dialog.gang_notebook.SetSelection(1)
            self.app.Yield()
            self.assertEqual(dialog.book.GetSelection(), PAGES.index('Define RF gangs'))
            self.assertEqual(dialog.step_list.GetSelection(), PAGES.index('Define RF gangs'))
            dialog.gang_notebook.SetSelection(0)
            self.app.Yield()
            self.assertEqual(dialog.book.GetSelection(), PAGES.index('Define RF gangs'))
            self.assertEqual(dialog.step_list.GetSelection(), PAGES.index('Define RF gangs'))
            self.assertEqual(dialog.current_gang, 'J101')
            self.assertEqual(dialog.gang_list.GetItemText(0, 2), 'Not defined')
            self.assertIsNone(self.wx.FindWindowByLabel('Apply definition', dialog.pages[PAGES.index('Define RF gangs')]))
            self.assertIsNone(self.wx.FindWindowByLabel('Revert', dialog.pages[PAGES.index('Define RF gangs')]))
            self.assertEqual(dialog.gang_editor_title.GetLabel(), 'J101 Gang definition')
            self.assertEqual(dialog.gang_rows.GetNumberCols(), 3)
            self.assertEqual(dialog.gang_rows.GetColLabelValue(2), 'X offset')
            self.assertLess(
                sum(dialog.gang_list.GetColumnWidth(column) for column in range(3)),
                dialog.gang_list.GetClientSize().width,
            )
            self.assertLess(dialog.gang_list.GetSize().height, 200)
            # A new gang starts empty: no rows, no pitch, nothing drawn.
            self.assertEqual(dialog.gang_rows.GetNumberRows(), 0)
            self.assertEqual(dialog.gang_pitch_x.GetValue(), '')
            self.assertEqual(dialog.gang_definition_status.GetLabel(), 'Not defined')
            self.assertEqual(dialog.pattern_view.points, {})
            dialog.add_gang_row(None)
            self.assertIn('Row 1: enter a position count',
                          dialog.gang_definition_status.GetLabel())
            self.assertEqual(dialog.gang_rows.GetCellValue(0, 2), '0 mil')
            self.assertEqual(dialog.gang_notebook.GetPageText(1), 'Numbering')
            self.assertEqual(dialog.gang_pitch_x.units.GetStringSelection(), 'mil')
            renderer = dialog.gang_rows.GetCellRenderer(0, 2)
            editor = dialog.gang_rows.GetCellEditor(0, 2)
            self.assertIsInstance(renderer, DimensionCellRenderer)
            self.assertIsInstance(editor, DimensionCellEditor)
            renderer.DecRef()
            editor.DecRef()
            dialog.gang_rows.SetCellValue(0, 1, '3')
            dialog.gang_draft_changed()
            self.assertIn('Enter the position pitch', dialog.gang_definition_status.GetLabel())
            dialog.gang_pitch_x.ChangeValue('100')
            dialog.gang_draft_changed()
            self.assertEqual(len(dialog.pattern_view.points), 3)
            self.assertIn('Needs input', dialog.gang_definition_status.GetLabel())
            self.assertEqual(dialog.gang_list.GetItemText(0, 2), 'Invalid')
            dialog.gang_rows.SetCellValue(0, 1, '2')
            dialog.gang_draft_changed()
            dialog.gang_pitch_x.ChangeValue('100 mil')
            dialog.gang_draft_changed()
            self.assertEqual(dialog.controller.gang('J101')['pitch_x'], 2.54)
            self.assertEqual(
                dialog.controller.gang('J101')['display_units']['pitch_x'], 'mil',
            )
            self.assertTrue(dialog.controller.gang('J101')['defined'])
            self.assertEqual(dialog.gang_list.GetItemText(0, 2), 'Defined')
            self.assertEqual(dialog.gang_preview_status.GetLabel(),
                             'Current gang definition')
            self.assertEqual(dialog.gang_rows.GetColLabelValue(0), 'Row')
            visible_grid_height = (dialog.gang_rows.GetColLabelSize() +
                                   5 * dialog.gang_rows.GetDefaultRowSize())
            self.assertGreaterEqual(dialog.gang_rows.GetMinSize().height, visible_grid_height)

            requirements = dialog.controller.rf_transition_requirements()
            self.assertEqual(len(requirements), 1)
            dialog.refresh_rf_transitions()
            self.assertEqual(dialog.transition_list.GetItemText(0), 'Not assigned')
            self.assertEqual(dialog.transition_page_status.GetLabel(),
                             '0 assigned · 1 unassigned (optional)')
            self.assertEqual(dialog.transition_found.GetLabel(),
                             'Found 0 transitions in the project folder')
            artifact = write_transition(
                Path(temporary.name) / 'test.gppo-rf.json', 'Approved:GPPO',
                part.sha256, netclass='CLOCK',
            )
            dialog.controller.set_rf_transition(requirements[0]['key'], artifact)
            dialog.refresh_rf_transitions()
            self.assertEqual(dialog.transition_list.GetItemText(0), 'Ready')
            self.assertEqual(dialog.transition_page_status.GetLabel(),
                             '1 assigned · 0 unassigned (optional)')
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_19_position_gang_offset_editor_activates_and_commits(self):
        """Regression: a wx.Panel editor raised in SWIG and crashed the KiCad host."""
        from plugins.connector_interposer_workflow.core.model import HEADERS
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet
        from plugins.connector_interposer_workflow.ui import WorkflowDialog

        dialog = WorkflowDialog()
        try:
            rows = [HEADERS]
            rows.extend(
                (f'SIGNAL{pin}', 'RF', 'E1', f'A{pin}', 'J501', str(pin), 'L2')
                for pin in range(1, 20)
            )
            dialog.controller.table = parse_sheet(Worksheet('Signals', tuple(rows)))
            dialog.controller.set_netclass_type('RF', 'RF')
            dialog.refresh_gangs()
            dialog.select_page(PAGES.index('Define RF gangs'))
            dialog.Show()
            self.app.Yield()
            self.assertLessEqual(
                dialog.pages[PAGES.index('Define RF gangs')].GetVirtualSize().width,
                dialog.pages[PAGES.index('Define RF gangs')].GetClientSize().width,
            )

            dialog.add_gang_row(None)
            dialog.gang_pitch_x.text.SetFocus()
            dialog.gang_pitch_x.text.SetValue('200 mil')
            dialog.gang_pitch_y.text.SetFocus()
            self.app.Yield()
            dialog.gang_pitch_y.text.SetValue('173.2 mil')
            dialog.gang_rows.SetCellValue(0, 1, '10')
            dialog.gang_draft_changed()
            dialog.add_gang_row(None)
            dialog.gang_rows.SetCellValue(1, 1, '9')
            dialog.gang_draft_changed()

            class ClickEvent:
                @staticmethod
                def GetRow(): return 1
                @staticmethod
                def GetCol(): return 2
                @staticmethod
                def Skip(): return None
            dialog.gang_cell_clicked(ClickEvent())
            self.app.Yield()
            focused = self.wx.Window.FindFocus()
            self.assertIsInstance(focused, self.wx.TextCtrl)
            self.assertGreaterEqual(dialog.gang_rows.GetRowSize(1), 36)
            unit_choice = next(
                child for child in focused.GetParent().GetChildren()
                if isinstance(child, self.wx.Choice)
            )
            self.assertGreaterEqual(unit_choice.GetSize().height, 28)
            focused.SetValue('100 mil')
            enter = self.wx.CommandEvent(self.wx.EVT_TEXT_ENTER.typeId, focused.GetId())
            enter.SetEventObject(focused)
            focused.GetEventHandler().ProcessEvent(enter)
            self.app.Yield()
            self.assertFalse(dialog.gang_rows.IsCellEditControlShown())

            gang = dialog.controller.gang('J501')
            self.assertEqual(gang['row_lengths'], [10, 9])
            self.assertAlmostEqual(gang['pitch_x'], 5.08)
            self.assertAlmostEqual(gang['pitch_y'], 4.39928)
            self.assertEqual(gang['row_offsets'], [0, 2.54])
            self.assertTrue(gang['defined'])
            first_layout, radius = dialog.pattern_view.screen_layout(285, 300)
            first_center = (
                sum(point[0] for point in first_layout.values()) / len(first_layout),
                sum(point[1] for point in first_layout.values()) / len(first_layout),
            )
            self.assertAlmostEqual(first_center[0], 285 / 2, delta=.6)
            self.assertAlmostEqual(first_center[1], 40 + (300 - 56) / 2, delta=.6)
            minimum_spacing = min(
                math.hypot(first[0] - second[0], first[1] - second[1])
                for index, first in enumerate(first_layout.values())
                for second in tuple(first_layout.values())[index + 1:]
            )
            self.assertGreaterEqual(minimum_spacing, radius * 2)
            preview_points = dialog.pattern_view.points
            dialog.pattern_view.points = {
                str(pin): (float(pin - 1), 0.0) for pin in range(1, 20)
            }
            dense_layout, dense_radius = dialog.pattern_view.screen_layout(330, 300)
            dialog.pattern_view.points = preview_points
            self.assertEqual(len(dense_layout), 19)
            self.assertLessEqual(
                dialog.pattern_view.label_point_size(dense_radius, 19, 10), 8,
            )

            dialog.gang_pattern_rotation.ChangeValue('37')
            dialog.gang_draft_changed()
            rotated_layout, _radius = dialog.pattern_view.screen_layout(285, 300)
            rotated_center = (
                sum(point[0] for point in rotated_layout.values()) / len(rotated_layout),
                sum(point[1] for point in rotated_layout.values()) / len(rotated_layout),
            )
            self.assertAlmostEqual(rotated_center[0], first_center[0], delta=.6)
            self.assertAlmostEqual(rotated_center[1], first_center[1], delta=.6)
            self.assertNotEqual(rotated_layout, first_layout)
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_gang_text_commits_on_enter_or_blur_with_one_refresh_per_edit(self):
        from plugins.connector_interposer_workflow.core.model import HEADERS
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet
        from plugins.connector_interposer_workflow.ui import WorkflowDialog

        dialog = WorkflowDialog()
        try:
            rows = [HEADERS]
            rows.extend(
                (f'SIGNAL{pin}', 'RF', 'E1', f'A{pin}', gang, str(pin), 'L2')
                for gang in ('J501', 'J502') for pin in range(1, 20)
            )
            dialog.controller.table = parse_sheet(Worksheet('Signals', tuple(rows)))
            dialog.controller.set_netclass_type('RF', 'RF')
            dialog.refresh_gangs()
            dialog.select_page(PAGES.index('Define RF gangs'))
            dialog.Show()
            self.app.Yield()
            dialog.add_gang_row(None)
            dialog.gang_rows.SetCellValue(0, 1, '19')
            dialog.gang_pitch_x.ChangeValue('100')
            dialog.gang_draft_changed()
            self.assertTrue(dialog.controller.gang('J501')['defined'])

            def press_enter(control):
                event = self.wx.CommandEvent(self.wx.wxEVT_TEXT_ENTER, control.GetId())
                event.SetEventObject(control)
                control.GetEventHandler().ProcessEvent(event)

            with mock.patch.object(dialog, '_refresh', wraps=dialog._refresh) as refresh:
                rotation = dialog.gang_pattern_rotation
                for partial in ('2', '27', '270'):
                    rotation.SetValue(partial)
                    self.app.Yield()
                self.assertEqual(refresh.call_count, 0)
                self.assertEqual(dialog.controller.gang('J501')['rotation'], 0)
                press_enter(rotation)
                self.assertEqual(refresh.call_count, 1)
                self.assertEqual(dialog.controller.gang('J501')['rotation'], 270)
                press_enter(rotation)
                self.assertEqual(refresh.call_count, 1)

                orientation = dialog.gang_connector_rotation
                orientation.SetFocus()
                orientation.SetValue('9')
                orientation.SetValue('90')
                self.assertEqual(dialog.controller.gang('J501')['connector_rotation'], 0)
                dialog.gang_pattern_rotation.SetFocus()
                self.app.Yield()
                focus = self.wx.FocusEvent(self.wx.wxEVT_KILL_FOCUS, orientation.GetId())
                focus.SetEventObject(orientation)
                orientation.GetEventHandler().ProcessEvent(focus)
                self.assertEqual(dialog.controller.gang('J501')['connector_rotation'], 90)
                self.assertEqual(refresh.call_count, 2)

                dialog.gang_rows.SetCellValue(0, 1, '10')
                dialog.gang_draft_changed()
                self.assertEqual(refresh.call_count, 3)
                dialog.guard(dialog.add_gang_row)(None)
                self.assertEqual(refresh.call_count, 4)
                dialog.gang_rows.SetCellValue(1, 1, '9')
                dialog.gang_pitch_y.ChangeValue('100')
                dialog.gang_draft_changed()
                self.assertEqual(refresh.call_count, 5)
                self.assertEqual(dialog.controller.gang('J501')['row_lengths'], [10, 9])

                # Loading another gang is not an edit and must not define it.
                dialog.current_gang = 'J502'
                dialog.select_gang(None)
                press_enter(dialog.gang_pattern_rotation)
                self.assertFalse(dialog.controller.gang('J502')['defined'])
                self.assertEqual(refresh.call_count, 5)
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_status_text_restored_state_and_placement_values_are_shown_completely(self):
        from plugins.connector_interposer_workflow.core.model import HEADERS
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet
        from plugins.connector_interposer_workflow.ui import WorkflowDialog

        dialog = WorkflowDialog()
        try:
            rows = [HEADERS, ('SIGNAL1', 'RF', 'E1', 'A1', 'J501', '1', 'L2'),
                    ('POWER1', 'DC', 'E1', 'A2', 'J1', '1', 'L2')]
            controller = dialog.controller
            controller.table = parse_sheet(Worksheet('Signals', tuple(rows)))
            controller.set_netclass_type('RF', 'RF')
            controller.set_netclass_type('DC', 'DC')
            # 18.3729 mm is on the metric grid; in mils it needs every digit.
            controller.set_gang('J501', {'anchor': [18.3729, 30.7975], 'anchor_accepted': True})
            controller.set_dc_connector('J1', {'anchor': [50.0225, 92.085], 'anchor_accepted': True})
            controller.config['mounting_holes'] = [{
                'reference': 'H1', 'library_id': 'Example:MountingHole_3.2mm_M3',
                'pose': [18.3729, 30.7975, 0], 'pose_units': ['mil', 'mil'],
            }]
            controller.config['outline'] = {'enabled': True, 'outer_uuids': [], 'cutout_uuids': []}
            dialog.restore()
            dialog.refresh_gangs()
            dialog.refresh_mounting_holes()
            dialog.select_page(PAGES.index('Mechanical placement'))
            dialog.Show()
            self.app.Yield()

            self.assertTrue(dialog.outline_enable.GetValue())
            self.assertTrue(dialog.outline_status.GetLabel().startswith('Enabled'))

            dialog.refresh_microvias()
            status = dialog.microvia_status
            for text in ('Off — no interposer microvias requested',
                         'Ready — selected Board Setup size maintains minimum copper spacing'):
                status.SetLabel(text)
                self.assertGreaterEqual(status.GetMinSize().width,
                                        status.GetTextExtent(text).width)

            for view, column, expected in (
                    (dialog.anchor_gang_list, 1, '723.342519685 mil'),
                    (dialog.anchor_gang_list, 2, '1212.5 mil'),
                    (dialog.dc_anchor_list, 1, '1969.38976378 mil'),
                    (dialog.dc_anchor_list, 2, '3625.39370079 mil'),
                    (dialog.hole_list, 2, '723.342519685 mil')):
                text = view.GetItemText(0, column)
                self.assertEqual(text, expected)
                self.assertGreaterEqual(view.GetColumnWidth(column),
                                        view.GetTextExtent(text).width)
                total = sum(view.GetColumnWidth(index)
                            for index in range(view.GetColumnCount()))
                self.assertGreaterEqual(view.GetMinSize().width, total)
            self.assertEqual(
                [dialog.hole_list.GetColumn(index).GetText() for index in (2, 3)],
                ['X', 'Y'],
            )

            controller.preview = {'conflicts': [], 'changes': []}
            dialog.journal.SetValue('/tmp/journal.json')
            self.app.Yield()
            self.assertIsNotNone(controller.preview)
            with mock.patch.object(self.wx, 'MessageBox') as message:
                dialog.recovered({'status': 'RECOVERED'})
                message.assert_called_once()
            self.assertTrue(dialog.status.GetLabel().startswith('Recovery finished'))
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_pick_in_editor_takes_the_next_selection_and_outline_reports_closure(self):
        from plugins.connector_interposer_workflow.ui import WorkflowDialog

        class Identity:
            def __init__(self, value): self.value = value
            def AsString(self): return self.value

        class Item:
            def __init__(self, value):
                self.m_Uuid = Identity(value)
                self.brightened = False
            def SetBrightened(self): self.brightened = True
            def ClearBrightened(self): self.brightened = False

        class Board:
            def __init__(self, items): self.items = items
            def GetDrawings(self): return list(self.items)

        class Editor:
            selection, focused, refreshed = [], [], 0
            board = None
            @classmethod
            def GetCurrentSelection(cls): return list(cls.selection)
            @classmethod
            def GetBoard(cls): return cls.board
            @classmethod
            def FocusOnItem(cls, item): cls.focused.append(item)
            @classmethod
            def Refresh(cls): cls.refreshed += 1

        first, second = Item('a'), Item('b')
        Editor.board = Board([first, second])
        Editor.selection = [first]
        dialog = WorkflowDialog(pcbnew_module=Editor)
        try:
            picked = []
            dialog._arm_pick('a circle', lambda: picked.append(dialog._selection_ids()))
            self.assertIn('Waiting for a circle', dialog.status.GetLabel())
            dialog._poll_pick(None)
            self.assertEqual(picked, [], 'An unchanged selection is not a pick')
            Editor.selection = [second]
            dialog._poll_pick(None)
            self.assertEqual(picked, [('b',)])
            self.assertIsNone(dialog._pick)
            dialog._poll_pick(None)
            self.assertEqual(len(picked), 1, 'A pick is taken once')

            # Deselecting first lets the same item be picked again.
            dialog._arm_pick('a circle', lambda: picked.append(dialog._selection_ids()))
            Editor.selection = []
            dialog._poll_pick(None)
            Editor.selection = [second]
            dialog._poll_pick(None)
            self.assertEqual(picked[-1], ('b',))

            dialog._show_in_editor(['a'])
            self.assertTrue(first.brightened)
            self.assertEqual(Editor.focused, [first])
            dialog._show_in_editor(['b'])
            self.assertFalse(first.brightened)
            self.assertTrue(second.brightened)

            square = [
                {'start': [0, 0], 'end': [10, 0]}, {'start': [10, 0], 'end': [10, 10]},
                {'start': [10, 10], 'end': [0, 10]}, {'start': [0, 10], 'end': [0, 0]},
            ]
            outline = dialog.controller.config['outline']
            outline.update(enabled=True, outer_uuids=['1', '2', '3', '4'],
                           source_preview={'outer': square})
            dialog.refresh_outline()
            self.assertEqual(dialog.outline_list.GetItemText(0, 1), '4')
            self.assertEqual(dialog.outline_list.GetItemText(0, 2), 'Closed')
            self.assertEqual(dialog.outline_list.GetItemText(1, 2), 'None')
            outline['source_preview'] = {'outer': square[:3]}
            dialog.refresh_outline()
            self.assertEqual(dialog.outline_list.GetItemText(0, 2), 'Problem')
            self.assertEqual(progress.checklist(dialog.controller, 'Board outline')[0].count,
                             'problem')
            dialog.outline_list.Select(0)
            dialog.clear_outline(None)
            self.assertEqual(dialog.outline_list.GetItemText(0, 1), '0')
            self.assertIsNone(self.wx.FindWindowByLabel('Validate / accept outline selection', dialog))
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_placement_reads_the_drawing_proposes_picks_and_shows(self):
        from types import SimpleNamespace
        from plugins.connector_interposer_workflow.core.model import HEADERS
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet
        from plugins.connector_interposer_workflow.ui import WorkflowDialog

        class Circle:
            def __init__(self, uuid, x, y):
                self.m_Uuid = SimpleNamespace(AsString=lambda: uuid)
                self.uuid, self.centre, self.brightened = uuid, (x, y), False
            def GetShape(self): return 1
            def GetLayer(self): return 0
            def GetCenter(self):
                return SimpleNamespace(x=self.centre[0] * 1e6, y=self.centre[1] * 1e6)
            def GetRadius(self): return 500000
            def SetBrightened(self): self.brightened = True
            def ClearBrightened(self): self.brightened = False
            def record(self):
                return {'uuid': self.uuid, 'center': list(self.centre), 'radius': .5}

        class Editor:
            PCB_SHAPE, SHAPE_T_CIRCLE = Circle, 1
            selection, focused, live = [], [], []
            @classmethod
            def GetCurrentSelection(cls): return list(cls.selection)
            @classmethod
            def GetBoard(cls):
                return SimpleNamespace(GetDrawings=lambda: list(cls.live),
                                       GetLayerName=lambda layer: 'User.Drawings')
            @classmethod
            def FocusOnItem(cls, item): cls.focused.append(item)
            @classmethod
            def Refresh(cls): pass

        class Backend:
            saved, reads = [], []
            def call(self, name, **arguments):
                self.reads.append((name, arguments['source_layer']))
                return {'circles': [circle.record() for circle in self.saved],
                        'segments': [], 'texts': [],
                        'revision': f'r{len(self.reads)}', 'geometry_revision': 'g'}

        site = [Circle('c0', 10, 10), Circle('c1', 12.54, 10)]
        stray, unsaved = Circle('c4', 70, 70), Circle('c9', 90, 90)
        Editor.live = [*site, stray, unsaved]
        Backend.saved = [*site, stray]
        dialog = WorkflowDialog(pcbnew_module=Editor)

        def settle():
            deadline = time.time() + 10
            while dialog.controller.busy and time.time() < deadline:
                time.sleep(.01)
            self.app.Yield()

        try:
            controller = dialog.controller
            controller.backend = Backend()
            controller.board_path = Path('board.kicad_pcb')
            controller.table = parse_sheet(Worksheet('Signals', (
                HEADERS,
                ('CLOCK1', 'CLOCK', 'E1', 'A1', 'J101', '1', 'L1'),
                ('CLOCK2', 'CLOCK', 'E1', 'A2', 'J101', '2', 'L1'),
            )))
            controller.set_netclass_type('CLOCK', 'RF')
            controller.set_gang('J101', {'row_lengths': [2], 'pitch_x': 2.54, 'defined': True})
            dialog.refresh_gangs()
            dialog.placement_notebook.SetSelection(1)

            # Opening the step reads the saved drawing; there is no button for it.
            dialog.select_page(PAGES.index('Mechanical placement'))
            settle()
            self.assertEqual(Backend.reads, [('inspect', 'User.Drawings')])
            self.assertEqual(dialog.drawing_status.GetLabel(), '3 circles')
            self.assertEqual(controller.config['mechanical']['inspected_revision'], 'r1')
            dialog.select_page(PAGES.index('Mechanical placement'))
            settle()
            self.assertEqual(len(Backend.reads), 1, 'A drawing in memory is not read again')

            # One fit is proposed and highlighted; accepting it places the gang.
            buttons = dialog.placement_buttons['gang']
            self.assertTrue(buttons['propose'].IsEnabled())
            self.assertTrue(dialog.proposal_text['gang'].GetLabel().startswith(
                'Proposed match for J101: 393.700787402 mil, 393.700787402 mil'))
            self.assertEqual([circle.brightened for circle in Editor.live],
                             [True, True, False, False])
            dialog.use_proposal('gang')
            gang = controller.gang('J101')
            self.assertEqual(gang['anchor'], [10, 10])
            self.assertEqual(gang['anchor_circle'],
                             {'uuid': 'c0', 'center': [10, 10], 'revision': 'r1'})
            self.assertTrue(controller.config['mechanical']['accepted'])
            self.assertEqual(
                [dialog.anchor_gang_list.GetItemText(0, column) for column in range(6)],
                ['J101', '393.700787402 mil', '393.700787402 mil', 'Front', '0°', 'Placed'])
            self.assertFalse(buttons['propose'].IsEnabled())
            self.assertEqual(dialog.proposal_text['gang'].GetLabel(), '')
            self.assertFalse(any(circle.brightened for circle in Editor.live))
            dialog.show_placement('gang')
            self.assertEqual([circle.brightened for circle in Editor.live],
                             [True, True, False, False])
            self.assertIs(Editor.focused[-1], site[0])

            # A circle not in the saved board is looked for again in a fresh read.
            controller.config['mounting_holes'].append({
                'reference': 'H1', 'library_id': 'Example:Hole', 'path': 'hole.kicad_mod',
                'sha256': 'x'})
            dialog.refresh_mounting_holes()
            self.assertEqual(
                [dialog.hole_list.GetItemText(0, column) for column in (2, 3, 4)],
                ['—', '—', 'Not placed'])
            with mock.patch.object(self.wx, 'MessageBox') as message:
                dialog.pick_hole(None)
                Editor.selection = [unsaved]
                dialog._poll_pick(None)
                settle()
                message.assert_called_once()
                self.assertIn('Save the board, then pick again', message.call_args[0][0])
            self.assertEqual(len(Backend.reads), 2)
            Backend.saved = [*site, stray, unsaved]
            Editor.selection = []
            dialog.pick_hole(None)
            Editor.selection = [unsaved]
            dialog._poll_pick(None)
            settle()
            self.assertEqual(len(Backend.reads), 3)
            hole = controller.config['mounting_holes'][0]
            self.assertEqual(hole['pose'], [90, 90, 0])
            self.assertEqual(hole['pose_circle']['revision'], 'r3')
            self.assertEqual(dialog.hole_list.GetItemText(0, 4), 'Placed')
            # The gang's circle is the same object in the same place: still placed.
            self.assertEqual(controller.gang('J101')['anchor_circle']['revision'], 'r3')
            self.assertEqual(dialog.anchor_gang_list.GetItemText(0, 5), 'Placed')
            dialog.show_hole(None)
            self.assertTrue(unsaved.brightened)
            # A live circle whose in-memory UUID differs from the saved one is
            # still found, by its position on the drawing layer.
            unsaved.m_Uuid = SimpleNamespace(AsString=lambda: 'transient')
            unsaved.brightened = False
            dialog.show_hole(None)
            self.assertTrue(unsaved.brightened)
            unsaved.m_Uuid = SimpleNamespace(AsString=lambda: 'c9')

            # Picking takes the editor selection; anything but a drawing circle is refused.
            Editor.selection = []
            dialog.pick_placement('gang')
            Editor.selection = [SimpleNamespace(
                m_Uuid=SimpleNamespace(AsString=lambda: 'a-track'))]
            with mock.patch.object(self.wx, 'MessageBox') as message:
                dialog._poll_pick(None)
                self.assertIn('Select one J101 location 1 circle on User.Drawings',
                              message.call_args[0][0])
            self.assertEqual(controller.gang('J101')['anchor'], [10, 10])
            Editor.selection = []
            dialog.pick_placement('gang')
            Editor.selection = [stray]
            dialog._poll_pick(None)
            self.assertEqual(controller.gang('J101')['anchor'], [70, 70])

            # A replaced drawing marks what must be picked again.
            controller.config['mechanical']['inspected_revision'] = 'other'
            dialog.refresh_placement()
            self.assertEqual(dialog.anchor_gang_list.GetItemText(0, 5), 'Pick again')
            self.assertEqual(dialog.hole_list.GetItemText(0, 4), 'Pick again')
            dialog.remove_hole(None)
            self.assertEqual(controller.config['mounting_holes'], [])
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_review_asks_before_saving_and_apply_confirms_then_shows_named_stages(self):
        from plugins.connector_interposer_workflow.core.model import Issue
        from plugins.connector_interposer_workflow.ui import WorkflowDialog

        saved, asked, answers = [], [], []

        class Editor:
            @staticmethod
            def GetBoard(): return object()
            @staticmethod
            def SaveBoard(path, board, flag): return saved.append(path) is None
            @staticmethod
            def Refresh(): pass

        dialog = WorkflowDialog(pcbnew_module=Editor)

        def settle():
            deadline = time.time() + 10
            while dialog.controller.busy and time.time() < deadline:
                time.sleep(.01)
            self.app.Yield()

        def rows(view):
            return [tuple(view.GetItemText(row, column)
                          for column in range(view.GetColumnCount()))
                    for row in range(view.GetItemCount())]

        def stages():
            return [(line.status, line.count) for line in dialog.stage_view.lines]

        try:
            controller = dialog.controller
            preview = {
                'status': 'READY', 'conflicts': [], 'warnings': [],
                'changes': [{'key': 'J101-1', 'kind': 'add'}, {'key': 'J101-2', 'kind': 'add'},
                            {'key': 'J1', 'kind': 'update',
                             'side': {'before': 'F', 'after': 'B'}},
                            {'key': 'a.kicad_sch', 'kind': 'project-file'}],
                'desired': {'instances': {
                    key: {'footprint': {'library_id': 'Lib:GPPO'}, 'side': 'F',
                          'pose': [25.4, 2.54, 90]} for key in ('J101-1', 'J101-2')}},
                'registry': {},
            }
            blocking = Issue('part_missing', 'J102: select an approved connector footprint')
            warning = Issue('mpn_missing', 'J101: no manufacturer part number',
                            severity='warning')
            found = [[blocking, warning]]

            def validate():
                controller.issues = list(found[0])
                return controller.issues

            def review():
                controller.preview = preview
                return preview

            controller.validate, controller.review = validate, review
            dialog.save = lambda event: True
            dialog._ask = lambda message, yes, title=None: (
                asked.append(yes) or answers.pop(0))
            dialog.select_page(PAGES.index('Review changes'))
            self.assertEqual(dialog.review_button.GetLabel(), 'Review Changes')
            self.assertEqual(dialog.review_status.GetLabel(), 'Not reviewed yet.')
            self.assertFalse(dialog.apply_button.IsEnabled())
            self.assertIsNone(self.wx.FindWindowByLabel(
                'I reviewed these live-project changes', dialog))
            self.assertIsNone(self.wx.FindWindowByLabel('Cancel analysis', dialog))

            # Saving the PCB is the drafter's decision.
            answers.append(False)
            dialog.review_changes(None)
            self.assertEqual((asked, saved), (['Save and Review'], []))
            self.assertEqual(dialog.problem_list.GetItemCount(), 0)

            # Blocking problems stop the review and name the step that fixes them.
            answers.append(True)
            with self.assertRaisesRegex(ValueError, '1 problem blocks the review'):
                dialog.review_changes(None)
            self.assertEqual(len(saved), 1)
            dialog.refresh()
            self.assertEqual(dialog.review_status.GetLabel(),
                             'Blocked. Fix the problems below, then review again.')
            self.assertEqual(rows(dialog.problem_list)[0][:3], (
                'Error', 'Connector assignments',
                'J102: select an approved connector footprint'))
            self.assertEqual(rows(dialog.problem_list)[1][0], 'Warning')
            self.assertFalse(dialog.goto_step_button.IsEnabled())
            dialog.problem_list.Select(0)
            self.app.Yield()
            self.assertTrue(dialog.goto_step_button.IsEnabled())
            dialog.goto_problem_step(None)
            self.assertEqual(PAGES[dialog.book.GetSelection()], 'Connector assignments')

            # A clean review lists what Apply will do, in future tense.
            found[0] = [warning]
            answers.append(True)
            dialog.review_changes(None)
            self.assertEqual(dialog.review_status.GetLabel(),
                             'Reading the saved board and libraries…')
            settle()
            self.assertEqual(dialog.review_status.GetLabel(),
                             'Review is up to date with your settings.')
            self.assertEqual(rows(dialog.change_summary), [
                ('Parts to add', '2'), ('Part to change', '1'),
                ('Project file to write', '1')])
            self.assertEqual(dialog.change_detail_title.GetLabel(), 'Parts to add — 2 items')
            self.assertEqual(rows(dialog.change_detail)[0], (
                'J101-1', '—', 'Lib:GPPO, front, 90°, at 1000 mil, 100 mil'))
            dialog.change_summary.Select(1)
            self.app.Yield()
            self.assertEqual(dialog.change_detail_title.GetLabel(), 'Part to change — 1 item')
            self.assertEqual(rows(dialog.change_detail), [('J1', 'front', 'back')])
            self.assertEqual(dialog.apply_lead.GetLabel(), 'Ready to apply:')
            self.assertEqual(dialog.apply_headline.GetLabel(),
                             '2 parts to add, 1 part to change, 1 project file to write')
            self.assertTrue(dialog.apply_button.IsEnabled())
            self.assertEqual(stages(), [('optional', '')] * 5)

            # Apply asks once, then shows each named stage as it runs.
            answers.append(False)
            dialog.apply(None)
            self.assertEqual(asked[-1], 'Apply')
            self.assertFalse(dialog._applying)
            seen = []

            def publish(prepared, pcbnew_module, progress=None):
                seen.append(stages())
                progress()
                seen.append(stages())
                controller.preview = None
                controller.applied = True
                return {'status': 'APPLIED', 'board_path': '/project/board.kicad_pcb',
                        'report_path': '/project/report.json',
                        'journal': '/project/journal.json', 'registry': {}}

            controller.apply = lambda: {'status': 'PREPARED'}
            controller.publish = publish
            answers.append(True)
            dialog.apply(None)
            self.assertEqual(stages()[0], ('caution', 'running'))
            settle()
            self.assertEqual(seen[0][:4], [('done', ''), ('done', ''),
                                           ('caution', 'running'), ('optional', '')])
            self.assertEqual(seen[1][2:4], [('done', ''), ('caution', 'running')])
            self.assertEqual(stages(), [('done', '')] * 5)
            self.assertEqual(dialog.apply_gauge.GetValue(), 5)
            self.assertEqual(dialog.result_fields['board'].GetLabel(),
                             '/project/board.kicad_pcb (updated and saved)')
            self.assertEqual(dialog.result_fields['report'].GetLabel(), '/project/report.json')
            self.assertEqual(dialog.journal.GetValue(), '/project/journal.json')
            self.assertEqual(PAGES[dialog.book.GetSelection()], 'Apply / results')
            self.assertTrue(dialog.status.GetLabel().startswith('Apply finished'))
            self.assertIn('"status": "APPLIED"', dialog.messages.GetValue())

            # A failure stops at its stage and is reported in a pop-up.
            controller.preview = preview

            def refuse():
                raise RuntimeError('The schematic is open')

            controller.apply = refuse
            answers.append(True)
            with mock.patch.object(self.wx, 'MessageBox') as message:
                dialog.apply(None)
                settle()
                self.assertEqual(message.call_args[0][:2],
                                 ('The schematic is open', 'Apply stopped'))
            self.assertEqual(stages()[0], ('missing', 'stopped'))
            self.assertFalse(dialog._applying)
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_checklist_line_points_at_its_control_with_a_fading_outline(self):
        from plugins.connector_interposer_workflow.ui import Spotlight, WorkflowDialog

        # Fade in, hold a few seconds, fade out: no blinking, no jump.
        self.assertEqual(Spotlight.strength(0), 0)
        self.assertEqual(Spotlight.strength(Spotlight.FADE_IN / 2), .5)
        self.assertEqual(Spotlight.strength(Spotlight.FADE_IN), 1)
        self.assertEqual(Spotlight.strength(Spotlight.FADE_IN + Spotlight.HOLD), 1)
        self.assertEqual(
            Spotlight.strength(Spotlight.FADE_IN + Spotlight.HOLD + Spotlight.FADE_OUT / 2), .5)
        self.assertEqual(
            Spotlight.strength(Spotlight.FADE_IN + Spotlight.HOLD + Spotlight.FADE_OUT), 0)
        self.assertTrue(1000 <= Spotlight.HOLD < 10000)

        dialog = WorkflowDialog()
        try:
            dialog.Show()
            self.app.Yield()
            for step in PAGES:
                dialog.select_page(PAGES.index(step))
                for index, line in enumerate(progress.checklist(dialog.controller, step)):
                    target = dialog._checklist_target(step, index)
                    self.assertIsNotNone(target, f'{step}: {line.label}')
                    self.assertTrue(target.IsShownOnScreen(), f'{step}: {line.label}')
                self.assertIsNone(dialog._checklist_target(step, 99))

            dialog.select_page(PAGES.index('Mechanical placement'))
            selection = dialog.step_list.GetSelection()
            dialog.checklist.on_select(2, dialog.checklist.lines[2])
            self.assertEqual(dialog.placement_notebook.GetSelection(), 1)
            self.assertIs(dialog.spotlight.target, dialog.anchor_gang_list)
            self.assertTrue(dialog.spotlight.timer.IsRunning())
            self.assertEqual(dialog.step_list.GetSelection(), selection)
            ticks = 0
            while dialog.spotlight.target is not None and ticks < 1000:
                dialog.spotlight._tick(None)
                ticks += 1
            total = Spotlight.FADE_IN + Spotlight.HOLD + Spotlight.FADE_OUT
            self.assertEqual(ticks, math.ceil(total / Spotlight.TICK))
            self.assertFalse(dialog.spotlight.timer.IsRunning())

            # A control out of view is reached in several even scroll steps.
            page = dialog.pages[PAGES.index('Mechanical placement')]
            self.app.Yield()
            page.SetScrollbars(8, 8, 100, 500)
            page.Scroll(0, 300)
            self.assertEqual(page.GetViewStart()[1], 300)
            plan = dialog.spotlight._scroll_plan(dialog.user_layer)
            self.assertEqual(len(plan), Spotlight.SCROLL_STEPS)
            self.assertEqual(plan[-1][1], 0)
            self.assertEqual([y for _x, y in plan], sorted((y for _x, y in plan), reverse=True))
            dialog.spotlight.show(dialog.user_layer)
            for _ in range(Spotlight.SCROLL_STEPS):
                dialog.spotlight._tick(None)
            self.assertEqual(page.GetViewStart()[1], 0)

            # Leaving the step removes the outline at once.
            dialog.select_page(PAGES.index('Board outline'))
            self.assertIsNone(dialog.spotlight.target)
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_step_icons_and_checklist_follow_a_menu_choice_at_once(self):
        from plugins.connector_interposer_workflow.core.model import HEADERS
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet
        from plugins.connector_interposer_workflow.ui import WorkflowDialog

        dialog = WorkflowDialog()
        try:
            dialog.controller.table = parse_sheet(Worksheet('Signals', (
                HEADERS,
                ('CLOCK1', 'CLOCK', 'E1', 'A1', 'J101', '1', 'L1'),
                ('POWER1', 'POWER', 'E1', 'A2', 'J1', '1', 'L1'),
            )))
            dialog.controller.set_netclass_type('CLOCK', 'RF')
            dialog.controller.set_netclass_type('POWER', 'DC')
            dialog.refresh_netclass_types()
            step = PAGES.index('Netclass type')
            dialog.select_page(step)
            self.assertEqual(dialog.checklist.lines[0].count, '2 of 2')
            self.assertEqual(dialog.step_list.statuses[step], 'done')
            names = [dialog.netclass_type_grid.GetCellValue(row, 0)
                     for row in range(dialog.netclass_type_grid.GetNumberRows())]
            # The choice arrives from a pop-up menu, outside any guarded handler.
            dialog._set_netclass_type_choice(names.index('CLOCK'), '')
            self.assertEqual(dialog.checklist.lines[0].count, '1 of 2')
            self.assertEqual(dialog.step_list.statuses[step], 'missing')
            # Any other settings change is followed too, once the event loop turns.
            dialog.controller.set_netclass_type('CLOCK', 'RF')
            self.app.Yield()
            self.assertEqual(dialog.checklist.lines[0].count, '2 of 2')
            self.assertEqual(dialog.step_list.statuses[step], 'done')
            # Define RF gangs has no message area: a wrong entry is marked in place.
            from plugins.connector_interposer_workflow.ui import ui_palette
            dialog.refresh_gangs()
            dialog.select_page(PAGES.index('Define RF gangs'))
            dialog.Show()
            self.app.Yield()
            error = ui_palette().error
            total = dialog.gang_position_total
            self.assertEqual(total.GetLabel(), '0 of 1 positions')
            dialog.add_gang_row(None)
            self.assertFalse(dialog.gang_definition_status.IsShown())
            self.assertEqual(dialog.gang_rows.GetCellBackgroundColour(0, 1), error)
            self.assertIn('enter a position count', total.GetToolTipText())
            self.assertIn('J101: Needs input', dialog.messages.GetValue())
            dialog.gang_rows.SetCellValue(0, 1, '3')
            dialog.gang_draft_changed()
            self.assertNotEqual(dialog.gang_rows.GetCellBackgroundColour(0, 1), error)
            self.assertEqual(dialog.gang_pitch_x.text.GetBackgroundColour(), error)
            self.assertEqual(total.GetLabel(), '3 of 1 positions')
            self.assertEqual(total.GetForegroundColour(), ui_palette().status['missing'])
            dialog.gang_rows.SetCellValue(0, 1, '1')
            dialog.gang_draft_changed()
            self.assertNotEqual(dialog.gang_pitch_x.text.GetBackgroundColour(), error)
            self.assertEqual(total.GetLabel(), '1 of 1 positions')
            self.assertEqual(total.GetToolTipText(), '')
            self.assertTrue(dialog.controller.gang('J101')['defined'])
            self.assertFalse(dialog.gang_definition_status.IsShown())
            self.assertFalse(dialog.gang_preview_status.IsShown())
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_dimension_inputs_normalize_suffixes_and_store_connector_placement_in_mm(self):
        from plugins.connector_interposer_workflow.core.model import HEADERS
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet
        from plugins.connector_interposer_workflow.ui import WorkflowDialog

        dialog = WorkflowDialog()
        try:
            dialog.controller.table = parse_sheet(Worksheet('Signals', (
                HEADERS,
                ('CLOCK1', 'CLOCK', 'E1', 'A1', 'J101', '1', 'L1'),
            )))
            dialog.controller.set_netclass_type('CLOCK', 'RF')
            dialog.controller.config['interposers']['E1'] = {'symbol': {'path': 'selected'}}
            dialog.controller.config['interposers']['E1']['pose_circle'] = {
                'uuid': 'center-circle', 'center': [25.4, 25.0],
            }
            dialog.interposer_side.SetSelection(1)
            dialog._interposer_placement_changed()
            selection = dialog.controller.config['interposers']['E1']
            self.assertEqual(selection['pose'][:2], [25.4, 25.0])
            self.assertEqual(selection['side'], 'B')

            self.assertEqual(dialog.feature_tolerance.GetValue(), '0.3')
            dialog.feature_tolerance.ChangeValue('0.4 mil')

            dialog.refresh_gangs()
            dialog._rf_anchor_changed(source_circle={
                'uuid': 'rf-circle', 'center': [2.0, 2.54],
            })
            gang = dialog.controller.gang('J101')
            self.assertEqual(gang['anchor'], [2.0, 2.54])
            self.assertTrue(gang['anchor_accepted'])
            self.assertTrue(dialog.controller.config['mechanical']['accepted'])
            self.assertAlmostEqual(
                dialog.controller.config['mechanical']['feature_tolerance_mm'], .01016,
            )
            self.assertEqual(
                dialog.controller.config['mechanical']['feature_tolerance_unit'], 'mil',
            )
            dialog.controller.config['mounting_holes'] = [{
                'reference': 'H1', 'library_id': 'Example:Hole',
                'pose': [25.4, 2.54, 0], 'pose_units': ['inch', 'mil'],
            }]
            dialog.refresh_mounting_holes()
            # Readouts follow the toolbar unit, at full precision, in either unit.
            self.assertEqual(dialog.hole_list.GetItemText(0, 2), '1000 mil')
            self.assertEqual(dialog.hole_list.GetItemText(0, 3), '100 mil')
            dialog.set_readout_unit('mm')
            self.assertEqual(dialog.hole_list.GetItemText(0, 2), '25.4 mm')
            self.assertEqual(dialog.hole_list.GetItemText(0, 3), '2.54 mm')
            self.assertEqual(dialog.unit_button.unit, 'mm')
            self.assertIs(dialog.unit_button.GetBitmap(), dialog.unit_button.bitmaps['mm'])
            # A click steps mm, in, mil as the PCB Editor's units button does.
            dialog.unit_button.press()
            self.assertEqual(dialog.readout_unit, 'in')
            self.assertEqual(dialog.hole_list.GetItemText(0, 2), '1 in')
            dialog.unit_button.press()
            self.assertEqual(dialog.readout_unit, 'mil')
            self.assertEqual(dialog.hole_list.GetItemText(0, 2), '1000 mil')
            # Holding the button opens every unit; picking one applies it.
            dialog.unit_button.open_palette()
            self.assertEqual(tuple(dialog.unit_button.items), ('mm', 'in', 'mil'))
            self.assertTrue(dialog.unit_button.items['mil'].GetValue())
            dialog.unit_button.items['mm'].press()
            self.assertEqual(dialog.readout_unit, 'mm')
            self.assertIsNone(dialog.unit_button.palette)
            dialog.set_readout_unit('mil')
            self.assertEqual(dialog._coordinate_text([25.4, 2.54]), '1000 mil, 100 mil')
            failure = (
                'J101-1: center deviation from its drawing circle is '
                '0.01 mm (0.393701 mil); limit is 0.00762 mm (0.3 mil)'
            )
            # A failed review is a pop-up and a row under Problems, in plain words.
            with mock.patch.object(self.wx, 'MessageBox') as message:
                dialog.failed(ValueError(failure))
                self.assertEqual(message.call_args[0][:2], (failure, 'Review stopped'))
            row = dialog.problem_list.GetItemCount() - 1
            self.assertEqual(
                [dialog.problem_list.GetItemText(row, column) for column in range(3)],
                ['Error', 'Review changes', failure])
        finally:
            dialog.Destroy()
            self.app.Yield()

    def test_explicit_live_circle_selection_stores_inspected_saved_uuid(self):
        from types import SimpleNamespace
        from plugins.connector_interposer_workflow.ui import WorkflowDialog

        dialog = WorkflowDialog()
        try:
            dialog.user_layer.ChangeValue('Dwgs.User')
            mechanical = dialog.controller.config['mechanical']
            mechanical.update(
                layer='Dwgs.User', inspected_revision='drawing-revision',
                feature_tolerance_mm=.00762,
            )
            dialog._inspected_revision = 'drawing-revision'
            dialog._inspected_layer = 'Dwgs.User'
            dialog._inspected_circles = ({
                'uuid': 'saved-board-uuid', 'center': [12.0, 34.0],
                'radius': 1.5,
            },)
            live = SimpleNamespace(
                m_Uuid=SimpleNamespace(AsString=lambda: 'live-canvas-uuid'),
                GetRadius=lambda: 1500000,
            )
            self.assertEqual(dialog._circle_record(live, [12.0, 34.0]), {
                'uuid': 'saved-board-uuid', 'center': [12.0, 34.0],
                'revision': 'drawing-revision',
            })
            dialog._inspected_circles += ({
                'uuid': 'ambiguous-saved-uuid', 'center': [12.0, 34.0],
                'radius': 1.5,
            },)
            with self.assertRaisesRegex(ValueError, 'resolve uniquely'):
                dialog._circle_record(live, [12.0, 34.0])
        finally:
            dialog.Destroy()
            self.app.Yield()




class NativeControllerEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import pcbnew
        except ImportError:
            raise unittest.SkipTest('Native controller integration needs KiCad Python')
        cls.p = pcbnew

    def test_saved_native_population_to_live_project_and_reopen_noop(self):
        from plugins.connector_interposer_workflow.controller import NativeProcessBackend
        from plugins.connector_interposer_workflow.backend import NativeBackend
        from test_connector_interposer_schematic import fixture
        import csv
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'source'
            source.mkdir()
            library = root/'approved.pretty'
            library.mkdir()
            records, gang_parts, interposer_parts = fixture(library, 3)
            path = source/'demo.kicad_pcb'
            board = self.p.BOARD()
            circle_ids = {}
            for index, record in enumerate(records):
                center = self.p.VECTOR2I((20+index*4)*1000000, 30*1000000)
                circle = self.p.PCB_SHAPE(board)
                circle.SetShape(self.p.SHAPE_T_CIRCLE)
                circle.SetLayer(self.p.Dwgs_User)
                circle.SetCenter(center)
                circle.SetEnd(self.p.VECTOR2I(center.x+1000000, center.y))
                circle.SetWidth(50000)
                board.Add(circle)
                circle_ids[record.key] = circle.m_Uuid.AsString()
                label = self.p.PCB_TEXT(board)
                label.SetText(record.key)
                label.SetLayer(self.p.Dwgs_User)
                label.SetPosition(self.p.VECTOR2I(center.x-2540000, center.y-2540000))
                board.Add(label)
            interposer_circle = self.p.PCB_SHAPE(board)
            interposer_circle.SetShape(self.p.SHAPE_T_CIRCLE)
            interposer_circle.SetLayer(self.p.Dwgs_User)
            interposer_circle.SetCenter(self.p.VECTOR2I(70000000, 60000000))
            interposer_circle.SetEnd(self.p.VECTOR2I(70500000, 60000000))
            board.Add(interposer_circle)
            interposer_circle_uuid = interposer_circle.m_Uuid.AsString()
            self.p.SaveBoard(str(path), board)
            (source/'demo.kicad_pro').write_text('{"board":{"designer_setting":42}}')
            workbook = root/'signals.csv'
            with workbook.open('w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(('Signal','NetClass','Interposer','Interposer Pin','Connector','Connector Pin','Layer'))
                for record in records: writer.writerow((record.signal, record.netclass, record.interposer, record.interposer_pin, record.connector, record.connector_pin, record.layer))
            controller = WorkflowController(path, backend=NativeProcessBackend())
            controller.load_workbook(workbook)
            controller.set_netclass_type('RF', 'RF')
            controller.set_layer_mapping('L1', 'F.Cu')
            inventory = controller.backend.call(
                'inspect', board_path=str(path), source_layer='Dwgs.User',
            )
            revision = inventory['revision']
            symbol = library/'Approved.kicad_sym'
            symbol_sha = hashlib.sha256(symbol.read_bytes()).hexdigest()
            controller.set_gang('J101', {'part_id': gang_parts['J101'].footprint.path, 'library_id': 'Approved:GPPO',
                                       'part_sha256': gang_parts['J101'].footprint.sha256,
                                       'symbol': {'path': str(symbol), 'library_id': 'Approved:GPPO', 'sha256': symbol_sha},
                                       'row_lengths': [3], 'pitch_x': 4, 'anchor': [20, 30],
                                       'anchor_circle': {'uuid': circle_ids['J101-1'],
                                                         'center': [20, 30],
                                                         'revision': revision},
                                       'anchor_accepted': True, 'defined': True})
            part = interposer_parts['E1'].footprint
            controller.config['interposers']['E1'] = dict(
                part.pin(), approved=True, pose=[70, 60, 0], pose_accepted=True, side='F',
                pose_circle={'uuid': interposer_circle_uuid, 'center': [70, 60],
                             'revision': revision},
                symbol={'path': str(symbol), 'library_id': 'Approved:Interposer', 'sha256': symbol_sha},
            )
            controller.config['mechanical'] = {
                'layer': 'Dwgs.User', 'accepted': True,
                'feature_tolerance_mm': .00762,
                'feature_tolerance_unit': 'mil',
                'inspected_revision': revision,
                'inspected_geometry': inventory['geometry_revision'],
            }
            before = path.read_bytes()
            controller.set_gang('J101', {'anchor': [21, 30]})
            with self.assertRaisesRegex(
                ValueError,
                r'J101-1: center deviation.*1 mm.*limit.*0\.00762 mm.*0\.3 mil',
            ):
                controller.review()
            controller.set_gang('J101', {'anchor': [20, 30]})
            preview = controller.review()
            self.assertEqual(preview['conflicts'], [])
            self.assertEqual(
                preview['desired']['mechanical']['interposer_anchors']['E1']['circle_uuid'],
                interposer_circle_uuid,
            )
            self.assertNotIn('rf', preview['desired']['instances']['J101-1'])
            import io
            bom_rows = {row['Reference']: row for row in csv.DictReader(io.StringIO(preview['desired']['project_files']['demo-connector-bom.csv']))}
            for reference, instance in preview['desired']['instances'].items():
                self.assertEqual(instance['symbol_fields'], {name: bom_rows[reference][name] for name in ('Value', 'MPN')})
            prepared = controller.apply()
            result = NativeBackend(self.p).publish_live(
                self.p.LoadBoard(str(path)), preview, prepared,
            )
            self.assertEqual(result['status'], 'APPLIED')
            self.assertNotEqual(before, path.read_bytes())
            output = Path(result['board_path'])
            self.assertEqual(output, path.resolve())
            self.assertTrue(output.with_suffix('.kicad_sch').exists())
            footprints = {footprint.GetReference(): footprint for footprint in self.p.LoadBoard(str(output)).GetFootprints()}
            self.assertEqual(len(footprints), 4)
            for reference, footprint in footprints.items():
                self.assertEqual(footprint.GetValue(), bom_rows[reference]['Value'])
                self.assertEqual(footprint.GetFieldText('MPN'), bom_rows[reference]['MPN'])
            import json
            project = json.loads(output.with_suffix('.kicad_pro').read_text())
            self.assertEqual(project['board']['designer_setting'], 42)
            self.assertEqual(project['net_settings']['netclass_assignments']['SIG1'], ['RF'])
            reopened = WorkflowController(output, backend=NativeProcessBackend())
            saved = reopened.config['workbook']
            reopened.load_workbook(saved['path'], saved['worksheet'], saved['header_row'], saved['ignored_rows'], saved['mapping'])
            repeated = reopened.review()
            self.assertEqual(repeated['status'], 'NOOP', str(repeated['changes']) + str(repeated['conflicts']))
            stamp = output.stat().st_mtime_ns
            self.assertEqual(reopened.apply()['status'], 'NOOP')
            self.assertEqual(output.stat().st_mtime_ns, stamp)
            import shutil
            shutil.rmtree(library)
            self.assertEqual(reopened.review()['status'], 'NOOP')

    def test_mixed_rf_and_dc_controller_apply_keeps_one_dc_instance_and_nc_pad(self):
        from plugins.connector_interposer_workflow.controller import NativeProcessBackend
        from plugins.connector_interposer_workflow.backend import NativeBackend
        from plugins.connector_interposer_workflow.core import SignalRecord, read_footprint
        from test_connector_interposer_schematic import fixture
        import csv
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'; source.mkdir()
            library = root / 'approved.pretty'; library.mkdir()
            _fixture_records, gang_parts, interposer_parts = fixture(library, 3)
            dc_footprint = library / 'DSub.kicad_mod'
            dc_footprint.write_text(
                '(footprint "DSub" (layer "F.Cu") '
                '(pad "1" thru_hole circle (at 5 7) (size 1 1) (drill .5) (layers "*.Cu" "*.Mask")) '
                '(pad "2" thru_hole circle (at 7 7) (size 1 1) (drill .5) (layers "*.Cu" "*.Mask")) '
                '(pad "3" thru_hole circle (at 9 7) (size 1 1) (drill .5) (layers "*.Cu" "*.Mask")))'
            )
            dc_symbol = root / 'DC.kicad_sym'
            dc_symbol.write_text('''(kicad_symbol_lib (version 20241209) (generator "test")
              (symbol "DSub" (pin_names (offset 0)) (in_bom yes) (on_board yes)
                (property "Reference" "J" (at 0 5 0) (effects (font (size 1 1))))
                (property "Value" "DSub" (at 0 7 0) (effects (font (size 1 1))))
                (property "ki_fp_filters" "DSub" (at 0 0 0) (effects (font (size 1 1)) hide))
                (symbol "DSub_1_1"
                  (pin passive line (at 0 0 180) (length 2.54) (name "1" (effects (font (size .8 .8)))) (number "1" (effects (font (size .8 .8)))))
                  (pin passive line (at 0 -5.08 180) (length 2.54) (name "2" (effects (font (size .8 .8)))) (number "2" (effects (font (size .8 .8)))))
                  (pin passive line (at 0 -10.16 180) (length 2.54) (name "3" (effects (font (size .8 .8)))) (number "3" (effects (font (size .8 .8))))))))''')
            records = (
                SignalRecord('RF1', 'RF', 'E1', 'A1', 'J101', '1', 'L1'),
                SignalRecord('GND', 'DC', 'E1', 'A2', 'J1', '1', 'L1'),
                SignalRecord('PWR', 'DC', 'E1', 'A3', 'J1', '2', 'L1'),
            )
            board = self.p.BOARD()
            pin1_uuid = None
            rf_pin1_uuid = None
            interposer_center_uuid = None
            for x, y, label in ((20, 30, 'J101-1'), (50, 50, ''),
                                (52, 50, ''), (54, 50, ''), (70, 60, '')):
                center = self.p.VECTOR2I(x * 1000000, y * 1000000)
                circle = self.p.PCB_SHAPE(board); circle.SetShape(self.p.SHAPE_T_CIRCLE)
                circle.SetLayer(self.p.Dwgs_User); circle.SetCenter(center)
                circle.SetEnd(self.p.VECTOR2I(center.x + 500000, center.y)); board.Add(circle)
                if x == 50:
                    pin1_uuid = circle.m_Uuid.AsString()
                elif x == 20:
                    rf_pin1_uuid = circle.m_Uuid.AsString()
                elif x == 70:
                    interposer_center_uuid = circle.m_Uuid.AsString()
                if label:
                    text = self.p.PCB_TEXT(board); text.SetText(label); text.SetLayer(self.p.Dwgs_User)
                    text.SetPosition(center); board.Add(text)
            board_path = source / 'mixed.kicad_pcb'
            self.p.SaveBoard(str(board_path), board)
            (source / 'mixed.kicad_pro').write_text('{}')
            workbook = root / 'mixed.csv'
            with workbook.open('w', newline='') as stream:
                writer = csv.writer(stream); writer.writerow(
                    ('Signal', 'NetClass', 'Interposer', 'Interposer Pin',
                     'Connector', 'Connector Pin', 'Layer')
                )
                for record in records:
                    writer.writerow(tuple(getattr(record, field) for field in
                                          ('signal', 'netclass', 'interposer', 'interposer_pin',
                                           'connector', 'connector_pin', 'layer')))
            controller = WorkflowController(board_path, backend=NativeProcessBackend())
            controller.load_workbook(workbook)
            controller.set_netclass_type('RF', 'RF'); controller.set_netclass_type('DC', 'DC')
            controller.set_layer_mapping('L1', 'F.Cu')
            inventory = controller.backend.call(
                'inspect', board_path=str(board_path), source_layer='Dwgs.User',
            )
            revision = inventory['revision']
            approved_symbols = library / 'Approved.kicad_sym'
            approved_sha = hashlib.sha256(approved_symbols.read_bytes()).hexdigest()
            controller.set_gang('J101', {
                'part_id': gang_parts['J101'].footprint.path, 'library_id': 'Approved:GPPO',
                'part_sha256': gang_parts['J101'].footprint.sha256,
                'symbol': {'path': str(approved_symbols), 'library_id': 'Approved:GPPO',
                           'sha256': approved_sha},
                'row_lengths': [1], 'anchor': [20, 30], 'anchor_accepted': True,
                'anchor_circle': {'uuid': rf_pin1_uuid, 'center': [20, 30],
                                  'revision': revision},
                'defined': True,
            })
            transition = write_transition(
                root / 'rf.gppo-rf.json', 'Approved:GPPO',
                gang_parts['J101'].footprint.sha256,
            )
            for requirement in controller.rf_transition_requirements():
                controller.set_rf_transition(requirement['key'], transition)
            dc_part = read_footprint(dc_footprint, 'Approved:DSub')
            controller.set_dc_connector('J1', {
                'path': dc_part.path, 'library_id': dc_part.library_id,
                'sha256': dc_part.sha256,
                'symbol': {'path': str(dc_symbol), 'library_id': 'Approved:DSub',
                           'sha256': hashlib.sha256(dc_symbol.read_bytes()).hexdigest()},
                'anchor': [50, 50], 'anchor_circle': {
                    'uuid': pin1_uuid, 'center': [50, 50], 'revision': revision,
                },
                'rotation': 0, 'side': 'F', 'anchor_accepted': True,
            })
            interposer = interposer_parts['E1'].footprint
            controller.config['interposers']['E1'] = dict(
                interposer.pin(), approved=True, pose=[70, 60, 0], pose_accepted=True, side='F',
                pose_circle={'uuid': interposer_center_uuid, 'center': [70, 60],
                             'revision': revision},
                symbol={'path': str(approved_symbols), 'library_id': 'Approved:Interposer',
                        'sha256': approved_sha},
            )
            controller.config['mechanical'] = {
                'layer': 'Dwgs.User', 'accepted': True,
                'feature_tolerance_mm': .00762,
                'feature_tolerance_unit': 'mil',
                'inspected_revision': revision,
                'inspected_geometry': inventory['geometry_revision'],
            }
            preview = controller.review()
            self.assertEqual(set(preview['desired']['instances']), {'J101-1', 'J1', 'E1'})
            self.assertEqual(preview['desired']['instances']['J1']['pads'], {'1': 'GND', '2': 'PWR'})
            prepared = controller.apply()
            result = NativeBackend(self.p).publish_live(
                self.p.LoadBoard(str(board_path)), preview, prepared,
            )
            output_board = self.p.LoadBoard(result['board_path'])
            placed = {footprint.GetReference(): footprint for footprint in output_board.GetFootprints()}
            self.assertEqual(set(placed), {'J101-1', 'J1', 'E1'})
            dc_pads = {pad.GetNumber(): pad for pad in placed['J1'].Pads()}
            self.assertEqual((dc_pads['1'].GetPosition().x, dc_pads['1'].GetPosition().y),
                             (50000000, 50000000))
            self.assertEqual(dc_pads['3'].GetNetname(), '')
            import json
            import shutil
            import subprocess
            cli = ([os.environ['KICAD_TEST_APP_RUN'], 'kicad-cli']
                   if os.environ.get('KICAD_TEST_APP_RUN') else [shutil.which('kicad-cli')])
            if cli[0]:
                report_path = root / 'mixed-erc.json'
                checked = subprocess.run(
                    cli + ['sch', 'erc', '--format', 'json', '-o', str(report_path),
                           str(Path(result['board_path']).with_suffix('.kicad_sch'))],
                    capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                report = json.loads(report_path.read_text())
                errors = [violation for sheet in report['sheets']
                          for violation in sheet['violations']
                          if violation.get('severity') == 'error']
                self.assertEqual(errors, [])


if __name__ == '__main__': unittest.main()
