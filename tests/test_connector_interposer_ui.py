"""Workflow session invariants and optional real native-dialog smoke coverage."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugins.connector_interposer_workflow.controller import WorkflowController, row_ranges, PAGES


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

    def test_layer_policy_survives_stackup_and_class_growth(self):
        self.controller.set_rule('RF', 'F.Cu', False)
        self.controller.set_layers(('F.Cu', 'In1.Cu', 'B.Cu'))
        permissions = self.controller.config['layer_rules']
        self.assertFalse(permissions['RF']['F.Cu'])
        self.assertTrue(permissions['RF']['In1.Cu'])
        self.controller.set_layer_mapping('L1', 'F.Cu')
        issues = self.controller.validate()
        self.assertEqual([issue.source_row for issue in issues if issue.code == 'layer_disallowed'], [3, 4])

    def test_independent_gangs_and_explicit_copy_preserve_anchors(self):
        self.controller.set_gang('J101', {'pitch_x': 4, 'anchor': [30, 20], 'anchor_accepted': True})
        self.controller.set_gang('J202', {'pitch_x': 9, 'anchor': [80, 40], 'anchor_accepted': True})
        self.assertEqual(self.controller.pattern('J101')['2'], (34, 20))
        self.assertEqual(self.controller.gang('J202')['pitch_x'], 9)
        self.controller.copy_gang('J101', ['J202'])
        self.assertEqual(self.controller.gang('J202')['pitch_x'], 4)
        self.assertEqual(self.controller.gang('J202')['anchor'], [80, 40])
        self.assertEqual(self.controller.gang('J101')['anchor'], [30, 20])

    def test_changed_external_artifact_blocks_apply(self):
        artifact = self.root/'rf.json'
        artifact.write_text('{"revision":"A"}')
        self.controller.set_gang('J101', {'rf_artifact': str(artifact)})
        self.controller.preview = {'reviewed': True}
        self.controller.preview_token = self.controller.token()
        artifact.write_text('{"revision":"B"}')
        with self.assertRaisesRegex(ValueError, 'stale'): self.controller.apply()
        self.assertIsNone(self.controller.preview)

    def test_destination_selection_does_not_authorize_a_changed_preview(self):
        before = self.controller.token()
        self.controller.config['output_dir'] = str(self.root/'output')
        self.assertEqual(before, self.controller.token())
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
        self.assertIn('cancelled', errors[0])


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

    def test_native_page_hierarchy_and_full_10000_row_popup(self):
        from plugins.connector_interposer_workflow.ui import (WorkflowDialog, DataDialog,
                                                               LibraryPartPickerDialog,
                                                               LibrarySelectionCtrl,
                                                               AssignmentRenderer)
        from plugins.connector_interposer_workflow.core.library_catalog import LibraryEntry, LibraryPart
        from plugins.connector_interposer_workflow.core.workbook import Worksheet, parse_sheet
        from plugins.connector_interposer_workflow.core.model import HEADERS
        dialog = WorkflowDialog()
        self.assertEqual(tuple(dialog.book.GetPageText(i) for i in range(dialog.book.GetPageCount())), PAGES)
        self.assertEqual(PAGES[0], 'Netlist Source')
        for page_index, page_name in enumerate(PAGES):
            self.assertIsNotNone(self.wx.FindWindowByLabel(page_name, dialog.pages[page_index]))
        self.assertFalse(dialog.view_button.IsEnabled())
        self.assertFalse(dialog.apply_button.IsEnabled())
        self.assertFalse(dialog.workbook_path.IsShown())
        self.assertEqual(dialog.mapper.GetNumberRows(), 6)
        self.assertEqual(dialog.mapper.GetNumberCols(), 9)
        self.assertIsInstance(dialog.mapper.GetCellRenderer(0, 2), AssignmentRenderer)
        self.assertIsNotNone(self.wx.FindWindowByLabel('Open full table…', dialog))
        self.assertIsInstance(dialog.interposer_path, LibrarySelectionCtrl)
        self.assertIsInstance(dialog.connector_symbol, LibrarySelectionCtrl)
        self.assertIsInstance(dialog.gang_fields['part_id'], LibrarySelectionCtrl)
        self.assertIsInstance(dialog.hole_path, LibrarySelectionCtrl)
        self.assertIsInstance(dialog.gang_fields['rf_artifact'], self.wx.TextCtrl)
        self.assertNotIsInstance(dialog.gang_fields['rf_artifact'], LibrarySelectionCtrl)
        dialog.gang_fields['part_id'].set_selection({
            'path': '/internal/Approved.pretty/GPPO.kicad_mod',
            'library_id': 'Approved:GPPO',
            'sha256': 'a' * 64,
        })
        self.assertEqual(dialog.gang_fields['part_id'].display.GetValue(), 'Approved:GPPO')
        self.assertEqual(dialog.gang_fields['part_id'].GetValue(),
                         '/internal/Approved.pretty/GPPO.kicad_mod')
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
        self.app.Yield()

    def test_spreadsheet_auto_mapping_uses_populated_columns_and_live_examples(self):
        from plugins.connector_interposer_workflow.ui import WorkflowDialog
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
                                 (220, 235, 250))
                self.assertEqual(len(dialog._spreadsheet_columns), 7)
                self.assertFalse(any(label.startswith('H ·') for label in dialog._spreadsheet_columns))
                self.assertEqual(dialog.columns_status.GetLabel(), '7 of 7 columns assigned')
                self.assertEqual(dialog.nets_status.GetLabel(), '2 nets parsed')

                dialog.ignored.ChangeValue('2')
                dialog._parse_spreadsheet('mapping')
                self.assertEqual(dialog.mapper.GetCellValue(1, 1), '3')
                self.assertEqual(dialog.mapper.GetCellValue(1, 2), 'DATA1')
                self.assertEqual(dialog.nets_status.GetLabel(), '1 net parsed')

                dialog.mapper.SetCellValue(0, 2, 'B · NetClass')
                dialog.mapper.SetCellValue(0, 3, 'A · Signal')
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
                                 (255, 244, 214))
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




class NativeControllerEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import pcbnew
        except ImportError:
            raise unittest.SkipTest('Native controller integration needs KiCad Python')
        cls.p = pcbnew

    def test_saved_native_population_to_project_copy_and_reopen_noop(self):
        from plugins.connector_interposer_workflow.controller import NativeProcessBackend
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
            for index, record in enumerate(records):
                center = self.p.VECTOR2I((20+index*4)*1000000, 30*1000000)
                circle = self.p.PCB_SHAPE(board)
                circle.SetShape(self.p.SHAPE_T_CIRCLE)
                circle.SetLayer(self.p.Dwgs_User)
                circle.SetCenter(center)
                circle.SetEnd(self.p.VECTOR2I(center.x+1000000, center.y))
                circle.SetWidth(50000)
                board.Add(circle)
                label = self.p.PCB_TEXT(board)
                label.SetText(record.key)
                label.SetLayer(self.p.Dwgs_User)
                label.SetPosition(self.p.VECTOR2I(center.x-2540000, center.y-2540000))
                board.Add(label)
            self.p.SaveBoard(str(path), board)
            (source/'demo.kicad_pro').write_text('{"board":{"designer_setting":42}}')
            workbook = root/'signals.csv'
            with workbook.open('w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(('Signal','NetClass','Interposer','Interposer Pin','Connector','Connector Pin','Layer'))
                for record in records: writer.writerow((record.signal, record.netclass, record.interposer, record.interposer_pin, record.connector, record.connector_pin, record.layer))
            controller = WorkflowController(path, backend=NativeProcessBackend())
            controller.load_workbook(workbook)
            controller.set_layer_mapping('L1', 'F.Cu')
            controller.set_gang('J101', {'part_id': gang_parts['J101'].footprint.path, 'library_id': 'Approved:GPPO',
                                       'row_lengths': [3], 'pitch_x': 4, 'anchor': [20, 30], 'anchor_accepted': True, 'mpn': 'APPROVED-MPN'})
            part = interposer_parts['E1'].footprint
            controller.config['interposers']['E1'] = dict(part.pin(), approved=True, pose=[70, 60, 0])
            symbol = library/'Approved.kicad_sym'
            controller.config['connector_symbol'] = {'path': str(symbol), 'library_id': 'Approved:GPPO', 'sha256': hashlib.sha256(symbol.read_bytes()).hexdigest()}
            controller.config['mechanical'] = {'layer': 'Dwgs.User', 'accepted': True, 'label_offset': [-2.54, -2.54]}
            before = path.read_bytes()
            controller.set_gang('J101', {'anchor': [21, 30]})
            with self.assertRaisesRegex(ValueError, 'does not match mechanical circle'):
                controller.review()
            controller.set_gang('J101', {'anchor': [20, 30]})
            preview = controller.review()
            self.assertEqual(preview['conflicts'], [])
            import io
            bom_rows = {row['Reference']: row for row in csv.DictReader(io.StringIO(preview['desired']['project_files']['demo-connector-bom.csv']))}
            for reference, instance in preview['desired']['instances'].items():
                self.assertEqual(instance['symbol_fields'], {name: bom_rows[reference][name] for name in ('Value', 'MPN')})
            controller.config['output_dir'] = str(root/'output')
            result = controller.apply()
            self.assertEqual(result['status'], 'APPLIED')
            self.assertEqual(before, path.read_bytes())
            output = Path(result['board_path'])
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
            reopened.config['output_dir'] = str(root/'unused')
            self.assertEqual(reopened.apply()['status'], 'NOOP')
            self.assertEqual(output.stat().st_mtime_ns, stamp)
            import shutil
            shutil.rmtree(library)
            self.assertEqual(reopened.review()['status'], 'NOOP')


if __name__ == '__main__': unittest.main()
